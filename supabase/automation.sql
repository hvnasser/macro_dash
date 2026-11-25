-- =============================================================================
-- Daily DI update automation — run once in the Supabase SQL editor.
--
-- 1. pipeline_runs: one row per run of scripts/daily_update.py
-- 2. pg_cron job that triggers the GitHub Actions workflow at an exact time
--    (GitHub's own cron is often delayed or skipped; it stays as a backup).
--
-- Before running step 2, create a GitHub fine-grained token:
--   github.com → Settings → Developer settings → Fine-grained tokens
--   Repository access: only hvnasser/macro_dash
--   Permissions: Actions → Read and write
-- and paste it below in place of <GITHUB_TOKEN>.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Run log
-- ---------------------------------------------------------------------------
create table if not exists pipeline_runs (
    id             bigserial   primary key,
    started_at     timestamptz not null,
    finished_at    timestamptz,
    trigger        text,         -- workflow_dispatch | schedule | manual
    status         text not null check (status in ('ok', 'warning', 'error')),
    saved_dates    date[],       -- raw settlements downloaded this run
    rebuilt_dates  date[],       -- curves rebuilt from raw already in DB
    missing_dates  date[],       -- not published yet or failed
    message        text
);
create index if not exists pipeline_runs_started_idx on pipeline_runs (started_at desc);


-- ---------------------------------------------------------------------------
-- 2. Exact-time trigger: pg_cron → GitHub workflow_dispatch
-- ---------------------------------------------------------------------------
create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Store the token encrypted in Supabase Vault (run once; to rotate, use
-- vault.update_secret).
select vault.create_secret('<GITHUB_TOKEN>', 'github_dispatch_token');

create or replace function trigger_di_update() returns bigint
language sql security definer as $$
    select net.http_post(
        url     := 'https://api.github.com/repos/hvnasser/macro_dash/actions/workflows/daily_update.yml/dispatches',
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets
                                           where name = 'github_dispatch_token'),
            'Accept', 'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'User-Agent', 'supabase-pg-cron'
        ),
        body    := jsonb_build_object('ref', 'claude/streamlit-dashboard-setup-xmycW')
    );
$$;
-- Not callable through the public API (only pg_cron / SQL editor)
revoke execute on function trigger_di_update() from public, anon, authenticated;

-- pg_cron runs in UTC. BRT = UTC-3.
select cron.schedule('di-update-evening', '5 0 * * 2-6',  'select trigger_di_update()');  -- 21:05 BRT Mon–Fri
select cron.schedule('di-update-morning', '5 10 * * 2-6', 'select trigger_di_update()');  -- 07:05 BRT Tue–Sat


-- ---------------------------------------------------------------------------
-- Useful checks
-- ---------------------------------------------------------------------------
-- Trigger a run right now (GitHub returns 204 if accepted):
--   select trigger_di_update();
--   select status_code, content from net._http_response order by created desc limit 1;
--
-- Scheduled jobs and their recent executions:
--   select * from cron.job;
--   select * from cron.job_run_details order by start_time desc limit 10;
--
-- Last pipeline runs:
--   select started_at, trigger, status, saved_dates, missing_dates from pipeline_runs
--   order by started_at desc limit 10;
