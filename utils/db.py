"""Supabase client and helper utilities."""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def _sanitize_value(v: object) -> object:
    """Convert a single value to a JSON/Postgres-safe Python native type.

    Handles three cases that commonly arise from pandas DataFrames:
      - NaN / Inf floats         → None
      - Whole-number floats      → int  (e.g. 166882.0 → 166882, needed for
                                         bigint/integer Postgres columns)
      - numpy scalar types       → Python native (int64 → int, float64 → float)
      - pandas NA                → None
    """
    import math

    # pandas NA (from nullable integer / boolean dtypes)
    try:
        import pandas as pd
        if v is pd.NA:
            return None
    except Exception:
        pass

    # numpy scalars → Python natives
    if hasattr(v, "item"):
        v = v.item()

    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        # whole-number float (e.g. trade_volume=166882.0) must be int for
        # Postgres bigint columns — numeric/decimal columns accept both
        if v.is_integer():
            return int(v)
        return v

    return v


def _clean_records(records: list[dict]) -> list[dict]:
    """Sanitise every value in every record before sending to Supabase."""
    return [{k: _sanitize_value(v) for k, v in row.items()} for row in records]


def upsert_records(
    table: str,
    records: list[dict],
    conflict_columns: list[str],
    update_on_conflict: bool = False,
) -> None:
    """Upsert records into *table*.

    By default (update_on_conflict=False) existing rows are left untouched —
    this preserves raw source data and revision history.

    Set update_on_conflict=True for derived/calculated tables (e.g.
    di_curve_vertices) where recalculating should overwrite the stored values.
    """
    if not records:
        return
    client = get_client()
    clean = _clean_records(records)
    client.table(table).upsert(
        clean,
        on_conflict=",".join(conflict_columns),
        ignore_duplicates=not update_on_conflict,
    ).execute()
