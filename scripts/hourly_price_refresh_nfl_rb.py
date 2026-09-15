#!/usr/bin/env python3
"""NFL running-back scoring correction layered onto the existing NFL refresh.

This keeps the existing NFL season-history expectation model, result-surprise
curve, team-result effect, and uncapped pricing intact. It fixes the RB receiving
TD omission and installs the narrow role-adjusted opportunity protection used to
prevent tiny reserve baselines from creating outsized injury-replacement moves.
"""
from __future__ import annotations

from typing import Any

import hourly_price_refresh as refresh
import hourly_price_refresh_nfl as nfl
import hourly_price_refresh_nfl_opportunity as opportunity

NFL_RB_MODEL_VERSION = "1.4-nfl-opportunity-floor-rb-receiving-td-credit"
RB_RECEIVING_TD_RECENT_WEIGHT = 8.0
RB_RECEIVING_TD_CAREER_WEIGHT = 4.0

_original_signal_bundle = refresh.signal_bundle


def is_nfl_running_back(record: dict[str, Any]) -> bool:
    if str(record.get("leagueOrMedium") or "") != "NFL":
        return False
    role = str(record.get("role") or "").lower().strip()
    return (
        role in {"rb", "fb"}
        or "running back" in role
        or "fullback" in role
        or role.endswith(" rb")
        or role.endswith(" fb")
    )


def signal_bundle_with_rb_receiving_td_credit(
    record: dict[str, Any],
    recent: dict[str, float],
    career: dict[str, float],
    awards: float,
) -> dict[str, float]:
    """Return the normal signal bundle plus missing RB receiving-TD credit."""
    signals = dict(_original_signal_bundle(record, recent, career, awards))
    if not is_nfl_running_back(record):
        return signals

    recent_receiving_tds = refresh.stat_value(recent, "receivingTouchdowns") or 0.0
    career_receiving_tds = refresh.stat_value(career, "receivingTouchdowns") or 0.0
    signals["recentProduction"] = float(signals.get("recentProduction") or 0.0) + (
        float(recent_receiving_tds) * RB_RECEIVING_TD_RECENT_WEIGHT
    )
    signals["careerProduction"] = float(signals.get("careerProduction") or 0.0) + (
        float(career_receiving_tds) * RB_RECEIVING_TD_CAREER_WEIGHT
    )
    return signals


def install_rb_receiving_td_credit():
    # Bump the NFL model version so the targeted opportunity migration can mark
    # repaired events once while leaving normal NFL listings untouched. The
    # opportunity module owns the denominator guard and zero-game-gap behavior.
    nfl.NFL_EXPECTATION_MODEL_VERSION = NFL_RB_MODEL_VERSION
    refresh.signal_bundle = signal_bundle_with_rb_receiving_td_credit
    opportunity.install_opportunity_protection()
    return nfl.install_nfl_layer()


if __name__ == "__main__":
    reliability = install_rb_receiving_td_credit()
    reliability.normalize_sports_tickers()
    reliability.repair_sports_price_integrity()
    reliability.seed_rookie_ipo_history()
    nfl.migrate_latest_nfl_expectations()
    raise SystemExit(refresh.main())
