-- Enable pg_cron in Supabase Database → Extensions first, then run this file.
-- Daily 19:00 UTC = 03:00 Asia/Shanghai, including days with no chat requests.
-- Re-running cron.schedule with the same job name updates that job.
select cron.schedule(
    'campus-context-cleanup',
    '0 19 * * *',
    $$select public.cleanup_context_memory('demo');$$
);
-- Inspect: select jobid,jobname,schedule,active from cron.job where jobname='campus-context-cleanup';
