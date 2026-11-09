-- 校园日程智能体 · 数据表（Supabase / PostgreSQL）
-- 在 Supabase 的 SQL Editor 里整段执行即可

create extension if not exists pgcrypto;

create table if not exists public.schedules (
    id           uuid primary key default gen_random_uuid(),
    user_id      text        not null default 'demo',
    title        text        not null,
    event_date   date        not null,
    start_time   time        not null,
    end_time     time        not null,
    location     text        default '',
    category     text        not null default '其他',
    status       text        not null default 'planned'
                 check (status in ('planned', 'done', 'undone', 'adjusted')),
    note         text        default '',
    source       text        not null default 'agent',
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    constraint time_order check (end_time > start_time)
);

create index if not exists idx_schedules_user_date
    on public.schedules (user_id, event_date);

-- 演示阶段：允许匿名读写，方便 Dify 直接调用。
-- 正式提交前建议改成按登录用户隔离，避免答辩时被问到"数据谁都能改"。
alter table public.schedules enable row level security;

drop policy if exists "demo_all_access" on public.schedules;
create policy "demo_all_access"
    on public.schedules
    for all
    using (true)
    with check (true);

-- 常用查询：查某天
-- GET /rest/v1/schedules?user_id=eq.demo&event_date=eq.2026-10-30&order=start_time.asc

-- 常用查询：查某周（周一 = 2026-10-26）
-- GET /rest/v1/schedules?user_id=eq.demo&event_date=gte.2026-10-26&event_date=lte.2026-11-01&order=event_date.asc,start_time.asc

-- v32: idempotent additions for existing demo deployments.
alter table public.schedules add column if not exists manual_override boolean not null default false;

create table if not exists public.tasks (
    id uuid primary key default gen_random_uuid(),
    user_id text not null default 'demo',
    title text not null check (length(trim(title)) > 0),
    task_date date not null default current_date,
    done boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists idx_tasks_user_date on public.tasks(user_id, task_date);

create table if not exists public.reviews (
    id uuid primary key default gen_random_uuid(),
    user_id text not null default 'demo',
    period text not null check (period in ('day','week','month')),
    range_start date not null,
    range_end date not null,
    report text not null,
    stats jsonb not null default '{}'::jsonb,
    advice_summary text not null default '',
    created_at timestamptz not null default now(),
    constraint review_range_order check (range_end >= range_start)
);
alter table public.reviews add column if not exists advice_summary text not null default '';
create index if not exists idx_reviews_user_period on public.reviews(user_id,period,created_at desc);

create table if not exists public.user_context (
    user_id text primary key default 'demo',
    profile jsonb not null default '{}'::jsonb check (jsonb_typeof(profile) = 'object'),
    updated_at timestamptz not null default now()
);

create or replace function public.touch_user_context_updated_at() returns trigger
language plpgsql set search_path = public as $$
begin
    new.updated_at := now();
    return new;
end;
$$;
drop trigger if exists user_context_updated_at on public.user_context;
create trigger user_context_updated_at before update on public.user_context
for each row execute function public.touch_user_context_updated_at();

-- Same anonymous, single-user demo access model as v31. No login is introduced.
alter table public.tasks enable row level security;
alter table public.reviews enable row level security;
alter table public.user_context enable row level security;
drop policy if exists demo_all_access on public.tasks;
create policy demo_all_access on public.tasks for all using (user_id = 'demo') with check (user_id = 'demo');
drop policy if exists demo_all_access on public.reviews;
create policy demo_all_access on public.reviews for all using (user_id = 'demo') with check (user_id = 'demo');
drop policy if exists demo_all_access on public.user_context;
create policy demo_all_access on public.user_context for all using (user_id = 'demo') with check (user_id = 'demo');

-- A single transaction saves both schedules and tasks. For confirmed plans, take
-- a per-user lock and recheck conflicts inside the transaction as well as in Dify.
-- SECURITY INVOKER preserves the tables' RLS and caller's grants.
create or replace function public.save_batch(
    schedule_rows jsonb,
    task_rows jsonb,
    reject_conflicts boolean default false
) returns setof public.schedules
language plpgsql security invoker set search_path = public as $$
declare
    r jsonb;
    inserted public.schedules;
    profile_timezone text;
begin
    if jsonb_typeof(schedule_rows) <> 'array' or jsonb_typeof(task_rows) <> 'array'
       or schedule_rows is null or task_rows is null then
        raise exception 'Batch payload must contain two arrays';
    end if;
    if jsonb_array_length(schedule_rows) > 500 or jsonb_array_length(task_rows) > 500 then
        raise exception 'Batch too large';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('campus-demo-batch', 0));
    select coalesce(profile->>'timezone','Asia/Shanghai') into profile_timezone
      from public.user_context where user_id='demo';
    profile_timezone := coalesce(profile_timezone,'Asia/Shanghai');
    for r in select value from jsonb_array_elements(schedule_rows) loop
        if (r->>'user_id') is distinct from 'demo' or length(trim(coalesce(r->>'title',''))) = 0 then
            raise exception 'Invalid demo schedule';
        end if;
        if reject_conflicts then
            if (r->>'event_date')::date + (r->>'start_time')::time < (now() at time zone profile_timezone) then
                raise exception 'Plan has expired; regenerate and confirm';
            end if;
            if exists (
                select 1 from public.schedules s
                where s.user_id='demo' and s.event_date=(r->>'event_date')::date
                and s.start_time < (r->>'end_time')::time
                and (r->>'start_time')::time < s.end_time
            ) then
                raise exception 'Plan conflicts with existing schedule; regenerate and confirm';
            end if;
        end if;
        insert into public.schedules(user_id,title,event_date,start_time,end_time,location,category,note,source,status)
        values ('demo',r->>'title',(r->>'event_date')::date,(r->>'start_time')::time,
                (r->>'end_time')::time,coalesce(r->>'location',''),coalesce(r->>'category','其他'),
                coalesce(r->>'note',''),'agent','planned') returning * into inserted;
        return next inserted;
    end loop;
    for r in select value from jsonb_array_elements(task_rows) loop
        if (r->>'user_id') is distinct from 'demo' or length(trim(coalesce(r->>'title',''))) = 0 then
            raise exception 'Invalid demo task';
        end if;
        insert into public.tasks(user_id,title,task_date,done)
        values ('demo',r->>'title',(r->>'task_date')::date,false);
    end loop;
    return;
end;
$$;

-- Supabase's anon role needs table permissions in addition to RLS policies.
grant usage on schema public to anon;
grant select,insert,update,delete on public.schedules, public.tasks, public.reviews, public.user_context to anon;
grant execute on function public.save_batch(jsonb,jsonb,boolean) to anon;

-- v36 also requires docs/context-memory.sql.
-- For cleanup on days without chat requests, enable pg_cron and run docs/context-memory-cron.sql.
