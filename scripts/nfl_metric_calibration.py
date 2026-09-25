#!/usr/bin/env python3
"""NFL-only calibration for TalentX pricing inputs.

The shared sports enrichment layer supplies role-aware percentile evidence. This
module converts that evidence into NFL metrics with cleaner semantics:

* Performance measures current playing level from one already-stabilized evidence window.
* Achievements are driven primarily by honors, not accumulated counting stats.
* Audience is independent of performance/awards and never feeds recursively from
  the prior audience score.
* Sample maturity saturates once an NFL player has a representative body of work.

The module intentionally leaves every non-NFL record unchanged.
"""
from __future__ import annotations

import math
from typing import Any

CALIBRATION_VERSION = "1.1-nfl-established-window-single-shrink"

NFL_ROLE_AUDIENCE = {
    "quarterback": 20,
    "wide receiver": 11,
    "receiver": 11,
    "running back": 8,
    "tight end": 6,
    "cornerback": 6,
    "defensive end": 6,
    "edge": 7,
    "safety": 4,
    "linebacker": 3,
    "defensive tackle": 3,
    "offensive tackle": 4,
    "tackle": 4,
    "guard": 1,
    "center": 1,
    "kicker": -5,
    "punter": -7,
    "long snapper": -10,
}


def optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    parsed = optional_number(value)
    if parsed is None:
        parsed = low
    return max(low, min(high, parsed))


def is_nfl(record: dict[str, Any]) -> bool:
    return str(record.get("leagueOrMedium") or "").strip().upper() == "NFL"


def role_audience_adjustment(record: dict[str, Any]) -> float:
    role = str(record.get("role") or "").lower()
    for token, adjustment in NFL_ROLE_AUDIENCE.items():
        if token in role:
            return float(adjustment)
    return 0.0


def sample_maturity(record: dict[str, Any]) -> float:
    """Return 0-100 certainty from representative NFL experience.

    Career games matter early, then rapidly stop mattering. Missing game totals
    for established players fall back to years of professional experience rather
    than being interpreted as zero evidence.
    """
    games = optional_number(record.get("professionalGames"))
    if games is not None and games > 0:
        return round(clamp(100.0 * (1.0 - math.exp(-games / 16.0))), 2)

    years = optional_number(record.get("experienceYears"))
    if years is not None and years > 0:
        equivalent_games = years * 14.0
        return round(clamp(100.0 * (1.0 - math.exp(-equivalent_games / 16.0))), 2)

    stage = str(record.get("careerStage") or "").lower()
    if "rookie" in stage:
        return 18.0
    return 25.0


def _percentiles(record: dict[str, Any]) -> dict[str, float]:
    summary = record.get("pricingEvidenceSummary")
    if not isinstance(summary, dict):
        return {}
    raw = summary.get("percentiles")
    if not isinstance(raw, dict):
        return {}
    output: dict[str, float] = {}
    for key, value in raw.items():
        parsed = optional_number(value)
        if parsed is not None:
            output[str(key)] = clamp(parsed, 0.0, 1.0)
    return output


def _raw_signals(record: dict[str, Any]) -> dict[str, float]:
    summary = record.get("pricingEvidenceSummary")
    if not isinstance(summary, dict):
        return {}
    raw = summary.get("rawSignals")
    if not isinstance(raw, dict):
        return {}
    output: dict[str, float] = {}
    for key, value in raw.items():
        parsed = optional_number(value)
        if parsed is not None:
            output[str(key)] = parsed
    return output


def _award_names(record: dict[str, Any]) -> list[str]:
    summary = record.get("pricingEvidenceSummary")
    if not isinstance(summary, dict):
        return []
    names = summary.get("awardNames")
    if not isinstance(names, list):
        return []
    return [str(value).strip() for value in names if str(value).strip()]


def performance_score(record: dict[str, Any], fallback: float) -> tuple[float, dict[str, float]]:
    pcts = _percentiles(record)
    required = ("recentProduction", "efficiency")
    if any(key not in pcts for key in required):
        return round(clamp(fallback), 1), {}

    recent = pcts["recentProduction"]
    efficiency = pcts["efficiency"]
    career = pcts.get("careerProduction", 0.50)

    # Current level is primarily recent production, supported by efficiency and
    # a smaller durable-career anchor. The career component is deliberately
    # modest so longevity does not masquerade as present performance.
    composite = recent * 0.50 + efficiency * 0.30 + career * 0.20
    raw_score = 20.0 + 78.0 * composite

    # NFL evidence is stabilized once upstream before it reaches this semantic
    # layer. Applying another sample shrink here double-discounts players such as
    # established 40-60 game veterans and early-season stars. Rookie and
    # second-year uncertainty is handled by the separate IPO-transition blend.
    maturity = sample_maturity(record)
    return round(clamp(raw_score, 20.0, 98.0), 1), {
        "recentProductionPct": round(recent, 4),
        "efficiencyPct": round(efficiency, 4),
        "careerProductionPct": round(career, 4),
        "rawPerformanceScore": round(raw_score, 2),
        "sampleMaturity": round(maturity, 2),
        "shrinkFactor": 1.0,
        "evidenceAlreadyStabilized": True,
    }


