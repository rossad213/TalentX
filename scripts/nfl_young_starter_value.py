#!/usr/bin/env python3
"""League-wide NFL young-starter valuation support.

This module adds verified role/opportunity and long career runway to the existing
production-led NFL model without hard-coding any player prices. It is intentionally
conservative: production remains the largest input and this layer may raise an
undervalued qualifying starter, but it never lowers the existing fundamental.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import nfl_production_pricing as core

MODEL_VERSION = "1.0-nfl-young-starter-role-runway"
QB_WEIGHTS = {
    "production": 0.47,
    "achievements": 0.08,
    "careerRunway": 0.13,
    "availability": 0.05,
    "opportunity": 0.20,
    "entryCapital": 0.07,
}
OTHER_WEIGHTS = {
    "production": 0.60,
    "achievements": 0.12,
    "careerRunway": 0.12,
    "availability": 0.05,
    "opportunity": 0.08,
    "entryCapital": 0.03,
}


def _num(value: Any) -> float | None:
    return core._number(value)


def is_quarterback(record: dict[str, Any]) -> bool:
    role = str(record.get("role") or "").lower()
    return role == "qb" or "quarterback" in role


def is_verified_starter(record: dict[str, Any]) -> bool:
    if record.get("starter") is True or record.get("isStarter") is True:
        return True
    role_status = str(record.get("roleStatus") or "").strip().lower()
    if role_status in {"starter", "starting", "first team", "first-team"}:
        return True
    evidence = record.get("situationEvidence") if isinstance(record.get("situationEvidence"), dict) else {}
    return evidence.get("starter") is True or str(evidence.get("roleStatus") or "").strip().lower() in {
        "starter", "starting", "first team", "first-team"
    }


def _young_window(record: dict[str, Any]) -> bool:
    age = _num(record.get("age"))
    experience = _num(record.get("experienceYears"))
    if is_quarterback(record):
        if age is not None and age <= 30:
            return True
        return experience is not None and experience <= 5
    if age is not None and age <= 28:
        return True
    return experience is not None and experience <= 4


def _qb_runway_floor(record: dict[str, Any]) -> float:
    age = _num(record.get("age"))
    if age is None: return 90.0
    if age <= 24: return 98.0
    if age <= 26: return 95.0
    if age <= 28: return 90.0
    if age <= 30: return 84.0
    return 70.0


def career_runway(record: dict[str, Any]) -> float:
    base = core.career_runway_score(record)
    if is_quarterback(record):
        return round(max(base, _qb_runway_floor(record)), 2)
    return round(base, 2)


def opportunity_score(record: dict[str, Any]) -> float:
    if not is_verified_starter(record):
        return 45.0
    return 100.0 if is_quarterback(record) else 88.0


def entry_capital_score(record: dict[str, Any]) -> float:
    draft = core._draft_capital_score(record)
    if draft is None:
        return 50.0
    experience = _num(record.get("experienceYears"))
    # Draft capital is useful context early, but it should disappear as NFL evidence matures.
    if experience is not None and experience > 4:
        return 50.0
    return round(float(draft), 2)


def fair_value(record: dict[str, Any]) -> tuple[float | None, dict[str, Any] | None]:
    if not core.is_nfl(record):
        return None, None
    if str(record.get("careerStatus") or "").strip().lower() not in {"", "active"}:
        return None, None
    if not is_verified_starter(record) or not _young_window(record):
        return None, None
    if not core._has_meaningful_professional_evidence(record):
        return None, None

    components = core.production_components(record)
    if components is None:
        return None, None

    production = float(components["productionScore"])
    achievements = core.achievement_score(record)
    runway = career_runway(record)
    availability = core.availability_score(record)
    opportunity = opportunity_score(record)
    entry_capital = entry_capital_score(record)
    weights = QB_WEIGHTS if is_quarterback(record) else OTHER_WEIGHTS
    values = {
        "production": production,
        "achievements": achievements,
        "careerRunway": runway,
        "availability": availability,
        "opportunity": opportunity,
        "entryCapital": entry_capital,
    }
    adjusted_score = core._clamp(sum(values[key] * weights[key] for key in weights))
    adjusted_fair = core.price_from_score(adjusted_score)

    base_values = {
        "production": production,
        "achievements": achievements,
        "careerRunway": core.career_runway_score(record),
        "availability": availability,
    }
    base_score = core._clamp(sum(base_values[key] * core.NFL_VALUE_WEIGHTS[key] for key in core.NFL_VALUE_WEIGHTS))
    base_fair = core.price_from_score(base_score)
    fair = round(max(base_fair, adjusted_fair), 2)

    return fair, {
        **components,
        "leagueWide": True,
        "verifiedStarter": True,
        "quarterback": is_quarterback(record),
        "baseCareerFairValue": round(base_fair, 2),
        "youngStarterFairValue": round(adjusted_fair, 2),
        "fairValue": fair,
        "valuationScore": round(adjusted_score, 2),
        "valueInputs": {key: round(float(value), 2) for key, value in values.items()},
        "valueWeights": dict(weights),
        "upliftApplied": adjusted_fair > base_fair,
        "pricingPrinciple": "league-wide production-led NFL valuation with verified starter opportunity, age/position-aware career runway, and small early-career draft context; no player-specific prices",
        "modelVersion": f"{core.MODEL_VERSION}+young-starter/{MODEL_VERSION}",
    }


def evidence_signature(record: dict[str, Any], explanation: dict[str, Any]) -> str:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    pcts = summary.get("percentiles") if isinstance(summary.get("percentiles"), dict) else {}
    payload = {
        "starter": is_verified_starter(record),
        "role": record.get("role"),
        "roleStatus": record.get("roleStatus"),
        "age": record.get("age"),
        "experience": record.get("experienceYears"),
        "draft": [record.get("draftYear"), record.get("draftRound"), record.get("draftPick")],
        "professionalGames": record.get("professionalGames"),
        "recentProduction": pcts.get("recentProduction"),
        "careerProduction": pcts.get("careerProduction"),
        "efficiency": pcts.get("efficiency"),
        "awardPoints": pcts.get("awardPoints"),
        "fair": explanation.get("fairValue"),
        "model": MODEL_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:20]
