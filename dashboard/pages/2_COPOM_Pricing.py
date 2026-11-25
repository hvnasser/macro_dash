"""
COPOM Pricing — meeting-by-meeting SELIC path implied by the DI1 curve
"""

import os
import sys
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_pipeline.bcb_meetings import load_meetings
from data_pipeline.copom_pricing import calculate_copom_pricing, hypothesis_curve
from data_pipeline.di_curve import load_available_dates, load_curve_from_db
from data_pipeline.selic_rate import fetch_cdi, fetch_selic_target

st.set_page_config(page_title="COPOM Pricing", layout="wide")

CHART_TEMPLATE = "plotly_dark"
COLOR_MARKET = "#00C8FF"
COLOR_HYP = "#FF9F43"
COLOR_UP = "#FF6B6B"
COLOR_DOWN = "#51CF66"


def _short(code: str) -> str:
    return code[3:] if code.startswith("DI1") else code


@st.cache_data(ttl=600)
def _dates() -> list[str]:
    return load_available_dates()


@st.cache_data(ttl=600)
def _curve(d: str) -> pd.DataFrame:
    return load_curve_from_db(date.fromisoformat(d))


@st.cache_data(ttl=600)
def _meetings() -> pd.DataFrame:
    return load_meetings()


@st.cache_data(ttl=3600)
def _sgs(kind: str, d: str):
    fn = fetch_cdi if kind == "cdi" else fetch_selic_target
    try:
        return fn(date.fromisoformat(d))
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("COPOM Pricing")

available = _dates()
if not available:
    st.error("No DI curve data. Download it in the Update Data page.")
    st.stop()

curve_date = st.sidebar.selectbox("DI curve date", available, index=0)
ref_date = date.fromisoformat(curve_date)
horizon_y = st.sidebar.slider("Horizon (years)", 0.5, 3.0, 2.0, 0.5)
max_du = int(252 * horizon_y)

df_curve = _curve(curve_date)
if df_curve.empty:
    st.error(f"No curve data for {curve_date}.")
    st.stop()

df_meetings = _meetings()
if df_meetings.empty:
    st.error("No COPOM calendar in `bcb_meetings`. Populate it in the Update Data page.")
    st.stop()

st.sidebar.divider()
st.sidebar.subheader(f"Overnight rate on {curve_date}")

cdi_bcb, cdi_obs = _sgs("cdi", curve_date)
selic_bcb, selic_obs = _sgs("selic", curve_date)

first_mtg_du = None
_upcoming = df_meetings[df_meetings["effective_date"] > curve_date]
if not _upcoming.empty:
    from bizdays import Calendar
    first_mtg_du = Calendar.load("ANBIMA").bizdays(
        ref_date, date.fromisoformat(_upcoming["effective_date"].iloc[0])
    )
_c = df_curve.assign(du=pd.to_numeric(df_curve["du"]), rate_252=pd.to_numeric(df_curve["rate_252"]))
_pre = _c[_c["du"] <= first_mtg_du] if first_mtg_du else _c.iloc[0:0]
cdi_di = float(_pre.sort_values("du").iloc[0]["rate_252"]) if not _pre.empty else None

sources = []
if cdi_bcb is not None:
    sources.append(f"BCB CDI — {cdi_bcb * 100:.2f}%")
if cdi_di is not None:
    sources.append(f"First DI before 1st meeting — {cdi_di * 100:.3f}%")
sources.append("Manual")

src = st.sidebar.radio(
    "CDI compounding today",
    sources,
    help=(
        "The DI index accrues the daily CDI, which runs ~10 bps below the SELIC target. "
        "BCB CDI = SGS series 4389 as of the curve date. "
        "First DI = the shortest contract expiring before the next meeting (market-implied CDI)."
    ),
)
if src.startswith("BCB"):
    cdi0 = cdi_bcb
elif src.startswith("First DI"):
    cdi0 = cdi_di
else:
    default = cdi_bcb or cdi_di or (selic_bcb - 0.001 if selic_bcb is not None else None)
    manual = st.sidebar.number_input(
        "CDI (% a.a.)", 0.0, 50.0,
        value=round(default * 100, 2) if default is not None else None,
        step=0.01, placeholder="e.g. 14.90",
    )
    if manual is None:
        st.warning("BCB is unreachable: enter today's CDI in the sidebar to price the meetings.")
        st.stop()
    cdi0 = manual / 100

if selic_bcb is not None:
    spread = selic_bcb - cdi0
    st.sidebar.caption(
        f"SELIC target on {selic_obs}: **{selic_bcb * 100:.2f}%** · "
        f"spread vs CDI used: **{spread * 1e4:.0f} bps**"
    )
