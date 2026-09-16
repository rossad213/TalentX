#!/usr/bin/env python3
"""Production-led NFL fair-value model for TalentX.

NFL valuation is driven primarily by normalized football production, with modest
achievement, career-runway, and availability context. Raw statistics from unlike
positions must be normalized before they reach this module; a quarterback is not
worth more than a running back simply because passing-yard totals are larger.

Early-season fundamentals are stabilized upstream so one or two games do not
silently replace an established player's durable career baseline. Game-to-game
surprise remains the event engine's job.

Drafted players who have not established meaningful NFL production or usage retain
a decaying IPO anchor even when they have dressed for games or roster systems label
them as second-year players. Meaningful professional evidence, not activation alone,
determines how quickly the draft/pre-pro anchor fades.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

MODEL_VERSION = "3.1-nfl-position-normalized-sample-stable-meaningful-usage-rookie-ipo"

PRICE_FLOOR = 4.0
PRICE_SCALE = 310.0
PRICE_EXPONENT = 4.4
PRICE_CEILING = 300.0
ROOKIE_IPO_SCALE = 135.0
GENERIC_NFL_ROOKIE_PRICE_CEILING = 92.0
NFL_MAX_DRAFT_PICKS = 257.0

# Recent production remains important, but durable career evidence has enough
# weight that a hot opening week cannot create a new fundamental by itself.
NFL_PRODUCTION_WEIGHTS = {
    "recentProduction": 0.50,
    "careerProduction": 0.35,
    "efficiency": 0.15,
}

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

NFL_ROOKIE_GAME_BANDS = (
    (0.0, 1.00),
    (4.0, 0.75),
    (10.0, 0.50),
    (17.0, 0.25),
    (24.0, 0.10),
    (34.0, 0.00),
)

# A player without meaningful NFL usage should not lose all pre-pro value merely
# because the roster feed says "2 years" or counts inactive/zero-usage appearances.
# The anchor still decays by time so a player cannot remain a rookie indefinitely.
NO_DEBUT_DRAFT_AGE_CAPS = {
    0: 1.00,
    1: 0.60,
    2: 0.30,
    3: 0.10,
}

NFL_ROOKIE_WEIGHTS = {
    "draftCapital": 0.35,
    "preProPerformance": 0.20,
    "opportunity": 0.15,
    "positionValue": 0.10,
    "development": 0.08,
    "availability": 0.07,
    "audience": 0.05,
}

NFL_ROOKIE_POSITION_VALUES = {
    "quarterback": 100,
    "edge": 88,
    "defensive end": 86,
    "wide receiver": 84,
    "offensive tackle": 82,
    "cornerback": 80,
    "defensive tackle": 74,
    "linebacker": 70,
    "tight end": 68,
    "running back": 64,
    "safety": 63,
    "guard": 60,
    "center": 60,
    "kicker": 35,
    "punter": 35,
}

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

# These are role-production signals. Generic usage/careerUsage are deliberately
# excluded because the catalog's usage formula itself includes games played.
MEANINGFUL_PRODUCTION_KEYS = (
    "recentProduction",
    "careerProduction",
)
MEANINGFUL_PRODUCTION_MIN = 1.0

# Optional explicit volume fields let future collectors provide a cleaner handoff
# than aggregate production scores without changing this model again.
MEANINGFUL_VOLUME_FIELDS = (
    "professionalTouches",
    "careerTouches",
    "professionalAttempts",
    "careerAttempts",
    "professionalTargets",
    "careerTargets",
    "professionalSnaps",
    "careerSnaps",
)


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


def _has_meaningful_professional_evidence(record: dict[str, Any]) -> bool:
    """Return whether the record contains real NFL role production evidence.

    ``professionalGames`` and generic usage are intentionally excluded as role
    signals. Zero-game records are never treated as having established NFL role
    evidence, and tiny provider/preseason production noise does not end IPO support.
    """
    games = max(0.0, _number(record.get("professionalGames")) or 0.0)
    if games <= 0:
        return False

    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    raw = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
    if any(abs(_number(raw.get(key)) or 0.0) >= MEANINGFUL_PRODUCTION_MIN for key in MEANINGFUL_PRODUCTION_KEYS):
        return True
    if any((_number(record.get(key)) or 0.0) > 0.0 for key in MEANINGFUL_VOLUME_FIELDS):
        return True
    return False


def _rookie_evidence_games(record: dict[str, Any]) -> float:
    """Games used for IPO decay, counting appearances only after role evidence exists."""
    games = max(0.0, _number(record.get("professionalGames")) or 0.0)
    return games if _has_meaningful_professional_evidence(record) else 0.0


def production_components(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return normalized verified-production inputs for the NFL value component."""
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
        source = "position-normalized NFL production percentiles"
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
    normalized = _clamp(score) / 100.0
    value = PRICE_FLOOR + PRICE_SCALE * (normalized ** PRICE_EXPONENT)
    return round(max(PRICE_FLOOR, min(PRICE_CEILING, value)), 2)


def _draft_capital_score(record: dict[str, Any]) -> float | None:
    pick = _number(record.get("draftPick"))
    if pick is None or pick < 1:
        return None
    pick = max(1.0, min(NFL_MAX_DRAFT_PICKS, pick))
    score = 100.0 - 78.0 * math.sqrt((pick - 1.0) / (NFL_MAX_DRAFT_PICKS - 1.0))
    return round(_clamp(score, 18.0, 100.0), 2)


