"""Compare COPOM pricing methods against Bloomberg for one DI curve date.

Usage:  uv run python debug_copom.py [YYYY-MM-DD]     (default: latest date in DB)
"""

import math
import sys
from datetime import date

import pandas as pd
from bizdays import Calendar

from data_pipeline.bcb_meetings import load_meetings
from data_pipeline.copom_pricing import calculate_copom_pricing, log_accrual, prepare_curve
from data_pipeline.di_curve import load_available_dates, load_curve_from_db
from data_pipeline.selic_rate import fetch_cdi, fetch_selic_target

BBG = {"2026-12-09": 8.30, "2027-01-27": 20.35, "2027-03-17": 26.15, "2027-04-28": 25.52}
cal = Calendar.load("ANBIMA")

ref_str = sys.argv[1] if len(sys.argv) > 1 else load_available_dates()[0]
ref = date.fromisoformat(ref_str)
curve = prepare_curve(load_curve_from_db(ref), max_du=400)
mtg = load_meetings()
mtg = mtg[mtg["effective_date"] > ref_str].reset_index(drop=True)
mtg["effective_du"] = mtg["effective_date"].apply(lambda s: cal.bizdays(ref, date.fromisoformat(s)))
mtg = mtg[mtg["effective_du"] < curve["du"].max()].reset_index(drop=True)

try:
    cdi, _ = fetch_cdi(ref)
except Exception as e:
    cdi = None
    print("CDI fetch failed:", e)
try:
    selic, _ = fetch_selic_target(ref)
except Exception as e:
    selic = None
    print("SELIC fetch failed:", e)
first_di = float(curve.iloc[0]["rate_252"])

print(f"\nCurve date {ref_str} | CDI {cdi} | SELIC target {selic} | first DI {first_di}")
print("\nCURVE (paste this back):")
print(curve[["contract_code", "expiry_date", "du", "rate_252"]].to_string(index=False))
print("\nMEETINGS:")
print(mtg[["decision_date", "effective_date", "effective_du"]].to_string(index=False))

eff = mtg["effective_du"].tolist()
L = dict(zip(curve["du"], curve["log_acc"]))
dus = [0] + curve["du"].tolist()
logs = [0.0] + curve["log_acc"].tolist()


def L_interp(x: float) -> float:
    """Flat-forward interpolation of ln(accrual) in DU."""
    for i in range(1, len(dus)):
        if x <= dus[i]:
            w = (x - dus[i - 1]) / (dus[i] - dus[i - 1])
            return logs[i - 1] + w * (logs[i] - logs[i - 1])
    return logs[-1]


def fwd(a: float, b: float) -> float:
    return math.exp((L_interp(b) - L_interp(a)) * 252 / (b - a)) - 1


def method_interp(cdi0):
    """Forward CDI between consecutive meeting dates, curve interpolated flat-forward."""
    pts = eff + [eff[-1] + 30]
    rates = [fwd(pts[k], pts[k + 1]) for k in range(len(eff))]
    base = fwd(0, eff[0]) if cdi0 is None else cdi0
    before = [base] + rates[:-1]
    return [(a - b) * 1e4 for a, b in zip(rates, before)]


def method_first_contract(cdi0):
    """Sequential bootstrap: each meeting solved from the first contract after it."""
    rates = []
    for k, e in enumerate(eff):
        nxt = curve[curve["du"] > e].iloc[0]
        T = int(nxt["du"])
        known = log_accrual(e, cdi0, eff[:k], rates)
        rates.append(math.exp((nxt["log_acc"] - known) * 252 / (T - e)) - 1)
    before = [cdi0] + rates[:-1]
    return [(a - b) * 1e4 for a, b in zip(rates, before)]


def method_page(cdi0):
    return calculate_copom_pricing(load_curve_from_db(ref), mtg, ref, cdi0, max_du=400).meetings["change_bps"].tolist()


cols = {}
for name, c0 in [("CDI", cdi), ("SELIC", selic), ("firstDI", first_di)]:
    if c0 is None:
        continue
    cols[f"page|{name}"] = method_page(c0)
    cols[f"first|{name}"] = method_first_contract(c0)
    cols[f"interp|{name}"] = method_interp(c0)
cols["interp|curve"] = method_interp(None)

out = pd.DataFrame({"decision": mtg["decision_date"], "BBG": mtg["decision_date"].map(BBG)})
for k, v in cols.items():
    out[k] = pd.Series(v[: len(out)])
pd.set_option("display.width", 250)
print("\nPRICED CHANGE PER MEETING (bps):")
print(out.head(6).round(2).to_string(index=False))

rmse = {k: ((out[k] - out["BBG"]) ** 2).mean() ** 0.5 for k in cols if out["BBG"].notna().any()}
print("\nRMSE vs BBG (bps):")
for k, v in sorted(rmse.items(), key=lambda kv: kv[1]):
    print(f"  {k:16s} {v:6.2f}")
