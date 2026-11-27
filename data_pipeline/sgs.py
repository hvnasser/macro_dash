"""
BCB SGS time series — download and append-only storage
=======================================================

SOURCE
------
https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?formato=json&dataInicial=01/01/2000
(without a start date BCB answers some long series, e.g. IPCA, with an empty 304)

STORAGE (append-only, revisions preserved)
------------------------------------------
Table `bcb_series`: one row per (series_code, ref_date, value).
A revised value is a NEW row (the old one is kept), so the full revision
history survives.  The view `bcb_series_latest` returns the most recently
collected value for each (series_code, ref_date).
"""

from __future__ import annotations

import time

import pandas as pd
import requests

from utils.db import _clean_records, get_client

_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?formato=json&dataInicial=01/01/2000"


def fetch_sgs(code: int, attempts: int = 4) -> pd.DataFrame:
    """History of one SGS series since 2000. Retries: BCB intermittently answers
    with an HTML error page instead of JSON, especially under quick requests."""
    for i in range(attempts):
        try:
            resp = requests.get(_SGS_URL.format(code=code), timeout=60)
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError):
            if i == attempts - 1:
                raise
            time.sleep(2 ** (i + 1))
    df = pd.DataFrame(data)
    if df.empty:
        return pd.DataFrame(columns=["ref_date", "value"])
    df["ref_date"] = pd.to_datetime(df["data"], format="%d/%m/%Y").dt.date.astype(str)
    df["value"] = pd.to_numeric(df["valor"], errors="coerce")
    return df.dropna(subset=["value"])[["ref_date", "value"]]


def save_series(code: int, df: pd.DataFrame, batch: int = 1000) -> int:
    """Insert values not stored yet (new months or revisions). Returns rows added."""
    if df.empty:
        return 0
    client = get_client()
    records = _clean_records(df.assign(series_code=code).to_dict(orient="records"))
    added = 0
    for i in range(0, len(records), batch):
        resp = client.table("bcb_series").upsert(
            records[i:i + batch],
            on_conflict="series_code,ref_date,value",
            ignore_duplicates=True,
        ).execute()
        added += len(resp.data or [])
    return added


def load_latest(codes: list[int]) -> pd.DataFrame:
    """Latest stored value per (series_code, ref_date), long format."""
    client = get_client()
    rows, start, page = [], 0, 1000
    while True:
        chunk = (
            client.table("bcb_series_latest")
            .select("series_code, ref_date, value, collected_at")
            .in_("series_code", codes)
            .order("series_code").order("ref_date")
            .range(start, start + page - 1)
            .execute().data
        )
        rows += chunk
        if len(chunk) < page:
            break
        start += page
    return pd.DataFrame(rows)
