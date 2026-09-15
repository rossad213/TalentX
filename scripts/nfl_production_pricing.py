#!/usr/bin/env python3
"""Production-only NFL fair-value model for TalentX.

NFL price level is based on verified production only. Position, starter/reserve
status, age, fame, awards, potential, availability, and narrative context do not
change the valuation. Position can still be used upstream to translate unlike
football statistics into a common production signal, but every NFL player is
then valued on the same production scale.

Game-to-game market movement is handled separately by the NFL event engine and
is based on actual production versus that individual player's expected
production.
"""
from __future__ import annotations

import math
from typing import Any

MODEL_VERSION = "1.2-nfl-production-only-universal"
PRICE_CURVE = 0.020
PRICE_FLOOR = 4.0
PRICE_CEILING = 250.0

# Price level is production only: current/recent output matters most, durable
# career output provides the long-run anchor, and efficiency distinguishes the
# quality of otherwise similar production.
NFL_PRODUCTION_WEIGHTS = {
    "recentProduction": 0.55,
    "careerProduction": 0.30,
    "efficiency": 0.15,
}

# Older/partial records that do not yet have ranked raw production may still use
# these two production-derived metrics. No achievements, audience, potential,
# availability, role, or position premium enters the fallback.
NFL_FALLBACK_WEIGHTS = {
    "performance": 0.70,
    "consistency": 0.30,
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
    for key in ("recentProduction", "careerProduction", "efficiency"):
        value = _number(raw.get(key))
        if value is not None:
            output[key] = max(0.0, min(1.0, value)) * 100.0
    return output


def production_components(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the production inputs used for NFL valuation."""
    if not is_nfl(record):
        return None

    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    percentiles = _percentiles(record)
    has_ranked_production = "recentProduction" in percentiles and "careerProduction" in percentiles

    if has_ranked_production:
        recent = percentiles["recentProduction"]
        career = percentiles["careerProduction"]
        efficiency = percentiles.get("efficiency", _clamp(metrics.get("performance", recent)))
        values = {
            "recentProduction": recent,
            "careerProduction": career,
            "efficiency": efficiency,
        }
        score = sum(values[key] * weight for key, weight in NFL_PRODUCTION_WEIGHTS.items())
        source = "NFL-wide production percentiles"
        weights = NFL_PRODUCTION_WEIGHTS
    else:
        fallback = _clamp(record.get("careerScore", 50.0))
        values = {
            "performance": _clamp(metrics.get("performance", fallback)),
            "consistency": _clamp(metrics.get("consistency", fallback)),
        }
        score = sum(values[key] * weight for key, weight in NFL_FALLBACK_WEIGHTS.items())
        source = "production-metric fallback"
        weights = NFL_FALLBACK_WEIGHTS

    score = _clamp(score, 0.0, 100.0)
    return {
        "source": source,
        "weights": dict(weights),
        "inputs": {key: round(float(value), 2) for key, value in values.items()},
        "productionScore": round(score, 2),
        "adjustedScore": round(score, 2),
        "pricingPrinciple": "production-only; no position, role, fame, age, awards, potential, or availability premium",
    }


def production_score(record: dict[str, Any]) -> float | None:
    components = production_components(record)
    return None if components is None else float(components["productionScore"])


def _rookie_anchor(record: dict[str, Any]) -> tuple[float | None, float]:
    """Return a temporary IPO anchor only when real NFL production is unavailable."""
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
    """Return NFL production score, fair value, and an explainable breakdown."""
    components = production_components(record)
    if components is None:
        return None, None, None

    score = float(components["productionScore"])
    fair = PRICE_FLOOR + PRICE_CURVE * score * score
    fair = max(PRICE_FLOOR, min(PRICE_CEILING, fair))

    # A pre-production rookie needs an IPO so the listing can exist. Once ranked
    # NFL production is present, the draft anchor disappears and production alone
    # determines the price.
    ranked = components["source"] == "NFL-wide production percentiles"
    rookie_anchor, rookie_influence = _rookie_anchor(record)
    if ranked:
        rookie_influence = 0.0
    elif rookie_anchor is not None and rookie_influence > 0:
        fair = rookie_anchor * rookie_influence + fair * (1.0 - rookie_influence)
    fair = max(PRICE_FLOOR, min(PRICE_CEILING, fair))

    explanation = {
        **components,
        "productionOnlyFairValue": round(fair, 2),
        "rookieIpoAnchor": round(rookie_anchor, 2) if rookie_anchor is not None else None,
        "rookieInfluence": round(rookie_influence, 4),
        "fairValue": round(fair, 2),
        "modelVersion": MODEL_VERSION,
    }
    return round(score, 2), round(fair, 2), explanation
