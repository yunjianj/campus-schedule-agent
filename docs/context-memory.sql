-- v36 memory migration. Run after schema.sql. Safe to apply again.
create table if not exists public.context_documents (
    user_id text primary key default 'demo',
    version bigint not null default 0,
    sessions jsonb not null default '{}'::jsonb,
    retention_days integer not null default 90 check (retention_days between 1 and 3650),
    pending_grace_days integer not null default 7 check (pending_grace_days between 1 and 30),
    auto_delete_enabled boolean not null default true,
    updated_at timestamptz not null default now()
);
create table if not exists public.context_entries (
    id uuid primary key default gen_random_uuid(),
    seq bigint generated always as identity unique,
    user_id text not null references public.context_documents(user_id),
    conversation_id text not null,
    turn_key text not null,
    status text not null default 'pending' check (status in ('pending','complete','error')),
    user_text text not null,
    assistant_text text not null default '',
    summary text not null default '',
    topics jsonb not null default '[]'::jsonb,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default clock_timestamp(),
    finished_at timestamptz,
    last_accessed_at timestamptz not null default clock_timestamp(),
    unique(user_id,conversation_id,turn_key)
);
create index if not exists context_entries_user_time on public.context_entries(user_id,created_at desc);
create index if not exists context_entries_session on public.context_entries(user_id,conversation_id,seq desc);
create index if not exists context_entries_expiry on public.context_entries(user_id,last_accessed_at);
alter table public.context_documents enable row level security;
alter table public.context_entries enable row level security;
drop policy if exists demo_context_documents on public.context_documents;
create policy demo_context_documents on public.context_documents for all using(user_id='demo') with check(user_id='demo');
drop policy if exists demo_context_entries on public.context_entries;
create policy demo_context_entries on public.context_entries for all using(user_id='demo') with check(user_id='demo');
grant select,insert,update,delete on public.context_documents,public.context_entries to anon;
grant usage,select on sequence public.context_entries_seq_seq to anon;

-- Only consuming a recalled entry refreshes last_accessed_at. Index reads do not.
-- An unfinished operation pins its source list for at most the configured grace
-- period after the session's last activity. Business tables/profile are untouched.
create or replace function public.cleanup_context_memory(p_user_id text default 'demo')
returns integer language plpgsql security invoker set search_path=public as $$
declare
    d public.context_documents;
    removed integer := 0;
    n integer;
    s record;
    st jsonb;
    protected uuid[];
begin
    select * into d from public.context_documents where user_id=p_user_id for update;
    if not found then return 0; end if;
    select coalesce(array_agg((v.value->'state'->'active_list'->>'scope_id')::uuid),array[]::uuid[])
      into protected from jsonb_each(d.sessions) v
      where coalesce(v.value->'state'->>'active_operation','') in ('add','update','delete','plan','review')
      and (v.value->>'last_activity_at')::timestamptz > now()-make_interval(days=>least(d.pending_grace_days,d.retention_days))
      and coalesce(v.value->'state'->'active_list'->>'scope_id','') ~ '^[0-9a-f-]{36}$';
    update public.context_entries set status='error',finished_at=clock_timestamp(),summary='请求未正常完成，未记录到最终回复'
      where user_id=p_user_id and status='pending' and created_at<now()-interval '7 days';
    delete from public.context_entries e where e.id in (
        select id from public.context_entries where user_id=p_user_id
        and last_accessed_at < now()-make_interval(days=>d.retention_days)
        and d.auto_delete_enabled and not (id=any(protected)) order by last_accessed_at limit 5000
    );
    get diagnostics removed=row_count;
    for s in select key,value from jsonb_each(d.sessions) loop
        st:=coalesce(s.value->'state','{}'::jsonb);
        if d.auto_delete_enabled and (s.value->>'last_activity_at')::timestamptz < now()-make_interval(days=>d.retention_days) then
            st:='{}'::jsonb;
        elsif coalesce(st->'active_list'->>'scope_id','')<>'' and not exists (
            select 1 from public.context_entries where user_id=p_user_id and id::text=st->'active_list'->>'scope_id'
        ) then
            st:=st-'active_list';
        end if;
        if coalesce(st->>'focus_entry_id','')<>'' and not exists(
            select 1 from public.context_entries where user_id=p_user_id and id::text=st->>'focus_entry_id'
        ) then
            st:=st-'last_referenced_items'-'focus_entry_id';
        end if;
        if (s.value->>'last_activity_at')::timestamptz < now()-make_interval(days=>d.pending_grace_days) then
            st:=st-'active_operation'-'pending_update'-'pending_delete'-'pending_delete_filter'-'delete_candidates'
                  -'delete_stage'-'pending_plan'-'plan_stage'-'review_stage'-'review_range'-'review_feedback'-'draft'-'pending_event';
        end if;
        if st is distinct from s.value->'state' then
            update public.context_documents set sessions=jsonb_set(sessions,array[s.key,'state'],st),version=version+1,updated_at=now()
            where user_id=p_user_id;
        end if;
    end loop;
    return removed;
