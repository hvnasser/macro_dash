"""
Brazilian DI Futures Curve Pipeline
====================================

DATA SOURCE
-----------
B3 (Brasil, Bolsa, Balcão) — the Brazilian exchange.
Specifically the **DI1 futures daily settlement file** (preço de ajuste diário),
downloaded from B3's public file server:

    https://arquivos.b3.com.br/apinegocios/cotacoesajuste/{YYYY-MM-DD}

This endpoint returns a JSON list of settlement prices for all derivative
contracts traded on that date, including DI1 (one-day interbank deposit futures).

DI1 CONTRACT MECHANICS
-----------------------
Each DI1 contract expires on the first business day of a given month.
B3 publishes a daily settlement *price* (PU — Preço Unitário) based on a
face value of R$ 100,000.  The relationship between PU and the annualised
252-business-day rate is:

    PU = 100_000 / (1 + rate) ^ (DU / 252)
    =>  rate = (100_000 / PU) ^ (252 / DU) - 1

where DU = number of business days between the reference date and expiry,
counted using the **ANBIMA calendar** (the standard for Brazilian fixed income).

HOLIDAY CALENDAR
----------------
We use the ANBIMA calendar provided by the `bizdays` library.
ANBIMA (Associação Brasileira das Entidades dos Mercados Financeiro e de
Capitais) maintains the official list of holidays for the Brazilian fixed
income market.  This includes:

    - National public holidays (feriados nacionais)
    - Carnival Monday and Tuesday (not a national holiday but a market holiday)
    - Corpus Christi
    - Other locally declared banking/market holidays

The ANBIMA calendar is the same used by B3, the BCB and all Brazilian
fixed-income practitioners for DU (dias úteis) calculations.

STORAGE DESIGN
--------------
Tables:
  di_futures_raw    — one row per (ref_date, contract_code): raw B3 settlement
                      data.  We also store DU here so downstream consumers
                      don't need to re-calculate it.
  di_curve_vertices — one row per (ref_date, contract_code): bootstrapped
                      zero-coupon vertex with DU and rate.

Neither table ever overwrites existing rows.  The unique constraint + upsert
with `ignore_duplicates=True` means the first snapshot for each
(ref_date, contract_code) is kept forever, preserving data revision history.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import requests
from bizdays import Calendar

from utils.db import upsert_records

# ---------------------------------------------------------------------------
# ANBIMA calendar — Brazilian fixed-income market standard
# ---------------------------------------------------------------------------
_CALENDAR = Calendar.load("ANBIMA")


def business_days_between(start: date, end: date) -> int:
    """Business days from *start* (exclusive) to *end* (inclusive), ANBIMA convention."""
    return _CALENDAR.bizdays(start, end)


def next_business_day(ref: date, offset: int = -1) -> date:
    """Offset business days from ref (negative = backward)."""
    return _CALENDAR.offset(ref, offset)


# ---------------------------------------------------------------------------
# B3 settlement download
# ---------------------------------------------------------------------------
_B3_SETTLEMENTS_URL = (
    "https://arquivos.b3.com.br/apinegocios/cotacoesajuste/{date}"
)
_DI1_PREFIX = "DI1"


def _fetch_b3_di_settlements(ref_date: date) -> pd.DataFrame:
    """Download DI1 settlement prices from B3 for *ref_date*.

    Source: B3 public settlements endpoint (preços de ajuste diário).
    Filters for DI1 contracts only.

    Returns a DataFrame with columns:
        ref_date, contract_code, expiry_date, du,
        settlement_price, open_interest
    """
    date_str = ref_date.strftime("%Y-%m-%d")
    url = _B3_SETTLEMENTS_URL.format(date=date_str)

    resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
    resp.raise_for_status()
    data = resp.json()

    rows = []
    # B3 response shape: list of dicts or nested under a key — handle both
    items = data if isinstance(data, list) else data.get("Ativo", data.get("items", []))

    for item in items:
        # Filter to DI1 contracts only
        code = item.get("Codigo") or item.get("cod") or item.get("contract_code", "")
        if not code.startswith(_DI1_PREFIX):
            continue

        expiry_str = (
            item.get("DataVencimento")
            or item.get("expiry_date")
            or item.get("maturity", "")
        )
        settlement_price = (
            item.get("PrecoAjuste")
            or item.get("settlement_price")
            or item.get("price")
        )
        open_interest = item.get("QuantContrAberto") or item.get("open_interest")

        if not expiry_str or settlement_price is None:
            continue

        try:
            expiry = datetime.strptime(expiry_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        du = business_days_between(ref_date, expiry)
        if du <= 0:
            continue

        rows.append(
            {
                "ref_date": ref_date.isoformat(),
                "contract_code": code,
                "expiry_date": expiry.isoformat(),
                "du": du,                          # business days to expiry (ANBIMA)
                "settlement_price": float(settlement_price),
                "open_interest": int(open_interest) if open_interest else None,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("du").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Persist raw data (append-only)
# ---------------------------------------------------------------------------
def save_raw_settlements(df: pd.DataFrame) -> None:
    """Append DI1 raw settlement rows to Supabase.

    Conflict key: (ref_date, contract_code).
    The first snapshot is kept; subsequent runs for the same key are ignored.
    """
    records = df.to_dict(orient="records")
    upsert_records(
        "di_futures_raw",
        records,
        conflict_columns=["ref_date", "contract_code"],
    )


# ---------------------------------------------------------------------------
# Curve bootstrapping
# ---------------------------------------------------------------------------
def _pu_to_rate(pu: float, du: int) -> float:
    """Convert DI settlement PU to annualised rate (252 bd convention, ANBIMA)."""
    if du <= 0 or pu <= 0:
        return float("nan")
    return (100_000.0 / pu) ** (252.0 / du) - 1.0


def bootstrap_di_curve(df_raw: pd.DataFrame, ref_date: date) -> pd.DataFrame:
    """Compute zero-coupon DI rates from raw settlement prices.

    For each contract:
        rate = (100_000 / PU) ^ (252 / DU) - 1

    DU already stored in raw data (ANBIMA calendar).

    Returns DataFrame with: ref_date, contract_code, expiry_date, du, rate_252
    sorted ascending by du.
    """
    df = df_raw[df_raw["ref_date"] == ref_date.isoformat()].copy()
    if df.empty:
        return pd.DataFrame()

    df["rate_252"] = df.apply(
        lambda r: _pu_to_rate(r["settlement_price"], r["du"]), axis=1
    )
    df = df.dropna(subset=["rate_252"]).sort_values("du").reset_index(drop=True)
    return df[["ref_date", "contract_code", "expiry_date", "du", "rate_252"]].copy()


# ---------------------------------------------------------------------------
# Persist bootstrapped curve (append-only)
# ---------------------------------------------------------------------------
def save_curve_vertices(df: pd.DataFrame) -> None:
    """Append DI curve vertices to Supabase."""
    records = df.to_dict(orient="records")
    upsert_records(
        "di_curve_vertices",
        records,
        conflict_columns=["ref_date", "contract_code"],
    )


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------
def run_pipeline(ref_date: date | None = None) -> pd.DataFrame:
    """Download → persist raw → bootstrap → persist curve for *ref_date*.

    If ref_date is None, uses the previous business day (ANBIMA calendar).
    Returns the bootstrapped curve DataFrame.
    """
    if ref_date is None:
        ref_date = next_business_day(date.today(), offset=-1)

    print(f"[DI Curve] Pipeline start — ref_date={ref_date}")

    df_raw = _fetch_b3_di_settlements(ref_date)
    if df_raw.empty:
        print(f"[DI Curve] No DI1 data from B3 for {ref_date}")
        return pd.DataFrame()

    print(f"[DI Curve] {len(df_raw)} DI1 contracts downloaded from B3")
    save_raw_settlements(df_raw)

    df_curve = bootstrap_di_curve(df_raw, ref_date)
    if df_curve.empty:
        print("[DI Curve] Bootstrapping returned empty — check settlement prices")
        return pd.DataFrame()

    print(f"[DI Curve] Curve bootstrapped — {len(df_curve)} vertices")
    save_curve_vertices(df_curve)
    return df_curve


# ---------------------------------------------------------------------------
# Loaders for the dashboard (read from Supabase)
# ---------------------------------------------------------------------------
def load_curve_from_db(ref_date: date | None = None) -> pd.DataFrame:
    """Load a bootstrapped DI curve from Supabase.

    If ref_date is None, loads the most recent available date.
    """
    from utils.db import get_client

    client = get_client()

    if ref_date is None:
        resp = (
            client.table("di_curve_vertices")
            .select("ref_date")
            .order("ref_date", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return pd.DataFrame()
        ref_date_str = resp.data[0]["ref_date"]
    else:
        ref_date_str = ref_date.isoformat()

    resp = (
        client.table("di_curve_vertices")
        .select("*")
        .eq("ref_date", ref_date_str)
        .order("du")
        .execute()
    )
    return pd.DataFrame(resp.data)


def load_available_dates() -> list[str]:
    """Return all dates that have curve data in Supabase, most recent first."""
    from utils.db import get_client

    client = get_client()
    resp = (
        client.table("di_curve_vertices")
        .select("ref_date")
        .order("ref_date", desc=True)
        .execute()
    )
    return sorted({r["ref_date"] for r in resp.data}, reverse=True)
