"""
Brazilian DI Futures Curve Pipeline
====================================
1. Download DI1 (One-Day Interbank Deposit) futures settlement prices from B3.
2. Persist raw settlement data to Supabase (append-only, no overrides).
3. Bootstrap the DI yield curve (business-day counts with the ANBIMA calendar).
4. Persist bootstrapped curve vertices to Supabase.

Tables used
-----------
di_futures_raw   : raw settlement data from B3
di_curve_vertices: bootstrapped zero-coupon vertices (rate per vertex per date)
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests
from bizdays import Calendar

from utils.db import upsert_records

# ---------------------------------------------------------------------------
# Calendar (ANBIMA / B3 Brazil business days)
# ---------------------------------------------------------------------------
cal = Calendar.load("ANBIMA")


def _business_days_between(start: date, end: date) -> int:
    """Count business days from start (exclusive) to end (inclusive)."""
    return cal.bizdays(start, end)


# ---------------------------------------------------------------------------
# B3 raw data download
# ---------------------------------------------------------------------------
B3_DAILY_BULLETIN_URL = (
    "https://www.b3.com.br/pesquisapregao/download?filelist=BDI{date}.zip"
)
B3_SETTLEMENTS_URL = (
    "https://arquivos.b3.com.br/apinegocios/cotacoesajuste?idProduto=DI1&dataReferencia={date}"
)


def _fetch_b3_di_settlements(ref_date: date) -> pd.DataFrame:
    """Fetch DI1 settlement prices from B3 public API for a given date.

    Returns a DataFrame with columns:
        contract_code, expiry_date, settlement_rate, open_interest, ref_date
    """
    date_str = ref_date.strftime("%Y-%m-%d")
    url = B3_SETTLEMENTS_URL.format(date=date_str)

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    rows = []
    for item in data.get("Ativo", []):
        for serie in item.get("Serie", []):
            expiry_str = serie.get("DataVencimento", "")
            settlement_price = serie.get("PrecoAjuste")
            open_interest = serie.get("QuantContrAberto")
            contract_code = serie.get("Codigo", "")

            if not expiry_str or settlement_price is None:
                continue

            try:
                expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            # B3 publishes the settlement PRICE (PU) of a DI contract.
            # PU = 100_000 / (1 + rate)^(du/252)
            # where du = business days to expiry. We store price; rate is derived later.
            rows.append(
                {
                    "ref_date": ref_date.isoformat(),
                    "contract_code": contract_code,
                    "expiry_date": expiry.isoformat(),
                    "settlement_price": float(settlement_price),
                    "open_interest": int(open_interest) if open_interest else None,
                }
            )

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Persist raw data
# ---------------------------------------------------------------------------
def save_raw_settlements(df: pd.DataFrame) -> None:
    """Append raw DI settlement rows to Supabase.

    Conflict key: (ref_date, contract_code) — keeps the first snapshot,
    never overwrites, preserving revisions history as separate rows when
    collected_at differs (see extended schema note in README).
    """
    records = df.to_dict(orient="records")
    upsert_records("di_futures_raw", records, conflict_columns=["ref_date", "contract_code"])


# ---------------------------------------------------------------------------
# Curve bootstrapping
# ---------------------------------------------------------------------------
def _pu_to_rate(pu: float, du: int) -> float:
    """Convert DI settlement price (PU) to annualised rate (252 bd convention)."""
    if du <= 0 or pu <= 0:
        return float("nan")
    return (100_000.0 / pu) ** (252.0 / du) - 1.0


def bootstrap_di_curve(df_raw: pd.DataFrame, ref_date: date) -> pd.DataFrame:
    """Bootstrap the DI zero-coupon curve from raw settlement prices.

    Returns a DataFrame with columns:
        ref_date, expiry_date, du, rate_252, contract_code
    sorted by du (business days to expiry).
    """
    df = df_raw[df_raw["ref_date"] == ref_date.isoformat()].copy()
    if df.empty:
        return pd.DataFrame()

    df["expiry_date_dt"] = pd.to_datetime(df["expiry_date"]).dt.date
    df["du"] = df["expiry_date_dt"].apply(lambda e: _business_days_between(ref_date, e))
    df = df[df["du"] > 0].copy()
    df["rate_252"] = df.apply(
        lambda r: _pu_to_rate(r["settlement_price"], r["du"]), axis=1
    )
    df = df.dropna(subset=["rate_252"]).sort_values("du").reset_index(drop=True)

    result = df[["ref_date", "contract_code", "expiry_date", "du", "rate_252"]].copy()
    return result


# ---------------------------------------------------------------------------
# Persist bootstrapped curve
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
# Public entry point
# ---------------------------------------------------------------------------
def run_pipeline(ref_date: date | None = None) -> pd.DataFrame:
    """Download, save, bootstrap and save the DI curve for ref_date.

    Returns the bootstrapped curve DataFrame.
    """
    if ref_date is None:
        # Use previous business day (B3 publishes end-of-day)
        today = date.today()
        ref_date = cal.offset(today, -1)

    print(f"[DI Curve] Running pipeline for {ref_date}")

    df_raw = _fetch_b3_di_settlements(ref_date)
    if df_raw.empty:
        print(f"[DI Curve] No data found for {ref_date}")
        return pd.DataFrame()

    print(f"[DI Curve] {len(df_raw)} contracts downloaded")
    save_raw_settlements(df_raw)

    df_curve = bootstrap_di_curve(df_raw, ref_date)
    if df_curve.empty:
        print("[DI Curve] Could not bootstrap curve (no valid contracts)")
        return pd.DataFrame()

    print(f"[DI Curve] Curve bootstrapped — {len(df_curve)} vertices")
    save_curve_vertices(df_curve)

    return df_curve


# ---------------------------------------------------------------------------
# Load from Supabase for the dashboard
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
    df = pd.DataFrame(resp.data)
    return df


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
    dates = sorted({r["ref_date"] for r in resp.data}, reverse=True)
    return dates
