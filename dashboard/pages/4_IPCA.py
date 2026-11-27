"""
IPCA — breakdowns, cores and seasonally adjusted metrics
"""

import os
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from data_pipeline import ipca

st.set_page_config(page_title="IPCA", layout="wide")

PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
GRAY, BLACK, ORANGE = "#9E9E9E", "#111111", "#FF6600"
BG = "#F2F2F2"


def _m(d: pd.Timestamp) -> str:
    return f"{PT[d.month - 1]}-{d:%y}"


@st.cache_data(ttl=3600)
def _load() -> pd.DataFrame:
    return ipca.load_ipca_metrics()


m = _load()
if m.empty:
    st.title("IPCA")
    st.info("No IPCA data yet. Go to **Update Data → ⑤ IPCA** and click the download button.")
    st.stop()

wide = {c: m.pivot(index="ref_date", columns="series_key", values=c)
        for c in ("mom", "mom_saar", "mm3m_saar", "yoy")}
months = wide["mom"]["geral"].dropna().index

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("IPCA")
ref = st.sidebar.selectbox("Reference month", list(reversed(months)), format_func=_m)
start_year = st.sidebar.slider("Charts start in", 2009, ref.year - 1, 2018)
start = pd.Timestamp(start_year, 6, 1)
st.sidebar.caption(
    "Source: BCB (SGS). SA: STL on m/m from 2008. MoM AR = SA m/m annualised; "
    "MM3M SAAR = 3-month average of SA m/m, annualised."
)


def val(metric: str, key: str, month: pd.Timestamp) -> float:
    try:
        return wide[metric].at[month, key]
    except KeyError:
        return np.nan


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("IPCA")
st.caption(f"Reference month: **{_m(ref)}**")
k = st.columns(5)
k[0].metric("IPCA m/m", f"{val('mom', 'geral', ref):.2f}%")
k[1].metric("IPCA 12m", f"{val('yoy', 'geral', ref):.2f}%")
k[2].metric("IPCA MM3M SAAR", f"{val('mm3m_saar', 'geral', ref):.1f}%")
k[3].metric("Média dos núcleos 12m", f"{val('yoy', 'core_avg', ref):.2f}%")
k[4].metric("Difusão", f"{val('mom', 'difusao', ref):.1f}%")

# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
BOLD = {"geral", "alim_bebidas", "habitacao", "artigos_res", "vestuario", "transportes", "saude",
        "desp_pessoais", "educacao", "comunicacao", "administrados", "livres", "servicos",
        "ind_subj", "serv_subj", "core_avg", "core_low_bias", "difusao"}


def styled(df: pd.DataFrame, keys: list[str], fmt: str):
    def row_style(row):
        key = keys[row.name]
        if key == "geral":
            return ["background-color: #00008B; color: white; font-weight: bold"] * len(row)
        return ["font-weight: bold" if key in BOLD else ""] * len(row)
    return df.style.apply(row_style, axis=1).format(fmt, na_rep="–", subset=df.columns[1:])


def mom_table(keys: list[str]):
    cols = [ref - pd.DateOffset(months=12), ref - pd.DateOffset(months=2), ref - pd.DateOffset(months=1), ref]
    rows = [{"": ipca.BY_KEY[key].label, **{f"IPCA {_m(c)}": val("mom", key, c) for c in cols}} for key in keys]
    return styled(pd.DataFrame(rows), keys, "{:.2f}")


def summary_table(metric: str, keys: list[str]):
    years = [ref.year - 3, ref.year - 2, ref.year - 1]
    cols = [ref - pd.DateOffset(months=i) for i in range(4, -1, -1)]
    rows = []
    for key in keys:
        g = m[m["series_key"] == key]
        ann = ipca.annual_yoy(g, years)
        rows.append({"": ipca.BY_KEY[key].label,
                     **{f"{y} YoY": ann[y] for y in years},
                     **{_m(c): val(metric, key, c) for c in cols}})
    return styled(pd.DataFrame(rows), keys, "{:.1f}")


st.divider()
st.subheader("Resumo MoM")
t1 = ["geral", "alim_bebidas", "alim_dom", "habitacao", "artigos_res", "vestuario", "transportes",
      "saude", "desp_pessoais", "educacao", "comunicacao"]
t2 = ["geral", "administrados", "livres", "alim_dom", "industriais", "servicos", "ind_subj", "serv_subj",
      "ex0", "ex1", "ex2", "ex3", "ms", "dp", "p55", "difusao"]
a, b = st.columns(2)
a.dataframe(mom_table(t1), hide_index=True, width="stretch", height=37 * len(t1) + 40)
b.dataframe(mom_table(t2), hide_index=True, width="stretch", height=37 * len(t2) + 40)

SUMMARY = ["geral", "livres", "alim_dom", "servicos", "serv_subj", "industriais", "ind_subj", "administrados",
           "ex0", "ex3", "ms", "dp", "p55", "core_avg", "core_low_bias"]
st.subheader("Resumo MM3M SAAR")
st.dataframe(summary_table("mm3m_saar", SUMMARY), hide_index=True, width="stretch", height=37 * len(SUMMARY) + 40)
st.subheader("Resumo YoY")
st.dataframe(summary_table("yoy", SUMMARY), hide_index=True, width="stretch", height=37 * len(SUMMARY) + 40)
st.caption(
    "Média dos núcleos = average of EX0, EX3, MS, DP and P55 · menor viés = average of MS and DP. "
    "Serviços/Industriais subjacente are BCB's “EX3 Serviços/Industriais”. "
    "Weights are not published by BCB (they come from IBGE)."
)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def _ticks(idx: pd.DatetimeIndex):
    t = [d for d in idx if d.month in (6, 12)]
    return t, [_m(d) for d in t]


