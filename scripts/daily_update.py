"""
Daily DI update — download missing B3 settlements and build curve vertices.

Idempotent: every run looks at the last LOOKBACK_BDAYS business days, downloads
whatever is missing from `di_futures_raw`, and (re)builds `di_curve_vertices`
for those dates.  A skipped or failed run is healed by the next one.

Exit code 1 only when the most recent PAST business day is still missing
(today's file may legitimately not be published yet).  Every run is logged to
the `pipeline_runs` table.

    uv run python scripts/daily_update.py
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bizdays import Calendar  # noqa: E402

from data_pipeline.di_curve import (  # noqa: E402
    build_curve_vertices,
    fetch_di_settlements,
    recalculate_curve_from_db,
    save_curve_vertices,
    save_raw_settlements,
)
from utils.db import get_client  # noqa: E402

BRT = ZoneInfo("America/Sao_Paulo")
CAL = Calendar.load("ANBIMA")
LOOKBACK_BDAYS = 10
PUBLISH_TIME = time(18, 30)  # B3 settlements are never out before this (BRT)


def target_dates(now: datetime) -> list[date]:
    """Last LOOKBACK_BDAYS trading days, oldest first. Today only after PUBLISH_TIME."""
    d = now.date() if now.time() >= PUBLISH_TIME else now.date() - timedelta(days=1)
    out: list[date] = []
    while len(out) < LOOKBACK_BDAYS:
        # B3 has no session on Dec 24 / Dec 31 although ANBIMA counts them
        if CAL.isbizday(d) and (d.month, d.day) not in {(12, 24), (12, 31)}:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def stored_dates(client, table: str, since: date) -> set[str]:
    rows = client.table(table).select("ref_date").gte("ref_date", since.isoformat()).execute().data
    return {r["ref_date"] for r in rows}


def log_run(client, row: dict) -> None:
    try:
        client.table("pipeline_runs").insert(row).execute()
    except Exception as e:
        print(f"WARNING: could not write pipeline_runs: {e}")


def main() -> int:
    now = datetime.now(BRT)
    started = datetime.now(ZoneInfo("UTC")).isoformat()
    client = get_client()
    targets = target_dates(now)
    raw = stored_dates(client, "di_futures_raw", targets[0])
    vertices = stored_dates(client, "di_curve_vertices", targets[0])

    saved, rebuilt, not_published, failed = [], [], [], {}
    for d in targets:
        iso = d.isoformat()
        try:
            if iso not in raw:
                df_raw = fetch_di_settlements(d)
                if df_raw.empty:
                    not_published.append(iso)
                    continue
                save_raw_settlements(df_raw)
                save_curve_vertices(build_curve_vertices(df_raw, d))
                saved.append(iso)
                raw.add(iso)
            elif iso not in vertices:
                if not recalculate_curve_from_db(d).empty:
                    rebuilt.append(iso)
        except Exception as e:
            failed[iso] = f"{type(e).__name__}: {e}"
            traceback.print_exc()

    last_past = max(d for d in targets if d < now.date()).isoformat()
    if last_past not in raw:
        status = "error"
    elif failed or not_published:
        status = "warning"
    else:
        status = "ok"

    summary = (
        f"status={status} | saved={saved or '-'} | rebuilt={rebuilt or '-'} | "
        f"not published={not_published or '-'} | failed={failed or '-'} | "
        f"checked {targets[0]}..{targets[-1]} (BRT now {now:%Y-%m-%d %H:%M})"
    )
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(path).write_text(f"### Daily DI update: {status}\n\n{summary}\n", encoding="utf-8")

    log_run(client, {
        "started_at": started,
        "finished_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "trigger": os.environ.get("GITHUB_EVENT_NAME", "manual"),
        "status": status,
        "saved_dates": saved,
        "rebuilt_dates": rebuilt,
        "missing_dates": not_published + list(failed),
        "message": summary[:2000],
    })

    if status == "error":
        print(f"ERROR: {last_past} is still missing from di_futures_raw.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
