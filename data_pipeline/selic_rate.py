"""
Current SELIC Target Rate — BCB (Banco Central do Brasil)
===========================================================

Source: BCB's public SGS (Sistema Gerenciador de Séries Temporais) API.

    Series 432 = SELIC target (meta SELIC) — updated on every COPOM decision.
    URL: https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json
    Response: [{"data": "DD/MM/YYYY", "valor": "13.25"}]  ← valor is already % p.a.

The SELIC target is the overnight rate that is currently compounding in the
DI index.  It is set at each COPOM meeting and does NOT change between meetings.
It is the correct starting rate for all COPOM pricing calculations.
"""

from __future__ import annotations

from datetime import date

import requests

_BCB_SGS_URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados/ultimos/{n}"
    "?formato=json"
)
_SELIC_TARGET_SERIES = 432


def fetch_selic_target() -> tuple[float, date]:
    """Fetch the current SELIC target rate from BCB SGS.

    Returns:
        (rate_decimal, reference_date)  e.g. (0.1325, date(2026, 1, 28))
    """
    url = _BCB_SGS_URL.format(series=_SELIC_TARGET_SERIES, n=1)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    latest = data[-1]
    rate_decimal = float(latest["valor"]) / 100.0
    ref_date = date(
        int(latest["data"][6:10]),
        int(latest["data"][3:5]),
        int(latest["data"][0:2]),
    )
    return rate_decimal, ref_date
