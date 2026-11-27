"""
Brazil credit data — BCB SGS monthly series
============================================

SOURCE
------
BCB "Estatísticas monetárias e de crédito" (Nota de crédito) via the SGS API.
Every code below was checked against the series name in BCB's SGS metadata.
Download and append-only storage (table `bcb_series`): see data_pipeline/sgs.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from bizdays import Calendar

from data_pipeline.sgs import fetch_sgs, load_latest, save_series

_CAL = Calendar.load("ANBIMA")


@dataclass(frozen=True)
class Series:
    code: int
    name: str
    unit: str


CREDIT_SERIES: dict[str, Series] = {s.name: s for s in [
    # Outstanding credit (saldo), R$ million
    Series(20539, "stock_total", "R$ mn"),
    Series(20540, "stock_pj", "R$ mn"),
    Series(20541, "stock_pf", "R$ mn"),
    Series(20542, "stock_free", "R$ mn"),
    Series(20543, "stock_free_pj", "R$ mn"),
    Series(20570, "stock_free_pf", "R$ mn"),
    Series(20593, "stock_earmarked", "R$ mn"),
    Series(20594, "stock_earmarked_pj", "R$ mn"),
    Series(20606, "stock_earmarked_pf", "R$ mn"),
    Series(20604, "stock_bndes_pj", "R$ mn"),
    Series(20616, "stock_bndes_pf", "R$ mn"),
    Series(2007, "stock_public_banks", "R$ mn"),
    Series(2043, "stock_private_banks", "R$ mn"),
    # Credit / GDP, %
    Series(20622, "stock_gdp", "% GDP"),
    Series(20623, "stock_gdp_pj", "% GDP"),
    Series(20624, "stock_gdp_pf", "% GDP"),
    # New loans (concessões), R$ million
    Series(20631, "new_total", "R$ mn"),
    Series(20632, "new_pj", "R$ mn"),
    Series(20633, "new_pf", "R$ mn"),
    Series(20668, "new_payroll_private", "R$ mn"),
    # Rates, % p.a. / spread p.p.
    Series(20714, "rate_total", "% p.a."),
    Series(20715, "rate_pj", "% p.a."),
    Series(20716, "rate_pf", "% p.a."),
    Series(20783, "spread_total", "p.p."),
    Series(4189, "selic_month", "% p.a."),
    # Delinquency, %
    Series(21082, "npl_total", "%"),
    Series(21083, "npl_pj", "%"),
    Series(21084, "npl_pf", "%"),
    Series(21003, "arrears_total", "%"),
    # NPL on free (non-earmarked) credit by product, %
    Series(21112, "npl_free_pf", "%"),
    Series(21121, "npl_free_pf_vehicles", "%"),
    Series(21129, "npl_free_pf_card", "%"),
    Series(21119, "npl_free_pf_payroll", "%"),
    Series(21114, "npl_free_pf_personal", "%"),
    Series(21118, "npl_free_pf_payroll_inss", "%"),
    Series(21117, "npl_free_pf_payroll_public", "%"),
    Series(21116, "npl_free_pf_payroll_private", "%"),
    Series(21086, "npl_free_pj", "%"),
    Series(21096, "npl_free_pj_vehicles", "%"),
    Series(21093, "npl_free_pj_wc", "%"),
    Series(21106, "npl_free_pj_card", "%"),
    Series(21087, "npl_free_pj_receivables", "%"),
    Series(21091, "npl_free_pj_wc_long", "%"),
    Series(21090, "npl_free_pj_wc_short", "%"),
    Series(21092, "npl_free_pj_wc_revolving", "%"),
    # Households, % of income
    Series(29037, "hh_indebtedness", "% income 12m"),
    Series(29034, "hh_income_commitment", "% income, sa"),
    # Prices
    Series(433, "ipca_mom", "% m/m"),
]}

_BY_CODE = {s.code: s.name for s in CREDIT_SERIES.values()}


# ---------------------------------------------------------------------------
# Download & store
# ---------------------------------------------------------------------------

def update_credit_data(progress=None) -> pd.DataFrame:
    """Download every credit series from BCB and store new/revised values.

    progress: optional callback(i, total, series_name).
    Returns one row per series: name, code, last_month, rows_added, error.
    """
    out = []
    items = list(CREDIT_SERIES.values())
    for i, s in enumerate(items):
        if progress:
            progress(i, len(items), s.name)
        try:
            df = fetch_sgs(s.code)
            added = save_series(s.code, df)
            out.append({"series": s.name, "code": s.code, "last_month": df["ref_date"].max(),
                        "rows_added": added, "error": ""})
        except Exception as e:
            out.append({"series": s.name, "code": s.code, "last_month": None,
                        "rows_added": 0, "error": f"{type(e).__name__}: {e}"})
    return pd.DataFrame(out)


def load_credit_data() -> pd.DataFrame:
    """Latest value of every credit series, wide: index = month (Timestamp), columns = names."""
    return to_wide(load_latest(list(_BY_CODE)))


def to_wide(long: pd.DataFrame) -> pd.DataFrame:
    if long.empty:
        return pd.DataFrame()
    if "collected_at" in long:
        long = long.sort_values("collected_at")
    long = long.drop_duplicates(["series_code", "ref_date"], keep="last")
    long = long.assign(
        name=long["series_code"].map(_BY_CODE),
        ref_date=pd.to_datetime(long["ref_date"]),
        value=pd.to_numeric(long["value"]),
    )
    return long.pivot(index="ref_date", columns="name", values="value").sort_index()


# ---------------------------------------------------------------------------
# Transformations
# ---------------------------------------------------------------------------

def price_index(w: pd.DataFrame) -> pd.Series:
    """IPCA index from monthly % changes, = 1 at the latest month (values in today's R$)."""
    idx = (1 + w["ipca_mom"].dropna() / 100).cumprod()
    return (idx / idx.iloc[-1]).reindex(w.index).ffill()


def real(w: pd.DataFrame, col: str) -> pd.Series:
    return w[col] / price_index(w)


def business_days(index: pd.DatetimeIndex) -> pd.Series:
    """ANBIMA business days in each month (NaN outside the calendar's range)."""
    def count(d: pd.Timestamp) -> float:
        # Not _CAL.bizdays(first, next_first): it counts (start, end], dropping
        # the 1st of the month whenever that is a business day.
        try:
            return float(sum(_CAL.isbizday(x.date()) for x in pd.date_range(d, d + pd.offsets.MonthEnd(0))))
        except Exception:
            return np.nan
    return pd.Series([count(d) for d in index], index=index, dtype=float)


def seasonal_adjust(s: pd.Series) -> pd.Series:
    """Multiplicative STL seasonal adjustment (log scale, robust to outliers)."""
    from statsmodels.tsa.seasonal import STL

    x = s.dropna()
    x = x[x > 0]
    if len(x) < 36:
        return s
    res = STL(np.log(x.asfreq("MS")), period=12, robust=True).fit()
    return np.exp(np.log(x) - res.seasonal).reindex(s.index)


def seasonal_adjust_td(s: pd.Series) -> pd.Series:
    """Seasonal + trading-day adjustment.

    Flows don't scale 1:1 with business days (card spending follows calendar
    days), so instead of dividing by business days we estimate the elasticity
    to an unusual day count for that calendar month (as X-13 does with a
    trading-day regressor) and remove only that effect before STL.
    """
    from statsmodels.tsa.seasonal import STL

    x = s.dropna()
    x = np.log(x[x > 0].asfreq("MS"))
    if len(x) < 36 or x.isna().any():
        return seasonal_adjust(s)
    lbd = np.log(business_days(x.index))
    dev = lbd - lbd.groupby(lbd.index.month).transform("mean")
    resid = STL(x, period=12, robust=True).fit().resid
    beta = np.polyfit(dev, resid, 1)[0]
    adj = x - beta * dev
    sa = adj - STL(adj, period=12, robust=True).fit().seasonal
    return np.exp(sa).reindex(s.index)


def yoy(s: pd.Series) -> pd.Series:
    return (s / s.shift(12) - 1) * 100


def growth_3m_saar(s: pd.Series) -> pd.Series:
    """3-month sum over previous 3-month sum, seasonally-adjusted annualised rate."""
    r3 = s.rolling(3).sum()
    return ((r3 / r3.shift(3)) ** 4 - 1) * 100


def gdp_12m(w: pd.DataFrame) -> pd.Series:
    """12-month nominal GDP (R$ mn) implied by BCB's own credit/GDP ratio."""
    return w["stock_total"] / (w["stock_gdp"] / 100)


def seasonal_profile(s: pd.Series, year: int, compare_years: int = 2, band_years: int = 10) -> pd.DataFrame:
    """Month-of-year view: selected year and the `compare_years` before it, plus the
    min/max band of the `band_years` preceding those (so compared years stand out)."""
    df = pd.DataFrame({"v": s.dropna()})
    df["year"], df["month"] = df.index.year, df.index.month
    table = df.pivot(index="month", columns="year", values="v")
    first = year - compare_years
    band = table[[y for y in table.columns if first - band_years <= y < first]]
    out = pd.DataFrame({"band_min": band.min(axis=1), "band_max": band.max(axis=1)})
    for y in range(year - compare_years, year + 1):
        if y in table.columns:
            out[y] = table[y]
    return out.reindex(range(1, 13))
