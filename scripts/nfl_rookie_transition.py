#!/usr/bin/env python3
"""Systemic NFL Rookie IPO transition; no player-specific prices."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import nfl_production_pricing as core

MODEL_VERSION = "2.0-nfl-rookie-multifactor-handoff"
MAX_DRAFT_AGE = 4
TIME_CAPS = {0: 1.00, 1: 0.78, 2: 0.58, 3: 0.32, 4: 0.12}
EXP_CAPS = {0: 1.00, 1: 1.00, 2: 0.78, 3: 0.58, 4: 0.32, 5: 0.12}
ROLE_GAME_PACE = {
    "quarterback": 0.72, "tight end": 0.88, "wide receiver": 0.92,
    "receiver": 0.92, "offensive tackle": 0.90, "guard": 0.92,
    "center": 0.92, "running back": 1.25, "kicker": 1.25, "punter": 1.25,
}
ANCHOR_WEIGHTS = {
    "draftCapital": 0.50, "positionValue": 0.15, "development": 0.15,
    "opportunity": 0.12, "availability": 0.08,
}


def _num(value: Any) -> float | None:
    return core._number(value)


def years_since_draft(record: dict[str, Any], current_year: int | None = None) -> int | None:
    draft_year = _num(record.get("draftYear"))
    if draft_year is None:
        return None
    year = int(current_year or datetime.now(timezone.utc).year)
    return max(0, year - int(round(draft_year)))


def _age_factor(record: dict[str, Any]) -> float:
    age = _num(record.get("age"))
    if age is None or age <= 23: return 1.00
    if age <= 24: return 0.96
    if age <= 25: return 0.90
    if age <= 26: return 0.78
    if age <= 27: return 0.62
    if age <= 28: return 0.45
    if age <= 29: return 0.30
    return 0.15


def _experience_cap(record: dict[str, Any]) -> float:
    experience = _num(record.get("experienceYears"))
    if experience is None: return 1.0
    return EXP_CAPS.get(max(0, int(round(experience))), 0.0)


def _role_pace(record: dict[str, Any]) -> float:
    role = str(record.get("role") or "").lower()
    for token, pace in ROLE_GAME_PACE.items():
        if token in role: return pace
    return 1.0


def _career_games(record: dict[str, Any]) -> float:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    return max(
        max(0.0, _num(record.get("professionalGames")) or 0.0),
        max(0.0, _num(summary.get("professionalGames")) or 0.0),
        float(core._verified_regular_game_count(record)),
    )


def _saved_anchor(record: dict[str, Any]) -> tuple[float | None, float]:
    pricing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else {}
    saved = _num(pricing.get("draftInfluencePct"))
    maximum = 1.0 if saved is None else max(0.0, min(1.0, saved / 100.0))
    anchor = _num(pricing.get("calibratedIpoPrice")) or _num(pricing.get("ipoPrice"))
    if anchor is None:
        score = _num(pricing.get("rookieScore"))
        if score is not None:
            anchor = core.PRICE_FLOOR + core.ROOKIE_IPO_SCALE * (core._clamp(score) / 100.0) ** 2
    return (round(anchor, 2) if anchor and anchor > 0 else None), maximum


def _derived_anchor(record: dict[str, Any]) -> tuple[float | None, dict[str, Any] | None]:
    draft = core._draft_capital_score(record)
    if draft is None: return None, None
    inputs = {
        "draftCapital": draft,
        "positionValue": core._rookie_position_value(record),
        "development": core._rookie_development(record),
        "opportunity": core._clamp(45.0 + draft * 0.45, 35.0, 96.0),
        "availability": max(82.0 if str(record.get("careerStatus") or "").lower() == "active" else 62.0,
                            core.availability_score(record)),
    }
    score = sum(inputs[key] * weight for key, weight in ANCHOR_WEIGHTS.items())
    anchor = 2.0 + core.GENERIC_NFL_ROOKIE_PRICE_CEILING * (core._clamp(score) / 100.0) ** 2
    detail = {key: round(float(value), 2) for key, value in inputs.items()}
    detail["rookieScore"] = round(score, 2)
    return round(anchor, 2), detail


def _anchor(record: dict[str, Any]) -> tuple[float | None, float, dict[str, Any] | None]:
    saved, maximum = _saved_anchor(record)
    if saved is not None: return saved, maximum, {"source": "saved-rookie-pricing"}
    anchor, detail = _derived_anchor(record)
    if anchor is None: return None, 0.0, None
    return anchor, maximum, {"source": "reconstructed-from-draft-metadata", **(detail or {})}


def influence(record: dict[str, Any], saved_max: float = 1.0,
              current_year: int | None = None) -> tuple[float, dict[str, Any]]:
    draft_score = core._draft_capital_score(record)
    draft_age = years_since_draft(record, current_year)
    if draft_score is None or draft_age is None or draft_age > MAX_DRAFT_AGE:
        return 0.0, {"eligible": False}
    games = _career_games(record)
    pace = _role_pace(record)
    game_cap = core._game_decay(games * pace)
    time_cap = TIME_CAPS.get(draft_age, 0.0)
    exp_cap = _experience_cap(record)
    draft_factor = 0.35 + 0.65 * (draft_score / 100.0)
    age_factor = _age_factor(record)
    meaningful = core._has_meaningful_professional_evidence(record)
    production_factor = 0.90 if meaningful else 1.0
    cap = max(0.0, min(saved_max, game_cap, time_cap, exp_cap))
    value = round(max(0.0, min(1.0, cap * draft_factor * age_factor * production_factor)), 4)
    return value, {
        "eligible": True, "draftCapitalScore": round(draft_score, 2),
        "draftFactor": round(draft_factor, 4), "ageFactor": round(age_factor, 4),
        "yearsSinceDraft": draft_age, "timeCap": round(time_cap, 4),
        "experienceCap": round(exp_cap, 4), "careerGameEvidence": round(games, 2),
        "roleGamePace": round(pace, 4), "gameCap": round(game_cap, 4),
        "meaningfulProduction": meaningful, "productionFactor": production_factor,
    }


def _working_record(record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Neutralize a false current-season zero when no recent sample exists."""
    working = dict(record)
    summary = dict(record.get("pricingEvidenceSummary") or {})
    pcts = dict(summary.get("percentiles") or {})
    raw = dict(summary.get("rawSignals") or {})
    age = years_since_draft(record)
    guarded = (
        age is not None and age <= MAX_DRAFT_AGE
        and summary.get("recentSampleGamesEstimate") is None
        and (_num(raw.get("recentProduction")) or 0.0) <= 0.0
        and (_num(raw.get("careerProduction")) or 0.0) > 0.0
        and _num(pcts.get("careerProduction")) is not None
    )
    if guarded:
        pcts["recentProduction"] = max(0.0, min(1.0, float(pcts["careerProduction"])))
        pcts["efficiency"] = 0.50
        summary["percentiles"] = pcts
        working["pricingEvidenceSummary"] = summary
    return working, guarded