end;
$$;

create or replace function public.finish_context_turn(
    p_user_id text,p_entry_id uuid,p_assistant_text text,p_summary text,
    p_topics jsonb,p_state jsonb,p_payload jsonb
) returns table(bundle jsonb) language plpgsql security invoker set search_path=public as $$
declare
    d public.context_documents;
    e public.context_entries;
    session_data jsonb;
    applied boolean;
begin
    select * into d from public.context_documents where user_id=p_user_id for update;
    select * into e from public.context_entries where user_id=p_user_id and id=p_entry_id for update;
    if e.id is null then raise exception 'Context entry missing or expired'; end if;
    if jsonb_typeof(p_state)<>'object' or jsonb_typeof(p_topics)<>'array' then raise exception 'Invalid context payload'; end if;
    -- Idempotent retries cannot rewrite a completed turn or overwrite later state.
    if e.status='complete' then
        return query select jsonb_build_object('entry_id',e.id,'saved',true,'state_applied',false,'duplicate',true);
        return;
    end if;
    update public.context_entries set assistant_text=p_assistant_text,summary=left(p_summary,1200),topics=p_topics,
        payload=p_payload,status='complete',finished_at=clock_timestamp(),last_accessed_at=clock_timestamp()
        where id=e.id;
    session_data:=coalesce(d.sessions->e.conversation_id,'{}'::jsonb);
    applied:=coalesce((session_data->>'last_started_seq')::bigint,0)=e.seq;
    if applied then
        session_data:=session_data||jsonb_build_object('state',p_state,'last_completed_seq',e.seq,'last_activity_at',greatest((session_data->>'last_activity_at')::timestamptz,e.created_at));
        update public.context_documents set sessions=jsonb_set(sessions,array[e.conversation_id],session_data),
            version=version+1,updated_at=now() where user_id=p_user_id;
    end if;
    return query select jsonb_build_object('entry_id',e.id,'saved',true,'state_applied',applied,'duplicate',false);
end;
$$;

create or replace function public.begin_context_turn(
    p_user_id text,p_conversation_id text,p_turn_key text,p_user_text text,
    p_ignore boolean default false,p_pending_sync jsonb default null
) returns table(bundle jsonb) language plpgsql security invoker set search_path=public as $$
declare
    d public.context_documents;
    e public.context_entries;
    sd jsonb;
    idx jsonb;
    cutoff timestamptz;
    removed integer;
    active_scope jsonb;
