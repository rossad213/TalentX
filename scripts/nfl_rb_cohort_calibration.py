#!/usr/bin/env python3
"""Current-season cohort calibration for NFL running backs.

The generic ESPN overview can expose different season rows as ``recent`` for
otherwise comparable players. This layer uses durable TalentX game events to put
RBs who have played in the current regular season on one consistent evidence
window before production percentiles are calculated.

It also replaces sparse award-percentile jumps with a smoother accomplishment
context based mainly on durable RB career production. No player-specific prices
or ranking overrides are used.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from enrich_current_catalog import percentile

RB_COHORT_CALIBRATION_VERSION = "1.0-rb-current-season-consistent-window"
EARLY_SEASON_FULL_WEIGHT_GAMES = 6.0


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def is_nfl_running_back(record: dict[str, Any]) -> bool:
    if str(record.get("leagueOrMedium") or "").strip().upper() != "NFL":
        return False
    role = str(record.get("role") or "").lower().strip()
    return (
        role in {"rb", "fb"}
        or "running back" in role
        or "fullback" in role
        or role.endswith(" rb")
        or role.endswith(" fb")
    )


def _parse_time(value: Any) -> datetime | None:
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


def _current_season(now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    return current.year if current.month >= 7 else current.year - 1


def current_regular_events(record: dict[str, Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Return deduplicated current-season September-February NFL game events."""
    season = _current_season(now)
    start = datetime(season, 9, 1, tzinfo=timezone.utc)
    end = datetime(season + 1, 2, 16, tzinfo=timezone.utc)
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    found: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict) or str(event.get("eventType") or "").lower() != "game":
            continue
        started = _parse_time(event.get("startedAt") or event.get("eventDate") or event.get("date"))
        if started is None or not (start <= started < end):
            continue
        stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
        if not stats:
            continue
        key = str(event.get("eventKey") or event.get("eventId") or started.isoformat())
        found[key] = event
    return sorted(found.values(), key=lambda item: str(item.get("startedAt") or ""))


def _stat(stats: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = _number(stats.get(key))
        if value is not None:
            return value
    return 0.0


def rb_current_season_signals(events: list[dict[str, Any]]) -> dict[str, float]:
    """Translate current regular-season RB box scores onto the stored signal scale."""
    carries = 0.0
    rushing_yards = 0.0
    rushing_tds = 0.0
    receptions = 0.0
    receiving_yards = 0.0
    receiving_tds = 0.0
    for event in events:
        stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
        carries += _stat(stats, "car", "rushingAttempts", "rushAttempts")
        rushing_yards += _stat(stats, "rushingYards")
        rushing_tds += _stat(stats, "rushingTouchdowns")
        receptions += _stat(stats, "receptions", "rec")
        receiving_yards += _stat(stats, "receivingYards")
        receiving_tds += _stat(stats, "receivingTouchdowns")

    yards_per_carry = rushing_yards / carries if carries > 0 else 0.0
    yards_per_reception = receiving_yards / receptions if receptions > 0 else 0.0
    recent_production = (
        rushing_yards / 12.0
        + rushing_tds * 8.0
        + receiving_yards / 24.0
        + receptions * 0.8
        + receiving_tds * 8.0
    )
    efficiency = yards_per_carry * 18.0 + yards_per_reception * 4.0
    return {
        "recentProduction": recent_production,
        "efficiency": efficiency,
        "games": float(len(events)),
        "carries": carries,
        "rushingYards": rushing_yards,
        "rushingTouchdowns": rushing_tds,
        "receptions": receptions,
        "receivingYards": receiving_yards,
        "receivingTouchdowns": receiving_tds,
    }


def _raw_signals(record: dict[str, Any]) -> dict[str, Any]:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    return summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}


@dataclass
class RBCohortContext:
    evidence: dict[str, dict[str, float]]
    recent_pool: list[float]
    efficiency_pool: list[float]
    career_pool: list[float]


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("id") or f"name:{record.get('name') or ''}")


