-- Run after docs/schema.sql in a disposable Supabase project or SQL Editor.
-- Everything below is rolled back. No existing rows are deleted.
begin;
set local role anon;

do $$
declare
    tag text := 'v32-test-' || gen_random_uuid()::text;
    test_day date := current_date + 30;
    event_row jsonb;
    good_task jsonb;
    bad_task jsonb;
    result_count integer;
    rejected boolean;
begin
    -- Select an unused date so existing demo data cannot invalidate the test.
    while exists (select 1 from public.schedules where user_id='demo' and event_date=test_day) loop
        test_day := test_day + 1;
    end loop;
    event_row := jsonb_build_object('user_id','demo','title',tag,
        'event_date',test_day,'start_time','16:00','end_time','17:00');
    good_task := jsonb_build_object('user_id','demo','title',tag,'task_date',test_day);
    bad_task := jsonb_build_object('user_id','another-user','title',tag,'task_date',test_day);

    rejected := false;
    begin
        perform * from public.save_batch(jsonb_build_array(event_row),jsonb_build_array(bad_task),false);
    exception when raise_exception then
        rejected := true;
    end;
    if not rejected or exists (select 1 from public.schedules where title=tag) then
        raise exception 'Atomic rollback failed';
    end if;

    select count(*) into result_count from public.save_batch(jsonb_build_array(event_row),jsonb_build_array(good_task),true);
    if result_count <> 1 or not exists (select 1 from public.tasks where title=tag) then
        raise exception 'Batch did not save schedules and tasks';
    end if;

    rejected := false;
    begin
        perform * from public.save_batch(jsonb_build_array(event_row), '[]'::jsonb,true);
    exception when raise_exception then rejected := true;
    end;
    if not rejected then raise exception 'Confirmed conflicting plan was accepted'; end if;

    rejected := false;
    begin
        perform * from public.save_batch(
            jsonb_build_array(event_row || jsonb_build_object('event_date',current_date-2)),
            '[]'::jsonb,true);
    exception when raise_exception then rejected := true;
    end;
    if not rejected then raise exception 'Expired plan was accepted'; end if;

    -- Adjacent slots are permitted; overlap within the same batch is rejected.
    perform * from public.save_batch(
        jsonb_build_array(event_row || jsonb_build_object('start_time','17:00','end_time','18:00')),
        '[]'::jsonb,true);
    rejected := false;
    begin
        perform * from public.save_batch(jsonb_build_array(
            event_row || jsonb_build_object('event_date',test_day+1),
            event_row || jsonb_build_object('event_date',test_day+1,'start_time','16:30','end_time','17:30')
        ), '[]'::jsonb,true);
    exception when raise_exception then rejected := true;
    end;
    if not rejected or exists (select 1 from public.schedules where title=tag and event_date=test_day+1) then
        raise exception 'Internal overlap was not rolled back';
    end if;

    insert into public.user_context(user_id,profile,updated_at)
    values ('demo','{"study_blocked_windows":[["21:00","23:59"]]}','2000-01-01')
    on conflict (user_id) do update set profile=excluded.profile;
    if not exists (select 1 from public.user_context where user_id='demo'
                   and profile->'study_blocked_windows' = '[["21:00","23:59"]]'::jsonb) then
        raise exception 'Profile persistence failed';
    end if;
end;
$$;

rollback;