begin
    if p_user_id<>'demo' or length(p_conversation_id)=0 or length(p_turn_key)=0 then raise exception 'Invalid demo context identity'; end if;
    insert into public.context_documents(user_id) values(p_user_id) on conflict do nothing;
    select * into d from public.context_documents where user_id=p_user_id for update;
    -- Retry only the old journal write, never a calendar operation.
    if p_pending_sync is not null and p_pending_sync<>'{}'::jsonb and exists(
       select 1 from public.context_entries where user_id=p_user_id and id::text=p_pending_sync->>'p_entry_id'
    ) then
        perform * from public.finish_context_turn(p_user_id,(p_pending_sync->>'p_entry_id')::uuid,
          p_pending_sync->>'p_assistant_text',p_pending_sync->>'p_summary',p_pending_sync->'p_topics',
          p_pending_sync->'p_state',p_pending_sync->'p_payload');
    end if;
    removed:=public.cleanup_context_memory(p_user_id);
    select * into d from public.context_documents where user_id=p_user_id;
    sd:=coalesce(d.sessions->p_conversation_id,'{}'::jsonb);
    insert into public.context_entries(user_id,conversation_id,turn_key,user_text)
        values(p_user_id,p_conversation_id,p_turn_key,p_user_text)
        on conflict(user_id,conversation_id,turn_key) do nothing;
    select * into e from public.context_entries where user_id=p_user_id and conversation_id=p_conversation_id and turn_key=p_turn_key;
    if p_ignore and e.status<>'complete' and e.seq>=coalesce((sd->>'last_started_seq')::bigint,0) then
        sd:=sd||jsonb_build_object('state','{}'::jsonb,'ignore_before',e.created_at,'ignore_before_seq',e.seq);
    end if;
    cutoff:=(sd->>'ignore_before')::timestamptz;
    sd:=sd||jsonb_build_object('last_started_seq',greatest(coalesce((sd->>'last_started_seq')::bigint,0),e.seq),'last_activity_at',clock_timestamp());
    update public.context_documents set sessions=jsonb_set(sessions,array[p_conversation_id],sd),version=version+1,updated_at=now()
        where user_id=p_user_id;
    select coalesce(jsonb_agg(x),'[]'::jsonb) into idx from (
        select id,conversation_id,created_at,left(summary,200) as summary,topics,
               payload ? 'list_snapshot' as has_list
          from public.context_entries where user_id=p_user_id and status='complete'
          and created_at>=now()-interval '30 days' and (cutoff is null or created_at>=cutoff)
          and seq>=coalesce((sd->>'ignore_before_seq')::bigint,0)
          order by seq desc limit 100
    ) x;
    select payload->'list_snapshot' into active_scope from public.context_entries
      where user_id=p_user_id and id::text=sd->'state'->'active_list'->>'scope_id';
    return query select jsonb_build_object('entry_id',e.id,'created_at',e.created_at,'version',d.version+1,'active_scope',coalesce(active_scope,'{}'::jsonb),
        'state',coalesce(sd->'state','{}'::jsonb),'index',idx,'ignore_before',cutoff,
        'retention_days',d.retention_days,'deleted_count',removed,'duplicate_complete',e.status='complete',
        'saved_reply',case when e.status='complete' then e.assistant_text else '' end);
end;
$$;

create or replace function public.recall_context(
    p_user_id text,p_conversation_id text,p_entry_id uuid,p_entry_ids jsonb default '[]',
    p_date_start timestamptz default null,p_date_end timestamptz default null,p_topics jsonb default '[]',
    p_explicit_history boolean default false,p_scope_id uuid default null,p_view_raw boolean default false
) returns table(bundle jsonb) language plpgsql security invoker set search_path=public as $$
declare
    d public.context_documents;
    cutoff timestamptz;
    selected uuid[];
    cutoff_seq bigint;
    entries jsonb;
    scope_entry public.context_entries;
    chosen_scope jsonb := '{}'::jsonb;
    scopes jsonb;
