#!/usr/bin/env python3
"""Evidence-weighted TalentX pricing model.

The model is deterministic and deliberately conservative when career evidence is
missing. High prices require high metric scores backed by either curated records
or explicit evidence overrides. Roster membership alone can never create a star
valuation.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import unicodedata
from pathlib import Path
from typing import Any

ACTIVE_WEIGHTS = {
    "performance": 0.30,
    "achievements": 0.25,
    "potential": 0.20,
    "audience": 0.15,
    "availability": 0.10,
}
LEGACY_WEIGHTS = {
    "legacy": 0.35,
    "audience": 0.25,
    "postCareer": 0.20,
    "recognition": 0.15,
    "liquidity": 0.05,
}
ROOKIE_WEIGHTS = {
    "draftCapital": 0.35,
    "preProPerformance": 0.20,
    "opportunity": 0.15,
    "positionValue": 0.10,
    "development": 0.08,
    "availability": 0.07,
    "audience": 0.05,
}
MODEL_VERSION = "3.2-achievements-weighted"


def clamp(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def deterministic_rng(record: dict[str, Any]) -> random.Random:
    key = f"{record.get('id','')}:{record.get('name','')}:{record.get('discipline','')}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def active_score(metrics: dict[str, Any]) -> float:
    return round(sum(clamp(metrics.get(key), 0, 100) * weight for key, weight in ACTIVE_WEIGHTS.items()), 1)


def legacy_score(metrics: dict[str, Any]) -> float:
    return round(sum(clamp(metrics.get(key), 0, 100) * weight for key, weight in LEGACY_WEIGHTS.items()), 1)


def rookie_score(metrics: dict[str, Any]) -> float:
    return round(sum(clamp(metrics.get(key), 0, 100) * weight for key, weight in ROOKIE_WEIGHTS.items()), 1)


def fundamental_from_score(score: float, *, legacy: bool = False, under_review: bool = False) -> float:
    """Non-linear price curve that separates fringe, established and elite careers."""
    ceiling = 160.0 if legacy else 180.0
    base = 2.0
    value = base + ceiling * (clamp(score, 0, 100) / 100.0) ** 2
    if under_review:
        value *= 0.90
    return round(max(2.0, value), 2)


LEAGUE_AUDIENCE = {
    "NFL": 18, "NBA": 18, "MLB": 15, "NHL": 12, "WNBA": 12,
    "Premier League": 18, "LaLiga": 15, "Bundesliga": 14, "Serie A": 14,
    "Ligue 1": 13, "Major League Soccer": 10, "NWSL": 8,
}
NFL_ROLE_AUDIENCE = {
    "quarterback": 20, "wide receiver": 11, "running back": 8, "tight end": 6,
    "cornerback": 6, "safety": 4, "linebacker": 3, "defensive end": 6,
    "edge": 7, "defensive tackle": 3, "offensive tackle": 4, "guard": 1,
    "center": 1, "kicker": -5, "punter": -7, "long snapper": -10,
}


def role_audience_adjustment(record: dict[str, Any]) -> float:
    role = str(record.get("role") or "").lower()
    league = str(record.get("leagueOrMedium") or "")
    if league == "NFL":
        for token, value in NFL_ROLE_AUDIENCE.items():
            if token in role:
                return value
    if "goalkeeper" in role or "goalie" in role:
        return 1
    if any(token in role for token in ("forward", "striker", "wing", "guard")):
        return 5
    return 0


def provisional_active_metrics(record: dict[str, Any]) -> dict[str, float]:
    """Build conservative metrics from roster evidence only.

    These are priors, not claims of career greatness. Missing awards/statistics
    keeps achievement and performance values capped until richer evidence exists.
    """
    exp_raw = record.get("experienceYears")
    try:
        exp = max(0, int(exp_raw)) if exp_raw is not None else None
    except (TypeError, ValueError):
        exp = None
    age_raw = record.get("age")
    try:
        age = int(age_raw) if age_raw is not None else None
    except (TypeError, ValueError):
        age = None
    starter = bool(record.get("starter"))
    league = str(record.get("leagueOrMedium") or "")

    if exp is None:
        performance = 38.0
        achievements = 18.0
        potential = 58.0 if age is None else clamp(93 - max(0, age - 19) * 4.0, 28, 88)
    else:
        performance = 36 + min(exp, 8) * 3.2 + (10 if starter else 0)
        achievements = 14 + min(exp, 10) * 2.8 + (5 if starter and exp >= 3 else 0)
        if age is not None:
            potential = clamp(94 - max(0, age - 19) * 4.2, 24, 92)
        else:
            potential = clamp(88 - exp * 5.0, 25, 88)

    # Roster-only evidence must not produce elite scores.
    performance = clamp(performance, 28, 68)
    achievements = clamp(achievements, 10, 55)
    audience = clamp(25 + LEAGUE_AUDIENCE.get(league, 5) + role_audience_adjustment(record), 15, 65)
    availability = 78 if record.get("careerStatus") == "Active" else 58
    consistency = clamp(38 + (min(exp or 0, 10) * 3.0), 35, 68)

    return {
        "performance": round(performance, 1),
        "achievements": round(achievements, 1),
        "potential": round(potential, 1),
        "audience": round(audience, 1),
        "availability": round(availability, 1),
        "consistency": round(consistency, 1),
    }


def load_overrides(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", payload) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("pricing_overrides.json must contain a records array")
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        output[(normalize(item["name"]), normalize(item.get("discipline", "")))] = item
    return output


def override_for(record: dict[str, Any], overrides: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    exact = overrides.get((normalize(record.get("name", "")), normalize(record.get("discipline", ""))))
    return exact or overrides.get((normalize(record.get("name", "")), ""))


def controlled_market_fields(record: dict[str, Any], score: float, metrics: dict[str, Any], fundamental: float) -> dict[str, Any]:
    rng = deterministic_rng(record)
    audience = clamp(metrics.get("audience"), 0, 100)
    performance = clamp(metrics.get("performance", metrics.get("legacy", 50)), 0, 100)
    data_confidence = clamp(record.get("pricingConfidence", record.get("dataConfidence", 0.5)), 0, 1)

    # Demand and momentum may move the market slightly, but may not overwhelm the model.
    demand = clamp((audience - 50) * 0.075 + rng.uniform(-0.6, 0.6), -4.0, 5.0)
    momentum = clamp((performance - 50) * 0.045 + rng.uniform(-0.5, 0.5), -3.0, 3.0)
    market = fundamental * (1 + demand / 100.0) * (1 + momentum / 100.0)
    if record.get("marketSegment") == "Under Review":
        market *= 0.82 + 0.18 * data_confidence
    market = round(max(2.0, market), 2)

    volatility = 1.2 + (100 - score) / 100 * 1.6
    daily = round(rng.uniform(-volatility, volatility), 2)
    base_volume = 8_000 + audience * 5_500 + max(0, score - 40) * 3_000
    volume = int(max(5_000, base_volume * rng.uniform(0.75, 1.25)))

    trend: list[float] = []
    value = market / (1 + daily / 100 if abs(daily) < 99 else 1)
    for _ in range(18):
        value *= 1 + rng.uniform(-0.012, 0.013)
        trend.append(round(max(1.0, value), 2))
    trend[-1] = market
    return {
        "marketPrice": market,
        "dailyChange": daily,
        "demandPremiumPct": round(demand, 2),
        "momentumPct": round(momentum, 2),
        "volume": volume,
        "trend": trend,
    }


def apply_active_pricing(record: dict[str, Any], overrides: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    result = dict(record)
    override = override_for(result, overrides)
    existing = result.get("activeMetrics") if isinstance(result.get("activeMetrics"), dict) else None

    if override and isinstance(override.get("activeMetrics"), dict):
        metrics = {**provisional_active_metrics(result), **override["activeMetrics"]}
        result["pricingDataStatus"] = "Verified career-evidence override"
        result["pricingConfidence"] = clamp(override.get("pricingConfidence", 0.92), 0, 1)
        result["pricingEvidence"] = override.get("evidence", [])
    elif existing and result.get("sourceName") == "TalentX current-first seed":
        metrics = dict(existing)
        result["pricingDataStatus"] = "Curated prototype metrics — verification required"
        result["pricingConfidence"] = clamp(result.get("dataConfidence", 0.70), 0, 1)
        result["pricingEvidence"] = []
    elif existing and result.get("pricingDataStatus") not in (None, ""):
        metrics = dict(existing)
    else:
        metrics = provisional_active_metrics(result)
        result["pricingDataStatus"] = "Provisional — roster, experience and role evidence only"
        result["pricingConfidence"] = 0.48 if result.get("experienceYears") is not None else 0.38
        result["pricingEvidence"] = [result.get("sourceUrl")] if result.get("sourceUrl") else []

    metrics = {key: round(clamp(value, 0, 100), 1) for key, value in metrics.items()}
    metrics.setdefault("consistency", round((metrics.get("performance", 50) + metrics.get("availability", 70)) / 2, 1))
    score = active_score(metrics)
    fundamental = fundamental_from_score(score)

    # Unsupported roster-only records cannot receive star-level valuations.
    if str(result.get("pricingDataStatus", "")).startswith("Provisional"):
        fundamental = min(fundamental, 62.0)

    result["activeMetrics"] = metrics
    result["careerScore"] = score
    result["fundamentalValue"] = round(fundamental, 2)
    result["modelType"] = "Active career model"
    result["pricingModelVersion"] = MODEL_VERSION
    result["pricingAudit"] = {
        "weights": ACTIVE_WEIGHTS,
        "score": score,
        "fundamentalFormula": "2 + 180 × (score ÷ 100)²",
        "limitedEvidenceCap": 62.0 if str(result.get("pricingDataStatus", "")).startswith("Provisional") else None,
    }
    result.update(controlled_market_fields(result, score, metrics, fundamental))
    return result


def apply_legacy_pricing(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    metrics = result.get("legacyMetrics") if isinstance(result.get("legacyMetrics"), dict) else {}
    metrics = {key: round(clamp(metrics.get(key, 45), 0, 100), 1) for key in LEGACY_WEIGHTS}
    score = legacy_score(metrics)
    under_review = result.get("marketSegment") == "Under Review"
    fundamental = fundamental_from_score(score, legacy=True, under_review=under_review)
    result["legacyMetrics"] = metrics
    result["careerScore"] = score
    result["fundamentalValue"] = fundamental
    result["pricingModelVersion"] = MODEL_VERSION
    result["pricingConfidence"] = clamp(result.get("dataConfidence", 0.45 if under_review else 0.65), 0, 1)
    result["pricingDataStatus"] = "Under review — confidence-adjusted" if under_review else "Legacy metrics model"
    result["pricingAudit"] = {
        "weights": LEGACY_WEIGHTS,
        "score": score,
        "fundamentalFormula": "2 + 160 × (score ÷ 100)²" + (" × 0.90 review discount" if under_review else ""),
    }
    result.update(controlled_market_fields(result, score, metrics, fundamental))
    return result


def apply_pricing(record: dict[str, Any], overrides: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    if record.get("marketSegment") in {"Legacy", "Under Review"} or str(record.get("modelType", "")).startswith("Legacy"):
        return apply_legacy_pricing(record)
    return apply_active_pricing(record, overrides)


def apply_pricing_to_records(records: list[dict[str, Any]], overrides: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    return [apply_pricing(record, overrides) for record in records]
