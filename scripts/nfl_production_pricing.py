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

MODEL_VERSION = "3.2-nfl-production-potential-shared-market-scale"

PRICE_FLOOR = 4.0
PRICE_SCALE = 0.0325
PRICE_EXPONENT = 2.0
PRICE_CEILING = 350.0
ROOKIE_IPO_SCALE = 135.0
GENERIC_NFL_ROOKIE_PRICE_CEILING = ROOKIE_IPO_SCALE
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
    "potential": 0.30,
}

NFL_PRODUCTION_BUCKET_WEIGHTS = {
    "normalizedProduction": 0.60,
    "achievements": 0.20,
    "consistency": 0.20,
}

NFL_POTENTIAL_BUCKET_WEIGHTS = {
    "careerRunway": 0.45,
    "opportunity": 0.25,
    "availability": 0.20,
    "development": 0.10,
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


def _parse_event_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _verified_regular_game_count(record: dict[str, Any]) -> int:
    """Count durable NFL regular/postseason game events while excluding preseason."""
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    keys: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or str(event.get("eventType") or "").lower() != "game":
            continue
        started = _parse_event_time(event.get("startedAt") or event.get("eventDate") or event.get("date"))
        if started is None:
            continue
        # NFL preseason evidence is primarily August. September-February game
        # events are durable professional evidence, including postseason games.
        if 3 <= started.month <= 8:
            continue
        key = str(event.get("eventKey") or event.get("eventId") or started.isoformat())
        keys.add(key)
    return len(keys)


def _reported_sample_games(record: dict[str, Any]) -> float:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    for key in ("recentSampleGamesEstimate", "recentSeasonGames", "recentGames", "seasonGames"):
        value = _number(summary.get(key))
        if value is not None and value > 0:
            return float(value)
    return 0.0


def _has_meaningful_professional_evidence(record: dict[str, Any]) -> bool:
    """Return whether the record contains verified, material NFL role evidence.

    Generic usage is not enough because it includes games played. Tiny production
    values can also be provider/preseason noise. Material production must pair with
    a professional-game signal, unless an explicit professional volume field proves
    real touches/attempts/targets/snaps directly.
    """
    if any((_number(record.get(key)) or 0.0) > 0.0 for key in MEANINGFUL_VOLUME_FIELDS):
        return True

    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    raw = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
    material_production = any(
        abs(_number(raw.get(key)) or 0.0) >= MEANINGFUL_PRODUCTION_MIN for key in MEANINGFUL_PRODUCTION_KEYS
    )
    if not material_production:
        return False

    games = max(0.0, _number(record.get("professionalGames")) or 0.0)
    if games > 0:
        return True
    if _reported_sample_games(record) > 0:
        return True
    return _verified_regular_game_count(record) > 0


def _rookie_evidence_games(record: dict[str, Any]) -> float:
    """Games used for IPO decay, with verified history repairing stale game counts."""
    if not _has_meaningful_professional_evidence(record):
        return 0.0
    games = max(0.0, _number(record.get("professionalGames")) or 0.0)
    sample = _reported_sample_games(record)
    verified = float(_verified_regular_game_count(record))
    # Explicit volume is meaningful even if every game-count field is missing.
    return max(games, sample, verified, 1.0)


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


def consistency_score(record: dict[str, Any], fallback: float = 65.0) -> float:
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    explicit = _number(metrics.get("consistency"))
    return round(_clamp(explicit if explicit is not None else fallback), 2)


def opportunity_score(record: dict[str, Any]) -> float:
    """Evidence-based current role/opportunity; not a fame or position premium."""
    role_status = str(record.get("roleStatus") or "").strip().lower()
    if record.get("starter") is True or role_status in {"starter", "starting", "first team", "first-team"}:
        return 95.0
    if role_status in {"reserve", "backup", "bench", "demoted"}:
        return 45.0

    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    percentiles = summary.get("percentiles") if isinstance(summary.get("percentiles"), dict) else {}
    usage = _number(percentiles.get("usage"))
    if usage is not None:
        if usage <= 1.0:
            usage *= 100.0
        return round(_clamp(45.0 + 0.50 * usage, 45.0, 95.0), 2)
    return 60.0


def development_score(record: dict[str, Any]) -> float:
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    explicit = _number(metrics.get("potential"))
    runway = career_runway_score(record)
    if explicit is None:
        return runway
    return round(_clamp(explicit * 0.60 + runway * 0.40), 2)


def production_bucket_score(record: dict[str, Any], normalized_production: float) -> tuple[float, dict[str, float]]:
    values = {
        "normalizedProduction": _clamp(normalized_production),
        "achievements": achievement_score(record),
        "consistency": consistency_score(record, normalized_production),
    }
    score = sum(values[key] * NFL_PRODUCTION_BUCKET_WEIGHTS[key] for key in NFL_PRODUCTION_BUCKET_WEIGHTS)
    return round(_clamp(score), 2), {key: round(value, 2) for key, value in values.items()}


def potential_bucket_score(record: dict[str, Any]) -> tuple[float, dict[str, float]]:
    values = {
        "careerRunway": career_runway_score(record),
        "opportunity": opportunity_score(record),
        "availability": availability_score(record),
        "development": development_score(record),
    }
    score = sum(values[key] * NFL_POTENTIAL_BUCKET_WEIGHTS[key] for key in NFL_POTENTIAL_BUCKET_WEIGHTS)
    return round(_clamp(score), 2), {key: round(value, 2) for key, value in values.items()}


def price_from_score(score: float) -> float:
    normalized = _clamp(score)
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
    opportunity = max(
        _clamp(38.0 + draft * 0.52, 35.0, 96.0),
        opportunity_score(record),
    )
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
    anchor = PRICE_FLOOR + GENERIC_NFL_ROOKIE_PRICE_CEILING * (_clamp(score) / 100.0) ** 2
    return round(anchor, 2), {**{key: round(value, 2) for key, value in inputs.items()}, "rookieScore": round(score, 2)}


def years_since_draft(record: dict[str, Any], *, current_year: int | None = None) -> int | None:
    draft_year = _number(record.get("draftYear"))
    if draft_year is None:
        return None
    year = int(current_year or datetime.now(timezone.utc).year)
    return max(0, year - int(round(draft_year)))


def is_rookie_ipo_player(record: dict[str, Any], *, current_year: int | None = None) -> bool:
    """TalentX IPO regime is limited to rookies and second-year NFL players."""
    age = years_since_draft(record, current_year=current_year)
    if age is not None:
        return age <= 1
    experience = _number(record.get("experienceYears"))
    if experience is not None:
        return experience <= 2
    stage = str(record.get("careerStage") or "").lower()
    return "rookie" in stage or "drafted" in stage or "pre-draft" in stage


def _draft_age_cap(record: dict[str, Any], *, current_year: int | None = None) -> float:
    age = years_since_draft(record, current_year=current_year)
    if age is None:
        return 1.0
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

    normalized_production = float(components["productionScore"])
    production, production_inputs = production_bucket_score(record, normalized_production)
    potential, potential_inputs = potential_bucket_score(record)

    inputs = {
        "production": production,
        "potential": potential,
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
        "productionSubweights": dict(NFL_PRODUCTION_BUCKET_WEIGHTS),
        "potentialSubweights": dict(NFL_POTENTIAL_BUCKET_WEIGHTS),
        "productionInputs": production_inputs,
        "potentialInputs": potential_inputs,
        "valuationScore": round(score, 2),
        "careerFairValue": round(career_fair, 2),
        "rookieIpoAnchor": round(rookie_anchor, 2) if rookie_anchor is not None else None,
        "rookieInfluence": round(ipo_influence, 4),
        "rookieEvidenceGames": round(_rookie_evidence_games(record), 2),
        "rookieAnchorReconstruction": reconstructed,
        "fairValue": round(fair, 2),
        "pricingPrinciple": (
            "NFL veteran fundamental value = 70% Production + 30% Potential on the shared TalentX athlete-market "
            "quadratic value scale; Production contains normalized recent/career/efficiency evidence, achievements, "
            "and consistency; Potential contains career runway, verified role/usage opportunity, availability, and "
            "development; no fame premium. Rookie IPO applies only to rookies and second-year players."
        ),
        "modelVersion": MODEL_VERSION,
    }
    return round(score, 2), round(fair, 2), explanation
