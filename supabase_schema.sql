-- =============================================================================
-- Supabase Schema — Macro Dashboard
-- Run this in the Supabase SQL editor to create the required tables.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- DI Futures Raw Settlement Prices (from B3)
-- ---------------------------------------------------------------------------
create table if not exists di_futures_raw (
    id               bigserial primary key,
    ref_date         date        not null,       -- trading/reference date
    contract_code    text        not null,        -- e.g. "DI1F25"
    expiry_date      date        not null,
    settlement_price numeric(18,6) not null,      -- PU (unit price), face 100 000
    open_interest    integer,
    collected_at     timestamptz not null default now(),

    -- Unique on (ref_date, contract_code) — first snapshot wins, no overrides.
    -- To store intra-day revisions, drop this constraint and rely on collected_at.
    constraint di_futures_raw_uq unique (ref_date, contract_code)
);

create index if not exists di_futures_raw_ref_date_idx on di_futures_raw (ref_date desc);
create index if not exists di_futures_raw_contract_idx  on di_futures_raw (contract_code);

-- ---------------------------------------------------------------------------
-- DI Curve Bootstrapped Vertices
-- ---------------------------------------------------------------------------
create table if not exists di_curve_vertices (
    id            bigserial primary key,
    ref_date      date        not null,
    contract_code text        not null,           -- ties back to raw table
    expiry_date   date        not null,
    du            integer     not null,            -- business days to expiry (252/year)
    rate_252      numeric(12,8) not null,          -- annualised rate (e.g. 0.1275 = 12.75%)
    collected_at  timestamptz not null default now(),

    constraint di_curve_vertices_uq unique (ref_date, contract_code)
);

create index if not exists di_curve_vertices_ref_date_idx on di_curve_vertices (ref_date desc);

-- ---------------------------------------------------------------------------
-- Row-Level Security (enable after testing if needed)
-- ---------------------------------------------------------------------------
-- alter table di_futures_raw   enable row level security;
-- alter table di_curve_vertices enable row level security;
