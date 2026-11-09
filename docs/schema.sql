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
