"""
COPOM Meeting Pricing from the DI Curve
=========================================

METHODOLOGY
-----------
The DI index compounds daily at the overnight CDI rate.  The CDI tracks the
SELIC target and only changes when COPOM decides a new rate (effective the
next business day after the decision).

For a DI contract expiring at du_T business days from today, the settlement
price implies:

    DF(0, T) = (1 + r_spot_T)^(du_T / 252)

This discount factor must equal the exact product of daily compoundings:

    DF(0, T) = (1 + SELIC_0)^(n_0/252)                   ← segment before meeting 1
             × (1 + SELIC_1)^(n_1/252)                   ← between meetings 1 and 2
             × ...
             × (1 + SELIC_k)^(n_k/252)                   ← from last meeting to T

where n_i are the ANBIMA business-day counts for each segment and
SELIC_0 is the CURRENT overnight rate (the target set at the LAST COPOM meeting).

ALGORITHM
---------
Iterate through DI curve periods left-to-right.  Within each period, for every
COPOM meeting we:
  1. Consume the discount factor for the pre-meeting segment at the known rate.
  2. Solve algebraically for the post-meeting rate from the residual DF.
  3. Carry that new rate into the next segment/period.

This guarantees that, for every DI contract, the implied SELIC path compounds
back to exactly the DI spot rate — i.e. the pricing is internally consistent.

KEY TIMING DETAIL
-----------------
The new SELIC takes effect the NEXT BUSINESS DAY after the COPOM decision
(Wednesday night → Thursday morning).  We use effective_date from
bcb_meetings, which is already computed as next_biz_day(decision_date).
"""

from __future__ import annotations

