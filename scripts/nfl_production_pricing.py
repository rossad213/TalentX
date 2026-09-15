#!/usr/bin/env python3
"""Production-led NFL fair-value model for TalentX.

NFL valuation remains driven primarily by verified football production, but the
price level also carries a modest amount of proven achievement, remaining career
runway, and availability. Position, starter/reserve status, and fame do not
create price premiums. Every NFL player is ultimately valued on one universal
TalentX NFL scale after unlike football statistics have been translated into the
common production signal.

Drafted rookies keep the existing Rookie IPO anchor while professional evidence
accumulates. That anchor fades smoothly with games played instead of disappearing
as soon as the first ranked NFL statistic appears.

Game-to-game movement remains separate: the event engine moves price from actual
production versus that individual player's expected production.
"""
from __future__ import annotations

import math
from typing import Any

MODEL_VERSION = "2.0-nfl-production-led-career-stage-rookie-ipo"

PRICE_FLOOR = 4.0
PRICE_SCALE = 310.0
PRICE_EXPONENT = 4.4
PRICE_CEILING = 300.0
ROOKIE_IPO_SCALE = 135.0

# Production is still the dominant input. These three inputs are themselves
# production measurements, not role or positional premiums.
NFL_PRODUCTION_WEIGHTS = {
    "recentProduction": 0.55,
    "careerProduction": 0.30,
    "efficiency": 0.15,
}

# Final NFL value is production-led rather than production-only. Age/career stage
# appears only inside the small runway component, so an older productive player
# is discounted modestly rather than erased.
NFL_VALUE_WEIGHTS = {
    "production": 0.70,
    "achievements": 0.15,
    "careerRunway": 0.10,
    "availability": 0.05,
}

NFL_FALLBACK_WEIGHTS = {
    "performance": 0.70,
    "consistency": 0.30,
}

# Smooth Rookie IPO decay. These are deliberately close to the original TalentX
# rookie policy: draft/pre-pro evidence matters most before games exist, then
# professional production gradually replaces it.
NFL_ROOKIE_GAME_BANDS = (
    (0.0, 1.00),
    (4.0, 0.75),
    (10.0, 0.50),
    (17.0, 0.25),
    (24.0, 0.10),
    (34.0, 0.00),
)