begin
    select * into d from public.context_documents where user_id=p_user_id;
    if not exists(select 1 from public.context_entries where id=p_entry_id and user_id=p_user_id and conversation_id=p_conversation_id) then
        raise exception 'Invalid context turn';
    end if;
    if jsonb_typeof(p_entry_ids)<>'array' or jsonb_array_length(p_entry_ids)>8
       or jsonb_typeof(p_topics)<>'array' or jsonb_array_length(p_topics)>10 then raise exception 'Invalid selection'; end if;
    cutoff_seq:=case when p_explicit_history then 0 else coalesce((d.sessions->p_conversation_id->>'ignore_before_seq')::bigint,0) end;
    cutoff:=case when p_explicit_history then null else (d.sessions->p_conversation_id->>'ignore_before')::timestamptz end;
    select coalesce(array_agg(x.id),array[]::uuid[]) into selected from (
      select e.id from public.context_entries e where e.user_id=p_user_id and e.status='complete'
      and (cutoff is null or e.created_at>=cutoff) and e.seq>=cutoff_seq
      and (p_date_start is null or e.created_at>=p_date_start)
      and (p_date_end is null or e.created_at<p_date_end)
      and (
        e.id::text in (select jsonb_array_elements_text(p_entry_ids))
        or (jsonb_array_length(p_topics)>0 and exists(select 1 from jsonb_array_elements_text(p_topics) t(word)
             where length(t.word)>0 and position(lower(t.word) in lower(e.summary||' '||e.user_text||' '||e.assistant_text))>0))
        or (jsonb_array_length(p_entry_ids)=0 and jsonb_array_length(p_topics)=0
            and (p_explicit_history or e.conversation_id=p_conversation_id))
      ) order by case when p_explicit_history and not p_view_raw and e.payload ? 'list_snapshot' then 1 else 0 end desc, e.seq desc limit 8
    ) x;
    -- Source scope can be used directly only in its own session or with explicit historical reference.
    if p_scope_id is not null then
        select * into scope_entry from public.context_entries where id=p_scope_id and user_id=p_user_id
          and (conversation_id=p_conversation_id or p_explicit_history) and status='complete' and payload ? 'list_snapshot'
          and (cutoff is null or created_at>=cutoff) and seq>=cutoff_seq;
        if scope_entry.id is not null then chosen_scope:=scope_entry.payload->'list_snapshot'; end if;
    end if;
    select coalesce(jsonb_agg(jsonb_build_object('scope_id',id,'conversation_id',conversation_id,'created_at',created_at,
       'label',payload->'list_snapshot'->>'label')),'[]'::jsonb) into scopes
      from public.context_entries where id=any(selected) and payload ? 'list_snapshot';
    if p_scope_id is null and p_explicit_history and jsonb_array_length(scopes)=1 then
        select payload->'list_snapshot' into chosen_scope from public.context_entries where id::text=scopes->0->>'scope_id';
    end if;
    update public.context_entries set last_accessed_at=clock_timestamp()
      where user_id=p_user_id and (id=any(selected) or id::text=chosen_scope->>'scope_id');
    select coalesce(jsonb_agg(jsonb_build_object('id',id,'conversation_id',conversation_id,'created_at',created_at,
       'summary',summary,'user_text',user_text,'assistant_text',assistant_text,'topics',topics)),'[]'::jsonb)
      into entries from (select * from public.context_entries where id=any(selected) order by seq desc) e;
    return query select jsonb_build_object('entries',entries,'scope',coalesce(chosen_scope,'{}'::jsonb),'scopes',scopes);
end;
$$;

-- A human-readable document view; no destructive "clear" operation is implied.
create or replace view public.context_document_view with (security_invoker=true) as
select user_id,conversation_id,id,seq,created_at,finished_at,last_accessed_at,status,
       user_text,assistant_text,summary,topics,payload
  from public.context_entries;
grant select on public.context_document_view to anon;
grant execute on function public.cleanup_context_memory(text) to anon;
grant execute on function public.begin_context_turn(text,text,text,text,boolean,jsonb) to anon;
grant execute on function public.finish_context_turn(text,uuid,text,text,jsonb,jsonb,jsonb) to anon;
grant execute on function public.recall_context(text,text,uuid,jsonb,timestamptz,timestamptz,jsonb,boolean,uuid,boolean) to anon;

-- Register the idle-day cleanup automatically when pg_cron is already enabled.
do $install$
begin
    if to_regprocedure('cron.schedule(text,text,text)') is not null then
        execute $job$select cron.schedule('campus-context-cleanup','0 19 * * *',
            'select public.cleanup_context_memory(''demo'');')$job$;
    else
        raise notice '入口自动清理已启用。无聊天时的每日清理需启用 pg_cron，再执行 context-memory-cron.sql。';
    end if;
end;
$install$;
