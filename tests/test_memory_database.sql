-- Integration tests for v36. Execute schema.sql and context-memory.sql first.
-- Use a disposable Supabase project. All row changes below are rolled back.
-- This script does not install or modify cron jobs.
begin;
set local role anon;
do $tests$
declare
    tag text := 'memory-test-'||gen_random_uuid()::text;
    a jsonb; b jsonb; c jsonb; d jsonb; result jsonb;
    first_id uuid; unused_id uuid;
    scope jsonb;
    original_access timestamptz;
    schedule_count bigint;
    task_count bigint;
    rejected boolean;
begin
    select count(*) into schedule_count from public.schedules;
    select count(*) into task_count from public.tasks;
    select bundle into a from public.begin_context_turn('demo',tag,'query','本周有哪些安排');
    first_id:=(a->>'entry_id')::uuid;
    scope:=jsonb_build_object('scope_id',first_id,'origin_entry_id',first_id,'conversation_id',tag,
        'created_at',a->>'created_at','label','测试原列表','items',jsonb_build_array(
            jsonb_build_object('id',gen_random_uuid(),'title','第一条','entity_type','schedule'),
            jsonb_build_object('id',gen_random_uuid(),'title','第二条','entity_type','schedule')),'updates','{}'::jsonb);
    select bundle into result from public.finish_context_turn('demo',first_id,'查询两条','原始列表',
        '["测试"]',jsonb_build_object('active_operation','query','active_list',scope-'items','focus_entry_id',first_id),
        jsonb_build_object('list_snapshot',scope));
    if result->>'state_applied'<>'true' then raise exception 'Initial state not saved'; end if;
    select bundle into b from public.begin_context_turn('demo',tag,'update','改第二条');
    if jsonb_array_length(b->'active_scope'->'items')<>2 then raise exception 'Original numbered list lost'; end if;
    select bundle into result from public.finish_context_turn('demo',(b->>'entry_id')::uuid,'第二条已修改','修改结果',
        '[]',jsonb_build_object('active_operation','delete','active_list',scope-'items'),'{}');
    select bundle into result from public.begin_context_turn('demo',tag,'update','改第二条');
    if result->>'duplicate_complete'<>'true' then raise exception 'Completed turn was not replayed'; end if;
    if (select count(*) from public.context_entries where conversation_id=tag and turn_key='update')<>1 then
        raise exception 'Duplicate turn inserted';
    end if;

    -- A late completion cannot overwrite a newer request's session state.
    select bundle into c from public.begin_context_turn('demo',tag,'concurrent-old','old');
    select bundle into d from public.begin_context_turn('demo',tag,'concurrent-new','new');
    perform * from public.finish_context_turn('demo',(d->>'entry_id')::uuid,'new','new','[]','{"marker":"new"}','{}');
    select bundle into result from public.finish_context_turn('demo',(c->>'entry_id')::uuid,'old','old','[]','{"marker":"old"}','{}');
    if result->>'state_applied'<>'false' then raise exception 'Old completion overwrote state'; end if;
    if (select sessions->tag->'state'->>'marker' from public.context_documents where user_id='demo')<>'new' then
        raise exception 'New state not retained';
    end if;

    -- Metadata/index reads do not renew last access; recalled content does.
    update public.context_entries set last_accessed_at=now()-interval '60 days' where id=first_id;
    select last_accessed_at into original_access from public.context_entries where id=first_id;
    select bundle into c from public.begin_context_turn('demo',tag||'-other','index-only','查看索引');
    if (select last_accessed_at from public.context_entries where id=first_id)<>original_access then
        raise exception 'Index read renewed expiry';
    end if;
    select bundle into result from public.recall_context('demo',tag||'-other',(c->>'entry_id')::uuid,
        jsonb_build_array(first_id),null,null,'[]',true,first_id,false);
    if result->'scope'->>'scope_id'<>first_id::text then raise exception 'Explicit historical scope not recalled'; end if;
    if (select last_accessed_at from public.context_entries where id=first_id)<=original_access then
        raise exception 'Recall did not renew last access';
    end if;

    -- Old unused entries expire, but an unfinished recent operation pins its source.
    select bundle into d from public.begin_context_turn('demo',tag||'-unused','u','unused');
    unused_id:=(d->>'entry_id')::uuid;
    perform * from public.finish_context_turn('demo',unused_id,'unused','unused','[]','{}','{}');
    update public.context_documents set retention_days=90,auto_delete_enabled=true,
        sessions=jsonb_set(sessions,array[tag],jsonb_build_object('last_activity_at',clock_timestamp(),
            'state',jsonb_build_object('active_operation','delete','active_list',scope-'items','focus_entry_id',first_id)))
        where user_id='demo';
    update public.context_entries set last_accessed_at=now()-interval '91 days' where id in (first_id,unused_id);
    perform public.cleanup_context_memory('demo');
    if exists(select 1 from public.context_entries where id=unused_id) then raise exception 'Unused entry not removed'; end if;
    if not exists(select 1 from public.context_entries where id=first_id) then raise exception 'Pending source not protected'; end if;
    update public.context_documents set sessions=jsonb_set(sessions,array[tag,'last_activity_at'],to_jsonb(now()-interval '8 days')) where user_id='demo';
    perform public.cleanup_context_memory('demo');
    if exists(select 1 from public.context_entries where id=first_id) then raise exception 'Expired protection never released'; end if;
    if (select sessions->tag->'state' ? 'active_list' from public.context_documents where user_id='demo') then
        raise exception 'Deleted scope pointer retained';
    end if;
    if (select count(*) from public.schedules)<>schedule_count or (select count(*) from public.tasks)<>task_count then
        raise exception 'Memory cleanup touched business data';
    end if;

    -- An ignore boundary cannot be bypassed by selecting a known older entry ID.
    select bundle into a from public.begin_context_turn('demo',tag||'-ignore','before','old context');
    perform * from public.finish_context_turn('demo',(a->>'entry_id')::uuid,'old','old','[]','{}','{}');
    select bundle into b from public.begin_context_turn('demo',tag||'-ignore','ignore','忽略之前的上下文',true);
    select bundle into result from public.recall_context('demo',tag||'-ignore',(b->>'entry_id')::uuid,
        jsonb_build_array(a->>'entry_id'),null,null,'[]',false,null,false);
    if jsonb_array_length(result->'entries')<>0 then raise exception 'Ignore boundary bypassed'; end if;

    -- End-to-end data is timestamped and a completed write is idempotent.
    perform * from public.finish_context_turn('demo',(b->>'entry_id')::uuid,'ignored','ignored','[]','{}','{}');
    select bundle into result from public.finish_context_turn('demo',(b->>'entry_id')::uuid,'different','different','[]','{}','{}');
    if result->>'duplicate'<>'true' then raise exception 'Duplicate journal completion not idempotent'; end if;
    if not exists(select 1 from public.context_entries where id=(b->>'entry_id')::uuid and assistant_text='ignored'
                  and finished_at>=created_at and status='complete') then raise exception 'Actual completed transcript changed'; end if;

    rejected:=false;
    begin
        perform * from public.begin_context_turn('other-user',tag,'illegal','illegal');
    exception when raise_exception then rejected:=true;
    end;
    if not rejected then raise exception 'Unexpected memory identity accepted'; end if;
end;
$tests$;
rollback;
