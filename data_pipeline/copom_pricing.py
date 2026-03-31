"""
COPOM Meeting Pricing from the DI Curve
=========================================

METHODOLOGY
-----------
The DI futures curve is built from settlement prices of DI1 contracts that
expire on the first business day of each month.  Between any two consecutive
DI vertices the market implies a flat forward rate.  When a COPOM decision
falls inside a period, that forward rate is actually the geometric average of:

    • the SELIC rate in force BEFORE the meeting takes effect
    • the SELIC rate in force AFTER  the meeting takes effect

We iterate left-to-right through the curve, and within each period we solve
for the implied post-meeting rate using:

    DF_period = (1 + r_before)^(Δdu_before/252) × (1 + r_after)^(Δdu_after/252)

    => r_after = [ DF_period / (1 + r_before)^(Δdu_before/252) ]^(252/Δdu_after) - 1

Key timing detail
-----------------
COPOM announces Wednesday night.  The new SELIC takes effect the NEXT BUSINESS
DAY (Thursday).  We use `effective_date` from the bcb_meetings table, which is
already the Thursday date (= bizday_offset(decision_date, +1)).

Hypothesis simulation
---------------------
Given a user-defined path of rate changes (bps per meeting), we reconstruct the
implied spot DI curve that would be consistent with that path, allowing a
direct comparison with the market curve.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from bizdays import Calendar

_CAL = Calendar.load("ANBIMA")


# ---------------------------------------------------------------------------
# Core: extract implied SELIC path from DI curve
# ---------------------------------------------------------------------------

def calculate_copom_pricing(
    df_curve: pd.DataFrame,
    df_meetings: pd.DataFrame,
    ref_date: date,
    selic_rate: float,
) -> pd.DataFrame:
    """Derive the SELIC rate implied at each upcoming COPOM meeting.

    Args:
        df_curve:    DI curve vertices — must have columns du, rate_252.
        df_meetings: COPOM meetings — must have decision_date, effective_date.
        ref_date:    Reference / pricing date (date of the DI curve snapshot).
        selic_rate:  Current SELIC target rate as a decimal (e.g. 0.1275).

    Returns:
        DataFrame with one row per meeting, columns:
            decision_date, effective_date, effective_du,
            rate_before (%), rate_after (%), change_bps
    """
    curve = df_curve[["du", "rate_252"]].copy()
    curve["du"] = curve["du"].astype(int)
    curve["rate_252"] = curve["rate_252"].astype(float)
    curve = curve.sort_values("du").reset_index(drop=True)

    if curve.empty:
        return pd.DataFrame()

    # Precompute spot discount factors: DF(0, i) = (1 + r_i)^(du_i/252)
    dfs: dict[int, float] = {0: 1.0}
    for _, row in curve.iterrows():
        du, r = int(row["du"]), float(row["rate_252"])
        dfs[du] = (1.0 + r) ** (du / 252.0)

    max_du = max(dfs.keys())

    # Compute effective_du for each meeting (biz days from ref_date)
    meetings = df_meetings.copy()
    meetings["effective_date_d"] = pd.to_datetime(meetings["effective_date"]).dt.date
    meetings["effective_du"] = meetings["effective_date_d"].apply(
        lambda d: _CAL.bizdays(ref_date, d) if d > ref_date else 0
    )
    meetings = (
        meetings[
            (meetings["effective_du"] > 0) & (meetings["effective_du"] <= max_du)
        ]
        .sort_values("effective_du")
        .reset_index(drop=True)
    )

    if meetings.empty:
        return pd.DataFrame()

    # Iterate through DI periods [du_start, du_end]
    boundaries = [0] + list(curve["du"])
    results: list[dict] = []
    current_rate = selic_rate  # SELIC before first meeting

    for i in range(1, len(boundaries)):
        du_start = boundaries[i - 1]
        du_end   = boundaries[i]

        # Forward discount factor for this period
        df_period = dfs[du_end] / dfs[du_start]

        # Meetings whose effective date falls in (du_start, du_end]
        mask = (meetings["effective_du"] > du_start) & (meetings["effective_du"] <= du_end)
        period_meetings = meetings[mask].sort_values("effective_du")

        remaining_df = df_period
        seg_start_du = du_start

        for _, mtg in period_meetings.iterrows():
            du_m         = int(mtg["effective_du"])
            delta_before = du_m - seg_start_du   # biz days at current_rate
            delta_after  = du_end - du_m          # biz days at rate_after

            # Consume the "before" segment
            if delta_before > 0:
                remaining_df /= (1.0 + current_rate) ** (delta_before / 252.0)

            if delta_after > 0:
                rate_after = remaining_df ** (252.0 / delta_after) - 1.0
            else:
                # Meeting falls exactly on vertex — look one period ahead if possible
                # Approximate: carry forward current rate (very rare edge case)
                rate_after = current_rate

            results.append({
                "decision_date": mtg["decision_date"],
                "effective_date": mtg["effective_date"],
                "effective_du":  du_m,
                "rate_before":   round(current_rate * 100, 4),
                "rate_after":    round(rate_after * 100, 4),
                "change_bps":    round((rate_after - current_rate) * 10_000, 1),
            })

            current_rate = rate_after
            seg_start_du = du_m

        # After all meetings in this period, the remainder of the period
        # runs at whatever rate is implied by the remaining discount factor.
        remaining_du = du_end - seg_start_du
        if remaining_du > 0 and remaining_df > 0:
            current_rate = remaining_df ** (252.0 / remaining_du) - 1.0

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Hypothesis: build implied DI curve from a user-defined SELIC path
# ---------------------------------------------------------------------------

def build_hypothesis_curve(
    df_curve: pd.DataFrame,
    df_meetings: pd.DataFrame,
    ref_date: date,
    selic_rate: float,
    hypothesis: dict[str, float],
) -> pd.DataFrame:
    """Compute the implied DI spot curve from a user-defined rate-change path.

    Args:
        df_curve:    Actual DI curve (used only for vertex dates / du values).
        df_meetings: COPOM meetings with decision_date, effective_date.
        ref_date:    Pricing date.
        selic_rate:  Current SELIC as a decimal.
        hypothesis:  {decision_date_iso: change_bps} — user's expected changes.

    Returns:
        DataFrame with columns: contract_code, expiry_date, du, rate_252
        representing the hypothetical DI curve.
    """
    curve = df_curve[["contract_code", "expiry_date", "du", "rate_252"]].copy()
    curve["du"] = curve["du"].astype(int)
    curve = curve.sort_values("du").reset_index(drop=True)

    meetings = df_meetings.copy()
    meetings["effective_date_d"] = pd.to_datetime(meetings["effective_date"]).dt.date
    meetings["effective_du"] = meetings["effective_date_d"].apply(
        lambda d: _CAL.bizdays(ref_date, d) if d > ref_date else 0
    )
    meetings = meetings[meetings["effective_du"] > 0].sort_values("effective_du")

    # Build the SELIC path implied by the hypothesis
    # selic_path: list of (effective_du, rate) sorted ascending
    selic_path: list[tuple[int, float]] = [(0, selic_rate)]
    current = selic_rate
    for _, mtg in meetings.iterrows():
        chg_bps = hypothesis.get(str(mtg["decision_date"]), 0.0)
        current = current + chg_bps / 10_000.0
        selic_path.append((int(mtg["effective_du"]), current))

    def _spot_rate_at(du: int) -> float:
        """Compute the implied spot rate at *du* from the piecewise-flat SELIC path."""
        if du <= 0:
            return selic_rate
        log_df = 0.0
        prev_du, prev_rate = 0, selic_rate
        for eff_du, rate in selic_path[1:]:
            if eff_du >= du:
                break
            seg = eff_du - prev_du
            log_df += seg * np.log1p(prev_rate) / 252.0
            prev_du, prev_rate = eff_du, rate
        # Remaining segment
        log_df += (du - prev_du) * np.log1p(prev_rate) / 252.0
        df_val = np.exp(log_df)
        return df_val ** (252.0 / du) - 1.0

    hyp = curve.copy()
    hyp["rate_252"] = hyp["du"].apply(_spot_rate_at)
    hyp["rate_pct"] = hyp["rate_252"] * 100
    return hyp
