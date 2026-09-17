#!/usr/bin/env python3
"""NFL veteran valuation using the same Production + Potential market architecture.

Rookies and second-year players remain on the separate Rookie IPO transition.
For established NFL players, fundamental value is built from two top-level buckets:
70% Production and 30% Potential. Sport-specific evidence is normalized inside
those buckets; no NBA code or NBA pricing constants are touched here.

This module computes fundamental/fair value only. Live market price remains a
separate state that moves from games, role changes, injuries, awards, and trading.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import nfl_production_pricing as core

MODEL_VERSION = "1.0-nfl-production-potential-veteran"
TOP_LEVEL_WEIGHTS = {"production": 0.70, "potential": 0.30}
PRODUCTION_SUBWEIGHTS = {
    "normalizedProduction": 0.60,
    "achievements": 0.20,
    "consistency": 0.20,
}
POTENTIAL_SUBWEIGHTS = {
    "careerRunway": 0.45,
    "roleOpportunity": 0.25,
    "availability": 0.20,
    "development": 0.10,
}


def _num(value: Any) -> float | None:
    return core._number(value)


def years_since_draft(record: dict[str, Any], current_year: int | None = None) -> int | None:
    year = _num(record.get("draftYear"))
    if year is None:
        return None
    now_year = int(current_year or datetime.now(timezone.utc).year)
    return max(0, now_year - int(round(year)))


def is_ipo_player(record: dict[str, Any], current_year: int | None = None) -> bool:
    """Rookies and second-year players stay on the IPO path."""
    draft_age = years_since_draft(record, current_year)
    if draft_age is not None:
        return draft_age <= 1
    experience = _num(record.get("experienceYears"))
    if experience is not None:
        return experience <= 2
    stage = str(record.get("careerStage") or "").strip().lower()
    return any(token in stage for token in ("rookie", "drafted", "pre-draft", "prospect"))


def _metrics(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}


def consistency_score(record: dict[str, Any], production: float) -> float:
    metrics = _metrics(record)
    explicit = _num(metrics.get("consistency"))
    if explicit is not None:
        return round(core._clamp(explicit), 2)
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    pcts = summary.get("percentiles") if isinstance(summary.get("percentiles"), dict) else {}
    career = _num(pcts.get("careerProduction"))
    recent = _num(pcts.get("recentProduction"))
    if career is not None and recent is not None:
        return round(core._clamp((career * 100.0) * 0.65 + (recent * 100.0) * 0.35), 2)
    return round(core._clamp(production), 2)


def role_opportunity_score(record: dict[str, Any]) -> float:
    role_status = str(record.get("roleStatus") or "").strip().lower()
    if record.get("starter") is True or record.get("isStarter") is True or role_status in {
        "starter", "starting", "first team", "first-team"
    }:
        return 92.0
    if role_status in {"rotation", "rotational", "committee", "sixth man"}:
        return 72.0
    if role_status in {"reserve", "backup", "bench", "demoted"}:
        return 45.0
    # Do not invent a starter premium when role evidence is missing.
    return 60.0


def development_score(record: dict[str, Any]) -> float:
    metrics = _metrics(record)
    explicit = _num(metrics.get("potential"))
    runway = core.career_runway_score(record)
    if explicit is None:
        return round(core._clamp(runway), 2)
    # Keep the saved development signal, but anchor it to evidence-based runway.
    return round(core._clamp(explicit * 0.55 + runway * 0.45), 2)


def production_bucket(record: dict[str, Any]) -> tuple[float | None, dict[str, float] | None]:
    components = core.production_components(record)
    if components is None:
        return None, None
    normalized = float(components["productionScore"])
    achievements = core.achievement_score(record)
    consistency = consistency_score(record, normalized)
    values = {
        "normalizedProduction": normalized,
        "achievements": achievements,
        "consistency": consistency,
    }
    score = sum(values[key] * PRODUCTION_SUBWEIGHTS[key] for key in PRODUCTION_SUBWEIGHTS)
    return round(core._clamp(score), 2), {key: round(value, 2) for key, value in values.items()}


def potential_bucket(record: dict[str, Any]) -> tuple[float, dict[str, float]]:
    values = {
        "careerRunway": core.career_runway_score(record),
        "roleOpportunity": role_opportunity_score(record),
        "availability": core.availability_score(record),
        "development": development_score(record),
    }
    score = sum(values[key] * POTENTIAL_SUBWEIGHTS[key] for key in POTENTIAL_SUBWEIGHTS)
    return round(core._clamp(score), 2), {key: round(value, 2) for key, value in values.items()}


def fair_value(record: dict[str, Any], current_year: int | None = None) -> tuple[float | None, dict[str, Any] | None]:
    if not core.is_nfl(record) or is_ipo_player(record, current_year):
        return None, None
    if str(record.get("careerStatus") or "").strip().lower() not in {"", "active"}:
        return None, None

    production, production_inputs = production_bucket(record)
    if production is None or production_inputs is None:
        return None, None
    potential, potential_inputs = potential_bucket(record)
    valuation_score = core._clamp(
        production * TOP_LEVEL_WEIGHTS["production"]
        + potential * TOP_LEVEL_WEIGHTS["potential"]
    )
    fair = core.price_from_score(valuation_score)
    return fair, {
        "leagueWide": True,
        "valuationArchitecture": "Production + Potential",
        "topLevelWeights": dict(TOP_LEVEL_WEIGHTS),
        "productionScore": production,
        "potentialScore": potential,
        "productionSubweights": dict(PRODUCTION_SUBWEIGHTS),
        "potentialSubweights": dict(POTENTIAL_SUBWEIGHTS),
        "productionInputs": production_inputs,
        "potentialInputs": potential_inputs,
        "valuationScore": round(valuation_score, 2),
        "fairValue": round(fair, 2),
        "pricingPrinciple": "NFL veteran fundamental value = 70% Production + 30% Potential; live market price is separate and event/trading driven",
        "modelVersion": f"{core.MODEL_VERSION}+veteran/{MODEL_VERSION}",
    }
