-- =============================================================================
-- IPCA derived metrics — run once in the Supabase SQL editor.
--
-- Raw IPCA series (% m/m) are stored in `bcb_series` (see bcb_series.sql).
-- This table holds the derived metrics. It is recalculated and overwritten on
-- every update, since seasonal adjustment revises past months.
-- =============================================================================

create table if not exists ipca_metrics (
    series_key   text        not null,   -- e.g. 'geral', 'servicos', 'core_avg'
    ref_date     date        not null,   -- first day of the reference month
    mom          numeric,                -- raw % m/m
    mom_sa       numeric,                -- seasonally adjusted % m/m
    mom_saar     numeric,                -- annualised SA m/m ("MoM AR")
    mm3m_saar    numeric,                -- annualised 3-month average of SA m/m
    yoy          numeric,                -- % over 12 months
    computed_at  timestamptz not null default now(),
    primary key (series_key, ref_date)
);

comment on table ipca_metrics is
    'IPCA metrics derived from BCB SGS series; overwritten on each update.';
