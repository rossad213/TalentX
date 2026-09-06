#!/usr/bin/env python3
"""Apply TalentX's explainable, results-proportional event pricing policy.

The policy is deterministic and idempotent. It introduces no random movement and
has no fixed event-percentage ceiling. Ordinary performance variance is damped by
a logarithmic surprise curve while increasingly exceptional verified results can
produce increasingly large modeled moves.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from results_event_pricing import result_move_from_delta, result_sensitivity

POLICY_VERSION = "2.0-explainable-results-proportional-uncapped"


def clamp(value: Any, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    if not math.isfinite(parsed):
        parsed = 0.0
    return max(low, min(high, parsed))


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def volatility_profile(record: dict[str, Any]) -> tuple[str, float]:
    """Compatibility wrapper around the shared uncapped sensitivity model."""
    return result_sensitivity(record)


def outcome_component(raw_move: float, performance_move: float) -> tuple[float, str]:
    """Estimate the small non-performance remainder for explanation purposes."""
    residual = clamp(raw_move - performance_move, -0.20, 0.20)
    if residual > 0.025:
        return residual, "Team result added a small positive effect"
    if residual < -0.025:
        return residual, "Team result added a small negative effect"
    return 0.0, "Team result had little additional effect"


def explainable_event_move(record: dict[str, Any]) -> tuple[float, dict[str, Any]] | None:
    event_id = str(record.get("lastPriceEventId") or "").strip()
    previous_price = number(record.get("previousMarketPrice"))
    raw_move = number(record.get("lastGameMovePct"))
    if not event_id or previous_price is None or previous_price <= 0 or raw_move is None:
        return None

    performance_delta = number(record.get("lastGamePerformanceDeltaPct")) or 0.0
    tier, sensitivity = result_sensitivity(record)
    performance_signal = result_move_from_delta(performance_delta) * sensitivity
    outcome_signal, outcome_label = outcome_component(raw_move, performance_signal)

    # Market demand and long-term quality are context/sensitivity terms rather
    # than independent reasons to move after a game. That keeps the event result
    # itself dominant and avoids turning routine games into large moves.
    demand_premium = clamp(record.get("demandPremiumPct", 0), -20, 20)
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    long_term_score = (
        clamp(metrics.get("achievements", 50), 0, 100) * 0.35
        + clamp(metrics.get("potential", 50), 0, 100) * 0.25
        + clamp(metrics.get("availability", 70), 0, 100) * 0.20
        + clamp(metrics.get("consistency", 70), 0, 100) * 0.20
    )
    market_context = demand_premium * 0.0015
    long_term_context = ((long_term_score - 60.0) / 40.0) * 0.025
    context_multiplier = max(0.85, 1.0 + market_context + long_term_context)

    base_move = performance_signal + outcome_signal
    final_move = round(base_move * context_multiplier, 2)

    if performance_delta >= 8:
        headline = "Strong game performance"
        performance_text = "Played better than expected"
    elif performance_delta <= -8:
        headline = "Below expectations"
        performance_text = "Played below expectations"
    else:
        headline = "Game performance update"
        performance_text = "Performance was close to expectations"

    performance_component = performance_signal * context_multiplier
    outcome_component_value = outcome_signal * context_multiplier
    explanation = {
        "version": POLICY_VERSION,
        "eventId": event_id,
        "event": str(record.get("lastPriceEvent") or "Completed game"),
        "eventAt": record.get("lastPriceEventAt"),
        "headline": headline,
        "summary": [performance_text, outcome_label, "No fixed event-movement cap is applied."],
        "direction": "increased" if final_move > 0 else "decreased" if final_move < 0 else "held steady",
        "finalMovePct": final_move,
        "confidence": round(clamp(record.get("pricingConfidence", record.get("dataConfidence", 0.5)), 0, 1), 2),
        "volatilityTier": tier,
        "resultSensitivity": round(sensitivity, 3),
        "hardMoveCapPct": None,
        "components": {
            "performancePct": round(performance_component, 3),
            "teamOutcomePct": round(outcome_component_value, 3),
            "contextPct": round(final_move - performance_component - outcome_component_value, 3),
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
    if move <= -100.0:
        return result, False
    new_price = max(0.01, round(previous_price * (1.0 + move / 100.0), 2))
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
