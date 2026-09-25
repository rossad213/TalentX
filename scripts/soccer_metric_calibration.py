#!/usr/bin/env python3
"""Soccer-specific global valuation calibration.

The generic athlete enrichment ranks players inside league/position cohorts.
That is useful for local performance context, but a 90th-percentile third-tier
attacker is not globally equivalent to a 90th-percentile top-flight attacker.

This module keeps the local evidence intact and produces a separate
`soccerGlobalMetrics` view for fair-value pricing. It also separates
current-performance evidence from legacy/Wikidata career proxies and gives
Soccer a more forward-looking metric mix than the generic athlete model.

No player-specific prices or rankings are hard-coded here.
"""
from __future__ import annotations

import math
import re
from typing import Any

SOCCER_CALIBRATION_VERSION = "1.0-global-competition-and-runway"

# Soccer value is more forward-looking than the generic athlete mix: current
# performance and future runway receive more weight, while career achievements
# and longevity remain meaningful but cannot dominate the ranking.
SOCCER_METRIC_WEIGHTS = {
    "performance": 0.36,
    "achievements": 0.18,
    "consistency": 0.14,
    "potential": 0.22,
    "availability": 0.10,
}

# These are divisional translation factors, not subjective player rankings.
# Top-flight competitions are left at 1.00. The explicitly mapped leagues are
# lower divisions represented in the current TalentX Soccer universe.
LOWER_DIVISION_FACTORS = {
    "english championship": (2, 0.82),
    "efl championship": (2, 0.82),
    "2 bundesliga": (2, 0.82),
    "2. bundesliga": (2, 0.82),
    "laliga 2": (2, 0.82),
    "la liga 2": (2, 0.82),
    "segunda division": (2, 0.82),
    "english league one": (3, 0.68),
    "efl league one": (3, 0.68),
    "english league two": (4, 0.58),
    "efl league two": (4, 0.58),
}

GLOBAL_METRIC_FLOOR = 25.0


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    number = _num(value, low)
    assert number is not None
    return max(low, min(high, number))


