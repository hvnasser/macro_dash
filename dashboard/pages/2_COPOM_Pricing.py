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
from data_pipeline.selic_rate import fetch_selic_target

st.set_page_config(page_title="COPOM Pricing", layout="wide")

CHART_TEMPLATE = "plotly_dark"
COLOR_MARKET   = "#00C8FF"
COLOR_HYP      = "#FF9F43"


def _nearest_di_rate(decision_date_str: str, df_curve: pd.DataFrame, ref_date: date) -> float:
    """Return the DI spot rate (%) for the contract nearest to the meeting date."""
    from bizdays import Calendar
    cal = Calendar.load("ANBIMA")
    try:
        d = date.fromisoformat(str(decision_date_str))
        target_du = cal.bizdays(ref_date, d)
    except Exception:
        return float("nan")
    curve = df_curve.sort_values("du")
    near = curve[curve["du"] >= target_du]
    row = near.iloc[0] if not near.empty else curve.iloc[-1]
    return float(row["rate_252"]) * 100

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

# Fetch current SELIC target from BCB SGS (series 432)
_bcb_selic, _bcb_date = None, None
try:
    _bcb_selic, _bcb_date = fetch_selic_target()
except Exception:
    pass

_default_selic = round(_bcb_selic * 100, 2) if _bcb_selic else round(
    float(df_curve.sort_values("du").iloc[0]["rate_252"]) * 100, 2
)

if _bcb_selic:
    st.sidebar.success(f"SELIC from BCB: **{_bcb_selic*100:.2f}%** (as of {_bcb_date})")
else:
    st.sidebar.warning("Could not fetch SELIC from BCB. Using shortest DI contract as proxy.")

selic_input = st.sidebar.number_input(
    "Current SELIC target (% a.a.)",
    min_value=0.0,
    max_value=50.0,
    value=_default_selic,
    step=0.25,
    help=(
        "SELIC target rate set at the last COPOM meeting. "
        "This is the overnight rate currently compounding in the DI index. "
        "Auto-filled from BCB SGS series 432."
    ),
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
# Section 1 — DI curve + COPOM pricing table (2 years horizon)
# ---------------------------------------------------------------------------
st.subheader("DI Curve — COPOM pricing")

# Limit to ~2 years
_2Y_DU = int(252 * 2)
df_2y = (
    df_curve[df_curve["du"] <= _2Y_DU]
    .sort_values("du")
    .reset_index(drop=True)
    .copy()
)
df_2y["rate_252"] = pd.to_numeric(df_2y["rate_252"], errors="coerce")

# Map each COPOM meeting to the DI contract whose period (prev_du, du] contains it.
# That contract's rate already "prices in" the meeting.
def _build_meeting_map(curve: pd.DataFrame, pricing: pd.DataFrame) -> dict:
    """Return {contract_code: row_of_pricing} for meetings within the 2Y horizon.

    The meeting is assigned to the contract that expires immediately BEFORE
    the meeting's effective date — i.e. one row above where the rate change
    actually kicks in.
    """
    mapping = {}
    sorted_c = curve.sort_values("du").reset_index(drop=True)
    for _, mtg in pricing.iterrows():
        eff_du = int(mtg["effective_du"])
        prev_code = None
        prev_du = 0
        for idx, c_row in sorted_c.iterrows():
            curr_du = int(c_row["du"])
            if prev_du < eff_du <= curr_du:
                # assign to the contract just BEFORE this period
                if prev_code is not None:
                    mapping[prev_code] = mtg
                break
            prev_code = c_row["contract_code"]
            prev_du = curr_du
    return mapping

meeting_map = _build_meeting_map(df_2y, df_pricing) if not df_pricing.empty else {}

# Build display rows
rows = []
for _, c_row in df_2y.iterrows():
    code  = str(c_row["contract_code"])
    mtg   = meeting_map.get(code)
    rows.append({
        "Contract":           code[3:] if code.startswith("DI1") else code,
        "Maturity":           str(c_row["expiry_date"]),
        "Rate (%)":           round(float(c_row["rate_252"]) * 100, 4),
        "│":                  "",   # visual separator column
        "COPOM Effective":    str(mtg["effective_date"]) if mtg is not None else "",
        "Priced (bps)":       float(mtg["change_bps"])  if mtg is not None else None,
        "CDI post-dec. (%)":  float(mtg["rate_after"])  if mtg is not None else None,
    })

tbl = pd.DataFrame(rows)

# --- Styler ---
has_meeting = tbl["COPOM Effective"] != ""

def _style_table(df):
    styles = pd.DataFrame("", index=df.index, columns=df.columns)

    # Highlight rows with a COPOM meeting
    for col in df.columns:
        styles.loc[has_meeting, col] = "background-color: rgba(0,200,255,0.07)"

    # Separator column — thin, muted
    styles["│"] = "color: #444444; width: 8px"

    # Priced bps colour
    for i, val in df["Priced (bps)"].items():
        if pd.notna(val) and val != 0:
            color = "#51CF66" if val < 0 else "#FF6B6B"
            styles.loc[i, "Priced (bps)"] = (
                styles.loc[i, "Priced (bps)"] + f"; color: {color}; font-weight: bold"
            )

    return styles

fmt = {
    "Rate (%)":          "{:.4f}",
    "Priced (bps)":      "{:+.1f}",
    "CDI post-dec. (%)": "{:.4f}",
}

st.dataframe(
    tbl.style.apply(_style_table, axis=None).format(fmt, na_rep=""),
    use_container_width=True,
    height=min(60 + len(tbl) * 35, 700),
    column_config={
        "│": st.column_config.TextColumn(width="small"),
        "COPOM Effective": st.column_config.TextColumn(width="medium"),
        "Priced (bps)":    st.column_config.NumberColumn(format="%+.1f bps"),
        "CDI post-dec. (%)": st.column_config.NumberColumn(format="%.4f %%"),
    },
)
st.caption(
    "**CDI post-dec.**: implied SELIC rate after the COPOM decision, "
    "calibrated so that the compounded daily rate exactly reproduces the DI contract price."
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
