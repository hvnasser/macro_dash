-- =============================================================================
-- Supabase Schema — Macro Dashboard
-- Run this in the Supabase SQL editor.
--
-- Data source: B3 (Brasil, Bolsa, Balcão) via PYield (github.com/crdcj/PYield)
--
-- Design principles:
--   - Append-only: unique constraint + upsert with ignore_duplicates.
--     The first snapshot for each (ref_date, contract_code) is kept forever.
--     Re-running the pipeline for the same date is always safe.
--   - DU (dias úteis) uses the B3/ANBIMA business day calendar (252/year).
--   - settlement_rate = (100_000 / settlement_price) ^ (252 / du) - 1
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. DI Futures Raw Settlement Data
--    Source : B3 DI1 futures, fetched via PYield
--    One row : one contract × one reference date
-- ---------------------------------------------------------------------------
create table if not exists di_futures_raw (
    id                bigserial     primary key,

    -- Identity
    ref_date          date          not null,   -- pregão date
    contract_code     text          not null,   -- e.g. "DI1F26"

    -- Contract details
    expiry_date       date          not null,   -- first biz day of expiry month
    du                integer       not null,   -- biz days ref_date → expiry (B3/ANBIMA)

    -- Settlement data (from B3 via PYield)
    settlement_price  numeric(18,6),            -- PU, face = R$ 100,000
    settlement_rate   numeric(12,8) not null,   -- annualised rate, e.g. 0.12750000 = 12.75%

    -- Volume
    trade_volume      bigint,                   -- number of contracts traded
    financial_volume  numeric(20,2),            -- R$ financial volume

    -- Audit
    collected_at      timestamptz   not null default now(),

    -- First snapshot wins; re-runs are safe (no override)
    constraint di_futures_raw_uq unique (ref_date, contract_code)
);

comment on table  di_futures_raw is
    'Raw DI1 futures settlement data from B3 (via PYield). Append-only.';
comment on column di_futures_raw.du is
    'Business days to expiry, B3/ANBIMA calendar (252-day convention).';
comment on column di_futures_raw.settlement_price is
    'Preço de ajuste (PU). Face = R$ 100,000. Null if B3 did not publish PU that day.';
comment on column di_futures_raw.settlement_rate is
    'Taxa de ajuste: (100_000/PU)^(252/DU) - 1. Already computed by PYield.';

create index if not exists di_futures_raw_ref_date_idx on di_futures_raw (ref_date desc);
create index if not exists di_futures_raw_contract_idx  on di_futures_raw (contract_code);
create index if not exists di_futures_raw_expiry_idx    on di_futures_raw (expiry_date);


-- ---------------------------------------------------------------------------
-- 2. DI Curve Vertices (bootstrapped zero-coupon curve)
--    rate_252 = settlement_rate from di_futures_raw (no further bootstrapping
--    needed — PYield already derives it from B3's settlement PU).
-- ---------------------------------------------------------------------------
create table if not exists di_curve_vertices (
    id            bigserial     primary key,

    ref_date      date          not null,
    contract_code text          not null,
    expiry_date   date          not null,
    du            integer       not null,    -- biz days to expiry (B3/ANBIMA)
    rate_252      numeric(12,8) not null,    -- annualised zero-coupon spot rate (decimal)
    forward_rate  numeric(12,8),            -- annualised forward rate to next vertex

    collected_at  timestamptz   not null default now(),

    constraint di_curve_vertices_uq unique (ref_date, contract_code)
);

comment on table  di_curve_vertices is
    'DI zero-coupon curve vertices, one per contract per date. Append-only.';
comment on column di_curve_vertices.rate_252 is
    'Annualised zero-coupon spot rate (252 biz-day convention). E.g. 0.1275 = 12.75% a.a.';
comment on column di_curve_vertices.forward_rate is
    'Annualised forward rate from previous vertex to this one (252 b.d. convention).
     First vertex: forward = spot rate (from ref_date to first expiry).
     Formula: [(1+r_i)^(du_i/252) / (1+r_{i-1})^(du_{i-1}/252)]^(252/Δdu) - 1';

create index if not exists di_curve_vertices_ref_date_idx on di_curve_vertices (ref_date desc);
create index if not exists di_curve_vertices_du_idx       on di_curve_vertices (du);


-- ---------------------------------------------------------------------------
-- 3. BCB / COPOM Meeting Calendar
--    Source: BCB official calendar — bcb.gov.br/en/monetarypolicy/copommeeting
--
--    Timing:
--      decision_date  = Wednesday (2nd day of 2-day COPOM meeting)
--      effective_date = next business day (Thursday) — when new SELIC takes effect
-- ---------------------------------------------------------------------------
create table if not exists bcb_meetings (
    id             bigserial    primary key,
    decision_date  date         not null,   -- Wednesday: COPOM decision day
    updated_at     timestamptz  not null default now(),

    constraint bcb_meetings_uq unique (decision_date)
);

comment on table  bcb_meetings               is 'COPOM decision dates. effective_date (when new SELIC takes effect) is derived as the next business day after decision_date.';
comment on column bcb_meetings.decision_date is 'Wednesday: COPOM announces the new SELIC target after market close (~21:00 BRT).';

create index if not exists bcb_meetings_decision_idx on bcb_meetings (decision_date);


-- ---------------------------------------------------------------------------
-- Row-Level Security (optional — enable after testing)
-- ---------------------------------------------------------------------------
-- alter table di_futures_raw    enable row level security;
-- alter table di_curve_vertices enable row level security;
-- alter table bcb_meetings       enable row level security;
