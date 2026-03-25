-- =============================================================================
-- Supabase Schema — Macro Dashboard
-- Run this in the Supabase SQL editor.
--
-- Design principles:
--   - Every table is append-only: the unique constraint + upsert with
--     ignore_duplicates keeps the FIRST snapshot and never overwrites it.
--   - DU (dias úteis / business days) is stored using the ANBIMA calendar,
--     the Brazilian fixed-income market standard.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. DI Futures Raw Settlement Prices
--    Source: B3 (Brasil, Bolsa, Balcão) — DI1 futures preço de ajuste diário
--    URL:    https://arquivos.b3.com.br/apinegocios/cotacoesajuste/{YYYY-MM-DD}
-- ---------------------------------------------------------------------------
create table if not exists di_futures_raw (
    id               bigserial     primary key,

    -- Identification
    ref_date         date          not null,   -- trading reference date (pregão)
    contract_code    text          not null,   -- e.g. "DI1F26" (DI1 + month letter + year)

    -- Contract details
    expiry_date      date          not null,   -- first business day of expiry month
    du               integer       not null,   -- business days ref_date → expiry (ANBIMA)

    -- Market data
    settlement_price numeric(18,6) not null,   -- PU (face = R$ 100,000)
    open_interest    integer,                  -- open contracts (contratos em aberto)

    -- Audit
    collected_at     timestamptz   not null default now(),

    -- First snapshot wins; never overwrite (preserves revisions history)
    constraint di_futures_raw_uq unique (ref_date, contract_code)
);

comment on table  di_futures_raw                  is 'Raw DI1 futures settlement prices from B3, append-only.';
comment on column di_futures_raw.du               is 'Business days to expiry using ANBIMA calendar (252/year convention).';
comment on column di_futures_raw.settlement_price is 'Preço de ajuste (PU). Face value = R$ 100,000. rate = (100000/PU)^(252/DU) - 1.';

create index if not exists di_futures_raw_ref_date_idx    on di_futures_raw (ref_date desc);
create index if not exists di_futures_raw_contract_idx    on di_futures_raw (contract_code);
create index if not exists di_futures_raw_expiry_date_idx on di_futures_raw (expiry_date);


-- ---------------------------------------------------------------------------
-- 2. DI Curve Bootstrapped Vertices
--    One row per (ref_date, contract_code) after rate conversion.
--    rate_252 = (100_000 / settlement_price) ^ (252 / du) - 1
-- ---------------------------------------------------------------------------
create table if not exists di_curve_vertices (
    id            bigserial     primary key,

    ref_date      date          not null,
    contract_code text          not null,    -- e.g. "DI1F26"
    expiry_date   date          not null,
    du            integer       not null,    -- business days to expiry (ANBIMA)
    rate_252      numeric(12,8) not null,    -- annualised rate, e.g. 0.12750000 = 12.75% a.a.

    collected_at  timestamptz   not null default now(),

    constraint di_curve_vertices_uq unique (ref_date, contract_code)
);

comment on table  di_curve_vertices          is 'Bootstrapped DI zero-coupon curve vertices, append-only.';
comment on column di_curve_vertices.du       is 'Business days to expiry using ANBIMA calendar.';
comment on column di_curve_vertices.rate_252 is 'Annualised zero-coupon rate, 252 business-day convention.';

create index if not exists di_curve_vertices_ref_date_idx on di_curve_vertices (ref_date desc);
create index if not exists di_curve_vertices_du_idx       on di_curve_vertices (du);


-- ---------------------------------------------------------------------------
-- Row-Level Security (optional — enable after testing)
-- ---------------------------------------------------------------------------
-- alter table di_futures_raw    enable row level security;
-- alter table di_curve_vertices enable row level security;
