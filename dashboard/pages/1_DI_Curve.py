"""
DI Curve Dashboard — Brazilian Interbank Yield Curve
=====================================================
"""

import os
import sys
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_pipeline.di_curve import load_available_dates, load_curve_from_db, run_pipeline

st.set_page_config(page_title="DI Curve", layout="wide")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Brazilian DI contract month letter → calendar month
_MONTH_LETTERS = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}

def _expiry_month(contract_code: str) -> int | None:
    """Return the calendar month from a DI1 contract code (e.g. 'DI1F26' → 1)."""
    try:
        return _MONTH_LETTERS.get(contract_code[3])
    except (IndexError, TypeError):
        return None


def _add_pct(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "rate_252" in df.columns:
        df["rate_pct"] = pd.to_numeric(df["rate_252"], errors="coerce") * 100
    if "forward_rate" in df.columns:
        df["fwd_pct"] = pd.to_numeric(df["forward_rate"], errors="coerce") * 100
    return df


def _load(date_str: str | None) -> pd.DataFrame:
    if not date_str:
        return pd.DataFrame()
    df = load_curve_from_db(date.fromisoformat(date_str))
    return _add_pct(df) if not df.empty else df


def _find_date_before(available: list[str], ref: str, bdays: int) -> str | None:
    """Return the closest available date that is >= bdays before ref."""
    ref_dt = date.fromisoformat(ref)
    # approximate: use calendar days (5 bdays ≈ 7 cal days, 1 bday ≈ 1-3 cal days)
    approx_target = (ref_dt - timedelta(days=bdays * 1.6)).isoformat()
    candidates = [d for d in available if d < ref and d <= approx_target]
    return candidates[0] if candidates else None


CHART_TEMPLATE = "plotly_dark"
COLOR_MAIN = "#00C8FF"
COLOR_COMP = "#FF9F43"
DASH_COMP = "dash"


def _curve_trace(df: pd.DataFrame, y_col: str, label: str, color: str,
                 width: float = 2.5, dash: str = "solid", size: int = 6) -> go.Scatter:
    return go.Scatter(
        x=df["contract_code"].map(_short_code),
        y=df[y_col],
        mode="lines+markers",
        name=label,
        line=dict(color=color, width=width, dash=dash),
        marker=dict(size=size, color=color),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "DU: %{customdata[0]}<br>"
            "Rate: %{y:.3f}%<extra></extra>"
        ),
        customdata=df[["du"]].values,
    )


def _short_code(contract_code: str) -> str:
    """Strip 'DI1' prefix: 'DI1J26' → 'J26'."""
    return contract_code[3:] if contract_code.startswith("DI1") else contract_code


def _filtered_trace(df: pd.DataFrame, y_col: str, label: str, color: str,
                    width: float = 2.5, dash: str = "solid", size: int = 7) -> go.Scatter:
    """Line+marker trace for filtered charts (3/4/5) using short contract code on x-axis."""
    x = df["contract_code"].map(_short_code)
    return go.Scatter(
        x=x,
        y=df[y_col],
        mode="lines+markers",
        name=label,
        line=dict(color=color, width=width, dash=dash),
        marker=dict(size=size, color=color),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "DU: %{customdata[0]}<br>"
            "Rate: %{y:.3f}%<extra></extra>"
        ),
        customdata=df[["du"]].values,
    )


def _layout(title: str, x_title: str, y_title: str, height: int = 400) -> dict:
    return dict(
        title=title,
        xaxis_title=x_title,
        yaxis_title=y_title,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        template=CHART_TEMPLATE,
        height=height,
        margin=dict(t=60, b=40),
    )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("DI Curve")

available_dates = load_available_dates()
if not available_dates:
    st.title("Curva DI — Brazilian Interbank Yield Curve")
    st.info("No data yet. Use the button below to fetch from B3.")
    if st.button("Run Pipeline"):
        with st.spinner("Fetching…"):
            run_pipeline()
        st.rerun()
    st.stop()

latest_date = available_dates[0]

compare_date = st.sidebar.selectbox(
    "Compare with date",
    options=["(none)"] + [d for d in available_dates if d != latest_date],
    index=0,
)
compare_date = None if compare_date == "(none)" else compare_date

