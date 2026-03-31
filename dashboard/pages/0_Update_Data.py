"""
Data Management — Download & Calculate
========================================
Central place to update all data in the database.
"""

import os
import sys
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from bizdays import Calendar

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_pipeline.bcb_meetings import populate_meetings
from data_pipeline.di_curve import (
    fetch_di_settlements,
    recalculate_curve_from_db,
    run_pipeline,
    save_raw_settlements,
    load_available_dates,
)

st.set_page_config(page_title="Data Management", layout="wide")
st.title("Data Management")

_CAL = Calendar.load("ANBIMA")


def _biz_days_in_range(start: date, end: date) -> list[date]:
    """Return all ANBIMA business days between start and end (inclusive)."""
    days = []
    d = start
    while d <= end:
        if _CAL.isbizday(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def _last_biz_day() -> date:
    return _CAL.offset(date.today(), -1)


# ---------------------------------------------------------------------------
# Section 1 — DI Raw Settlements
# ---------------------------------------------------------------------------
st.subheader("① DI Raw Settlements  (`di_futures_raw`)")
st.caption("Download DI1 settlement prices from B3 and save to Supabase.")

mode_raw = st.radio(
    "Date selection",
    ["Latest business day", "Specific date", "Date range"],
    horizontal=True,
    key="mode_raw",
)

if mode_raw == "Latest business day":
    raw_dates = [_last_biz_day()]
    st.info(f"Will download: **{raw_dates[0]}**")

elif mode_raw == "Specific date":
    raw_date = st.date_input("Date", value=_last_biz_day(), key="raw_single")
    raw_dates = [raw_date]

else:  # Date range
    c1, c2 = st.columns(2)
    raw_start = c1.date_input("From", value=_last_biz_day() - timedelta(days=30), key="raw_start")
    raw_end   = c2.date_input("To",   value=_last_biz_day(), key="raw_end")
    raw_dates = _biz_days_in_range(raw_start, raw_end)
    st.info(f"{len(raw_dates)} business days selected ({raw_start} → {raw_end})")

if st.button("⬇️ Download & Save raw settlements", type="primary"):
    total = len(raw_dates)
    progress = st.progress(0, text="Starting…")
    log = st.empty()
    ok, skipped, failed = 0, 0, 0

    for i, d in enumerate(raw_dates):
        progress.progress((i + 1) / total, text=f"Fetching {d}…")
        try:
            df_raw = fetch_di_settlements(d)
            if df_raw.empty:
                log.warning(f"{d}: no data — market holiday or data not yet published by B3")
                skipped += 1
            else:
                save_raw_settlements(df_raw)
                log.success(f"{d}: {len(df_raw)} contracts saved")
                ok += 1
        except Exception as e:
            err = str(e)
            if "404" in err:
                log.warning(f"{d}: data not yet available — B3 usually publishes after market close (~18:30 BRT). Try again later or select a prior date.")
            else:
                log.error(f"{d}: {err}")
            failed += 1

    progress.empty()
    st.success(f"Done — {ok} days saved · {skipped} skipped · {failed} failed")

st.divider()

# ---------------------------------------------------------------------------
# Section 2 — DI Curve Vertices
# ---------------------------------------------------------------------------
st.subheader("② DI Curve Vertices  (`di_curve_vertices`)")
st.caption(
    "Recalculate spot rates and forward rates from raw settlements already in the DB. "
    "No B3 download needed."
)

available = load_available_dates()  # dates already in di_curve_vertices

mode_curve = st.radio(
    "Date selection",
    ["Latest date in DB", "Specific date", "Date range", "All dates in DB"],
    horizontal=True,
    key="mode_curve",
)

if mode_curve == "Latest date in DB":
    curve_dates = [date.fromisoformat(available[0])] if available else []
    if curve_dates:
        st.info(f"Will recalculate: **{curve_dates[0]}**")
    else:
        st.warning("No dates found in di_curve_vertices.")

elif mode_curve == "Specific date":
    default_d = date.fromisoformat(available[0]) if available else _last_biz_day()
    c_date = st.date_input("Date", value=default_d, key="curve_single")
    curve_dates = [c_date]

elif mode_curve == "Date range":
    c1, c2 = st.columns(2)
    c_start = c1.date_input("From", value=_last_biz_day() - timedelta(days=30), key="curve_start")
    c_end   = c2.date_input("To",   value=_last_biz_day(), key="curve_end")
    curve_dates = _biz_days_in_range(c_start, c_end)
    st.info(f"{len(curve_dates)} business days selected")

else:  # All dates
    curve_dates = [date.fromisoformat(d) for d in available]
    st.info(f"{len(curve_dates)} dates in di_curve_vertices will be recalculated")

if st.button("⚙️ Calculate / Recalculate curve vertices", type="primary"):
    total = len(curve_dates)
    if total == 0:
        st.warning("No dates to process.")
    else:
        progress = st.progress(0, text="Starting…")
        log = st.empty()
        ok, skipped, failed = 0, 0, 0

        for i, d in enumerate(curve_dates):
            progress.progress((i + 1) / total, text=f"Processing {d}…")
            try:
                df_out = recalculate_curve_from_db(d)
                if df_out.empty:
                    log.warning(f"{d}: no raw data in DB — run download first")
                    skipped += 1
                else:
                    log.success(f"{d}: {len(df_out)} vertices calculated")
                    ok += 1
            except Exception as e:
                log.error(f"{d}: {e}")
                failed += 1

        progress.empty()
        st.success(f"Done — {ok} days calculated · {skipped} skipped · {failed} failed")

st.divider()

# ---------------------------------------------------------------------------
# Section 3 — BCB Meeting Calendar
# ---------------------------------------------------------------------------
st.subheader("③ BCB / COPOM Meeting Calendar  (`bcb_meetings`)")
st.caption("Load the pre-configured 2025–2026 COPOM decision dates into Supabase.")

if st.button("📅 Populate meetings calendar"):
    try:
        populate_meetings()
        st.success("Meeting calendar saved to Supabase.")
    except Exception as e:
        st.error(str(e))

st.divider()

# ---------------------------------------------------------------------------
# Status overview
# ---------------------------------------------------------------------------
st.subheader("Database status")

try:
    from utils.db import get_client
    client = get_client()

    c1, c2, c3 = st.columns(3)

    # di_futures_raw
    r = client.table("di_futures_raw").select("ref_date", count="exact").execute()
    latest_raw = client.table("di_futures_raw").select("ref_date").order("ref_date", desc=True).limit(1).execute()
    c1.metric(
        "di_futures_raw",
        f"{r.count:,} rows",
        delta=f"latest: {latest_raw.data[0]['ref_date']}" if latest_raw.data else "empty",
        delta_color="off",
    )

    # di_curve_vertices
    r2 = client.table("di_curve_vertices").select("ref_date", count="exact").execute()
    latest_cv = client.table("di_curve_vertices").select("ref_date").order("ref_date", desc=True).limit(1).execute()
    c2.metric(
        "di_curve_vertices",
        f"{r2.count:,} rows",
        delta=f"latest: {latest_cv.data[0]['ref_date']}" if latest_cv.data else "empty",
        delta_color="off",
    )

    # bcb_meetings
    r3 = client.table("bcb_meetings").select("decision_date", count="exact").execute()
    latest_m = client.table("bcb_meetings").select("decision_date").order("decision_date", desc=True).limit(1).execute()
    c3.metric(
        "bcb_meetings",
        f"{r3.count:,} rows",
        delta=f"latest: {latest_m.data[0]['decision_date']}" if latest_m.data else "empty",
        delta_color="off",
    )
except Exception as e:
    st.error(f"Could not fetch DB status: {e}")
