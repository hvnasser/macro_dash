"""
DI Curve — Brazilian Interest Rate Yield Curve
================================================
Displays the bootstrapped DI zero-coupon curve stored in Supabase.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_pipeline.di_curve import load_available_dates, load_curve_from_db, run_pipeline

st.set_page_config(page_title="DI Curve", layout="wide")
st.title("Curva DI — Brazilian Interbank Yield Curve")

# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
st.sidebar.header("Settings")

with st.sidebar.expander("Update Data", expanded=False):
    st.write("Download the latest DI settlement data from B3 and save to Supabase.")
    if st.button("Run Pipeline (latest biz day)"):
        with st.spinner("Downloading and processing…"):
            df_new = run_pipeline()
        if df_new.empty:
            st.warning("No data returned. Check the date or B3 availability.")
        else:
            st.success(f"Pipeline complete — {len(df_new)} vertices saved.")

available_dates = load_available_dates()
if not available_dates:
    st.info(
        "No curve data in the database yet. "
        "Use the **Run Pipeline** button in the sidebar to fetch data."
    )
    st.stop()

selected_date = st.sidebar.selectbox(
    "Reference date",
    options=available_dates,
    index=0,
    format_func=lambda d: d,
)

# Optionally add a comparison date
compare_dates = st.sidebar.multiselect(
    "Compare with (optional)",
    options=[d for d in available_dates if d != selected_date],
    default=[],
)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
from datetime import date as _date

df_main = load_curve_from_db(ref_date=_date.fromisoformat(selected_date))

if df_main.empty:
    st.warning(f"No curve data for {selected_date}.")
    st.stop()

df_main["rate_pct"] = df_main["rate_252"] * 100

# ---------------------------------------------------------------------------
# Chart — yield curve
# ---------------------------------------------------------------------------
fig = go.Figure()

fig.add_trace(
    go.Scatter(
        x=df_main["du"],
        y=df_main["rate_pct"],
        mode="lines+markers",
        name=selected_date,
        line=dict(width=2.5),
        marker=dict(size=6),
        hovertemplate=(
            "<b>%{customdata}</b><br>"
            "DU: %{x}<br>"
            "Rate: %{y:.3f}%<extra></extra>"
        ),
        customdata=df_main["expiry_date"],
    )
)

for cdate in compare_dates:
    df_cmp = load_curve_from_db(ref_date=_date.fromisoformat(cdate))
    if df_cmp.empty:
        continue
    df_cmp["rate_pct"] = df_cmp["rate_252"] * 100
    fig.add_trace(
        go.Scatter(
            x=df_cmp["du"],
            y=df_cmp["rate_pct"],
            mode="lines+markers",
            name=cdate,
            line=dict(width=1.5, dash="dash"),
            marker=dict(size=5),
            hovertemplate=(
                "<b>%{customdata}</b><br>"
                "DU: %{x}<br>"
                "Rate: %{y:.3f}%<extra></extra>"
            ),
            customdata=df_cmp["expiry_date"],
        )
    )

fig.update_layout(
    title=f"DI Zero-Coupon Curve — {selected_date}",
    xaxis_title="Business Days to Expiry (DU)",
    yaxis_title="Annualised Rate (% a.a.)",
    legend_title="Date",
    hovermode="x unified",
    template="plotly_dark",
    height=520,
)

st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Metrics row
# ---------------------------------------------------------------------------
cols = st.columns(4)

short_end = df_main[df_main["du"] <= 63].iloc[-1] if len(df_main[df_main["du"] <= 63]) else df_main.iloc[0]
mid = df_main[(df_main["du"] >= 250) & (df_main["du"] <= 760)]
mid_row = mid.iloc[len(mid) // 2] if not mid.empty else df_main.iloc[len(df_main) // 2]
long_end = df_main.iloc[-1]
slope = long_end["rate_pct"] - df_main.iloc[0]["rate_pct"]

cols[0].metric("Short end (≤3M)", f"{short_end['rate_pct']:.2f}%", help=short_end["expiry_date"])
cols[1].metric("Mid (≈1Y)", f"{mid_row['rate_pct']:.2f}%", help=mid_row["expiry_date"])
cols[2].metric("Long end", f"{long_end['rate_pct']:.2f}%", help=long_end["expiry_date"])
cols[3].metric("Slope (long - short)", f"{slope:+.2f}%")

# ---------------------------------------------------------------------------
# Raw data table
# ---------------------------------------------------------------------------
with st.expander("Raw curve data", expanded=False):
    st.dataframe(
        df_main[["contract_code", "expiry_date", "du", "rate_252", "rate_pct"]]
        .rename(columns={"rate_pct": "rate (% a.a.)", "rate_252": "rate (decimal)", "du": "DU"})
        .style.format({"rate (% a.a.)": "{:.4f}", "rate (decimal)": "{:.6f}"}),
        use_container_width=True,
    )
