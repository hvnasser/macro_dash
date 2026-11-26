-- =============================================================================
-- BCB SGS time series (credit data and any future BCB monthly series)
-- Run once in the Supabase SQL editor.
--
-- Append-only with revision history: BCB revises recent months, and a revised
-- value is stored as a NEW row. `bcb_series_latest` returns the most recently
-- collected value per (series_code, ref_date).
-- =============================================================================

create table if not exists bcb_series (
    id            bigserial   primary key,
    series_code   integer     not null,   -- SGS code, e.g. 20539
    ref_date      date        not null,   -- first day of the reference month
    value         numeric     not null,
    collected_at  timestamptz not null default now(),
    constraint bcb_series_uq unique (series_code, ref_date, value)
);

comment on table bcb_series is
    'BCB SGS series (source: api.bcb.gov.br). Append-only; revised values are new rows.';

create index if not exists bcb_series_code_date_idx
    on bcb_series (series_code, ref_date, collected_at desc);

create or replace view bcb_series_latest as
select distinct on (series_code, ref_date)
       series_code, ref_date, value, collected_at
from bcb_series
order by series_code, ref_date, collected_at desc;