def prepare_rb_context(records: list[dict[str, Any]], *, now: datetime | None = None) -> RBCohortContext:
    """Build same-window RB pools and write corrected raw recent signals in-place."""
    evidence: dict[str, dict[str, float]] = {}
    career_pool: list[float] = []
    for record in records:
        if not is_nfl_running_back(record):
            continue
        raw = _raw_signals(record)
        career = _number(raw.get("careerProduction"))
        if career is not None:
            career_pool.append(career)
        events = current_regular_events(record, now=now)
        if not events:
            continue
        signals = rb_current_season_signals(events)
        evidence[_record_key(record)] = signals

        summary = dict(record.get("pricingEvidenceSummary") or {})
        raw_copy = dict(summary.get("rawSignals") or {})
        prior_recent = _number(raw_copy.get("recentProduction"))
        prior_efficiency = _number(raw_copy.get("efficiency"))
        raw_copy["recentProduction"] = round(signals["recentProduction"], 4)
        raw_copy["efficiency"] = round(signals["efficiency"], 4)
        summary["rawSignals"] = raw_copy
        summary["rbCurrentSeasonSource"] = "verified regular-season priceEvents"
        summary["rbCurrentSeasonGames"] = int(signals["games"])
        record["pricingEvidenceSummary"] = summary
        record["nflRbCohortCalibration"] = {
            "version": RB_COHORT_CALIBRATION_VERSION,
            "priorRecentProduction": round(prior_recent, 4) if prior_recent is not None else None,
            "currentSeasonRecentProduction": round(signals["recentProduction"], 4),
            "priorEfficiency": round(prior_efficiency, 4) if prior_efficiency is not None else None,
            "currentSeasonEfficiency": round(signals["efficiency"], 4),
            "currentSeasonGames": int(signals["games"]),
        }

    recent_pool = [item["recentProduction"] for item in evidence.values()]
    efficiency_pool = [item["efficiency"] for item in evidence.values()]
    return RBCohortContext(evidence=evidence, recent_pool=recent_pool, efficiency_pool=efficiency_pool, career_pool=career_pool)


def has_current_rb_evidence(record: dict[str, Any], context: RBCohortContext) -> bool:
    return _record_key(record) in context.evidence


def apply_rb_percentiles(record: dict[str, Any], context: RBCohortContext) -> bool:
    """Replace mixed-window RB percentiles with current-season comparable percentiles."""
    signals = context.evidence.get(_record_key(record))
    if signals is None or not context.recent_pool or not context.career_pool:
        return False
    summary = dict(record.get("pricingEvidenceSummary") or {})
    raw = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
    career = _number(raw.get("careerProduction"))
    if career is None:
        return False

    raw_recent_pct = percentile(signals["recentProduction"], context.recent_pool)
    career_pct = percentile(career, context.career_pool)
    raw_efficiency_pct = percentile(signals["efficiency"], context.efficiency_pool) if context.efficiency_pool else 0.5
    games = max(1.0, signals["games"])
    sample_weight = max(0.0, min(1.0, games / EARLY_SEASON_FULL_WEIGHT_GAMES))
    recent_pct = career_pct + (raw_recent_pct - career_pct) * sample_weight
    efficiency_pct = 0.5 + (raw_efficiency_pct - 0.5) * sample_weight

    # Sparse award counts should add context, not create a near-binary 50-point
    # achievement gap. Durable career production remains the main accomplishment
    # signal, with raw awards supplying a modest absolute bonus.
    award_points = max(0.0, _number(raw.get("awardPoints")) or 0.0)
    award_context = 0.50 + 0.40 * min(1.0, award_points / 12.0)
    achievement_pct = career_pct * 0.70 + award_context * 0.30

    percentiles = dict(summary.get("percentiles") or {})
    percentiles.update({
        "recentProduction": round(recent_pct, 4),
        "careerProduction": round(career_pct, 4),
        "efficiency": round(efficiency_pct, 4),
        "awardPoints": round(achievement_pct, 4),
    })
    unstabilized = dict(summary.get("unstabilizedPercentiles") or {})
    unstabilized.update({
        "recentProduction": round(raw_recent_pct, 4),
        "careerProduction": round(career_pct, 4),
        "efficiency": round(raw_efficiency_pct, 4),
        "awardPoints": round(achievement_pct, 4),
    })
    summary["percentiles"] = percentiles
    summary["unstabilizedPercentiles"] = unstabilized
    summary["recentSampleGamesEstimate"] = int(games)
    summary["recentSampleWeight"] = round(sample_weight, 4)
    summary["rbAchievementCalibration"] = {
        "careerProductionWeight": 0.70,
        "awardContextWeight": 0.30,
        "rawAwardPoints": round(award_points, 2),
        "calibratedAchievementPercentile": round(achievement_pct, 4),
    }
    summary["cohort"] = "NFL · RB current-season normalized production"
    record["pricingEvidenceSummary"] = summary

    metrics = dict(record.get("activeMetrics") or {})
    metrics["performance"] = round(max(20.0, min(98.0, 24.0 + 72.0 * (recent_pct * 0.70 + efficiency_pct * 0.30))), 1)
    metrics["consistency"] = round(max(24.0, min(97.0, 24.0 + 72.0 * (career_pct * 0.65 + recent_pct * 0.35))), 1)
    metrics["achievements"] = round(max(8.0, min(99.0, achievement_pct * 100.0)), 1)
    record["activeMetrics"] = metrics
    return True


def needs_rb_rebase(record: dict[str, Any], context: RBCohortContext) -> bool:
    if not has_current_rb_evidence(record, context):
        return False
    return str(record.get("nflRbCohortCalibrationVersion") or "") != RB_COHORT_CALIBRATION_VERSION


def mark_rb_rebased(record: dict[str, Any]) -> None:
    record["nflRbCohortCalibrationVersion"] = RB_COHORT_CALIBRATION_VERSION
