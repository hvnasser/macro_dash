"""
COPOM Pricing — What is the DI Curve Pricing per Meeting?
===========================================================
"""

import os
import sys
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_pipeline.bcb_meetings import load_meetings, populate_meetings
from data_pipeline.copom_pricing import build_hypothesis_curve, calculate_copom_pricing
from data_pipeline.di_curve import load_available_dates, load_curve_from_db

st.set_page_config(page_title="COPOM Pricing", layout="wide")

CHART_TEMPLATE = "plotly_dark"
COLOR_MARKET   = "#00C8FF"
COLOR_HYP      = "#FF9F43"

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("COPOM Pricing")

available_dates = load_available_dates()
if not available_dates:
    st.error("No DI curve data found. Run the DI Curve pipeline first.")
    st.stop()

curve_date = st.sidebar.selectbox("DI Curve date", available_dates, index=0)

st.sidebar.divider()

# Current SELIC input — pre-filled from shortest contract as proxy
df_curve = load_curve_from_db(date.fromisoformat(curve_date))
if df_curve.empty:
    st.error(f"No curve data for {curve_date}.")
    st.stop()

df_curve["rate_252"] = pd.to_numeric(df_curve["rate_252"], errors="coerce")
df_curve["du"]       = pd.to_numeric(df_curve["du"], errors="coerce").astype(int)

proxy_selic = float(df_curve.sort_values("du").iloc[0]["rate_252"])

selic_input = st.sidebar.number_input(
    "Current SELIC target (% a.a.)",
    min_value=0.0,
    max_value=50.0,
    value=round(proxy_selic * 100, 2),
    step=0.25,
    help="SELIC target rate decided at last COPOM meeting. Used as the starting rate for pricing.",
)
selic_rate = selic_input / 100.0

st.sidebar.divider()
with st.sidebar.expander("Meetings calendar"):
    st.caption("Load the pre-configured meeting calendar into Supabase.")
    if st.button("Populate meetings"):
        populate_meetings()
        st.success("Done.")
        st.rerun()

# ---------------------------------------------------------------------------
# Load meetings
# ---------------------------------------------------------------------------
ref_date = date.fromisoformat(curve_date)
df_meetings = load_meetings(from_date=ref_date)

if df_meetings.empty:
    st.title("COPOM Pricing")
    st.warning(
        "No meeting calendar found in Supabase. "
        "Use **Populate meetings** in the sidebar."
    )
    st.stop()

# Only meetings whose effective_date is after the curve date
df_meetings = df_meetings[df_meetings["effective_date"] > curve_date].reset_index(drop=True)

# ---------------------------------------------------------------------------
# Calculate implied pricing
# ---------------------------------------------------------------------------
df_pricing = calculate_copom_pricing(df_curve, df_meetings, ref_date, selic_rate)

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("COPOM Pricing")
st.caption(
    f"DI curve: **{curve_date}** · "
    f"SELIC: **{selic_input:.2f}%** · "
    f"Meetings priced: **{len(df_pricing)}**"
)

# ---------------------------------------------------------------------------
# Section 1 — Implied changes per meeting
# ---------------------------------------------------------------------------
st.subheader("Implied SELIC change per meeting")

if df_pricing.empty:
    st.info("No upcoming meetings within the curve horizon.")
