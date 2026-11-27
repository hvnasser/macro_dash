"""
Brazil Credit — BCB monthly credit statistics
"""

import os
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_pipeline import credit as cr

st.set_page_config(page_title="Brazil Credit", layout="wide")

TEMPLATE = "plotly_dark"
C1, C2, C3 = "#00C8FF", "#FF9F43", "#A0A0A0"
C4, C5 = "#51CF66", "#CC5DE8"


@st.cache_data(ttl=3600)
def _load() -> pd.DataFrame:
    return cr.load_credit_data()


w = _load()
if w.empty:
    st.title("Brazil Credit")
    st.info("No credit data yet. Go to **Update Data → ④ Credit data** and click the download button.")
    st.stop()

last_month = w["stock_total"].dropna().index.max()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("Brazil Credit")
start_year = st.sidebar.slider("Charts start in", 2007, last_month.year - 1, 2012)
start = pd.Timestamp(start_year, 1, 1)
st.sidebar.caption(
    "Source: BCB (SGS). Real values deflated by IPCA, in prices of the latest month. "
    "Seasonal adjustment: STL (statsmodels), multiplicative."
)


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------
def chart(title: str, subtitle: str, lines: dict, y2: dict | None = None, zero: bool = False,
          since: pd.Timestamp | None = None, y_title: str = "", y2_title: str = "",
          total: str | None = None):
    """`total`: name of the series drawn as a thick gray reference line."""
    since = since or start
    fig = go.Figure()
    colors = iter([C1, C2, C4, C5, C3])
    for name, s in lines.items():
        s = s.dropna()
        s = s[s.index >= since]
        line = dict(color=C3, width=3.5) if name == total else dict(color=next(colors), width=2)
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name, mode="lines", line=line))
    for name, s in (y2 or {}).items():
        s = s.dropna()
        s = s[s.index >= since]
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=f"{name} (rhs)", mode="lines", yaxis="y2",
                                 line=dict(color=next(colors), width=2, dash="dot")))
    if zero:
        fig.add_hline(y=0, line_color="#666", line_width=1)
    layout = dict(
        title=dict(text=f"{title}<br><sup>{subtitle}</sup>"),
        template=TEMPLATE, height=380, margin=dict(t=60, b=60, l=10, r=10),
        hovermode="x unified", yaxis_title=y_title,
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0),
    )
    if y2:
        layout["yaxis2"] = dict(overlaying="y", side="right", showgrid=False, title=y2_title)
    fig.update_layout(**layout)
    st.plotly_chart(fig, width="stretch")


def seasonal_chart(title: str, s: pd.Series, year: int):
    prof = cr.seasonal_profile(s, year)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=months, y=prof["band_max"], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=months, y=prof["band_min"], fill="tonexty", fillcolor="rgba(160,160,160,0.25)",
                             mode="lines", line=dict(width=0), name=f"{year - 12}–{year - 3} range"))
    styles = {year: dict(color=C1, width=3), year - 1: dict(color=C2, width=2, dash="dash"),
              year - 2: dict(color=C3, width=2, dash="dash")}
    for y, st_ in styles.items():
        if y in prof.columns:
            fig.add_trace(go.Scatter(x=months, y=prof[y], name=str(y), mode="lines+markers" if y == year else "lines",
                                     line=st_))
    fig.update_layout(
        title=dict(text=f"{title}<br><sup>% of credit over 90 days late · shaded = min–max of the 10 years before those shown</sup>"),
        template=TEMPLATE, height=380, margin=dict(t=60, b=60, l=10, r=10), hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0),
    )
    st.plotly_chart(fig, width="stretch")


def _bn(s: pd.Series) -> pd.Series:
    return s / 1e3


# ---------------------------------------------------------------------------
# Derived series
# ---------------------------------------------------------------------------
bd = cr.business_days(w.index)
# Headline = seasonally adjusted only (same convention as J.P. Morgan's "CPI deflated, sa")
new_sa = {k: cr.seasonal_adjust(cr.real(w, f"new_{k}")) for k in ("total", "pj", "pf")}
new_td_sa = cr.seasonal_adjust_td(cr.real(w, "new_total"))
gdp = cr.gdp_12m(w)
banks_total = w["stock_public_banks"] + w["stock_private_banks"]
ryoy = {c: cr.yoy(cr.real(w, c)) for c in [
    "stock_total", "stock_free", "stock_earmarked", "stock_free_pj", "stock_free_pf",
    "stock_earmarked_pj", "stock_earmarked_pf"]}
