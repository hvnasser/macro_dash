"""
COPOM pricing implied by the DI1 curve
=======================================

MODEL
-----
The DI index accrues the daily CDI.  The CDI is flat between COPOM meetings and
jumps on each meeting's effective date (decision date + 1 business day).
With today's CDI r0 and post-meeting CDIs r1..rN, a contract expiring in T
business days must satisfy:

    (1 + R_T)^(T/252) = (1+r0)^(e1/252) · (1+r1)^((e2-e1)/252) · ... · (1+rk)^((T-ek)/252)

where e_k = business days from the curve date to meeting k's effective date.
Days are counted [start, end): the CDI of the curve date is included, the CDI of
the expiry date is not — the same convention as the contract's DU.

FIT
---
All meeting changes Δ1..ΔN are fitted jointly to every contract in the horizon
by least squares, minimising

    Σ_contracts (model_rate − market_rate)²  +  λ² · Σ_k (Δ_{k+1} − Δ_k)²     (bps)

A sequential bootstrap (one meeting at a time) chases noise: B3 rates carry
0.001% rounding and illiquid monthly contracts settle off interpolation, and
when a meeting is only seen by a few days of one contract that noise becomes
tens of bps.  The small smoothness penalty λ spreads it across the curve.
λ = 0.2 reproduced Bloomberg's per-meeting pricing within ~1 bp on
2026-11-24; λ = 0 is the unregularised least-squares fit.

Meetings effective after the last contract in the horizon are not priced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from bizdays import Calendar
from scipy.optimize import least_squares

_CAL = Calendar.load("ANBIMA")
GRID_BPS = 25.0
DEFAULT_SMOOTHNESS = 0.2


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
    c["log_acc"] = c["du"] / 252.0 * np.log1p(c["rate_252"])
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


def _days_matrix(contract_dus: np.ndarray, eff_dus: list[int]) -> np.ndarray:
    """D[i, k] = business days contract i accrues at rate k (k=0 is today's CDI)."""
    edges = np.array([0] + list(eff_dus) + [np.inf])
    T = contract_dus[:, None]
    return np.clip(np.minimum(T, edges[1:]) - edges[:-1], 0, None)


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
# Fit
# ---------------------------------------------------------------------------

def calculate_copom_pricing(
    df_curve: pd.DataFrame,
    df_meetings: pd.DataFrame,
    ref_date: date,
    cdi0: float,
    max_du: int | None = None,
    smoothness: float = DEFAULT_SMOOTHNESS,
) -> CopomPricing:
    curve = prepare_curve(df_curve, max_du)
    if curve.empty:
        return CopomPricing(pd.DataFrame(), pd.DataFrame(), cdi0)
    mtgs = prepare_meetings(df_meetings, ref_date)
    mtgs = mtgs[mtgs["effective_du"] < curve["du"].max()].reset_index(drop=True)

    eff = mtgs["effective_du"].tolist()
    T = curve["du"].to_numpy(dtype=float)
    market = curve["rate_252"].to_numpy()
    D = _days_matrix(T, eff)

    def model_rates(changes_bps: np.ndarray) -> np.ndarray:
        path = np.r_[cdi0, cdi0 + np.cumsum(changes_bps) / 1e4]
        return np.exp(D @ np.log1p(path) / T) - 1.0

    def residuals(x: np.ndarray) -> np.ndarray:
        return np.r_[(model_rates(x) - market) * 1e4, smoothness * np.diff(x)]

    if eff:
        changes = least_squares(residuals, np.zeros(len(eff)), xtol=1e-12, ftol=1e-12).x
    else:
        changes = np.array([])

    rates = (cdi0 + np.cumsum(changes) / 1e4).tolist()
    priced = mtgs.copy()
    priced["cdi_before"] = [cdi0] + rates[:-1] if rates else []
    priced["cdi_after"] = rates
    priced["change_bps"] = changes
    priced["cumulative_bps"] = np.cumsum(changes)
    priced["probabilities"] = priced["change_bps"].apply(grid_probabilities)

    contracts = curve[["contract_code", "expiry_date", "du", "rate_252"]].copy()
    contracts["model_rate"] = model_rates(changes)
    contracts["error_bps"] = (contracts["model_rate"] - contracts["rate_252"]) * 1e4

    return CopomPricing(priced, contracts, cdi0)


# ---------------------------------------------------------------------------
# Hypothesis: curve implied by a user-defined path of changes
# ---------------------------------------------------------------------------

def hypothesis_curve(
    contracts: pd.DataFrame,
    eff_dus: list[int],
    cdi0: float,
    changes_bps: list[float],
) -> pd.DataFrame:
    rates = (cdi0 + np.cumsum(changes_bps) / 1e4).tolist()
    out = contracts[["contract_code", "expiry_date", "du", "rate_252"]].copy()
    out["hyp_rate"] = out["du"].apply(lambda du: rate_from_path(int(du), cdi0, eff_dus, rates))
    out["diff_bps"] = (out["hyp_rate"] - out["rate_252"]) * 1e4
    return out