def _rookie_position_value(record: dict[str, Any]) -> float:
    role = str(record.get("role") or "").lower()
    for token, value in NFL_ROOKIE_POSITION_VALUES.items():
        if token in role:
            return float(value)
    return 72.0


def _rookie_development(record: dict[str, Any]) -> float:
    age = _number(record.get("age"))
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    active_potential = _clamp(metrics.get("potential", 72.0))
    if age is None:
        age_score = 78.0
    elif age <= 19:
        age_score = 96.0
    elif age <= 20:
        age_score = 92.0
    elif age <= 21:
        age_score = 88.0
    elif age <= 22:
        age_score = 83.0
    elif age <= 23:
        age_score = 76.0
    elif age <= 24:
        age_score = 68.0
    else:
        age_score = 58.0
    return round(_clamp(age_score * 0.70 + active_potential * 0.30, 35.0, 98.0), 2)


def _derived_rookie_anchor(record: dict[str, Any]) -> tuple[float | None, dict[str, float] | None]:
    """Reconstruct a missing NFL IPO anchor from factual draft metadata.

    This is used when an upstream generic-pricing pass removed ``rookiePricing``
    even though the player has not established meaningful NFL role evidence. It
    never invents draft position or a manual price.
    """
    draft = _draft_capital_score(record)
    if draft is None or _has_meaningful_professional_evidence(record):
        return None, None

    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    achievements = _clamp(metrics.get("achievements", 20.0))
    pre_pro = _clamp(52.0 + draft * 0.38 + achievements * 0.05, 48.0, 96.0)
    opportunity = _clamp(38.0 + draft * 0.52 + (7.0 if record.get("starter") else 0.0), 35.0, 96.0)
    position = _rookie_position_value(record)
    development = _rookie_development(record)
    availability = max(82.0 if str(record.get("careerStatus") or "") == "Active" else 62.0, _clamp(metrics.get("availability", 75.0)))
    audience = _clamp(_clamp(metrics.get("audience", 40.0)) + max(0.0, draft - 55.0) * 0.45, 25.0, 92.0)
    inputs = {
        "draftCapital": draft,
        "preProPerformance": pre_pro,
        "opportunity": opportunity,
        "positionValue": position,
        "development": development,
        "availability": availability,
        "audience": audience,
    }
    score = sum(inputs[key] * weight for key, weight in NFL_ROOKIE_WEIGHTS.items())
    anchor = 2.0 + GENERIC_NFL_ROOKIE_PRICE_CEILING * (_clamp(score) / 100.0) ** 2
    return round(anchor, 2), {**{key: round(value, 2) for key, value in inputs.items()}, "rookieScore": round(score, 2)}


def _draft_age_cap(record: dict[str, Any], *, current_year: int | None = None) -> float:
    draft_year = _number(record.get("draftYear"))
    if draft_year is None:
        return 1.0
    year = int(current_year or datetime.now(timezone.utc).year)
    age = max(0, year - int(round(draft_year)))
    return NO_DEBUT_DRAFT_AGE_CAPS.get(age, 0.0)


def _rookie_anchor(record: dict[str, Any]) -> tuple[float | None, float, dict[str, Any] | None]:
    pricing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else {}
    saved_influence = _number(pricing.get("draftInfluencePct"))

    anchor = _number(pricing.get("calibratedIpoPrice"))
    if anchor is None or anchor <= 0:
        anchor = _number(pricing.get("ipoPrice"))
    if anchor is None or anchor <= 0:
        rookie_score = _number(pricing.get("rookieScore"))
        if rookie_score is not None and rookie_score >= 0:
            anchor = PRICE_FLOOR + ROOKIE_IPO_SCALE * (_clamp(rookie_score) / 100.0) ** 2

    source: dict[str, Any] | None = None
    if anchor is None or anchor <= 0:
        anchor, derived = _derived_rookie_anchor(record)
        if anchor is not None and derived is not None:
            source = {"source": "reconstructed-from-draft-metadata", **derived}

    if anchor is None or anchor <= 0:
        return None, 0.0, None

    if saved_influence is None:
        saved_influence = 100.0
    maximum = max(0.0, min(1.0, saved_influence / 100.0))
    return round(anchor, 2), maximum, source


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
    if saved_max <= 0:
        return 0.0
    games = _rookie_evidence_games(record)
    by_games = _game_decay(games)
    by_time = _draft_age_cap(record)
    return round(max(0.0, min(saved_max, by_games, by_time)), 4)


def production_fair_value(record: dict[str, Any]) -> tuple[float | None, float | None, dict[str, Any] | None]:
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

    rookie_anchor, saved_rookie_max, reconstructed = _rookie_anchor(record)
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
        "rookieEvidenceGames": round(_rookie_evidence_games(record), 2),
        "rookieAnchorReconstruction": reconstructed,
        "fairValue": round(fair, 2),
        "pricingPrinciple": (
            "position-normalized production-led NFL value; early-season fundamentals are sample-stabilized; "
            "modest achievement, career-runway, and availability context; no fame or starter premium; "
            "Rookie IPO fades with meaningful professional role evidence and time since draft"
        ),
        "modelVersion": MODEL_VERSION,
    }
    return round(score, 2), round(fair, 2), explanation