st.sidebar.divider()
with st.sidebar.expander("Update data"):
    if st.button("Run pipeline (latest biz day)"):
        with st.spinner("Fetching from B3…"):
            df_new = run_pipeline()
        if df_new.empty:
            st.warning("No data returned.")
        else:
            st.success(f"{len(df_new)} vertices saved.")
            st.rerun()

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
df_main = _load(latest_date)
df_comp = _load(compare_date) if compare_date else pd.DataFrame()

# For the delta table
prev_1d_date = available_dates[1] if len(available_dates) > 1 else None
prev_1w_date = _find_date_before(available_dates, latest_date, bdays=5)

df_1d = _load(prev_1d_date)
df_1w = _load(prev_1w_date)

if df_main.empty:
    st.error(f"No curve data for {latest_date}.")
    st.stop()

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("Curva DI — Brazilian Interbank Yield Curve")
st.caption(
    f"Latest data: **{latest_date}**"
    + (f"   |   Comparing with: **{compare_date}**" if compare_date else "")
)

# Quick metrics
m1, m2, m3, m4, m5 = st.columns(5)
def _metric(col, label, df, du_max=None, du_min=None, idx=None):
    sub = df
    if du_max:
        sub = df[df["du"] <= du_max]
    if du_min:
        sub = df[df["du"] >= du_min]
    row = sub.iloc[idx if idx is not None else -1] if not sub.empty else df.iloc[0]
    col.metric(label, f"{row['rate_pct']:.2f}%", help=f"{row['contract_code']} · DU {row['du']}")

_metric(m1, "Short (≤3M)",  df_main, du_max=63)
_metric(m2, "6M",           df_main, du_min=100, du_max=150)
_metric(m3, "1Y",           df_main, du_min=230, du_max=280)
_metric(m4, "2Y",           df_main, du_min=480, du_max=540)
_metric(m5, "Long end",     df_main, idx=-1)

st.divider()

# ---------------------------------------------------------------------------
# Chart 1 & 2 — full spot and full forward curves
# ---------------------------------------------------------------------------
c1, c2 = st.columns(2)

with c1:
    fig1 = go.Figure()
    fig1.add_trace(_curve_trace(df_main, "rate_pct", latest_date, COLOR_MAIN))
    if not df_comp.empty:
        fig1.add_trace(_curve_trace(df_comp, "rate_pct", compare_date, COLOR_COMP,
                                    width=1.5, dash=DASH_COMP, size=5))
    fig1.update_layout(**_layout(
        f"① Spot Zero-Coupon Curve — {latest_date}",
        "Contract", "Rate (% a.a.)", height=420,
    ))
    st.plotly_chart(fig1, use_container_width=True)

with c2:
    fig2 = go.Figure()
    fig2.add_trace(_curve_trace(df_main, "fwd_pct", latest_date, COLOR_MAIN))
    if not df_comp.empty and "fwd_pct" in df_comp.columns:
        fig2.add_trace(_curve_trace(df_comp, "fwd_pct", compare_date, COLOR_COMP,
                                    width=1.5, dash=DASH_COMP, size=5))
    fig2.update_layout(**_layout(
        f"② Full Forward Rate Curve — {latest_date}",
        "Contract", "Forward Rate (% a.a.)", height=420,
    ))
    st.plotly_chart(fig2, use_container_width=True)

# ---------------------------------------------------------------------------
# Charts 3, 4, 5 — quarterly / semester / annual forward rates
# ---------------------------------------------------------------------------

