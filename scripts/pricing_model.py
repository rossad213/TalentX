#!/usr/bin/env python3
"""Evidence-weighted TalentX pricing model.

Active careers use verified performance, achievements, potential, audience and
availability evidence. Drafted rookies use a separate IPO anchor based primarily
on draft capital, then transition automatically toward the active-career model as
professional games accumulate.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVE_WEIGHTS = {
    "performance": 0.30,
    "achievements": 0.25,
    "potential": 0.20,
    "audience": 0.15,
    "availability": 0.10,
}
ATHLETE_ACTIVE_WEIGHTS = {
    "performance": 0.35,
    "achievements": 0.25,
    "potential": 0.15,
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
MODEL_VERSION = "3.5-production-first-athlete"

ROOKIE_SPORT_CONFIG: dict[str, dict[str, Any]] = {
    "NFL": {
        "maxPicks": 257,
        "priceCeiling": 92.0,
        "gameBands": ((0, 1.00), (4, 0.75), (10, 0.50), (17, 0.25), (24, 0.10)),
    },
    "NBA": {
        "maxPicks": 60,
        "priceCeiling": 82.0,
        "gameBands": ((0, 1.00), (10, 0.75), (25, 0.50), (50, 0.25), (82, 0.10)),
    },
    "WNBA": {
        "maxPicks": 36,
        "priceCeiling": 58.0,
        "gameBands": ((0, 1.00), (8, 0.75), (18, 0.50), (30, 0.25), (44, 0.10)),
    },
    "NHL": {
        "maxPicks": 224,
        "priceCeiling": 78.0,
        "gameBands": ((0, 1.00), (15, 0.75), (35, 0.50), (60, 0.25), (82, 0.10)),
    },
    "MLB": {
        "maxPicks": 615,
        "priceCeiling": 68.0,
        "gameBands": ((0, 1.00), (30, 0.75), (80, 0.50), (140, 0.25), (162, 0.10)),
    },
}

ROOKIE_POSITION_VALUES = {
    "NFL": {
        "quarterback": 100, "edge": 88, "defensive end": 86, "wide receiver": 84,
        "offensive tackle": 82, "cornerback": 80, "defensive tackle": 74,
        "linebacker": 70, "tight end": 68, "running back": 64, "safety": 63,
        "guard": 60, "center": 60, "kicker": 35, "punter": 35,
    },
    "NBA": {"wing": 92, "guard": 86, "forward": 85, "center": 81},
    "WNBA": {"wing": 92, "guard": 87, "forward": 85, "center": 82},
    "NHL": {"center": 91, "defenseman": 87, "defender": 87, "wing": 83, "goalie": 75},
    "MLB": {
        "shortstop": 93, "starting pitcher": 88, "pitcher": 84, "center field": 85,
        "catcher": 82, "outfield": 80, "third base": 76, "second base": 75,
        "first base": 70, "relief pitcher": 66,
    },
}


def clamp(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def deterministic_rng(record: dict[str, Any]) -> random.Random:
    key = f"{record.get('id','')}:{record.get('name','')}:{record.get('discipline','')}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def active_weights_for(record: dict[str, Any]) -> dict[str, float]:
    if record.get("primaryCategory") == "Athlete":
        return ATHLETE_ACTIVE_WEIGHTS
    return ACTIVE_WEIGHTS


def active_score(metrics: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    selected = weights or ACTIVE_WEIGHTS
    return round(sum(clamp(metrics.get(key), 0, 100) * weight for key, weight in selected.items()), 1)


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


def rookie_fundamental_from_score(score: float, league: str) -> float:
    config = ROOKIE_SPORT_CONFIG.get(league, {"priceCeiling": 72.0})
    ceiling = float(config.get("priceCeiling", 72.0))
    return round(2.0 + ceiling * (clamp(score, 0, 100) / 100.0) ** 2, 2)


def draft_capital_score(record: dict[str, Any]) -> float | None:
    pick = optional_number(record.get("draftPick"))
    league = str(record.get("leagueOrMedium") or "")
    config = ROOKIE_SPORT_CONFIG.get(league)
    if pick is None or pick < 1 or not config:
        return None
    max_picks = max(2.0, float(config["maxPicks"]))
    pick = clamp(pick, 1, max_picks)
    return round(clamp(100 - 78 * math.sqrt((pick - 1) / (max_picks - 1)), 18, 100), 1)


def rookie_position_value(record: dict[str, Any]) -> float:
    league = str(record.get("leagueOrMedium") or "")
    role = str(record.get("role") or "").lower()
    mapping = ROOKIE_POSITION_VALUES.get(league, {})
    for token, score in mapping.items():
        if token in role:
            return float(score)
    return 72.0


def rookie_development_score(record: dict[str, Any], active_metrics: dict[str, Any]) -> float:
    age = optional_number(record.get("age"))
    active_potential = clamp(active_metrics.get("potential", 72), 0, 100)
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
    return round(clamp(age_score * 0.70 + active_potential * 0.30, 35, 98), 1)


def professional_games(record: dict[str, Any]) -> int:
    value = optional_number(record.get("professionalGames"))
    if value is not None:
        return max(0, int(round(value)))
    experience = optional_number(record.get("experienceYears"))
    if experience is None or experience <= 0:
        return 0
    # Missing game totals after one listed year should not preserve a full IPO anchor.
    if experience <= 1:
        return -1
    return 10_000


def rookie_influence(record: dict[str, Any]) -> float:
    draft_score = draft_capital_score(record)
    league = str(record.get("leagueOrMedium") or "")
    config = ROOKIE_SPORT_CONFIG.get(league)
    if draft_score is None or not config:
        return 0.0
    games = professional_games(record)
    experience = optional_number(record.get("experienceYears"))
    draft_year = optional_number(record.get("draftYear"))
    current_year = datetime.now(timezone.utc).year
    if experience is not None and experience >= 2:
        return 0.0
    if draft_year is not None and draft_year < current_year - 1 and (experience is None or experience <= 0):
        return 0.0
    if games < 0:
        return 0.25
    for maximum_games, influence in config["gameBands"]:
        if games <= int(maximum_games):
            return float(influence)
    return 0.0


def rookie_metrics_for(record: dict[str, Any], active_metrics: dict[str, Any]) -> dict[str, float] | None:
    draft_score = draft_capital_score(record)
    if draft_score is None:
        return None
    existing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else {}
    pre_pro = optional_number(existing.get("preProPerformanceScore"))
    if pre_pro is None:
        pre_pro = clamp(52 + draft_score * 0.38 + clamp(active_metrics.get("achievements", 20), 0, 100) * 0.05, 48, 96)
    opportunity = optional_number(existing.get("opportunityScore"))
    if opportunity is None:
        opportunity = clamp(38 + draft_score * 0.52 + (7 if record.get("starter") else 0), 35, 96)
    position_value = optional_number(existing.get("positionValueScore"))
    if position_value is None:
        position_value = rookie_position_value(record)
    development = optional_number(existing.get("developmentScore"))
    if development is None:
        development = rookie_development_score(record, active_metrics)
    availability = optional_number(existing.get("availabilityScore"))
    if availability is None:
        availability = max(82.0 if record.get("careerStatus") == "Active" else 62.0, clamp(active_metrics.get("availability", 75), 0, 100))
    audience = optional_number(existing.get("audienceScore"))
    if audience is None:
        base_audience = clamp(active_metrics.get("audience", 40), 0, 100)
        audience = clamp(base_audience + max(0.0, draft_score - 55) * 0.45, 25, 92)
    return {
        "draftCapital": round(draft_score, 1),
        "preProPerformance": round(clamp(pre_pro, 0, 100), 1),
        "opportunity": round(clamp(opportunity, 0, 100), 1),
        "positionValue": round(clamp(position_value, 0, 100), 1),
        "development": round(clamp(development, 0, 100), 1),
        "availability": round(clamp(availability, 0, 100), 1),
        "audience": round(clamp(audience, 0, 100), 1),
    }


def active_pricing_components(record: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    active_weights = active_weights_for(record)
    active_model_score = active_score(metrics, active_weights)
    active_fundamental = fundamental_from_score(active_model_score)
    provisional_cap: float | None = None
    if str(record.get("pricingDataStatus", "")).startswith("Provisional"):
        provisional_cap = 62.0
        active_fundamental = min(active_fundamental, provisional_cap)

    influence = rookie_influence(record)
    rookie_metrics = rookie_metrics_for(record, metrics) if influence > 0 else None
    if rookie_metrics:
        ipo_score = rookie_score(rookie_metrics)
        ipo_fundamental = rookie_fundamental_from_score(ipo_score, str(record.get("leagueOrMedium") or ""))
        blended_model_score = round(ipo_score * influence + active_model_score * (1 - influence), 1)
        blended_fundamental = round(ipo_fundamental * influence + active_fundamental * (1 - influence), 2)
        # Career score stays comparable across the market even though rookie IPOs
        # intentionally use a lower price ceiling than established careers.
        blended_score = round(math.sqrt(max(0.0, blended_fundamental - 2.0) / 180.0) * 100.0, 1)
    else:
        ipo_score = None
        ipo_fundamental = None
        blended_model_score = active_model_score
        blended_score = active_model_score
        blended_fundamental = active_fundamental
        influence = 0.0

    return {
        "activeWeights": active_weights,
        "activeScore": active_model_score,
        "activeFundamental": round(active_fundamental, 2),
        "rookieMetrics": rookie_metrics,
        "rookieScore": ipo_score,
        "rookieFundamental": ipo_fundamental,
        "rookieInfluence": influence,
        "blendedModelScore": blended_model_score,
        "score": blended_score,
        "fundamental": blended_fundamental,
        "limitedEvidenceCap": provisional_cap if not rookie_metrics else None,
    }


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
    """Build conservative metrics from roster evidence only."""
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
    performance = clamp(metrics.get("performance", metrics.get("preProPerformance", metrics.get("legacy", 50))), 0, 100)
    data_confidence = clamp(record.get("pricingConfidence", record.get("dataConfidence", 0.5)), 0, 1)

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
    components = active_pricing_components(result, metrics)
    score = float(components["score"])
    fundamental = float(components["fundamental"])
    influence = float(components["rookieInfluence"])
    rookie_metrics = components["rookieMetrics"]

    result["activeMetrics"] = metrics
    result["careerScore"] = score
    result["fundamentalValue"] = round(fundamental, 2)
    result["pricingModelVersion"] = MODEL_VERSION

    market_metrics: dict[str, Any] = metrics
    if rookie_metrics:
        games = professional_games(result)
        draft_pct = int(round(influence * 100))
        pro_pct = 100 - draft_pct
        transition_stage = "Opening IPO" if games == 0 else f"Rookie transition: {draft_pct}% draft / {pro_pct}% professional evidence"
        result["modelType"] = "Rookie transition model"
        result["pricingConfidence"] = max(clamp(result.get("pricingConfidence", 0.78), 0, 1), 0.78)
        if games == 0 or not str(result.get("pricingDataStatus", "")).startswith("Evidence enriched"):
            result["pricingDataStatus"] = "Rookie IPO — verified draft position; awaiting professional statistics"
        else:
            result["pricingDataStatus"] = f"Rookie transition — {draft_pct}% draft anchor, {pro_pct}% professional evidence"
        result["rookiePricing"] = {
            "draftSport": result.get("leagueOrMedium"),
            "draftYear": result.get("draftYear"),
            "draftRound": result.get("draftRound"),
            "overallPick": result.get("draftPick"),
            "position": result.get("role"),
            "draftCapitalScore": rookie_metrics["draftCapital"],
            "preProPerformanceScore": rookie_metrics["preProPerformance"],
            "opportunityScore": rookie_metrics["opportunity"],
            "positionValueScore": rookie_metrics["positionValue"],
            "developmentScore": rookie_metrics["development"],
            "availabilityScore": rookie_metrics["availability"],
            "audienceScore": rookie_metrics["audience"],
            "rookieScore": components["rookieScore"],
            "ipoPrice": components["rookieFundamental"],
            "activeModelPrice": components["activeFundamental"],
            "draftInfluencePct": draft_pct,
            "professionalEvidencePct": pro_pct,
            "professionalGames": max(0, games),
            "transitionStage": transition_stage,
        }
        market_metrics = {
            "performance": rookie_metrics["preProPerformance"] * influence + metrics.get("performance", 50) * (1 - influence),
            "audience": rookie_metrics["audience"] * influence + metrics.get("audience", 50) * (1 - influence),
        }
    else:
        result["modelType"] = "Active career model"
        result.pop("rookiePricing", None)
        if str(result.get("pricingDataStatus", "")).startswith("Rookie") and professional_games(result) > 0:
            result["pricingDataStatus"] = "Evidence enriched — professional statistics; draft influence expired"

    result["pricingAudit"] = {
        "weights": components["activeWeights"],
        "performanceFormula": (
            "24 + 72 × (70% recent-production percentile + 30% efficiency percentile)"
            if result.get("primaryCategory") == "Athlete"
            else None
        ),
        "usageIncludedInPerformance": False if result.get("primaryCategory") == "Athlete" else None,
        "availabilityRule": (
            "75 active / 55 inactive pending normalized games-available evidence"
            if result.get("primaryCategory") == "Athlete"
            else None
        ),
        "activeScore": components["activeScore"],
        "activeFundamental": components["activeFundamental"],
        "rookieWeights": ROOKIE_WEIGHTS if rookie_metrics else None,
        "rookieScore": components["rookieScore"],
        "rookieFundamental": components["rookieFundamental"],
        "rookieInfluence": influence,
        "blendedModelScore": components["blendedModelScore"],
        "score": score,
        "fundamentalFormula": "Draft IPO anchor blended with active-career value as professional games accumulate" if rookie_metrics else "2 + 180 × (score ÷ 100)²",
        "limitedEvidenceCap": components["limitedEvidenceCap"],
    }
    result.update(controlled_market_fields(result, score, market_metrics, fundamental))
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
