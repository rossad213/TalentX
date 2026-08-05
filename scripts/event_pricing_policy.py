#!/usr/bin/env python3
"""Apply TalentX's explainable, stability-aware event pricing policy.

This post-processing step is intentionally deterministic and idempotent. It only
acts on records with a supported price event, starts from the recorded
``previousMarketPrice``, and always produces the same result from the same
inputs. No random movement is introduced.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

MAX_GAME_MOVE_PCT = 2.5
PERFORMANCE_SHARE = 0.70
OUTCOME_SHARE = 0.15
MARKET_SHARE = 0.10
LONG_TERM_SHARE = 0.05
POLICY_VERSION = "1.0-explainable-event-pricing"


def clamp(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    return max(low, min(high, number))


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def volatility_profile(record: dict[str, Any]) -> tuple[str, float]:
    games = max(0.0, number(record.get("professionalGames")) or 0.0)
    stage = str(record.get("careerStage") or "").lower()
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    consistency = clamp(metrics.get("consistency", 70), 0, 100)
    if "rookie" in stage or games < 20:
        return "High", 1.15
    if "emerging" in stage or games < 80:
        return "Medium-high", 1.05
    if consistency >= 85 and games >= 200:
        return "Low", 0.72
    if consistency >= 75 and games >= 100:
        return "Medium-low", 0.84
    return "Medium", 0.94


def outcome_component(record: dict[str, Any], raw_move: float, performance_move: float) -> tuple[float, str]:
    residual = clamp(raw_move - performance_move, -0.15, 0.15)
    if residual > 0.025:
        return residual, "Team won"
    if residual < -0.025:
        return residual, "Team lost"
    return 0.0, "Team result had little effect"


def explainable_event_move(record: dict[str, Any]) -> tuple[float, dict[str, Any]] | None:
    event_id = str(record.get("lastPriceEventId") or "").strip()
    previous_price = number(record.get("previousMarketPrice"))
    raw_move = number(record.get("lastGameMovePct"))
    if not event_id or previous_price is None or previous_price <= 0 or raw_move is None:
        return None

    performance_delta = clamp(record.get("lastGamePerformanceDeltaPct", 0), -150, 150)
    performance_signal = clamp(performance_delta / 100.0 * 2.25, -2.25, 2.25)
    outcome_signal, outcome_label = outcome_component(record, raw_move, performance_signal)
    demand_premium = clamp(record.get("demandPremiumPct", 0), -20, 20)
    market_signal = clamp(demand_premium * 0.0125, -0.20, 0.20)
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    long_term_score = (
        clamp(metrics.get("achievements", 50), 0, 100) * 0.35
        + clamp(metrics.get("potential", 50), 0, 100) * 0.25
        + clamp(metrics.get("availability", 70), 0, 100) * 0.20
        + clamp(metrics.get("consistency", 70), 0, 100) * 0.20
    )
    long_term_signal = clamp((long_term_score - 60.0) / 40.0 * 0.12, -0.12, 0.12)
    tier, volatility_multiplier = volatility_profile(record)
    combined = (
        performance_signal * PERFORMANCE_SHARE
        + outcome_signal * OUTCOME_SHARE
        + market_signal * MARKET_SHARE
        + long_term_signal * LONG_TERM_SHARE
    )
    final_move = clamp(combined * volatility_multiplier, -MAX_GAME_MOVE_PCT, MAX_GAME_MOVE_PCT)
    if abs(final_move) < 0.05 and abs(performance_delta) >= 1:
        final_move = 0.05 if performance_delta > 0 else -0.05
    final_move = round(final_move, 2)

    if performance_delta >= 8:
        headline = "Strong game performance"
        performance_text = "Played better than expected"
    elif performance_delta <= -8:
        headline = "Below expectations"
        performance_text = "Played below expectations"
    else:
        headline = "Game performance update"
        performance_text = "Performance was close to expectations"

    explanation = {
        "version": POLICY_VERSION,
        "eventId": event_id,
        "event": str(record.get("lastPriceEvent") or "Completed game"),
        "eventAt": record.get("lastPriceEventAt"),
        "headline": headline,
        "summary": [performance_text, outcome_label],
        "direction": "increased" if final_move > 0 else "decreased" if final_move < 0 else "held steady",
        "finalMovePct": final_move,
        "confidence": round(clamp(record.get("pricingConfidence", record.get("dataConfidence", 0.5)), 0, 1), 2),
        "volatilityTier": tier,
        "components": {
            "performancePct": round(performance_signal * PERFORMANCE_SHARE * volatility_multiplier, 3),
            "teamOutcomePct": round(outcome_signal * OUTCOME_SHARE * volatility_multiplier, 3),
            "marketDemandPct": round(market_signal * MARKET_SHARE * volatility_multiplier, 3),
            "longTermAnchorPct": round(long_term_signal * LONG_TERM_SHARE * volatility_multiplier, 3),
        },
        "performanceVsExpectationPct": round(performance_delta, 2),
    }
    return final_move, explanation


def apply_policy(record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    result = dict(record)
    event_id = str(result.get("lastPriceEventId") or "").strip()
    prior_explanation = result.get("priceExplanation")
    if (
        event_id
        and isinstance(prior_explanation, dict)
        and prior_explanation.get("version") == POLICY_VERSION
        and str(prior_explanation.get("eventId") or "") == event_id
    ):
        return result, False

    calculated = explainable_event_move(result)
    if calculated is None:
        return result, False
    move, explanation = calculated
    previous_price = float(result["previousMarketPrice"])
    new_price = round(previous_price * (1.0 + move / 100.0), 2)
    changed = (
        round(number(result.get("marketPrice")) or 0.0, 2) != new_price
        or round(number(result.get("lastGameMovePct")) or 0.0, 2) != move
        or result.get("priceExplanation") != explanation
    )
    result["marketPrice"] = new_price
    result["dailyChange"] = move
    result["hourlyChangePct"] = move
    result["lastGameMovePct"] = move
    result["priceExplanation"] = explanation
    result["volatilityTier"] = explanation["volatilityTier"]
    trend = [number(item) for item in result.get("trend", []) if number(item) is not None]
    if trend:
        trend[-1] = new_price
    else:
        trend = [previous_price, new_price]
    result["trend"] = [round(float(item), 2) for item in trend[-18:]]
    return result, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    updated: list[dict[str, Any]] = []
    changed = 0
    explained = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        record, did_change = apply_policy(item)
        updated.append(record)
        changed += int(did_change)
        explained += int(bool(record.get("priceExplanation")))
    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Applied explainable pricing policy to {explained:,} event records; changed {changed:,} records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
