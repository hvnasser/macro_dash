"""
BCB / COPOM Meeting Calendar
=============================

SOURCE
------
BCB (Banco Central do Brasil) publishes the COPOM meeting calendar for the
upcoming year at the end of each preceding year:
    https://www.bcb.gov.br/en/monetarypolicy/copommeeting

TIMING
------
COPOM meets over two days (Tuesday + Wednesday).
The decision is announced Wednesday after market close (~21:00 BRT).
The new SELIC target rate takes effect the NEXT BUSINESS DAY (Thursday).

For pricing purposes we always use `effective_date` (Thursday), because
that is when the DI rate starts compounding at the new SELIC level.

STORAGE
-------
Table `bcb_meetings` in Supabase — append-only, one row per meeting.
Unique key: decision_date.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from bizdays import Calendar

from utils.db import get_client, upsert_records

_CAL = Calendar.load("ANBIMA")

# ---------------------------------------------------------------------------
# Known COPOM decision dates (Wednesdays)
# Source: BCB official calendar — verify at bcb.gov.br for future years
# ---------------------------------------------------------------------------
_KNOWN_DECISION_DATES: list[date] = [
    # 2025
    date(2025, 1, 29),
    date(2025, 3, 19),
    date(2025, 5, 7),
    date(2025, 6, 18),
    date(2025, 7, 30),
    date(2025, 9, 17),
    date(2025, 11, 5),
    date(2025, 12, 10),
    # 2026 — verify against official BCB calendar at bcb.gov.br
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 11, 4),
    date(2026, 12, 9),
]


def _build_records() -> list[dict]:
    return [
        {"decision_date": d.isoformat()}
        for d in sorted(_KNOWN_DECISION_DATES)
    ]


def populate_meetings() -> None:
    """Insert all known COPOM meeting dates into Supabase.

    Safe to re-run — existing rows are never overwritten (first snapshot wins).
    """
    records = _build_records()
    upsert_records("bcb_meetings", records, conflict_columns=["decision_date"])
    print(f"[BCB Meetings] {len(records)} meetings upserted to Supabase.")


def load_meetings(
    from_date: date | None = None,
    to_date: date | None = None,
    upcoming_only: bool = False,
) -> pd.DataFrame:
    """Load COPOM meetings from Supabase.

    Args:
        from_date:     Include meetings with decision_date >= from_date.
        to_date:       Include meetings with decision_date <= to_date.
        upcoming_only: If True, only meetings whose effective_date is in the future.
    """
    client = get_client()
    q = client.table("bcb_meetings").select("*").order("decision_date")
    if from_date:
        q = q.gte("decision_date", from_date.isoformat())
    if to_date:
        q = q.lte("decision_date", to_date.isoformat())
    resp = q.execute()
    df = pd.DataFrame(resp.data)
    if df.empty:
        return df

    # Derive effective_date (next business day after decision) — not stored in DB
    df["decision_date_d"] = pd.to_datetime(df["decision_date"]).dt.date
    df["effective_date"]  = df["decision_date_d"].apply(lambda d: _CAL.offset(d, 1).isoformat())
    df = df.drop(columns=["decision_date_d"])

    if upcoming_only:
        today = date.today().isoformat()
        df = df[df["effective_date"] > today]
    return df.reset_index(drop=True)
