#!/usr/bin/env python3
"""Motorsport-specific semantic calibration.

Motorsport cannot use team-sport professionalGames as its evidence clock and
current championship rank must not masquerade as achievements, consistency,
potential, and audience simultaneously. This adapter turns source-backed racing
evidence into distinct TalentX metrics while leaving non-Motorsport records
unchanged.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

CALIBRATION_VERSION = "1.0-motorsport-race-evidence"

SERIES_AUDIENCE_PRIOR = {
    "FORMULA 1": 88.0,
    "MOTOGP": 78.0,
    "NTT INDYCAR SERIES": 72.0,
    "INDYCAR": 72.0,
    "NASCAR CUP SERIES": 76.0,
}


def num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def optional_num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, num(value)))


def is_motorsport(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() == "motorsport"
    )


def evidence(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("motorsportEvidence")
    return dict(value) if isinstance(value, dict) else {}


def _standing_percentile(record: dict[str, Any], ev: dict[str, Any]) -> float | None:
    rank = optional_num(ev.get("seasonRank"))
    if rank is None or rank <= 0:
        rank = optional_num(record.get("sourceRank"))
    if rank is None or rank <= 0:
        rank = optional_num(record.get("rosterSourceRank"))
    field = optional_num(ev.get("fieldSize"))
    if field is None or field < rank:
        field = optional_num(record.get("motorsportFieldSize"))
    if field is None or field < rank:
        return None
    if field <= 1:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (rank - 1.0) / (field - 1.0)))


def _rate(value: Any, denominator: Any) -> float | None:
    numerator = optional_num(value)
    base = optional_num(denominator)
    if numerator is None or base is None or base <= 0:
        return None
    return max(0.0, min(1.0, numerator / base))


def performance_score(record: dict[str, Any], ev: dict[str, Any]) -> float:
    """Current racing performance; current rank appears only here."""
    standing = _standing_percentile(record, ev)
    starts = optional_num(ev.get("seasonStarts"))
    wins = _rate(ev.get("seasonWins"), starts)
    podiums = _rate(ev.get("seasonPodiums"), starts)
    top5 = _rate(ev.get("seasonTop5"), starts)
    top10 = _rate(ev.get("seasonTop10"), starts)

    signals: list[tuple[float, float]] = []
    if standing is not None:
        signals.append((standing, 0.50))
    if wins is not None:
        # Winning even 20% of races is elite in a major series.
        signals.append((min(1.0, wins / 0.28), 0.22))
    if podiums is not None:
        signals.append((min(1.0, podiums / 0.55), 0.15))
    if top5 is not None:
        signals.append((min(1.0, top5 / 0.72), 0.08))
    elif top10 is not None:
        signals.append((min(1.0, top10 / 0.82), 0.08))
    points_share = optional_num(ev.get("seasonPointsShare"))
    if points_share is not None:
        signals.append((max(0.0, min(1.0, points_share)), 0.05))

    if not signals:
        return 58.0
    total = sum(weight for _, weight in signals)
    composite = sum(value * weight for value, weight in signals) / total
    return round(clamp(46.0 + 52.0 * composite, 42.0, 98.0), 1)


def achievement_score(ev: dict[str, Any]) -> float:
    """Career accomplishment. Deliberately independent of current standings."""
    championships = max(0.0, num(ev.get("careerChampionships")))
    wins = max(0.0, num(ev.get("careerWins")))
    podiums = max(0.0, num(ev.get("careerPodiums")))
    poles = max(0.0, num(ev.get("careerPoles")))
    major_wins = max(0.0, num(ev.get("majorWins")))
    season_wins = max(0.0, num(ev.get("seasonWins")))

    if not any((championships, wins, podiums, poles, major_wins)):
        # A current win is an achievement, but current standings are not.
        return round(clamp(28.0 + 7.0 * math.sqrt(season_wins), 28.0, 58.0), 1)

    championship_component = 100.0 * (1.0 - math.exp(-championships / 1.9))
    win_component = 100.0 * (1.0 - math.exp(-wins / 24.0))
    podium_component = 100.0 * (1.0 - math.exp(-podiums / 48.0))
    pole_component = 100.0 * (1.0 - math.exp(-poles / 32.0))
    major_component = 100.0 * (1.0 - math.exp(-major_wins / 2.0))
    value = (
        championship_component * 0.40
        + win_component * 0.28
        + podium_component * 0.15
        + pole_component * 0.10
        + major_component * 0.07
    )
    return round(clamp(28.0 + 0.72 * value, 28.0, 99.0), 1)


def consistency_score(ev: dict[str, Any]) -> float:
    """Repeatability and reliability, not championship position."""
    starts = optional_num(ev.get("seasonStarts"))
    dnfs = optional_num(ev.get("seasonDNFs"))
    podium_rate = _rate(ev.get("seasonPodiums"), starts)
    top5_rate = _rate(ev.get("seasonTop5"), starts)
    top10_rate = _rate(ev.get("seasonTop10"), starts)

    components: list[tuple[float, float]] = []
    if starts is not None and starts > 0 and dnfs is not None:
        reliability = max(0.0, min(1.0, 1.0 - dnfs / starts))
        components.append((reliability, 0.48))
    if podium_rate is not None:
        components.append((min(1.0, podium_rate / 0.55), 0.28))
    elif top5_rate is not None:
        components.append((min(1.0, top5_rate / 0.72), 0.28))
    if top10_rate is not None:
        components.append((min(1.0, top10_rate / 0.85), 0.24))

    if not components:
        years = max(0.0, num(ev.get("yearsActive")))
        return round(clamp(58.0 + min(18.0, years * 1.4), 58.0, 78.0), 1)
    total = sum(weight for _, weight in components)
    composite = sum(value * weight for value, weight in components) / total
    return round(clamp(45.0 + 50.0 * composite, 45.0, 96.0), 1)


def potential_score(record: dict[str, Any], ev: dict[str, Any], performance: float) -> float:
    current_year = datetime.now(timezone.utc).year
    birth = optional_num(ev.get("birthYear"))
    if birth is None:
        birth = optional_num(record.get("birthYear"))
    if birth is not None:
        age = current_year - int(round(birth))
        if age <= 20:
            age_score = 97.0
        elif age <= 23:
            age_score = 94.0 - (age - 20) * 3.0
        elif age <= 27:
            age_score = 85.0 - (age - 23) * 4.0
        elif age <= 31:
            age_score = 69.0 - (age - 27) * 4.5
        elif age <= 36:
            age_score = 51.0 - (age - 31) * 2.5
        else:
            age_score = 38.0
    else:
        age_score = 64.0
    return round(clamp(age_score * 0.72 + performance * 0.28, 35.0, 97.0), 1)


def availability_score(record: dict[str, Any], ev: dict[str, Any]) -> float:
    if str(record.get("careerStatus") or "").lower() != "active":
        return 35.0
    starts = optional_num(ev.get("seasonStarts"))
    scheduled = optional_num(ev.get("seasonScheduledStarts"))
    if starts is not None and scheduled is not None and scheduled > 0:
        return round(clamp(70.0 + 27.0 * min(1.0, starts / scheduled), 70.0, 97.0), 1)
    if starts is not None and starts > 0:
        return 92.0
    return 78.0


def audience_score(record: dict[str, Any]) -> float:
    existing = optional_num((record.get("activeMetrics") or {}).get("audience")) if isinstance(record.get("activeMetrics"), dict) else None
    series = str(record.get("leagueOrMedium") or "").strip().upper()
    prior = SERIES_AUDIENCE_PRIOR.get(series, 66.0)
    if existing is None:
        return prior
    # Existing attention can contribute, but current standings never determine it.
    return round(clamp(prior * 0.72 + existing * 0.28, 45.0, 94.0), 1)


def calibrate_record(record: dict[str, Any]) -> dict[str, Any]:
    if not is_motorsport(record):
        return dict(record)
    result = dict(record)
    ev = evidence(result)
    if not ev:
        return result

    performance = performance_score(result, ev)
    metrics = {
        "performance": performance,
        "achievements": achievement_score(ev),
        "consistency": consistency_score(ev),
        "potential": potential_score(result, ev, performance),
        "availability": availability_score(result, ev),
        "audience": audience_score(result),
    }
    result["motorsportMetrics"] = metrics
    result["motorsportCalibrationVersion"] = CALIBRATION_VERSION
    result["motorsportEvidenceStatus"] = "structured-official-series-evidence"
    return result
