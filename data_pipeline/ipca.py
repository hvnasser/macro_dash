"""
IPCA — breakdowns, cores and derived metrics
=============================================

SOURCE
------
BCB SGS monthly series (% m/m), every code checked against its SGS name.
Raw values go to `bcb_series` (append-only, see data_pipeline/sgs.py).

DERIVED METRICS (table `ipca_metrics`, recalculated and overwritten each update)
---------------------------------------------------------------------------------
mom        raw % m/m
mom_sa     seasonally adjusted % m/m
mom_saar   (1 + mom_sa)^12 - 1            → "MoM AR"
mm3m_saar  (1 + avg of last 3 mom_sa)^12 - 1
yoy        compounded last 12 months of raw mom

Seasonal adjustment: additive STL on the m/m series from 2008, with a
near-stable seasonal pattern (seasonal=51, not robust).  Against Itaú's
chartbook (Jun-2026) this reproduced MM3M SAAR within ~0.6 pp RMSE across 13
series; Itaú most likely uses X-13, which needs an external binary.  YoY needs
no adjustment and matches exactly.

`core_avg` = mean of the five BCB cores (EX0, EX3, MS, DP, P55) — Itaú's
"Média dos núcleos"; `core_low_bias` = mean of MS and DP.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from data_pipeline.sgs import fetch_sgs, save_series
from utils.db import _clean_records, get_client

SA_START = "2008-01-01"
SA_SEASONAL = 51


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    code: int | None = None            # SGS code; None for derived series
    components: tuple[str, ...] = ()   # derived = mean of these keys' m/m


ITEMS: list[Item] = [
    Item("geral", "Índice geral", 433),
    # Groups
    Item("alim_bebidas", "Alimentação e bebidas", 1635),
    Item("habitacao", "Habitação", 1636),
    Item("artigos_res", "Artigos de residência", 1637),
    Item("vestuario", "Vestuário", 1638),
    Item("transportes", "Transportes", 1639),
    Item("saude", "Saúde e cuidados pessoais", 1641),
    Item("desp_pessoais", "Despesas pessoais", 1642),
    Item("educacao", "Educação", 1643),
    Item("comunicacao", "Comunicação", 1640),
    # Classifications
    Item("administrados", "Administrados", 4449),
    Item("livres", "Livres", 11428),
    Item("alim_dom", "Alimentação no domicílio", 27864),
    Item("industriais", "Industriais", 27863),
    Item("servicos", "Serviços", 10844),
    Item("ind_subj", "Industriais subjacente", 29684),     # SGS name: "EX3 Industriais"
    Item("serv_subj", "Serviços subjacente", 29683),       # SGS name: "EX3 Serviços"
    Item("comercializaveis", "Comercializáveis", 4447),
    Item("nao_comercializaveis", "Não comercializáveis", 4448),
    # Cores
    Item("ex0", "IPCA-EX0", 11427),
    Item("ex1", "IPCA-EX1", 16121),
    Item("ex2", "IPCA-EX2", 27838),
    Item("ex3", "IPCA-EX3", 27839),
    Item("exfe", "IPCA-EXFE", 28751),
    Item("ms", "IPCA-MS", 4466),
    Item("dp", "IPCA-DP", 16122),
    Item("p55", "IPCA-P55", 28750),
    Item("core_avg", "Média dos núcleos", components=("ex0", "ex3", "ms", "dp", "p55")),
    Item("core_low_bias", "Média núcleos com menor viés", components=("ms", "dp")),
    # Other
    Item("ipca15", "IPCA-15", 7478),
    Item("difusao", "Difusão", 21379),   # % of items rising — a level, no metrics
]
BY_KEY = {i.key: i for i in ITEMS}
LEVEL_ONLY = {"difusao"}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def seasonal_adjust(mom: pd.Series) -> pd.Series:
    from statsmodels.tsa.seasonal import STL

    x = mom.loc[SA_START:].dropna().asfreq("MS")
    if len(x) < 36 or x.isna().any():
        return pd.Series(np.nan, index=mom.index)
    sa = x - STL(x, period=12, seasonal=SA_SEASONAL, robust=False).fit().seasonal
    return sa.reindex(mom.index)


def annualize(m: pd.Series) -> pd.Series:
    return ((1 + m / 100) ** 12 - 1) * 100


def yoy(mom: pd.Series) -> pd.Series:
    return ((1 + mom / 100).rolling(12).apply(np.prod, raw=True) - 1) * 100


def metrics_for(mom: pd.Series) -> pd.DataFrame:
    sa = seasonal_adjust(mom)
    return pd.DataFrame({
        "mom": mom,
        "mom_sa": sa,
        "mom_saar": annualize(sa),
        "mm3m_saar": annualize(sa.rolling(3).mean()),
        "yoy": yoy(mom),
    })


def compute_metrics(raw: pd.DataFrame) -> pd.DataFrame:
    """raw: wide % m/m by item key (month index). Returns long metrics table."""
    raw = raw.copy()
    for it in ITEMS:
        if it.components:
            raw[it.key] = raw[list(it.components)].mean(axis=1, skipna=False)
    frames = []
    for it in ITEMS:
        if it.key not in raw:
            continue
        s = raw[it.key].dropna()
        if it.key in LEVEL_ONLY:
            m = pd.DataFrame({"mom": s})
        else:
            m = metrics_for(s)
        frames.append(m.assign(series_key=it.key, ref_date=m.index))
    out = pd.concat(frames, ignore_index=True)
    out["ref_date"] = pd.to_datetime(out["ref_date"]).dt.date.astype(str)
    return out[["series_key", "ref_date", "mom", "mom_sa", "mom_saar", "mm3m_saar", "yoy"]]


# ---------------------------------------------------------------------------
# Update & load
# ---------------------------------------------------------------------------

def save_metrics(metrics: pd.DataFrame, batch: int = 1000) -> int:
    client = get_client()
    now = pd.Timestamp.now(tz="UTC").isoformat()
    records = _clean_records(metrics.round(6).assign(computed_at=now).to_dict(orient="records"))
    for i in range(0, len(records), batch):
        client.table("ipca_metrics").upsert(
            records[i:i + batch], on_conflict="series_key,ref_date", ignore_duplicates=False,
        ).execute()
    return len(records)


def update_ipca_data(progress=None) -> pd.DataFrame:
    """Download raw IPCA series to `bcb_series`, then recompute `ipca_metrics`.

    Returns one row per series: key, code, last_month, rows_added, error.
    """
    raw, out = {}, []
    sources = [i for i in ITEMS if i.code]
    for n, it in enumerate(sources):
        if progress:
            progress(n, len(sources) + 1, it.label)
        try:
            df = fetch_sgs(it.code)
            added = save_series(it.code, df)
            raw[it.key] = df.set_index("ref_date")["value"]
            out.append({"series": it.label, "code": it.code, "last_month": df["ref_date"].max(),
                        "rows_added": added, "error": ""})
        except Exception as e:
            out.append({"series": it.label, "code": it.code, "last_month": None,
                        "rows_added": 0, "error": f"{type(e).__name__}: {e}"})
    if progress:
        progress(len(sources), len(sources) + 1, "metrics")
    wide = pd.DataFrame(raw)
    wide.index = pd.to_datetime(wide.index)
    save_metrics(compute_metrics(wide.sort_index()))
    return pd.DataFrame(out)


def load_ipca_metrics() -> pd.DataFrame:
    """All stored metrics, long: series_key, ref_date (Timestamp), mom, mom_sa, mom_saar, mm3m_saar, yoy."""
    client = get_client()
    rows, start, page = [], 0, 1000
    while True:
        chunk = (
            client.table("ipca_metrics")
            .select("series_key, ref_date, mom, mom_sa, mom_saar, mm3m_saar, yoy")
            .order("series_key").order("ref_date")
            .range(start, start + page - 1)
            .execute().data
        )
        rows += chunk
        if len(chunk) < page:
            break
        start += page
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["ref_date"] = pd.to_datetime(df["ref_date"])
    for c in ("mom", "mom_sa", "mom_saar", "mm3m_saar", "yoy"):
        df[c] = pd.to_numeric(df[c])
    return df


# ---------------------------------------------------------------------------
# Views used by the page
# ---------------------------------------------------------------------------

def annual_yoy(m: pd.DataFrame, years: list[int]) -> dict[int, float]:
    """Dec/Dec inflation for each year (from the YoY of December)."""
    dec = m[m["ref_date"].dt.month == 12]
    by_year = pd.Series(dec["yoy"].values, index=dec["ref_date"].dt.year)
    return {y: by_year.get(y, np.nan) for y in years}


def seasonal_profile(mom: pd.Series, year: int, base_years=(2017, 2019), band_years: int = 10) -> pd.DataFrame:
    """Raw m/m by calendar month: `year`, `year-1`, average of base years, min/max of last 10 years."""
    df = pd.DataFrame({"v": mom.dropna()})
    df["year"], df["month"] = df.index.year, df.index.month
    t = df.pivot(index="month", columns="year", values="v").reindex(range(1, 13))
    band = t[[y for y in t.columns if year - band_years <= y < year]]
    base = t[[y for y in t.columns if base_years[0] <= y <= base_years[1]]]
    return pd.DataFrame({
        "band_min": band.min(axis=1), "band_max": band.max(axis=1),
        "base_avg": base.mean(axis=1),
        "prev": t.get(year - 1), "curr": t.get(year),
    })
