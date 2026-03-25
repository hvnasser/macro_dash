"""Supabase client and helper utilities."""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def _clean_records(records: list[dict]) -> list[dict]:
    """Replace float NaN/Inf with None so records are JSON-serialisable."""
    import math
    cleaned = []
    for row in records:
        cleaned.append(
            {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in row.items()}
        )
    return cleaned


def upsert_records(table: str, records: list[dict], conflict_columns: list[str]) -> None:
    """Insert records, ignoring duplicates based on conflict_columns.

    We never overwrite existing rows — this preserves data revisions history.
    If you want to track revisions, add a 'collected_at' timestamp to the
    conflict_columns list so each snapshot is stored independently.
    """
    if not records:
        return
    client = get_client()
    clean = _clean_records(records)
    # on_conflict with ignore keeps the old row intact (no override)
    client.table(table).upsert(clean, on_conflict=",".join(conflict_columns), ignore_duplicates=True).execute()