bndes_yoy = cr.yoy((w["stock_bndes_pj"] + w["stock_bndes_pf"]) / cr.price_index(w))

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Brazil Credit")
st.caption(f"Latest month: **{last_month:%b %Y}** · BCB monthly credit statistics")

m = w.loc[last_month]
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Credit / GDP", f"{m['stock_gdp']:.1f}%")
k2.metric("Outstanding credit, real y/y", f"{ryoy['stock_total'].loc[last_month]:+.1f}%")
k3.metric("New loans, real SA m/m", f"{(new_sa['total'].dropna().pct_change().iloc[-1]) * 100:+.1f}%")
k4.metric("Avg. lending rate", f"{m['rate_total']:.1f}%")
k5.metric("NPL (90+ days)", f"{m['npl_total']:.2f}%")

# ---------------------------------------------------------------------------
# 1. Outstanding credit
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Outstanding credit")
a, b, c = st.columns(3)
with a:
    chart("1 · Outstanding credit", "% of GDP", {"Credit / GDP": w["stock_gdp"]}, since=min(start, pd.Timestamp(2007, 1, 1)))
with b:
    chart("9 · Public vs private banks", "% of GDP",
          {"Private banks": w["stock_private_banks"] / gdp * 100, "Public banks": w["stock_public_banks"] / gdp * 100})
with c:
    chart("10 · Public vs private banks", "% of total credit",
          {"Private banks": w["stock_private_banks"] / banks_total * 100,
           "Public banks": w["stock_public_banks"] / banks_total * 100})

# ---------------------------------------------------------------------------
# 2. New loans
# ---------------------------------------------------------------------------
st.divider()
st.subheader("New loans (concessões)")
a, b = st.columns(2)
with a:
    chart("2 · New loans", "R$ billion, IPCA-deflated, seasonally adjusted", {"Total": _bn(new_sa["total"])})
with b:
    chart("3 · New loans to corporates and households", "R$ billion, IPCA-deflated, seasonally adjusted",
          {"Corporates": _bn(new_sa["pj"]), "Households": _bn(new_sa["pf"])})
a, b = st.columns(2)
with a:
    chart("4 · New loans growth", "% 3m/3m saar, IPCA-deflated",
          {"Original": cr.growth_3m_saar(new_sa["total"]), "Trading-days adjusted": cr.growth_3m_saar(new_td_sa)},
          zero=True, since=max(start, pd.Timestamp(2019, 1, 1)))
with b:
    chart("18 · New loans — private payroll-deductible", "R$ billion per business day, IPCA-deflated",
          {"Private payroll": _bn(cr.real(w, "new_payroll_private") / bd)},
          since=max(start, pd.Timestamp(2023, 1, 1)))

# ---------------------------------------------------------------------------
# 3. Earmarked vs non-earmarked
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Earmarked vs non-earmarked credit")
st.caption("Earmarked (direcionado): credit with specific purposes, often from public banks at subsidised rates.")
a, b = st.columns(2)
with a:
    chart("5 · Earmarked and non-earmarked credit", "% y/y, IPCA-deflated",
          {"Non-earmarked": ryoy["stock_free"], "Earmarked": ryoy["stock_earmarked"]}, zero=True)
with b:
    chart("8 · Outstanding credit growth", "% y/y, IPCA-deflated",
          {"BNDES": bndes_yoy, "Loans (total)": ryoy["stock_total"]}, zero=True)
a, b = st.columns(2)
with a:
    chart("6 · Non-earmarked credit", "% y/y, IPCA-deflated",
          {"Corporates": ryoy["stock_free_pj"], "Households": ryoy["stock_free_pf"]}, zero=True)