STAGE_RUNWAY = {
    "prospect": 98.0,
    "pre-draft": 97.0,
    "drafted": 96.0,
    "rookie ipo": 95.0,
    "active rookie": 94.0,
    "rookie": 93.0,
    "early career": 86.0,
    "established": 70.0,
    "veteran": 45.0,
    "late veteran": 35.0,
    "retired": 15.0,
    "retired — legacy": 10.0,
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
    for key in ("recentProduction", "careerProduction", "efficiency", "awardPoints"):
        value = _number(raw.get(key))
        if value is not None:
            output[key] = max(0.0, min(1.0, value)) * 100.0
    return output


def production_components(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the verified-production inputs used for the dominant NFL component."""
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
    }


def production_score(record: dict[str, Any]) -> float | None:
    components = production_components(record)
    return None if components is None else float(components["productionScore"])


def achievement_score(record: dict[str, Any]) -> float:
    """Use verified NFL-wide achievement evidence without introducing fame."""
    percentiles = _percentiles(record)
    if "awardPoints" in percentiles:
        return round(_clamp(percentiles["awardPoints"]), 2)
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    return round(_clamp(metrics.get("achievements", 35.0)), 2)


def _age_runway(age: float | None) -> float | None:
    if age is None or age <= 0:
        return None
    if age <= 23:
        return 95.0
    if age <= 27:
        return 95.0 - (age - 23.0) * 4.0
    if age <= 31:
        return 79.0 - (age - 27.0) * 5.0
    return max(30.0, 59.0 - (age - 31.0) * 5.0)


def _stage_runway(record: dict[str, Any]) -> float | None:
    stage = str(record.get("careerStage") or record.get("career_stage") or "").strip().lower()
    if not stage:
        return None
    if stage in STAGE_RUNWAY:
        return STAGE_RUNWAY[stage]
    for key, value in STAGE_RUNWAY.items():
        if key in stage:
            return value
    return None


def career_runway_score(record: dict[str, Any]) -> float:
    """Small age/stage input representing remaining expected career runway.

    This does not use position-specific aging curves. A veteran is discounted
    enough to distinguish remaining runway, but production still dominates.
    """
    age_score = _age_runway(_number(record.get("age")))
    stage_score = _stage_runway(record)
    if age_score is not None and stage_score is not None:
        return round(_clamp(age_score * 0.70 + stage_score * 0.30), 2)
    if age_score is not None:
        return round(_clamp(age_score), 2)
    if stage_score is not None:
        return round(_clamp(stage_score), 2)
    experience = _number(record.get("experienceYears"))
    if experience is not None:
        return round(_clamp(92.0 - max(0.0, experience) * 5.5, 35.0, 92.0), 2)
    return 65.0


def availability_score(record: dict[str, Any]) -> float:
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    explicit = _number(metrics.get("availability"))
    if explicit is not None:
        return round(_clamp(explicit), 2)
    status = str(record.get("careerStatus") or "").lower()
    if "injured" in status:
        return 45.0
    if "suspended" in status:
        return 40.0
    return 75.0 if status in {"", "active"} else 60.0


def price_from_score(score: float) -> float:
    """Map the universal NFL value score onto TalentX dollars.

    The steeper-than-quadratic curve keeps ordinary contributors well below
    cross-market stars while still giving genuinely elite producers room to be
    worth $200-$300.
    """
    normalized = _clamp(score) / 100.0
    value = PRICE_FLOOR + PRICE_SCALE * (normalized ** PRICE_EXPONENT)
    return round(max(PRICE_FLOOR, min(PRICE_CEILING, value)), 2)


def _rookie_anchor(record: dict[str, Any]) -> tuple[float | None, float]:
    """Return the saved Rookie IPO anchor and saved maximum influence."""
    pricing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else {}
    saved_influence = _number(pricing.get("draftInfluencePct"))

    anchor = _number(pricing.get("calibratedIpoPrice"))
    if anchor is None or anchor <= 0:
        anchor = _number(pricing.get("ipoPrice"))
    if anchor is None or anchor <= 0:
        rookie_score = _number(pricing.get("rookieScore"))
        if rookie_score is not None and rookie_score >= 0:
            anchor = PRICE_FLOOR + ROOKIE_IPO_SCALE * (_clamp(rookie_score) / 100.0) ** 2

    if anchor is None or anchor <= 0:
        return None, 0.0

    if saved_influence is None:
        saved_influence = 100.0
    return round(anchor, 2), max(0.0, min(1.0, saved_influence / 100.0))


def _game_decay(games: float) -> float:
    games = max(0.0, games)
    for index, (start_games, start_influence) in enumerate(NFL_ROOKIE_GAME_BANDS):
        if index == len(NFL_ROOKIE_GAME_BANDS) - 1:
            return start_influence
        end_games, end_influence = NFL_ROOKIE_GAME_BANDS[index + 1]
        if games <= end_games:
            width = max(1e-9, end_games - start_games)
            progress = (games - start_games) / width
            return start_influence + (end_influence - start_influence) * progress
    return 0.0


def rookie_influence(record: dict[str, Any], saved_max: float) -> float:
    """Fade Rookie IPO evidence with games instead of switching it off."""
    if saved_max <= 0:
        return 0.0
    pricing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else {}
    if not pricing:
        return 0.0
    games = max(0.0, _number(record.get("professionalGames")) or 0.0)
    decayed = _game_decay(games)
    return round(max(0.0, min(saved_max, decayed)), 4)


def production_fair_value(record: dict[str, Any]) -> tuple[float | None, float | None, dict[str, Any] | None]:
    """Return NFL value score, fair value, and an explainable breakdown."""
    components = production_components(record)
    if components is None:
        return None, None, None

    production = float(components["productionScore"])
    achievements = achievement_score(record)
    runway = career_runway_score(record)
    availability = availability_score(record)

    inputs = {
        "production": production,
        "achievements": achievements,
        "careerRunway": runway,
        "availability": availability,
    }
    score = sum(inputs[key] * weight for key, weight in NFL_VALUE_WEIGHTS.items())
    score = _clamp(score)
    career_fair = price_from_score(score)

    rookie_anchor, saved_rookie_max = _rookie_anchor(record)
    ipo_influence = rookie_influence(record, saved_rookie_max)
    fair = career_fair
    if rookie_anchor is not None and ipo_influence > 0:
        fair = rookie_anchor * ipo_influence + career_fair * (1.0 - ipo_influence)
    fair = max(PRICE_FLOOR, min(PRICE_CEILING, fair))

    explanation = {
        **components,
        "valueWeights": dict(NFL_VALUE_WEIGHTS),
        "valueInputs": {key: round(value, 2) for key, value in inputs.items()},
        "valuationScore": round(score, 2),
        "careerFairValue": round(career_fair, 2),
        "rookieIpoAnchor": round(rookie_anchor, 2) if rookie_anchor is not None else None,
        "rookieInfluence": round(ipo_influence, 4),
        "fairValue": round(fair, 2),
        "pricingPrinciple": (
            "production-led universal NFL value; modest achievement, career-runway, "
            "and availability context; no position, starter/reserve, or fame premium; "
            "Rookie IPO fades gradually with professional games"
        ),
        "modelVersion": MODEL_VERSION,
    }
    return round(score, 2), round(fair, 2), explanation