def _league_key(value: Any) -> str:
    text = re.sub(r"[^a-z0-9.]+", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def is_soccer(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() in {"soccer", "football"}
    )


def competition_context(record: dict[str, Any]) -> tuple[int, float, str]:
    """Return domestic division level, global translation factor and rationale."""
    key = _league_key(record.get("leagueOrMedium"))
    level, factor = LOWER_DIVISION_FACTORS.get(key, (1, 1.0))
    rationale = (
        "top-flight/no lower-division discount"
        if level == 1
        else f"domestic division {level} translation"
    )
    return level, factor, rationale


def _globalize(value: Any, factor: float) -> float:
    local = _clamp(value, 0, 100)
    return round(_clamp(GLOBAL_METRIC_FLOOR + (local - GLOBAL_METRIC_FLOOR) * factor), 2)


def _percentiles(record: dict[str, Any]) -> dict[str, float]:
    summary = record.get("pricingEvidenceSummary")
    values = summary.get("percentiles") if isinstance(summary, dict) else None
    if not isinstance(values, dict):
        return {}
    output: dict[str, float] = {}
    for key, value in values.items():
        parsed = _num(value)
        if parsed is not None:
            output[str(key)] = max(0.0, min(1.0, parsed))
    return output


def _rebuild_stat_metrics(record: dict[str, Any], metrics: dict[str, float]) -> tuple[dict[str, float], bool]:
    """Rebuild production concepts from saved cohort percentiles when available."""
    pcts = _percentiles(record)
    recent = pcts.get("recentProduction")
    efficiency = pcts.get("efficiency")
    if recent is None or efficiency is None:
        return metrics, False

    output = dict(metrics)
    output["performance"] = round(_clamp(24 + 72 * (recent * 0.70 + efficiency * 0.30), 20, 98), 2)

    career = pcts.get("careerProduction")
    awards = pcts.get("awardPoints")
    if career is not None and awards is not None:
        output["achievements"] = round(_clamp(8 + 88 * (career * 0.70 + awards * 0.30), 8, 99), 2)
        output["consistency"] = round(_clamp(24 + 72 * (career * 0.65 + recent * 0.35), 24, 97), 2)
    return output, True


def _legacy_proxy_reliability(record: dict[str, Any]) -> float:
    """Discount current-performance proxies when only career/Wikidata evidence exists."""
    status = str(record.get("pricingDataStatus") or "")
    if not status.startswith("Provisional"):
        return 1.0
    if not record.get("soccerEvidenceStatus"):
        return 1.0

    age = _num(record.get("age"))
    if age is None or age < 31:
        return 1.0
    if age >= 41:
        return 0.56
    if age >= 37:
        return max(0.56, 0.72 - (age - 37) * 0.04)
    if age >= 34:
        return 0.82
    return 0.92


def _apply_proxy_reliability(record: dict[str, Any], metrics: dict[str, float]) -> tuple[dict[str, float], float]:
    reliability = _legacy_proxy_reliability(record)
    if reliability >= 0.999:
        return metrics, 1.0
    output = dict(metrics)
    for key in ("performance", "consistency"):
        value = _clamp(output.get(key, 50))
        output[key] = round(50.0 + (value - 50.0) * reliability, 2)
    return output, reliability


def _apply_runway(metrics: dict[str, float], record: dict[str, Any]) -> tuple[dict[str, float], float | None]:
    """Cap late-career future potential without erasing current ability or legacy."""
    age = _num(record.get("age"))
    if age is None or age < 37:
        return metrics, None

    cap = max(18.0, 38.0 - (age - 37.0) * 6.0)
    output = dict(metrics)
    output["potential"] = round(min(_clamp(output.get("potential", 50)), cap), 2)
    return output, round(cap, 2)


def calibrated_metrics(record: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any]]:
    """Return globally comparable Soccer metrics plus an explainable audit."""
    raw = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    metrics = {
        key: _clamp(raw.get(key, 50))
        for key in ("performance", "achievements", "consistency", "potential", "availability", "audience")
    }

    metrics, rebuilt_from_stats = _rebuild_stat_metrics(record, metrics)
    if rebuilt_from_stats:
        proxy_reliability = 1.0
    else:
        metrics, proxy_reliability = _apply_proxy_reliability(record, metrics)
    metrics, runway_cap = _apply_runway(metrics, record)

    level, factor, rationale = competition_context(record)
    global_metrics = dict(metrics)
    for key in ("performance", "achievements", "consistency"):
        global_metrics[key] = _globalize(metrics[key], factor)

    # Potential should travel better than current production: young prospects can
    # be globally valuable before they reach a top division. Apply only a small
    # lower-division translation to future runway.
    if factor < 1.0:
        potential_factor = 0.90 + 0.10 * factor
        global_metrics["potential"] = round(
            _clamp(GLOBAL_METRIC_FLOOR + (metrics["potential"] - GLOBAL_METRIC_FLOOR) * potential_factor),
            2,
        )

    audit = {
        "version": SOCCER_CALIBRATION_VERSION,
        "competition": str(record.get("leagueOrMedium") or ""),
        "competitionLevel": level,
        "competitionFactor": factor,
        "competitionRationale": rationale,
        "rebuiltFromStatPercentiles": rebuilt_from_stats,
        "legacyProxyReliability": round(proxy_reliability, 3),
        "lateCareerPotentialCap": runway_cap,
        "localMetrics": {key: round(value, 2) for key, value in metrics.items()},
        "globalMetrics": {key: round(value, 2) for key, value in global_metrics.items()},
        "weights": SOCCER_METRIC_WEIGHTS,
    }
    return global_metrics, audit


def calibrate_record(record: dict[str, Any]) -> dict[str, Any]:
    if not is_soccer(record):
        return dict(record)
    result = dict(record)
    metrics, audit = calibrated_metrics(result)
    result["soccerGlobalMetrics"] = metrics
    result["soccerCalibration"] = audit
    result["soccerCalibrationVersion"] = SOCCER_CALIBRATION_VERSION
    result["soccerCompetitionLevel"] = audit["competitionLevel"]
    result["soccerCompetitionFactor"] = audit["competitionFactor"]
    return result
