#!/usr/bin/env python3
"""NFL production-first fair-value model for TalentX.

NFL prices should primarily describe what a player produces on the field relative
to peers at the same position. Marketability, age and narrative context may
modify a valuation at the edges, but they must not outrank verified production.

The model consumes the position-normalized percentiles already persisted by the
sports enrichment pipeline. When those percentiles are unavailable it falls back
to the existing active metrics without inventing statistics.
"""
from __future__ import annotations

import math
from typing import Any

MODEL_VERSION = "1.0-nfl-production-primary"
PRICE_CURVE = 0.027
PRICE_FLOOR = 4.0
PRICE_CEILING = 350.0

# 88% of the core score is direct football production/efficiency. Awards,
# potential and availability are deliberately secondary.
NFL_PRODUCTION_WEIGHTS = {
    "recentProduction": 0.50,
    "efficiency": 0.15,
    "careerProduction": 0.23,
    "awardPoints": 0.04,
    "potential": 0.05,
    "availability": 0.03,
}

NFL_FALLBACK_WEIGHTS = {
    "performance": 0.60,
    "consistency": 0.20,
    "achievements": 0.10,
    "potential": 0.07,
    "availability": 0.03,
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    parsed = _number(value)
    if parsed is None:
        parsed = low
    return max(low, min(high, parsed))


def is_nfl(record: dict[str, Any]) -> bool:
    return str(record.get("leagueOrMedium") or "").strip().upper() == "NFL"


def _percentiles(record: dict[str, Any]) -> dict[str, float]:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    raw = summary.get("percentiles") if isinstance(summary.get("percentiles"), dict) else {}
    output: dict[str, float] = {}
    for key in ("recentProduction", "efficiency", "careerProduction", "awardPoints"):
        value = _number(raw.get(key))
        if value is not None:
            output[key] = max(0.0, min(1.0, value)) * 100.0
    return output


def production_components(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the explainable NFL score inputs used for valuation."""
    if not is_nfl(record):
        return None

    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    percentiles = _percentiles(record)
    has_ranked_production = "recentProduction" in percentiles and "careerProduction" in percentiles

    if has_ranked_production:
        recent = percentiles["recentProduction"]
        efficiency = percentiles.get("efficiency", _clamp(metrics.get("performance", recent)))
        career = percentiles["careerProduction"]
        awards = percentiles.get("awardPoints", _clamp(metrics.get("achievements", 35.0)))
        potential = _clamp(metrics.get("potential", 50.0))
        availability = _clamp(metrics.get("availability", 75.0))
        values = {
            "recentProduction": recent,
            "efficiency": efficiency,
            "careerProduction": career,
            "awardPoints": awards,
            "potential": potential,
            "availability": availability,
        }
        score = sum(values[key] * weight for key, weight in NFL_PRODUCTION_WEIGHTS.items())
        source = "position-normalized production percentiles"
        weights = NFL_PRODUCTION_WEIGHTS
    else:
        fallback = _clamp(record.get("careerScore", 50.0))
        values = {
            "performance": _clamp(metrics.get("performance", fallback)),
            "consistency": _clamp(metrics.get("consistency", fallback)),
            "achievements": _clamp(metrics.get("achievements", fallback)),
            "potential": _clamp(metrics.get("potential", fallback)),
            "availability": _clamp(metrics.get("availability", 75.0)),
        }
        score = sum(values[key] * weight for key, weight in NFL_FALLBACK_WEIGHTS.items())
        source = "active-metric fallback"
        weights = NFL_FALLBACK_WEIGHTS

    # Situation may slightly distinguish a current starter from a reserve, but it
    # is intentionally too small to overpower the production score.
    role_status = str(record.get("roleStatus") or "").strip().lower()
    starter = record.get("starter") is True or role_status in {"starter", "starting", "first team"}
    reserve = role_status in {"bench", "reserve", "demoted"}
    status = str(record.get("careerStatus") or "").lower()
    situation_adjustment = 0.0
    if starter:
        situation_adjustment += 1.5
    elif reserve:
        situation_adjustment -= 1.5
    if "injured" in status or "suspended" in status:
        situation_adjustment -= 2.5

    adjusted_score = _clamp(score + situation_adjustment, 0.0, 100.0)
    return {
        "source": source,
        "weights": dict(weights),
        "inputs": {key: round(float(value), 2) for key, value in values.items()},
        "productionScore": round(score, 2),
        "situationAdjustment": round(situation_adjustment, 2),
        "adjustedScore": round(adjusted_score, 2),
    }


def production_score(record: dict[str, Any]) -> float | None:
    components = production_components(record)
    return None if components is None else float(components["adjustedScore"])


def production_fair_value(record: dict[str, Any]) -> tuple[float | None, float | None, dict[str, Any] | None]:
    """Return NFL score, fair value and explanation.

    Confidence is only a small evidence-quality modifier once verified ranked
    production exists. A productive early-career player should not be priced far
    below a less productive veteran merely because the veteran has a longer
    sample.
    """
    components = production_components(record)
    if components is None:
        return None, None, None

    score = float(components["adjustedScore"])
    confidence = _number(record.get("pricingConfidence", record.get("dataConfidence")))
    confidence = max(0.0, min(1.0, confidence if confidence is not None else 0.70))
    ranked = components["source"] == "position-normalized production percentiles"
    confidence_factor = (0.985 + 0.015 * confidence) if ranked else (0.94 + 0.06 * confidence)
    effective_score = _clamp(score * confidence_factor, 0.0, 100.0)
    fair = PRICE_FLOOR + PRICE_CURVE * effective_score * effective_score
    fair = max(PRICE_FLOOR, min(PRICE_CEILING, fair))

    explanation = {
        **components,
        "confidenceFactor": round(confidence_factor, 4),
        "effectiveScore": round(effective_score, 2),
        "fairValue": round(fair, 2),
        "modelVersion": MODEL_VERSION,
    }
    return round(score, 2), round(fair, 2), explanation