else:
    spread = 0.0
    st.sidebar.warning(f"Could not fetch SELIC target from BCB ({selic_obs}). Implied SELIC = CDI.")
if cdi_bcb is None:
    st.sidebar.warning(f"Could not fetch CDI from BCB ({cdi_obs}).")

# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
res = calculate_copom_pricing(df_curve, df_meetings, ref_date, cdi0, max_du=max_du)
mt, ct = res.meetings, res.contracts

st.title("COPOM Pricing")
st.caption(
    f"DI curve **{curve_date}** · CDI **{cdi0 * 100:.3f}%** · "
    f"horizon **{horizon_y:g}y** · meetings priced **{len(mt)}**"
)

if mt.empty:
    st.warning(
        "No COPOM meeting falls inside the curve horizon. "
        "Check that `bcb_meetings` has dates after the curve date."
    )
    st.stop()

mt = mt.assign(selic_after=mt["cdi_after"] + spread)

# Headline metrics
c1, c2, c3, c4 = st.columns(4)
nxt = mt.iloc[0]
c1.metric("Next meeting", nxt["decision_date"], f"{nxt['change_bps']:+.1f} bps", delta_color="off")
c2.metric("Implied SELIC after next", f"{nxt['selic_after'] * 100:.2f}%")
within_12m = mt[mt["effective_du"] <= 252]
c3.metric("Priced next 12M", f"{within_12m['change_bps'].sum():+.0f} bps" if not within_12m.empty else "—")
c4.metric(f"Priced to {mt['decision_date'].iloc[-1]}", f"{mt['cumulative_bps'].iloc[-1]:+.0f} bps")

# ---------------------------------------------------------------------------
# 1 — Per-meeting table
# ---------------------------------------------------------------------------
st.subheader("Priced per meeting")

tbl = pd.DataFrame({
    "Decision": mt["decision_date"],
    "Effective": mt["effective_date"],
    "DU": mt["effective_du"],
    "CDI before (%)": mt["cdi_before"] * 100,
    "CDI after (%)": mt["cdi_after"] * 100,
    "Implied SELIC (%)": mt["selic_after"] * 100,
    "Δ (bps)": mt["change_bps"],
    "Cumulative (bps)": mt["cumulative_bps"],
    "Probabilities (25 bps grid)": mt["probabilities"],
    "Fitted on": mt["anchor"] + mt["shared"].map({True: "  (shared)", False: ""}),
})


def _sign_color(v):
    if pd.isna(v) or abs(v) < 0.05:
        return ""
    return f"color: {COLOR_UP if v > 0 else COLOR_DOWN}; font-weight: bold"


st.dataframe(
    tbl.style.format({
        "CDI before (%)": "{:.3f}", "CDI after (%)": "{:.3f}", "Implied SELIC (%)": "{:.2f}",
        "Δ (bps)": "{:+.1f}", "Cumulative (bps)": "{:+.1f}",
    }).map(_sign_color, subset=["Δ (bps)", "Cumulative (bps)"]),
    hide_index=True,
    width="stretch",
)
st.caption(
    "Each meeting is fitted to the DI contracts expiring between it and the next meeting, "
    "given the path already solved for earlier meetings. "
    "**(shared)**: no contract expires between those meetings, so the move is split equally. "
    "Implied SELIC = CDI + (SELIC target − CDI) spread on the curve date."
)

# ---------------------------------------------------------------------------
# 2 — Charts
# ---------------------------------------------------------------------------
g1, g2 = st.columns(2)

