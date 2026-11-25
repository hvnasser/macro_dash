"""
SELIC target and CDI — BCB SGS API
===================================

    Series 432  = SELIC target (meta SELIC), % p.a.
    Series 4389 = CDI annualised, base 252, % p.a.  ← the rate the DI index compounds at

Values are fetched AS OF a reference date (last observation on or before it),
so a historical DI curve is always paired with the rates in force that day.
"""

from __future__ import annotations

from datetime import date, timedelta

import requests

_URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados"
    "?formato=json&dataInicial={start}&dataFinal={end}"
)
SELIC_TARGET = 432
CDI_252 = 4389


def fetch_sgs_as_of(series: int, ref_date: date, lookback_days: int = 20) -> tuple[float, date]:
    """Return (value_decimal, observation_date) for the last observation <= ref_date."""
    start = (ref_date - timedelta(days=lookback_days)).strftime("%d/%m/%Y")
    end = ref_date.strftime("%d/%m/%Y")
    resp = requests.get(_URL.format(series=series, start=start, end=end), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"SGS {series}: no data between {start} and {end}")
    last = data[-1]
    d, m, y = last["data"].split("/")
    return float(last["valor"]) / 100.0, date(int(y), int(m), int(d))


def fetch_selic_target(ref_date: date) -> tuple[float, date]:
    return fetch_sgs_as_of(SELIC_TARGET, ref_date)


def fetch_cdi(ref_date: date) -> tuple[float, date]:
    return fetch_sgs_as_of(CDI_252, ref_date)
