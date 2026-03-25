"""
Brazilian DI Futures Curve Pipeline
====================================

DATA SOURCE
-----------
B3 (Brasil, Bolsa, Balcão) via the **PYield** library (github.com/crdcj/PYield).

PYield uses two B3 endpoints depending on date:
  - Dates up to 2025-12-12: legacy HTML endpoint at www2.bmf.com.br
  - Dates from 2025-12-13: POST to arquivos.b3.com.br/bdi/table/export/csv

Both return the DI1 settlement data. PYield handles authentication headers,
parsing, and already computes `SettlementRate` and `BDaysToExp`.

DI1 CONTRACT MECHANICS
-----------------------
Each DI1 contract expires on the first business day of a given month.
PU (Preço Unitário) has face value R$ 100,000:

    rate = (100_000 / PU) ^ (252 / DU) - 1     [forward to rate]
    PU   = 100_000 / (1 + rate) ^ (DU / 252)   [rate to forward]

DU = business days from ref_date to expiry, using B3/ANBIMA calendar.
PYield computes both PU and rate directly from the B3 settlement file.

HOLIDAY CALENDAR
----------------
PYield uses B3's own business day library internally (`pyield.bday`), which
matches the ANBIMA calendar — the official Brazilian fixed income standard.
It covers national holidays + Carnival + Corpus Christi + banking holidays.

STORAGE DESIGN (append-only, no overrides)
------------------------------------------
Tables:
  di_futures_raw     — raw B3 data: PU + rate + DU per contract per date
  di_curve_vertices  — cleaned curve: rate_252 + DU, sorted by DU
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pyield as yd

from utils.db import upsert_records

# ---------------------------------------------------------------------------
# Fetch from B3 via PYield
# ---------------------------------------------------------------------------

def fetch_di_settlements(ref_date: date) -> pd.DataFrame:
    """Download DI1 settlement data from B3 for *ref_date* using PYield.

    Returns a pandas DataFrame with columns:
        ref_date, contract_code, expiry_date, du,
        settlement_price, settlement_rate,
        trade_volume, financial_volume
    Sorted ascending by du. Empty DataFrame if no data.
    """
    pl_df = yd.futures(date=ref_date, contract_code="DI1")

    if pl_df.is_empty():
        return pd.DataFrame()

    # PYield returns a Polars DataFrame — convert to pandas
    df = pl_df.to_pandas()

    # Keep only contracts with a valid settlement rate
    df = df[df["SettlementRate"].notna() & (df["SettlementRate"] > 0)].copy()

    if df.empty:
        return pd.DataFrame()

    result = pd.DataFrame({
        "ref_date":         ref_date.isoformat(),
        "contract_code":    df["TickerSymbol"],
        "expiry_date":      df["ExpirationDate"].astype(str),
        "du":               df["BDaysToExp"].astype(int),
        "settlement_price": df["SettlementPrice"],   # PU, face = R$ 100,000
        "settlement_rate":  df["SettlementRate"],    # annualised rate (e.g. 0.1275)
        "trade_volume":     df["TradeVolume"].apply(lambda x: int(x) if pd.notna(x) else None) if "TradeVolume" in df.columns else None,
        "financial_volume": df.get("FinancialVolume"),
    })

    return result.sort_values("du").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Persist raw data (append-only)
# ---------------------------------------------------------------------------

def save_raw_settlements(df: pd.DataFrame) -> None:
    """Append DI1 raw settlement rows to Supabase.

    Conflict key: (ref_date, contract_code).
    First snapshot wins — existing rows are never overwritten.
    """
    records = df.to_dict(orient="records")
    upsert_records("di_futures_raw", records, conflict_columns=["ref_date", "contract_code"])


# ---------------------------------------------------------------------------
# Build curve vertices
# ---------------------------------------------------------------------------

def build_curve_vertices(df_raw: pd.DataFrame, ref_date: date) -> pd.DataFrame:
    """Select and clean the curve vertices from raw settlement data.

    Since PYield already derives the zero-coupon rate from the PU via:
        rate = (100_000 / PU) ^ (252 / DU) - 1
    using B3's own calendar, no further bootstrapping is needed.
    We just rename columns and drop rows without a settlement rate.

    Returns DataFrame with: ref_date, contract_code, expiry_date, du, rate_252
    """
    df = df_raw[df_raw["ref_date"] == ref_date.isoformat()].copy()
    df = df[df["settlement_rate"].notna() & (df["du"] > 0)].copy()
    df = df.sort_values("du").reset_index(drop=True)

    return df[["ref_date", "contract_code", "expiry_date", "du", "settlement_rate"]].rename(
        columns={"settlement_rate": "rate_252"}
    )


# ---------------------------------------------------------------------------
# Persist curve (append-only)
# ---------------------------------------------------------------------------

def save_curve_vertices(df: pd.DataFrame) -> None:
    """Append DI curve vertices to Supabase."""
    records = df.to_dict(orient="records")
    upsert_records("di_curve_vertices", records, conflict_columns=["ref_date", "contract_code"])


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------

def run_pipeline(ref_date: date | None = None) -> pd.DataFrame:
    """Fetch → save raw → build curve → save curve for *ref_date*.

    If ref_date is None, uses yesterday (PYield resolves the last valid trade date).
    Returns the curve vertices DataFrame.
    """
    from datetime import timedelta
    if ref_date is None:
        ref_date = date.today() - timedelta(days=1)

    print(f"[DI Curve] Pipeline start — ref_date={ref_date}")

    df_raw = fetch_di_settlements(ref_date)
    if df_raw.empty:
        print(f"[DI Curve] No DI1 data from B3 for {ref_date} (holiday or invalid date?)")
        return pd.DataFrame()

    print(f"[DI Curve] {len(df_raw)} DI1 contracts fetched from B3")
    save_raw_settlements(df_raw)

    df_curve = build_curve_vertices(df_raw, ref_date)
    if df_curve.empty:
        print("[DI Curve] No valid curve vertices after filtering")
        return pd.DataFrame()

    print(f"[DI Curve] {len(df_curve)} vertices saved to di_curve_vertices")
    save_curve_vertices(df_curve)
    return df_curve


# ---------------------------------------------------------------------------
# Loaders for the dashboard (read from Supabase)
# ---------------------------------------------------------------------------

def load_curve_from_db(ref_date: date | None = None) -> pd.DataFrame:
    """Load a DI curve from Supabase. If ref_date is None, loads the latest."""
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
    """Return all dates with curve data, most recent first."""
    from utils.db import get_client
    client = get_client()
    resp = (
        client.table("di_curve_vertices")
        .select("ref_date")
        .order("ref_date", desc=True)
        .execute()
    )
    return sorted({r["ref_date"] for r in resp.data}, reverse=True)