with g1:
    fig = go.Figure(go.Bar(
        x=mt["decision_date"],
        y=mt["change_bps"],
        marker_color=[COLOR_UP if v > 0 else COLOR_DOWN for v in mt["change_bps"]],
        text=[f"{v:+.0f}" for v in mt["change_bps"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>%{y:+.1f} bps<extra></extra>",
    ))
    fig.update_layout(
        title="Priced change per meeting", xaxis_title="Decision date", yaxis_title="bps",
        template=CHART_TEMPLATE, height=360, margin=dict(t=50, b=40), xaxis_type="category",
    )
    st.plotly_chart(fig, width="stretch")

with g2:
    selic0 = cdi0 + spread
    fig = go.Figure(go.Scatter(
        x=[curve_date] + mt["effective_date"].tolist(),
        y=[selic0 * 100] + (mt["selic_after"] * 100).tolist(),
        mode="lines+markers",
        line=dict(color=COLOR_MARKET, width=2.5, shape="hv"),
        marker=dict(size=7),
        hovertemplate="<b>%{x}</b><br>SELIC %{y:.2f}%<extra></extra>",
    ))
    fig.update_layout(
        title="Market-implied SELIC path", xaxis_title="Effective date", yaxis_title="% a.a.",
        template=CHART_TEMPLATE, height=360, margin=dict(t=50, b=40),
    )
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# 3 — Curve & meetings timeline
# ---------------------------------------------------------------------------
st.divider()
st.subheader("DI curve and meetings timeline")

rows = [{
    "_du": int(r.du), "_ord": 1, "Item": _short(r.contract_code), "Date": r.expiry_date,
    "DU": int(r.du), "DI market (%)": r.rate_252 * 100, "DI model (%)": r.model_rate * 100,
    "Fit error (bps)": r.error_bps, "COPOM Δ (bps)": None, "CDI after (%)": None,
} for r in ct.itertuples()]
rows += [{
    "_du": int(r.effective_du), "_ord": 0, "Item": f"COPOM {r.decision_date}", "Date": r.effective_date,
    "DU": int(r.effective_du), "DI market (%)": None, "DI model (%)": None,
    "Fit error (bps)": None, "COPOM Δ (bps)": r.change_bps, "CDI after (%)": r.cdi_after * 100,
} for r in mt.itertuples()]
# A meeting effective on a contract's expiry day sits after that contract
timeline = pd.DataFrame(rows).sort_values(["_du", "_ord"], ascending=[True, False])
timeline = timeline.drop(columns=["_du", "_ord"]).reset_index(drop=True)
is_mtg = timeline["Item"].str.startswith("COPOM")


def _row_style(df):
    s = pd.DataFrame("", index=df.index, columns=df.columns)
    s.loc[is_mtg, :] = "background-color: rgba(0,200,255,0.10)"
    for i, v in df["COPOM Δ (bps)"].items():
        if pd.notna(v):
            s.loc[i, "COPOM Δ (bps)"] += "; " + _sign_color(v)
    return s


st.dataframe(
    timeline.style.apply(_row_style, axis=None).format({
        "DI market (%)": "{:.3f}", "DI model (%)": "{:.3f}", "Fit error (bps)": "{:+.2f}",
        "COPOM Δ (bps)": "{:+.1f}", "CDI after (%)": "{:.3f}",
    }, na_rep=""),
    hide_index=True,
    width="stretch",
    height=min(40 + 35 * len(timeline), 720),
)
st.caption(
    "**DI model**: contract rate recomputed by compounding the implied CDI path. "
    "**Fit error** = model − market; large errors flag noisy contracts or a wrong calendar."
)

# ---------------------------------------------------------------------------
# 4 — Hypothesis
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Your scenario vs. market")

hyp_in = pd.DataFrame({
    "Decision": mt["decision_date"],
    "Market Δ (bps)": mt["change_bps"].round(1),
    "Your Δ (bps)": (mt["change_bps"] / 25).round() * 25,
})
edited = st.data_editor(
    hyp_in,
    hide_index=True,
    num_rows="fixed",
    width="stretch",
    column_config={
        "Decision": st.column_config.TextColumn(disabled=True),
        "Market Δ (bps)": st.column_config.NumberColumn(disabled=True, format="%+.1f"),
        "Your Δ (bps)": st.column_config.NumberColumn(step=25.0, format="%+.0f"),
    },
)

hyp = hypothesis_curve(ct, mt["effective_du"].tolist(), cdi0, edited["Your Δ (bps)"].fillna(0).tolist())

h1, h2 = st.columns(2)
with h1:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hyp["contract_code"].map(_short), y=hyp["rate_252"] * 100, name=f"Market {curve_date}",
        mode="lines+markers", line=dict(color=COLOR_MARKET, width=2.5),
    ))
    fig.add_trace(go.Scatter(
        x=hyp["contract_code"].map(_short), y=hyp["hyp_rate"] * 100, name="Your scenario",
        mode="lines+markers", line=dict(color=COLOR_HYP, width=2.5, dash="dash"),
    ))
    fig.update_layout(
        title="DI curve — market vs. scenario", xaxis_title="Contract", yaxis_title="% a.a.",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template=CHART_TEMPLATE, height=380, margin=dict(t=60, b=40),
    )
    st.plotly_chart(fig, width="stretch")

with h2:
    fig = go.Figure(go.Bar(
        x=hyp["contract_code"].map(_short), y=hyp["diff_bps"],
        marker_color=[COLOR_UP if v > 0 else COLOR_DOWN for v in hyp["diff_bps"]],
        hovertemplate="<b>%{x}</b><br>%{y:+.1f} bps<extra></extra>",
    ))
    fig.update_layout(
        title="Scenario − market (bps)", xaxis_title="Contract", yaxis_title="bps",
        template=CHART_TEMPLATE, height=380, margin=dict(t=60, b=40),
    )
    st.plotly_chart(fig, width="stretch")

st.caption(
    "Positive = your scenario implies a higher rate than the market for that contract "
    "(you would pay — be 'tomado' in — DI there)."
)