from datetime import date

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

    The implied rates are calibrated so that compounding the SELIC path
    at each segment exactly reproduces every DI contract's discount factor.

    Args:
        df_curve:    DI curve vertices — must have columns du, rate_252.
        df_meetings: COPOM meetings — must have decision_date, effective_date.
        ref_date:    Reference / pricing date (date of the DI curve snapshot).
        selic_rate:  Current SELIC target (decimal).  This is the rate currently
                     in force, set at the LAST COPOM meeting.

    Returns:
        DataFrame with one row per meeting within the curve horizon:
            decision_date, effective_date, effective_du,
            rate_before (%), rate_after (%), change_bps,
            verify_rate (%) — spot rate recomputed from implied path (≈ DI rate)
    """
    curve = (
        df_curve[["du", "rate_252"]].copy()
        .assign(du=lambda d: d["du"].astype(int),
                rate_252=lambda d: d["rate_252"].astype(float))
        .sort_values("du")
        .reset_index(drop=True)
    )

    if curve.empty:
        return pd.DataFrame()

    # Spot discount factors from DI curve: DF(0, du_i) = (1 + r_i)^(du_i/252)
    spot_dfs: dict[int, float] = {0: 1.0}
    for _, row in curve.iterrows():
        du, r = int(row["du"]), float(row["rate_252"])
        spot_dfs[du] = (1.0 + r) ** (du / 252.0)

    max_du = max(spot_dfs.keys())

    # Compute effective_du for each meeting
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

    # -----------------------------------------------------------------------
    # Iterate period by period through the curve
    # -----------------------------------------------------------------------
    boundaries = [0] + list(curve["du"])
    results: list[dict] = []
    current_rate = selic_rate   # ← SELIC currently in force (overnight rate)
    acc_df = 1.0                # accumulated discount factor from t=0

    for i in range(1, len(boundaries)):
        du_start = boundaries[i - 1]
        du_end   = boundaries[i]

        # Forward DF for this period derived from spot curve
        period_df = spot_dfs[du_end] / spot_dfs[du_start]

        # Meetings with effective date in (du_start, du_end]
        mask = (
            (meetings["effective_du"] > du_start) &
            (meetings["effective_du"] <= du_end)
        )
        period_meetings = meetings[mask].sort_values("effective_du")

        remaining_df = period_df
        seg_start_du = du_start

        for _, mtg in period_meetings.iterrows():
            du_m         = int(mtg["effective_du"])
            delta_before = du_m - seg_start_du    # biz days at current_rate
            delta_after  = du_end - du_m           # biz days at implied rate_after

            # Consume pre-meeting segment at current SELIC
            if delta_before > 0:
                remaining_df /= (1.0 + current_rate) ** (delta_before / 252.0)

            if delta_after > 0:
                # Solve: remaining_df = (1 + rate_after)^(delta_after/252)
                rate_after = remaining_df ** (252.0 / delta_after) - 1.0
            else:
                rate_after = current_rate  # edge case: meeting on last day of period

            # Verification: accumulated DF to du_m compounded at rate_after for
            # the rest of the period should equal spot_dfs[du_end]
            # (This is guaranteed by construction — we note it for transparency.)
            verify_df = acc_df * spot_dfs[du_start] * (
                (1.0 + current_rate) ** (delta_before / 252.0)
            ) if delta_before > 0 else acc_df * spot_dfs[du_start]

            results.append({
                "decision_date":  mtg["decision_date"],
                "effective_date": mtg["effective_date"],
                "effective_du":   du_m,
                "rate_before":    round(current_rate * 100, 4),
                "rate_after":     round(rate_after  * 100, 4),
                "change_bps":     round((rate_after - current_rate) * 10_000, 1),
            })

            current_rate = rate_after
            seg_start_du = du_m

        # Flat rate for remainder of period (no more meetings in this period)
        remaining_du = du_end - seg_start_du
        if remaining_du > 0 and remaining_df > 0:
            current_rate = remaining_df ** (252.0 / remaining_du) - 1.0

    # -----------------------------------------------------------------------
    # Verification column: recompute DI spot rate from implied SELIC path
    # for each meeting's horizon vertex (nearest contract after the meeting)
    # -----------------------------------------------------------------------
    df_result = pd.DataFrame(results)
    if df_result.empty:
        return df_result

    df_result["verify_rate"] = df_result["effective_du"].apply(
        lambda tgt: round(_recompute_spot_rate(
            tgt, curve, selic_rate, df_result
        ) * 100, 4)
    )

    return df_result


def _recompute_spot_rate(
    target_du: int,
    curve: pd.DataFrame,
    selic_rate: float,
    df_result: pd.DataFrame,
) -> float:
    """Recompute the spot rate at target_du from the implied SELIC path.

    Should match the DI curve's spot rate at that vertex — confirms calibration.
    """
    # Build SELIC path up to this point from results already computed
    selic_path = [(0, selic_rate)]
    for _, row in df_result[df_result["effective_du"] <= target_du].iterrows():
        selic_path.append((int(row["effective_du"]), float(row["rate_after"]) / 100.0))

    return _spot_from_path(target_du, selic_path, selic_rate)


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

    Uses EXACT discrete compounding: (1 + r)^(n/252) — same convention as DI.

    Args:
        df_curve:    Actual DI curve (vertex dates / du values used for output).
        df_meetings: COPOM meetings with decision_date, effective_date.
        ref_date:    Pricing date.
        selic_rate:  Current SELIC as decimal.
        hypothesis:  {decision_date_iso: change_bps} user's expected changes.

    Returns:
        DataFrame with contract_code, expiry_date, du, rate_252, rate_pct.
    """
    curve = (
        df_curve[["contract_code", "expiry_date", "du", "rate_252"]].copy()
        .assign(du=lambda d: d["du"].astype(int))
        .sort_values("du")
        .reset_index(drop=True)
    )

    meetings = df_meetings.copy()
    meetings["effective_date_d"] = pd.to_datetime(meetings["effective_date"]).dt.date
    meetings["effective_du"] = meetings["effective_date_d"].apply(
        lambda d: _CAL.bizdays(ref_date, d) if d > ref_date else 0
    )
    meetings = meetings[meetings["effective_du"] > 0].sort_values("effective_du")

    # Build SELIC path from hypothesis
    selic_path: list[tuple[int, float]] = [(0, selic_rate)]
    current = selic_rate
    for _, mtg in meetings.iterrows():
        chg_bps = hypothesis.get(str(mtg["decision_date"]), 0.0)
        current = current + chg_bps / 10_000.0
        selic_path.append((int(mtg["effective_du"]), current))

    # For each DI vertex, compute spot rate using exact discrete compounding
    hyp = curve.copy()
    hyp["rate_252"] = hyp["du"].apply(
        lambda du: _spot_from_path(du, selic_path, selic_rate)
    )
    hyp["rate_pct"] = hyp["rate_252"] * 100
    return hyp


def _spot_from_path(
    du: int,
    selic_path: list[tuple[int, float]],
    selic_rate: float,
) -> float:
    """Compute the DI spot rate at *du* from a piecewise-flat SELIC path.

    Uses exact discrete compounding: DF = Π (1 + r_i)^(Δdu_i / 252)
    Spot rate at du = DF^(252/du) - 1
    """
    if du <= 0:
        return selic_rate

    df_acc = 1.0
    prev_du, prev_rate = 0, selic_rate

    for eff_du, rate in selic_path[1:]:
        if eff_du >= du:
            break
        seg = eff_du - prev_du
        df_acc *= (1.0 + prev_rate) ** (seg / 252.0)
        prev_du, prev_rate = eff_du, rate

    # Remaining segment from last event to du
    seg = du - prev_du
    df_acc *= (1.0 + prev_rate) ** (seg / 252.0)

    return df_acc ** (252.0 / du) - 1.0