def _filter_by_months(df: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    df = df.copy()
    df["_month"] = pd.to_datetime(df["expiry_date"]).dt.month
    return df[df["_month"].isin(months)].drop(columns=["_month"]).reset_index(drop=True)

# Jan=1, Apr=4, Jul=7, Oct=10
quarterly_months  = [1, 4, 7, 10]
semester_months   = [1, 7]
annual_months     = [1]

df_q  = _filter_by_months(df_main, quarterly_months)
df_s  = _filter_by_months(df_main, semester_months)
df_a  = _filter_by_months(df_main, annual_months)

df_q_c = _filter_by_months(df_comp, quarterly_months) if not df_comp.empty else pd.DataFrame()
df_s_c = _filter_by_months(df_comp, semester_months)  if not df_comp.empty else pd.DataFrame()
df_a_c = _filter_by_months(df_comp, annual_months)    if not df_comp.empty else pd.DataFrame()

c3, c4, c5 = st.columns(3)

with c3:
    fig3 = go.Figure()
    if not df_q.empty:
        fig3.add_trace(_filtered_trace(df_q, "fwd_pct", latest_date, COLOR_MAIN))
    if not df_q_c.empty:
        fig3.add_trace(_filtered_trace(df_q_c, "fwd_pct", compare_date, COLOR_COMP,
                                       width=1.5, dash=DASH_COMP, size=5))
    fig3.update_layout(**_layout(
        "③ Quarterly Fwd (Jan/Apr/Jul/Oct)", "Contract", "Forward Rate (% a.a.)", height=380,
    ))
    st.plotly_chart(fig3, use_container_width=True)

with c4:
    fig4 = go.Figure()
    if not df_s.empty:
        fig4.add_trace(_filtered_trace(df_s, "fwd_pct", latest_date, COLOR_MAIN))
    if not df_s_c.empty:
        fig4.add_trace(_filtered_trace(df_s_c, "fwd_pct", compare_date, COLOR_COMP,
                                       width=1.5, dash=DASH_COMP, size=5))
    fig4.update_layout(**_layout(
        "④ Semester Fwd (Jan/Jul)", "Contract", "Forward Rate (% a.a.)", height=380,
    ))
    st.plotly_chart(fig4, use_container_width=True)

with c5:
    fig5 = go.Figure()
    if not df_a.empty:
        fig5.add_trace(_filtered_trace(df_a, "fwd_pct", latest_date, COLOR_MAIN))
    if not df_a_c.empty:
        fig5.add_trace(_filtered_trace(df_a_c, "fwd_pct", compare_date, COLOR_COMP,
                                       width=1.5, dash=DASH_COMP, size=5))
    fig5.update_layout(**_layout(
        "⑤ Annual Fwd (January contracts)", "Contract", "Forward Rate (% a.a.)", height=380,
    ))
    st.plotly_chart(fig5, use_container_width=True)

# ---------------------------------------------------------------------------
# Delta table
# ---------------------------------------------------------------------------
st.divider()
st.subheader(f"Rates & Deltas — {latest_date}")

def _merge_delta(base: pd.DataFrame, other: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Left-join base with other on contract_code, add a bps delta column."""
    if other.empty:
        base[f"Δ {suffix} (bps)"] = None
        return base
    merged = base.merge(
        other[["contract_code", "rate_pct"]].rename(columns={"rate_pct": f"_r_{suffix}"}),
        on="contract_code",
        how="left",
    )
    merged[f"Δ {suffix} (bps)"] = (
        (merged["rate_pct"] - merged[f"_r_{suffix}"]) * 100
    ).round(1)
    return merged.drop(columns=[f"_r_{suffix}"])


tbl = df_main[["contract_code", "expiry_date", "du", "rate_pct", "fwd_pct"]].copy()
tbl = tbl.rename(columns={
    "contract_code": "Contract",
    "expiry_date":   "Expiry",
    "du":            "DU",
    "rate_pct":      "Spot Rate (%)",
    "fwd_pct":       "Fwd Rate (%)",
})

# Add delta columns
tbl_work = df_main[["contract_code", "rate_pct"]].copy()
tbl_work = _merge_delta(tbl_work, df_1d, f"1D vs {prev_1d_date or '—'}")
tbl_work = _merge_delta(tbl_work, df_1w, f"1W vs {prev_1w_date or '—'}")
if compare_date:
    tbl_work = _merge_delta(tbl_work, df_comp, f"vs {compare_date}")

delta_cols = [c for c in tbl_work.columns if "Δ" in c]
tbl = tbl.merge(tbl_work[["contract_code"] + delta_cols], left_on="Contract", right_on="contract_code")
tbl = tbl.drop(columns=["contract_code"])

# Colour positive (rates went up) red, negative green — market convention
def _colour_delta(val):
    if pd.isna(val):
        return ""
    return "color: #FF6B6B" if val > 0 else ("color: #51CF66" if val < 0 else "")

fmt = {"Spot Rate (%)": "{:.3f}", "Fwd Rate (%)": "{:.3f}"}
for col in delta_cols:
    fmt[col] = "{:+.1f}"

styled = (
    tbl.style
    .format(fmt, na_rep="—")
    .applymap(_colour_delta, subset=delta_cols)
)

st.dataframe(styled, use_container_width=True, height=600)

st.caption(
    "Deltas in basis points (bps). "
    "Red = rates moved up · Green = rates moved down. "
    f"1D reference: {prev_1d_date or 'N/A'} · "
    f"1W reference: {prev_1w_date or 'N/A'}."
)
