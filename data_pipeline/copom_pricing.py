"""
COPOM pricing implied by the DI1 curve
=======================================

MODEL
-----
The DI index accrues the daily CDI.  The CDI is flat between COPOM meetings and
jumps on each meeting's effective date (decision date + 1 business day).
With today's CDI r0 and post-meeting CDIs r1..rN, a contract expiring in T
business days must satisfy, for every DI1 contract:

    (1 + R_T)^(T/252) = (1+r0)^(e1/252) · (1+r1)^((e2-e1)/252) · ... · (1+rk)^((T-ek)/252)

where e_k = business days from the curve date to meeting k's effective date.
Days are counted [start, end): the CDI of the curve date is included, the CDI of
the expiry date is not — the same convention as the contract's DU.

BOOTSTRAP
---------
Meetings are solved in order.  Meeting k is identified by the contracts
expiring in (e_k, e_{k+1}] — their accrual includes r_k but not r_{k+1}.
Given r0..r_{k-1}, r_k is the least-squares fit (in rate space) to ALL of those
contracts.  Using every contract in the window, rather than only the first,
avoids blowing up B3's rate rounding when a contract expires 1-2 days after a
meeting.  For the last meeting only contracts within six months are used.

If no contract expires between meeting k and the next one, those meetings are
grouped and fitted jointly with the contracts after the group, assuming an
equal change at each meeting of the group (flagged as `shared`).

Meetings after the last contract within the horizon cannot be priced and are
dropped.  Every contract is then repriced from the implied path and the
residual is reported in bps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd
from bizdays import Calendar
from scipy.optimize import minimize_scalar

_CAL = Calendar.load("ANBIMA")
GRID_BPS = 25.0


@dataclass
class CopomPricing:
    meetings: pd.DataFrame   # one row per priced meeting
    contracts: pd.DataFrame  # every contract in the horizon, market vs model
    cdi0: float


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def prepare_curve(df_curve: pd.DataFrame, max_du: int | None = None) -> pd.DataFrame:
    c = df_curve[["contract_code", "expiry_date", "du", "rate_252"]].copy()
    c["du"] = pd.to_numeric(c["du"]).astype(int)
    c["rate_252"] = pd.to_numeric(c["rate_252"]).astype(float)
    c = c[(c["du"] > 0) & c["rate_252"].notna()]
    if max_du is not None:
        c = c[c["du"] <= max_du]
    c = c.sort_values("du").reset_index(drop=True)
    c["log_acc"] = c["du"] / 252.0 * (1.0 + c["rate_252"]).apply(math.log)
    return c


def prepare_meetings(df_meetings: pd.DataFrame, ref_date: date) -> pd.DataFrame:
    m = df_meetings[["decision_date", "effective_date"]].copy()
    m["effective_du"] = m["effective_date"].apply(
        lambda s: _CAL.bizdays(ref_date, date.fromisoformat(str(s)))
    )
    return m[m["effective_du"] > 0].sort_values("effective_du").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Path arithmetic
# ---------------------------------------------------------------------------

def log_accrual(du: int, cdi0: float, eff_dus: list[int], rates: list[float]) -> float:
    """ln of the CDI accrual factor from day 0 to *du* along a step path."""
    acc, prev_du, prev_r = 0.0, 0, cdi0
    for e, r in zip(eff_dus, rates):
        if e >= du:
            break
        acc += (e - prev_du) / 252.0 * math.log1p(prev_r)
        prev_du, prev_r = e, r
    return acc + (du - prev_du) / 252.0 * math.log1p(prev_r)


def rate_from_path(du: int, cdi0: float, eff_dus: list[int], rates: list[float]) -> float:
    return math.exp(log_accrual(du, cdi0, eff_dus, rates) * 252.0 / du) - 1.0


def grid_probabilities(change_bps: float, step: float = GRID_BPS) -> str:
    """Split a priced change between the two neighbouring multiples of *step*."""
    lo = math.floor(change_bps / step + 1e-9) * step
    p_hi = (change_bps - lo) / step
    if p_hi < 0.005:
        return f"100% {lo:+.0f}"
    if p_hi > 0.995:
        return f"100% {lo + step:+.0f}"
    return f"{p_hi:.0%} {lo + step:+.0f} · {1 - p_hi:.0%} {lo:+.0f}"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def calculate_copom_pricing(
    df_curve: pd.DataFrame,
    df_meetings: pd.DataFrame,
    ref_date: date,
    cdi0: float,
    max_du: int | None = None,
) -> CopomPricing:
    curve = prepare_curve(df_curve, max_du)
    mtgs = prepare_meetings(df_meetings, ref_date)
    if curve.empty:
        return CopomPricing(pd.DataFrame(), pd.DataFrame(), cdi0)
    mtgs = mtgs[mtgs["effective_du"] < curve["du"].max()].reset_index(drop=True)

    eff = mtgs["effective_du"].tolist()
    n = len(eff)
    rates: list[float] = []
    anchors: list[str] = []
    shared: list[bool] = []

    k = 0
    while k < n:
        # Smallest j >= k with contracts expiring in (e_j, e_{j+1}]
        j, cands = k, None
        while j < n:
            upper = eff[j + 1] if j + 1 < n else math.inf
            found = curve[(curve["du"] > eff[j]) & (curve["du"] <= upper)]
            if not found.empty:
                cands = found
                break
            j += 1
        if cands is None:
            break
        if j == n - 1:
            # After the last meeting the path is flat: fit only six months of
            # contracts so long-end term premium doesn't leak into the last meeting.
            near = cands[cands["du"] <= eff[j] + 126]
            cands = near if not near.empty else cands.iloc[:1]

        prev = rates[-1] if rates else cdi0
        size = j - k + 1
        fit_dus = cands["du"].astype(int).tolist()
        fit_rates = cands["rate_252"].tolist()

        def sse(step: float) -> float:
            trial = rates + [prev + step * (i + 1) for i in range(size)]
            return sum(
                (rate_from_path(T, cdi0, eff[: j + 1], trial) - r) ** 2
                for T, r in zip(fit_dus, fit_rates)
            )

        step = minimize_scalar(sse, bounds=(-0.2, 0.2), method="bounded",
                               options={"xatol": 1e-11}).x
        new = [prev + step * (i + 1) for i in range(size)]

        rates += new
        label = ", ".join(c[3:] if c.startswith("DI1") else c for c in cands["contract_code"])
        anchors += [label] * len(new)
        shared += [len(new) > 1] * len(new)
        k = j + 1

    priced = mtgs.iloc[: len(rates)].copy()
    before = [cdi0] + rates[:-1]
    priced["cdi_before"] = before
    priced["cdi_after"] = rates
    priced["change_bps"] = [(a - b) * 1e4 for a, b in zip(rates, before)]
    priced["cumulative_bps"] = priced["change_bps"].cumsum()
    priced["anchor"] = anchors
    priced["shared"] = shared
    priced["probabilities"] = priced["change_bps"].apply(grid_probabilities)

    contracts = curve[["contract_code", "expiry_date", "du", "rate_252"]].copy()
    eff_p = priced["effective_du"].tolist()
    contracts["model_rate"] = contracts["du"].apply(
        lambda du: rate_from_path(int(du), cdi0, eff_p, rates)
    )
    contracts["error_bps"] = (contracts["model_rate"] - contracts["rate_252"]) * 1e4

    return CopomPricing(priced.reset_index(drop=True), contracts, cdi0)


# ---------------------------------------------------------------------------
# Hypothesis: curve implied by a user-defined path of changes
# ---------------------------------------------------------------------------

def hypothesis_curve(
    contracts: pd.DataFrame,
    eff_dus: list[int],
    cdi0: float,
    changes_bps: list[float],
) -> pd.DataFrame:
    rates, r = [], cdi0
    for chg in changes_bps:
        r += chg / 1e4
        rates.append(r)
    out = contracts[["contract_code", "expiry_date", "du", "rate_252"]].copy()
    out["hyp_rate"] = out["du"].apply(lambda du: rate_from_path(int(du), cdi0, eff_dus, rates))
    out["diff_bps"] = (out["hyp_rate"] - out["rate_252"]) * 1e4
    return out
