#!/usr/bin/env python3
"""NFL evidence adapter for the TalentX Sports refresh.

NFL football statistics are translated into comparable production signals and
NFL-specific expectations, but the resulting listing follows the same market
architecture as NBA: the shared v2 fundamental model owns fair value, and each
unique completed game moves the prior market price by performance versus the
player's pre-game expectation.

Position remains evidence context rather than a direct price premium. NFL does
not re-anchor the market price to a separate production target before a game.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import hourly_price_refresh as refresh
import hourly_price_refresh_nfl as nfl
import hourly_price_refresh_nfl_opportunity as opportunity
import repair_nfl_persisted_opportunity_prices as persisted_opportunity

NFL_RB_MODEL_VERSION = "1.8-nfl-position-normalized-established-window"
RB_RECEIVING_TD_RECENT_WEIGHT = 8.0
RB_RECEIVING_TD_CAREER_WEIGHT = 4.0

_original_signal_bundle = refresh.signal_bundle
_original_cohort_key = refresh.cohort_key


def is_nfl_running_back(record: dict[str, Any]) -> bool:
    """Stat-translation helper only; this does not alter price by position."""
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
    """Return common production signals with missing RB receiving-TD credit."""
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


def nfl_position_cohort_key(record: dict[str, Any]) -> tuple[str, str]:
    """Use one authoritative position-aware cohort path for every NFL player.

    Quarterbacks, running backs, receivers/tight ends, defenders, offensive
    linemen and special-teamers are normalized against comparable football roles
    before all resulting 0-100 scores enter the universal TalentX value scale.
    """
    return _original_cohort_key(record)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def install_rb_receiving_td_credit():
    """Install NFL evidence/expectation logic while preserving the shared NBA-style market path."""
    nfl.NFL_EXPECTATION_MODEL_VERSION = NFL_RB_MODEL_VERSION
    refresh.signal_bundle = signal_bundle_with_rb_receiving_td_credit
    refresh.cohort_key = nfl_position_cohort_key
    opportunity.install_opportunity_protection()
    reliability = nfl.install_nfl_layer()

    # Keep the shared results-based event application used by NBA/WNBA:
    # prior market price -> unique verified game move -> new market price.
    # NFL-specific code changes the evidence and expectation, not the price base.
    refresh.apply_game_market_moves = reliability.apply_game_market_moves_with_history
    return reliability


if __name__ == "__main__":
    reliability = install_rb_receiving_td_credit()
    reliability.normalize_sports_tickers()
    reliability.repair_sports_price_integrity()
    reliability.seed_rookie_ipo_history()
    nfl.migrate_latest_nfl_expectations()
    # Repair only known historical opportunity distortions. Do not rebase the
    # entire NFL to a separate production-price model; v2 + the event ledger own
    # the price path, matching NBA behavior.
    persisted_opportunity.repair_catalog(Path("data/current_catalog.json"))
    raise SystemExit(refresh.main())