else:
    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        colors = [
            "#51CF66" if v < 0 else ("#FF6B6B" if v > 0 else "#868E96")
            for v in df_pricing["change_bps"]
        ]
        fig_bar = go.Figure(
            go.Bar(
                x=df_pricing["decision_date"],
                y=df_pricing["change_bps"],
                marker_color=colors,
                text=df_pricing["change_bps"].apply(lambda v: f"{v:+.0f} bps"),
                textposition="outside",
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "Change: %{y:+.1f} bps<br>"
                    "Rate before: %{customdata[0]:.2f}%<br>"
                    "Rate after:  %{customdata[1]:.2f}%<extra></extra>"
                ),
                customdata=df_pricing[["rate_before", "rate_after"]].values,
            )
        )
        fig_bar.update_layout(
            title="Priced change per COPOM meeting (bps)",
            xaxis_title="Decision date",
            yaxis_title="Change (bps)",
            template=CHART_TEMPLATE,
            height=380,
            margin=dict(t=50, b=40),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    with col_table:
        tbl = df_pricing[["decision_date", "effective_date", "rate_before", "rate_after", "change_bps"]].copy()
        tbl.columns = ["Decision", "Effective", "Before (%)", "After (%)", "Δ (bps)"]

        def _color_bps(val):
            if pd.isna(val) or val == 0:
                return ""
            return "color: #51CF66" if val < 0 else "color: #FF6B6B"

        st.dataframe(
            tbl.style
            .format({"Before (%)": "{:.4f}", "After (%)": "{:.4f}", "Δ (bps)": "{:+.1f}"})
            .applymap(_color_bps, subset=["Δ (bps)"]),
            use_container_width=True,
            height=380,
        )

# ---------------------------------------------------------------------------
# Section 2 — Implied SELIC path
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Implied SELIC path")

if not df_pricing.empty:
    # Build step chart: SELIC level before and after each meeting
    path_dates  = [curve_date] + list(df_pricing["effective_date"])
    path_rates  = [selic_input] + list(df_pricing["rate_after"])

    fig_path = go.Figure(
        go.Scatter(
            x=path_dates,
            y=path_rates,
            mode="lines+markers",
            line=dict(color=COLOR_MARKET, width=2.5, shape="hv"),  # step chart
            marker=dict(size=8, color=COLOR_MARKET),
            name="Market implied",
            hovertemplate="<b>%{x}</b><br>SELIC: %{y:.4f}%<extra></extra>",
        )
    )
    fig_path.add_hline(
        y=selic_input,
        line_dash="dot",
        line_color="gray",
        annotation_text=f"Current SELIC {selic_input:.2f}%",
        annotation_position="top left",
    )
    fig_path.update_layout(
        title="Market-implied SELIC path",
        xaxis_title="Effective date",
        yaxis_title="SELIC (% a.a.)",
        template=CHART_TEMPLATE,
        height=350,
        margin=dict(t=50, b=40),
    )
    st.plotly_chart(fig_path, use_container_width=True)

# ---------------------------------------------------------------------------
# Section 3 — Hypothesis
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Hypothesis — your rate path vs. market")

st.caption(
    "Edit the expected change (bps) for each meeting below. "
    "The chart compares the resulting implied DI curve with the market curve."
)

if df_pricing.empty:
    st.info("No meetings within curve horizon to build a hypothesis.")
    st.stop()

# Build editable hypothesis table
hyp_df = df_pricing[["decision_date", "effective_date", "rate_before", "change_bps"]].copy()
hyp_df = hyp_df.rename(columns={
    "decision_date":  "Decision date",
    "effective_date": "Effective date",
    "rate_before":    "Rate before (%)",
    "change_bps":     "Market Δ (bps)",
})
hyp_df["Your Δ (bps)"] = hyp_df["Market Δ (bps)"]  # default = market

edited = st.data_editor(
    hyp_df,
    column_config={
        "Decision date":  st.column_config.TextColumn(disabled=True),
        "Effective date": st.column_config.TextColumn(disabled=True),
        "Rate before (%)": st.column_config.NumberColumn(disabled=True, format="%.4f"),
        "Market Δ (bps)": st.column_config.NumberColumn(disabled=True, format="%+.1f"),
        "Your Δ (bps)": st.column_config.NumberColumn(
            help="Enter your expected SELIC change in basis points (e.g. -25, 0, +50).",
            step=25.0,
            format="%+.0f",
        ),
    },
    use_container_width=True,
    hide_index=True,
    num_rows="fixed",
)

# Map decision_date → user change
hypothesis = {
    str(row["Decision date"]): float(row["Your Δ (bps)"])
    for _, row in edited.iterrows()
}

# Build hypothesis curve
df_hyp = build_hypothesis_curve(df_curve, df_meetings, ref_date, selic_rate, hypothesis)
df_curve["rate_pct"] = df_curve["rate_252"] * 100
df_hyp["rate_pct"]   = df_hyp["rate_252"]  * 100

def _short(code: str) -> str:
    return code[3:] if code.startswith("DI1") else code

fig_hyp = go.Figure()

fig_hyp.add_trace(go.Scatter(
    x=df_curve["contract_code"].map(_short),
    y=df_curve["rate_pct"],
    mode="lines+markers",
    name=f"Market ({curve_date})",
    line=dict(color=COLOR_MARKET, width=2.5),
    marker=dict(size=6),
    hovertemplate="<b>%{x}</b><br>DU: %{customdata}<br>Rate: %{y:.3f}%<extra></extra>",
    customdata=df_curve["du"],
))

fig_hyp.add_trace(go.Scatter(
    x=df_hyp["contract_code"].map(_short),
    y=df_hyp["rate_pct"],
    mode="lines+markers",
    name="Your hypothesis",
    line=dict(color=COLOR_HYP, width=2.5, dash="dash"),
    marker=dict(size=6),
    hovertemplate="<b>%{x}</b><br>DU: %{customdata}<br>Rate: %{y:.3f}%<extra></extra>",
    customdata=df_hyp["du"],
))

fig_hyp.update_layout(
    title="Market DI curve vs. your hypothesis",
    xaxis_title="Contract",
    yaxis_title="Spot Rate (% a.a.)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    template=CHART_TEMPLATE,
    height=420,
    margin=dict(t=60, b=40),
)
st.plotly_chart(fig_hyp, use_container_width=True)

# Show hypothesis implied SELIC path
hyp_rates = [selic_input]
cur = selic_rate
for _, row in edited.iterrows():
    cur += float(row["Your Δ (bps)"]) / 10_000.0
    hyp_rates.append(round(cur * 100, 4))

hyp_dates = [curve_date] + list(edited["Effective date"])

fig_hyp_path = go.Figure()
fig_hyp_path.add_trace(go.Scatter(
    x=path_dates, y=path_rates,
    mode="lines+markers", name="Market implied",
    line=dict(color=COLOR_MARKET, width=2, shape="hv"),
    marker=dict(size=7),
))
fig_hyp_path.add_trace(go.Scatter(
    x=hyp_dates, y=hyp_rates,
    mode="lines+markers", name="Your hypothesis",
    line=dict(color=COLOR_HYP, width=2, dash="dash", shape="hv"),
    marker=dict(size=7),
))
fig_hyp_path.update_layout(
    title="SELIC path — market vs. hypothesis",
    xaxis_title="Effective date",
    yaxis_title="SELIC (% a.a.)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    template=CHART_TEMPLATE,
    height=320,
    margin=dict(t=60, b=40),
)
st.plotly_chart(fig_hyp_path, use_container_width=True)
