#!/usr/bin/env python3
"""NFL production-first fair-value model for TalentX.

NFL prices primarily describe verified on-field production relative to peers at
the same position. A small positional-value modifier keeps the cross-position
market intuitive without allowing fame, audience, age or narrative context to
overpower football output. Rookie IPO influence is preserved until verified
professional evidence is meant to take over.
"""
from __future__ import annotations

import math
from typing import Any

MODEL_VERSION = "1.1-nfl-production-primary-position-value"
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

# Production still determines the score. These intentionally modest multipliers
# only translate position-normalized production into one cross-position market.
# Specialists are the lone larger discount because their opportunities and total
# game impact are structurally narrower than offensive/defensive every-down roles.
NFL_POSITION_VALUE = {
    "QB": 1.08,
    "WR": 1.06,
    "RB": 1.00,
    "TE": 0.91,
    "EDGE": 1.04,
    "LB": 0.95,
    "CB": 0.95,
    "S": 0.92,
    "IDL": 0.94,
    "OL": 0.90,
    "ST": 0.72,
    "DEF": 0.95,
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


def nfl_position_group(record: dict[str, Any]) -> str:
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


def position_value_multiplier(record: dict[str, Any]) -> float:
    return float(NFL_POSITION_VALUE.get(nfl_position_group(record), 0.95))


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
        "positionGroup": nfl_position_group(record),
        "positionValueMultiplier": round(position_value_multiplier(record), 3),
    }


def production_score(record: dict[str, Any]) -> float | None:
    components = production_components(record)
    return None if components is None else float(components["adjustedScore"])


def _rookie_anchor(record: dict[str, Any]) -> tuple[float | None, float]:
    pricing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else {}
    influence = _number(pricing.get("draftInfluencePct"))
    if influence is None or influence <= 0:
        return None, 0.0
    anchor = _number(pricing.get("calibratedIpoPrice"))
    if anchor is None or anchor <= 0:
        anchor = _number(pricing.get("ipoPrice"))
    if anchor is None or anchor <= 0:
        return None, 0.0
    return anchor, max(0.0, min(1.0, influence / 100.0))


def production_fair_value(record: dict[str, Any]) -> tuple[float | None, float | None, dict[str, Any] | None]:
    """Return NFL score, fair value and an explainable production breakdown."""
    components = production_components(record)
    if components is None:
        return None, None, None

    score = float(components["adjustedScore"])
    confidence = _number(record.get("pricingConfidence", record.get("dataConfidence")))
    confidence = max(0.0, min(1.0, confidence if confidence is not None else 0.70))
    ranked = components["source"] == "position-normalized production percentiles"
    confidence_factor = (0.985 + 0.015 * confidence) if ranked else (0.94 + 0.06 * confidence)
    effective_score = _clamp(score * confidence_factor, 0.0, 100.0)

    production_only_fair = PRICE_FLOOR + PRICE_CURVE * effective_score * effective_score
    positional_fair = production_only_fair * position_value_multiplier(record)
    positional_fair = max(PRICE_FLOOR, min(PRICE_CEILING, positional_fair))

    rookie_anchor, rookie_influence = _rookie_anchor(record)
    fair = positional_fair
    if rookie_anchor is not None and rookie_influence > 0:
        fair = rookie_anchor * rookie_influence + positional_fair * (1.0 - rookie_influence)
    fair = max(PRICE_FLOOR, min(PRICE_CEILING, fair))

    explanation = {
        **components,
        "confidenceFactor": round(confidence_factor, 4),
        "effectiveScore": round(effective_score, 2),
        "productionOnlyFairValue": round(production_only_fair, 2),
        "positionAdjustedFairValue": round(positional_fair, 2),
        "rookieIpoAnchor": round(rookie_anchor, 2) if rookie_anchor is not None else None,
        "rookieInfluence": round(rookie_influence, 4),
        "fairValue": round(fair, 2),
        "modelVersion": MODEL_VERSION,
    }
    return round(score, 2), round(fair, 2), explanation
