#!/usr/bin/env python3
"""NFL production-first pricing layer for the TalentX Sports refresh.

The NFL market is event-driven, but verified football production owns the price
level. Each completed game refreshes a position-normalized production target;
the verified game surprise then moves from that target. This prevents stale
absolute prices, audience, age, or veteran status from outranking production.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import hourly_price_refresh as refresh
import hourly_price_refresh_nfl as nfl
import hourly_price_refresh_nfl_opportunity as opportunity
import nfl_production_pricing as production_pricing
import repair_nfl_persisted_opportunity_prices as persisted_opportunity
from game_event_history import attach_price_events

NFL_RB_MODEL_VERSION = "1.5-nfl-production-primary-opportunity-floor"
RB_RECEIVING_TD_RECENT_WEIGHT = 8.0
RB_RECEIVING_TD_CAREER_WEIGHT = 4.0

_original_signal_bundle = refresh.signal_bundle
_original_cohort_key = refresh.cohort_key
_original_apply_moves = None
_reliability = None


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


def nfl_pricing_role_group(record: dict[str, Any]) -> str:
    """Use true NFL position peer groups for pricing percentiles.

    The production formulas may remain shared across related roles, but a tight
    end should not be percentile-ranked against wide receivers and a linebacker
    should not be ranked against every defensive position.
    """
    if str(record.get("leagueOrMedium") or "").upper() != "NFL":
        return _original_cohort_key(record)[1]
    role = str(record.get("role") or "").lower().strip()
    compact = f" {role} "
    if "quarterback" in role or role == "qb":
        return "QB"
    if "running back" in role or "fullback" in role or role in {"rb", "fb"}:
        return "RB"
    if "tight end" in role or role == "te":
        return "TE"
    if "wide receiver" in role or role == "wr" or ("receiver" in role and "tight" not in role):
        return "WR"
    if any(token in role for token in ("offensive tackle", "offensive guard", "guard", "center", "offensive line")):
        return "OL"
    if any(token in role for token in ("kicker", "punter", "long snapper")):
        return "ST"
    if "cornerback" in role or role == "cb":
        return "CB"
    if "safety" in role or role in {"fs", "ss"}:
        return "S"
    if "linebacker" in role or role in {"lb", "ilb", "olb", "mlb"} or " lb " in compact:
        return "LB"
    if any(token in role for token in ("defensive end", "edge rusher")) or role in {"de", "edge"}:
        return "EDGE"
    if any(token in role for token in ("defensive tackle", "nose tackle")) or role in {"dt", "nt"}:
        return "IDL"
    return "DEF"


def nfl_pricing_cohort_key(record: dict[str, Any]) -> tuple[str, str]:
    if str(record.get("leagueOrMedium") or "").upper() != "NFL":
        return _original_cohort_key(record)
    return ("NFL", nfl_pricing_role_group(record))


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def production_first_apply_game_market_moves(
    old_record,
    new_record,
    item,
    events,
    _legacy_max_game_move_pct,
    refreshed_at,
):
    """Re-anchor NFL price to current production when a verified game occurs."""
    if str(old_record.get("leagueOrMedium") or new_record.get("leagueOrMedium") or "").upper() != "NFL":
        return _original_apply_moves(
            old_record, new_record, item, events, _legacy_max_game_move_pct, refreshed_at
        )

    old_price = max(0.01, float(old_record.get("marketPrice") or new_record.get("marketPrice") or 0.01))
    score, production_target, explanation = production_pricing.production_fair_value(new_record)
    model_target = max(0.01, float(production_target or new_record.get("marketPrice") or old_price))
    price = old_price
    prior_trend = [float(value) for value in old_record.get("trend", []) if isinstance(value, (int, float))]
    trend = [round(value, 2) for value in prior_trend] or [round(old_price, 2)] * 18
    event_results = []
    seen_keys: set[str] = set()
    production_anchor_applied = False

    for event in sorted(events, key=lambda value: str(value.get("startedAt") or "")):
        key = str(event.get("eventKey") or event.get("eventId") or "").strip()
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)

        event_move, evidence = _reliability.results_based_game_event_move(old_record, item, event, None)
        if not evidence.get("comparable"):
            event_results.append({**event, **evidence, "movePct": 0.0})
            continue
        if event_move <= -100.0:
            event_results.append({**event, **evidence, "comparable": False, "reason": "Invalid move would make price non-positive", "movePct": 0.0})
            continue

        before = price
        event_base = model_target if not production_anchor_applied else price
        anchor_move = ((event_base / before) - 1.0) * 100.0 if before > 0 else 0.0
        next_price = max(0.01, round(event_base * (1.0 + event_move / 100.0), 2))
        actual_move = round((next_price / before - 1.0) * 100.0, 3)
        price = next_price
        trend = trend[-17:] + [price]
        production_anchor_applied = True
        event_results.append({
            **event,
            **evidence,
            "modelMovePct": round(event_move, 3),
            "movePct": actual_move,
            "priceBefore": round(before, 2),
            "priceAfter": price,
            "productionAnchorPrice": round(event_base, 2),
            "productionAnchorMovePct": round(anchor_move, 3),
            "nflProductionPriceModelVersion": production_pricing.MODEL_VERSION,
        })

    change_pct = round((price / old_price - 1.0) * 100.0, 2)
    result = dict(new_record)
    result["modelTargetPrice"] = round(model_target, 2)
    result["previousMarketPrice"] = round(old_price, 2)
    result["marketPrice"] = round(price, 2)
    result["dailyChange"] = change_pct
    result["hourlyChangePct"] = change_pct
    result["lastPriceRefreshAt"] = refreshed_at
    result["trend"] = trend
    result["eventPricingModel"] = getattr(_reliability, "RESULTS_MODEL_VERSION", result.get("eventPricingModel"))
    result["nflProductionPriceModelVersion"] = production_pricing.MODEL_VERSION
    if score is not None:
        result["nflProductionScore"] = score
    if explanation is not None:
        result["nflProductionPricing"] = explanation
        result["fairValue"] = round(model_target, 2)
        result["fundamentalValue"] = round(model_target, 2)
    result.pop("eventPriceBand", None)

    comparable = [event for event in event_results if event.get("comparable")]
    if comparable:
        latest = comparable[-1]
        result["lastPriceEventAt"] = latest.get("startedAt") or refreshed_at
        result["lastPriceEvent"] = str(latest.get("name") or "Completed game")
        result["lastPriceEventId"] = latest.get("eventKey") or latest.get("eventId")
        result["lastGameMovePct"] = latest.get("movePct")
        result["lastGameSurpriseMovePct"] = latest.get("modelMovePct")
        result["lastGamePerformanceDeltaPct"] = latest.get("performanceDeltaPct")
        result["lastGameStats"] = latest.get("stats", {})
        result["volatilityTier"] = latest.get("volatilityTier") or result.get("volatilityTier")

    result = attach_price_events(old_record, result, event_results)
    return result, change_pct, event_results


def install_rb_receiving_td_credit():
    global _original_apply_moves, _reliability
    nfl.NFL_EXPECTATION_MODEL_VERSION = NFL_RB_MODEL_VERSION
    refresh.signal_bundle = signal_bundle_with_rb_receiving_td_credit
    refresh.cohort_key = nfl_pricing_cohort_key
    opportunity.install_opportunity_protection()
    reliability = nfl.install_nfl_layer()
    _reliability = reliability
    _original_apply_moves = reliability.apply_game_market_moves_with_history
    reliability.apply_game_market_moves_with_history = production_first_apply_game_market_moves
    refresh.apply_game_market_moves = production_first_apply_game_market_moves
    return reliability


if __name__ == "__main__":
    reliability = install_rb_receiving_td_credit()
    reliability.normalize_sports_tickers()
    reliability.repair_sports_price_integrity()
    reliability.seed_rookie_ipo_history()
    nfl.migrate_latest_nfl_expectations()
    # First clean up any historical reserve-opportunity distortion, then apply
    # the production-first market rebase before processing the next live game.
    persisted_opportunity.repair_catalog(Path("data/current_catalog.json"))
    from repair_nfl_production_prices import repair_catalog as repair_nfl_production_catalog
    repair_nfl_production_catalog(Path("data/current_catalog.json"))
    raise SystemExit(refresh.main())