def itau_chart(key: str, title: str | None = None, chart_key: str | None = None):
    label = ipca.BY_KEY[key].label
    data = pd.DataFrame({c: wide[c][key] for c in ("mom_saar", "mm3m_saar", "yoy")}).loc[start:ref].dropna(how="all")
    last = data.iloc[-1]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=data.index, y=data["mom_saar"], mode="lines+markers",
                             name=f"MoM AR ({_m(ref)}: {last['mom_saar']:.1f}%)",
                             line=dict(color=GRAY, width=1, dash="dot"), marker=dict(size=4, color=GRAY)))
    fig.add_trace(go.Scatter(x=data.index, y=data["mm3m_saar"], mode="lines+markers",
                             name=f"MM3M SAAR ({_m(ref)}: {last['mm3m_saar']:.1f}%)",
                             line=dict(color=BLACK, width=2, dash="dash"), marker=dict(size=5, color=BLACK)))
    fig.add_trace(go.Scatter(x=data.index, y=data["yoy"], mode="lines",
                             name=f"YoY ({_m(ref)}: {last['yoy']:.1f}%)", line=dict(color=ORANGE, width=3)))
    tv, tt = _ticks(data.index)
    fig.update_layout(
        title=dict(text=title or f"IPCA - {label}", font=dict(color=BLACK)),
        template="plotly_white", paper_bgcolor="white", plot_bgcolor=BG, font=dict(color=BLACK),
        height=430, margin=dict(t=50, b=40, l=10, r=10), hovermode="x unified",
        xaxis=dict(tickvals=tv, ticktext=tt, tickangle=-90, showgrid=False),
        yaxis=dict(title="%", gridcolor="white", zeroline=False),
        legend=dict(x=0.01, y=0.02, xanchor="left", yanchor="bottom", bgcolor="rgba(242,242,242,0.85)",
                    font=dict(size=13)),
    )
    st.plotly_chart(fig, width="stretch", key=chart_key or f"itau-{key}")


def seasonal_chart(key: str, year: int, chart_key: str | None = None):
    label = ipca.BY_KEY[key].label
    p = ipca.seasonal_profile(wide["mom"][key], year)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=PT, y=p["band_max"], mode="lines+markers", line=dict(color="#CFCFCF", width=2),
                             marker=dict(size=5, color="#CFCFCF"), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=PT, y=p["band_min"], mode="lines+markers", line=dict(color="#CFCFCF", width=2),
                             marker=dict(size=5, color="#CFCFCF"), fill="tonexty", fillcolor="rgba(200,200,200,0.45)",
                             name=f"Amplitude (10 anos, {year - 10}–{year - 1})"))
    fig.add_trace(go.Scatter(x=PT, y=p["prev"], mode="lines+markers", name=str(year - 1),
                             line=dict(color=BLACK, width=2), marker=dict(size=6)))
    fig.add_trace(go.Scatter(x=PT, y=p["curr"], mode="lines+markers", name=str(year),
                             line=dict(color=ORANGE, width=2.5), marker=dict(size=6)))
    fig.add_trace(go.Scatter(x=PT, y=p["base_avg"], mode="lines+markers", name="Média (2017-19)",
                             line=dict(color=BLACK, width=2, dash="dot"), marker=dict(size=6)))
    fig.update_layout(
        title=dict(text=label, x=0.5, font=dict(color=BLACK)), template="plotly_white",
        paper_bgcolor="white", font=dict(color=BLACK), height=430,
        margin=dict(t=50, b=40, l=10, r=10), hovermode="x unified", yaxis=dict(title="% m/m"),
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5),
    )
    st.plotly_chart(fig, width="stretch", key=chart_key or f"seasonal-{key}-{year}")


selectable = [i.key for i in ipca.ITEMS if i.key not in ipca.LEVEL_ONLY]
fmt_key = lambda k: ipca.BY_KEY[k].label  # noqa: E731

st.divider()
st.subheader("Explorer")
a, b = st.columns(2)
with a:
    k1 = st.selectbox("Series — MoM AR / MM3M SAAR / YoY", selectable, format_func=fmt_key,
                      index=selectable.index("geral"))
    itau_chart(k1, chart_key="explorer-itau")
with b:
    c1, c2 = st.columns([3, 1])
    k2 = c1.selectbox("Series — seasonal chart (raw m/m)", selectable, format_func=fmt_key,
                      index=selectable.index("core_avg"))
    yr = c2.selectbox("Year", list(range(ref.year, 2010, -1)))
    seasonal_chart(k2, yr, chart_key="explorer-seasonal")

st.divider()
st.subheader("Índice geral")
itau_chart("geral")

st.subheader("Serviços")
a, b = st.columns(2)
with a:
    itau_chart("servicos")
with b:
    itau_chart("serv_subj")
st.info(
    "BCB's service cores (sensitive to inertia, idleness, inertia & idleness, labour) are not published in SGS — "
    "they need IBGE sub-item data and BCB's classification lists."
)

st.subheader("Industriais")
a, b = st.columns(2)
with a:
    itau_chart("industriais")
with b:
    itau_chart("ind_subj")

st.subheader("Núcleos")
a, b = st.columns(2)
with a:
    itau_chart("ex0", "IPCA-EX0")
with b:
    itau_chart("ex3", "IPCA-EX3")
a, b = st.columns(2)
with a:
    itau_chart("core_avg", "Média dos núcleos")
with b:
    seasonal_chart("core_avg", ref.year)