def fair_value(record: dict[str, Any], current_year: int | None = None) -> tuple[float | None, dict[str, Any] | None]:
    draft_age = years_since_draft(record, current_year)
    if not core.is_nfl(record) or draft_age is None or draft_age > MAX_DRAFT_AGE:
        return None, None
    working, guarded = _working_record(record)
    components = core.production_components(working)
    if components is None: return None, None
    values = {
        "production": float(components["productionScore"]),
        "achievements": core.achievement_score(working),
        "careerRunway": core.career_runway_score(working),
        "availability": core.availability_score(working),
    }
    score = core._clamp(sum(values[k] * w for k, w in core.NFL_VALUE_WEIGHTS.items()))
    career_fair = core.price_from_score(score)
    anchor, maximum, anchor_detail = _anchor(record)
    rookie_influence, factors = influence(record, maximum, current_year)
    fair = career_fair if anchor is None else anchor * rookie_influence + career_fair * (1.0 - rookie_influence)
    fair = round(max(core.PRICE_FLOOR, min(core.PRICE_CEILING, fair)), 2)
    return fair, {
        **components, "valueWeights": dict(core.NFL_VALUE_WEIGHTS),
        "valueInputs": {k: round(v, 2) for k, v in values.items()},
        "valuationScore": round(score, 2), "careerFairValue": round(career_fair, 2),
        "rookieIpoAnchor": anchor, "rookieInfluence": rookie_influence,
        "rookieAnchorReconstruction": anchor_detail, "rookieIpoFactors": factors,
        "rookieIpoMissingRecentSampleGuard": guarded, "fairValue": fair,
        "pricingPrinciple": "production-led NFL value plus a temporary Rookie IPO bridge using draft capital, age, experience, durable games, meaningful NFL production, and role; no player-specific prices",
        "modelVersion": f"{core.MODEL_VERSION}+rookie/{MODEL_VERSION}",
    }


def evidence_signature(record: dict[str, Any], explanation: dict[str, Any]) -> str:
    factors = explanation.get("rookieIpoFactors") or {}
    summary = record.get("pricingEvidenceSummary") or {}
    pcts = summary.get("percentiles") or {}
    payload = {
        "draft": [record.get("draftYear"), record.get("draftRound"), record.get("draftPick")],
        "age": record.get("age"), "experience": record.get("experienceYears"),
        "role": record.get("role"), "games": factors.get("careerGameEvidence"),
        "meaningful": factors.get("meaningfulProduction"),
        "careerPct": round(float(_num(pcts.get("careerProduction")) or 0.0), 2),
        "guard": explanation.get("rookieIpoMissingRecentSampleGuard"), "model": MODEL_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