def _honor_quality_score(record: dict[str, Any]) -> float:
    signals = _raw_signals(record)
    pcts = _percentiles(record)
    points = max(0.0, signals.get("awardPoints", 0.0))
    award_pct = pcts.get("awardPoints", 0.50)

    # Raw award evidence distinguishes the magnitude of a resume while the
    # cohort percentile protects against source-scale differences. This avoids
    # the old behavior where career counting stats supplied 70% of Achievements.
    magnitude = 100.0 * (1.0 - math.exp(-points / 12.0))
    percentile_support = 20.0 + 80.0 * award_pct
    score = magnitude * 0.60 + percentile_support * 0.40

    # When award titles are available, recognize genuinely major individual
    # honors without treating every appearance-level award as equivalent.
    names = " | ".join(name.lower() for name in _award_names(record))
    bonus = 0.0
    if any(token in names for token in ("most valuable", " mvp", "mvp ", "mvp|")):
        bonus += 8.0
    if any(token in names for token in ("offensive player of the year", "defensive player of the year", "dpoy", "opoy")):
        bonus += 6.0
    if "first-team all-pro" in names or "first team all-pro" in names:
        bonus += 5.0
    if "second-team all-pro" in names or "second team all-pro" in names:
        bonus += 3.0
    if "rookie of the year" in names:
        bonus += 3.0
    return clamp(score + bonus)


def _postseason_score(record: dict[str, Any]) -> float:
    names = " | ".join(name.lower() for name in _award_names(record))
    if not names:
        # Unknown is neutral, not zero. Missing title detail should not erase a
        # player's resume, but it also cannot create a postseason premium.
        return 50.0

    score = 35.0
    if "super bowl mvp" in names:
        score += 35.0
    if any(token in names for token in ("super bowl champion", "super bowl championship", "champion")):
        score += 20.0
    if "conference championship" in names:
        score += 8.0
    return clamp(score)


def achievement_score(record: dict[str, Any], fallback: float) -> tuple[float, dict[str, float]]:
    pcts = _percentiles(record)
    if not pcts:
        return round(clamp(fallback), 1), {}

    honors = _honor_quality_score(record)
    postseason = _postseason_score(record)
    career_pct = pcts.get("careerProduction", 0.50)
    milestones = 20.0 + 80.0 * career_pct

    # Honors dominate. Team/postseason accomplishment matters, but far less than
    # individual recognition, and cumulative production is only a milestone
    # component rather than the definition of "Achievements."
    score = honors * 0.70 + postseason * 0.20 + milestones * 0.10
    return round(clamp(score, 8.0, 99.0), 1), {
        "individualHonorsScore": round(honors, 2),
        "postseasonScore": round(postseason, 2),
        "careerMilestoneScore": round(milestones, 2),
    }


def attention_score(record: dict[str, Any], news_count: float | None = None) -> float:
    if news_count is None:
        summary = record.get("pricingEvidenceSummary")
        if isinstance(summary, dict):
            news_count = optional_number(summary.get("newsCount"))
    if news_count is None:
        return 50.0
    count = max(0.0, float(news_count))
    return round(clamp(35.0 + 13.0 * math.log1p(count), 30.0, 95.0), 1)


def audience_score(record: dict[str, Any], news_count: float | None = None) -> tuple[float, dict[str, float]]:
    # Stable role visibility replaces the recursive old-audience input. Repeated
    # refreshes therefore cannot ratchet stars toward a 97-point ceiling.
    established_popularity = clamp(55.0 + role_audience_adjustment(record) * 0.90, 40.0, 82.0)
    attention = attention_score(record, news_count)

    explicit_demand = None
    for key in ("fanDemandScore", "commercialDemandScore", "marketDemandScore"):
        explicit_demand = optional_number(record.get(key))
        if explicit_demand is not None:
            break
    demand = clamp(explicit_demand if explicit_demand is not None else 50.0)

    score = established_popularity * 0.45 + attention * 0.35 + demand * 0.20
    return round(clamp(score, 20.0, 95.0), 1), {
        "establishedPopularity": round(established_popularity, 2),
        "attentionScore": round(attention, 2),
        "commercialDemandScore": round(demand, 2),
    }


def calibrated_metrics(
    record: dict[str, Any],
    metrics: dict[str, Any],
    *,
    news_count: float | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    output = {str(key): clamp(value) for key, value in metrics.items()}
    if not is_nfl(record):
        return output, {}

    performance, performance_detail = performance_score(record, output.get("performance", 50.0))
    achievements, achievement_detail = achievement_score(record, output.get("achievements", 35.0))
    audience, audience_detail = audience_score(record, news_count)

    output["performance"] = performance
    output["achievements"] = achievements
    output["audience"] = audience
    output["attention"] = audience_detail["attentionScore"]

    detail = {
        "version": CALIBRATION_VERSION,
        "performance": performance_detail,
        "achievements": achievement_detail,
        "audience": audience_detail,
        "sampleMaturity": sample_maturity(record),
        "principle": "games establish certainty early; performance and achievements establish value thereafter",
    }
    return output, detail


def calibrate_record(record: dict[str, Any], *, news_count: float | None = None) -> dict[str, Any]:
    if not is_nfl(record):
        return dict(record)
    result = dict(record)
    metrics = result.get("activeMetrics") if isinstance(result.get("activeMetrics"), dict) else {}
    calibrated, detail = calibrated_metrics(result, metrics, news_count=news_count)
    result["activeMetrics"] = calibrated
    result["nflMetricCalibrationVersion"] = CALIBRATION_VERSION
    result["nflMetricCalibration"] = detail
    result["nflAttentionScore"] = calibrated.get("attention", 50.0)
    # Until explicit virtual-trading activity exists, liquidity is neutral
    # rather than inferred from talent or synthetic volume.
    result.setdefault("nflLiquidityScore", 50.0)
    return result