with b:
    chart("7 · Earmarked credit", "% y/y, IPCA-deflated",
          {"Corporates": ryoy["stock_earmarked_pj"], "Households": ryoy["stock_earmarked_pf"]}, zero=True)

# ---------------------------------------------------------------------------
# 4. Rates
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Interest rates")
a, b = st.columns(2)
with a:
    chart("11 · Lending, funding and policy rates", "% p.a. · funding = lending rate − spread",
          {"Lending rate": w["rate_total"]},
          y2={"Funding rate": w["rate_total"] - w["spread_total"], "SELIC": w["selic_month"]},
          y_title="lending", y2_title="funding / SELIC")
with b:
    chart("12 · Lending rates to corporates and households", "% p.a.",
          {"Corporates": w["rate_pj"], "Households": w["rate_pf"]})

# ---------------------------------------------------------------------------
# 5. Delinquency
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Delinquency")
a, b = st.columns(2)
with a:
    chart("13 · NPL and arrears", "% · NPL = over 90 days late; arrears = 15–90 days late",
          {"NPL": w["npl_total"], "Arrears": w["arrears_total"]})
with b:
    chart("14 · NPL by borrower", "% over 90 days late",
          {"Corporates": w["npl_pj"], "Households": w["npl_pf"]})

years = sorted({d.year for d in w["npl_total"].dropna().index}, reverse=True)
year = st.selectbox("Year to highlight (charts 15–16)", years, index=0)
a, b = st.columns(2)
with a:
    seasonal_chart("15 · Delinquency — corporates", w["npl_pj"], year)
with b:
    seasonal_chart("16 · Delinquency — households", w["npl_pf"], year)

st.markdown("##### NPL by product — non-earmarked credit")
st.caption("% of credit over 90 days late, seasonally adjusted (STL). Gray = segment total.")


def _npl_sa(cols: dict) -> dict:
    return {label: cr.seasonal_adjust(w[col]) for label, col in cols.items()}


a, b = st.columns(2)
with a:
    chart("19 · Households — by product", "NPL, %, SA", _npl_sa({
        "Vehicles": "npl_free_pf_vehicles",
        "Credit card": "npl_free_pf_card",
        "Payroll-deductible": "npl_free_pf_payroll",
        "Personal (non-payroll)": "npl_free_pf_personal",
        "Households total": "npl_free_pf",
    }), total="Households total")
with b:
    chart("20 · Households — payroll-deductible by borrower", "NPL, %, SA", _npl_sa({
        "INSS retirees": "npl_free_pf_payroll_inss",
        "Public servants": "npl_free_pf_payroll_public",
        "Private-sector workers": "npl_free_pf_payroll_private",
        "Payroll total": "npl_free_pf_payroll",
    }), total="Payroll total")
a, b = st.columns(2)
with a:
    chart("21 · Companies — by product", "NPL, %, SA", _npl_sa({
        "Vehicles": "npl_free_pj_vehicles",
        "Working capital": "npl_free_pj_wc",
        "Credit card": "npl_free_pj_card",
        "Receivables discounting": "npl_free_pj_receivables",
        "Companies total": "npl_free_pj",
    }), total="Companies total")
with b:
    chart("22 · Companies — working capital by term", "NPL, %, SA", _npl_sa({
        "Over 365 days": "npl_free_pj_wc_long",
        "Up to 365 days": "npl_free_pj_wc_short",
        "Revolving": "npl_free_pj_wc_revolving",
        "Working capital total": "npl_free_pj_wc",
    }), total="Working capital total")

# ---------------------------------------------------------------------------
# 6. Households
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Household debt")
chart("17 · Indebtedness and income commitment",
      "Indebtedness: % of last-12m disposable income · commitment: debt service, % of income, SA",
      {"Indebtedness": w["hh_indebtedness"]}, y2={"Income commitment": w["hh_income_commitment"]},
      since=min(start, pd.Timestamp(2005, 1, 1)), y_title="indebtedness", y2_title="commitment")

st.caption(
    "Chart 11: J.P. Morgan also plots the 1-year swap rate; BCB discontinued that series in 2019, "
    "so the monthly SELIC is shown instead."
)
