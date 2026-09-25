#!/usr/bin/env python3
"""NFL expectation layer for the TalentX hourly Sports refresh.

This is intentionally a narrow extension of the existing Sports engine. Verified
event discovery, durable history, result-proportional movement, and uncapped
pricing stay intact. The NFL-specific layer makes completed games compare with a
true pre-game per-game baseline instead of ESPN projected season totals.

For established players early in a season, the most recent completed season is
the primary expectation. As the current season accumulates games, the baseline
blends toward current-season per-game production. ESPN's season-history endpoint
is used because it supplies explicit Games Played values for each season.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import hourly_price_refresh as refresh
from category_market_store import load_records, write_records
from results_event_pricing import MODEL_VERSION as RESULTS_MODEL_VERSION
from results_event_pricing import result_move_from_delta, result_sensitivity

NFL_EXPECTATION_MODEL_VERSION = "1.8-nfl-established-window-position-safe"
NFL_FUNDAMENTAL_EVIDENCE_VERSION = "1.0-established-multiseason-opportunity-weighted"
NFL_RESULT_SCALE = 1.45
NFL_MIGRATION_LOOKBACK_DAYS = 14
NFL_STATS_HISTORY = (
    "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/"
    "athletes/{athlete_id}/stats?season={season}"
)

_original_expected_game_signal = refresh.expected_game_signal
_original_fetch_hourly_evidence = refresh.fetch_hourly_evidence
_original_reliable_results_move = None

_GAME_COUNT_KEYS = {"gamesplayed", "games", "appearances", "gp"}
_RATE_HINTS = (
    "avg", "average", "pct", "percentage", "rate", "rating", "qbr",
    "perattempt", "percarry", "perreception", "pergame", "yardsper",
    "longest", "long",
)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


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


def _game_count(stats: dict[str, Any], fallback: Any = None) -> float | None:
    games = refresh.stat_value(stats, "gamesPlayed", "games", "appearances", "gp")
    if games is None:
        games = _finite(fallback)
    return games if games is not None and games > 0 else None


def _is_rate_stat(key: str) -> bool:
    normalized = refresh.norm_key(key)
    return any(hint in normalized for hint in _RATE_HINTS)


def nfl_per_game_stats(stats: dict[str, Any], games: float) -> dict[str, float]:
    """Convert cumulative NFL season stats into one-game values."""
    output: dict[str, float] = {}
    if games <= 0:
        return output
    for key, raw in stats.items():
        value = refresh.numeric_box_value(raw)
        if value is None:
            continue
        normalized = refresh.norm_key(key)
        if not normalized or normalized in _GAME_COUNT_KEYS:
            continue
        output[normalized] = value if _is_rate_stat(key) else value / games
    return output


def _category_stat_priority(category_name: str, key: str) -> int:
    """Prefer the ESPN category that owns an ambiguous stat name.

    ESPN reuses names such as ``interceptions`` for passes thrown and defensive
    interceptions. A QB's passing INT total must not be overwritten by a later
    defensive category containing zero interceptions.
    """
    category = refresh.norm_key(category_name)
    if key == "interceptions":
        if "pass" in category:
            return 100
        if "def" in category:
            return 40
    if "pass" in category:
        return 80
    if "rush" in category:
        return 80
    if "receiv" in category:
        return 80
    if "def" in category:
        return 60
    if "general" in category:
        return 20
    return 10


def parse_nfl_season_history(payload: dict[str, Any]) -> dict[int, dict[str, float]]:
    """Merge ESPN category rows into one collision-safe stat map per season."""
    seasons: dict[int, dict[str, float]] = {}
    priorities: dict[tuple[int, str], int] = {}
    categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
    for category in categories:
        if not isinstance(category, dict):
            continue
        category_name = str(category.get("name") or category.get("displayName") or "")
        names = category.get("names") if isinstance(category.get("names"), list) else []
        rows = category.get("statistics") if isinstance(category.get("statistics"), list) else []
        if not names:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            season = row.get("season") if isinstance(row.get("season"), dict) else {}
            try:
                year = int(season.get("year") or season.get("displayName") or 0)
            except (TypeError, ValueError):
                year = 0
            values = row.get("stats") if isinstance(row.get("stats"), list) else []
            if year <= 0 or not values:
                continue
            target = seasons.setdefault(year, {})
            for name, raw in zip(names, values):
                value = refresh.numeric_box_value(raw)
                key = refresh.norm_key(name)
                if value is None or not key:
                    continue
                if key == "gamesplayed":
                    target[key] = max(value, target.get(key, 0.0))
                    continue
                priority = _category_stat_priority(category_name, key)
                prior_priority = priorities.get((year, key), -1)
                if priority >= prior_priority:
                    target[key] = value
                    priorities[(year, key)] = priority
    return seasons


def _season_history_games(history: dict[int, dict[str, float]]) -> int:
    total = 0
    for stats in history.values():
        games = _game_count(stats)
        if games is not None:
            total += int(round(games))
    return total



def _nfl_season_year(now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    return current.year if current.month >= 7 else current.year - 1


def _role_group(record: dict[str, Any]) -> str:
    role = str(record.get("role") or "").lower().strip()
    if role == "qb" or "quarterback" in role:
        return "QB"
    if role in {"rb", "fb"} or "running back" in role or "fullback" in role:
        return "RB"
    if role in {"wr", "te"} or "wide receiver" in role or "receiver" in role or "tight end" in role:
        return "REC"
    if any(token in role for token in ("kicker", "punter", "long snapper")):
        return "ST"
    if any(token in role for token in ("offensive line", "offensive tackle", "offensive guard")) or role in {"ot", "og", "c"}:
        return "OL"
    return "DEF"


def _uses_rookie_transition(record: dict[str, Any], season: int) -> bool:
    """Keep rookies and second-year players on the separate IPO transition."""
    draft_year = _finite(record.get("draftYear"))
    if draft_year is not None:
        age = season - int(round(draft_year))
        return 0 <= age <= 1
    experience = _finite(record.get("experienceYears"))
    return experience is not None and 0 < experience <= 2


def _season_signal(record: dict[str, Any], stats: dict[str, Any], games: float) -> tuple[float, float]:
    if games <= 0:
        return 0.0, 0.0
    per_game = nfl_per_game_stats(stats, games)
    signals = refresh.signal_bundle(record, per_game, {}, 0.0)
    return (
        max(0.0, float(signals.get("recentProduction") or 0.0)),
        float(signals.get("efficiency") or 0.0),
    )


def _weighted_recent_prior(values: list[tuple[int, float]]) -> float | None:
    """Blend the two most recent completed seasons without making career length a premium."""
    if not values:
        return None
    ordered = sorted(values, key=lambda pair: pair[0], reverse=True)
    if len(ordered) == 1:
        return ordered[0][1]
    return ordered[0][1] * 0.65 + ordered[1][1] * 0.35


def _efficiency_opportunities(record: dict[str, Any], stats: dict[str, Any], games: float) -> tuple[float, float]:
    """Return current opportunities and a role-specific stabilization prior.

    Rate statistics should earn authority through attempts/touches/targets rather
    than merely through calendar games. Count-based defensive/line evidence falls
    back to games because public box scores do not expose snaps consistently.
    """
    group = _role_group(record)
    if group == "QB":
        attempts = refresh.stat_value(stats, "passingAttempts", "passAttempts", "attempts") or 0.0
        return attempts, 300.0
    if group == "RB":
        carries = refresh.stat_value(stats, "rushingAttempts", "rushAttempts", "carries", "car") or 0.0
        receptions = refresh.stat_value(stats, "receptions", "rec") or 0.0
        return carries + receptions, 200.0
    if group == "REC":
        targets = refresh.stat_value(stats, "receivingTargets", "targets") or 0.0
        receptions = refresh.stat_value(stats, "receptions", "rec") or 0.0
        return (targets if targets > 0 else receptions), 120.0
    if group == "ST":
        field_goal_attempts = refresh.stat_value(stats, "fieldGoalsAttempted", "fieldGoalAttempts") or 0.0
        punts = refresh.stat_value(stats, "punts") or 0.0
        return field_goal_attempts + punts, 40.0
    return max(0.0, games), 10.0


def _established_fundamental_signals(
    record: dict[str, Any],
    item: dict[str, Any],
    history: dict[int, dict[str, float]],
    season: int,
) -> tuple[dict[str, float], dict[str, Any]] | None:
    """Build one stable fundamental evidence window for established NFL players.

    Completed seasons and career per-game production establish the baseline.
    Current-season production then earns weight gradually by games, while rate
    efficiency earns weight by real opportunities such as attempts or touches.
    This evidence is for fundamentals only; individual games still move market
    price separately through the verified event ledger.
    """
    if _uses_rookie_transition(record, season):
        return None

    current_stats = history.get(season, {})
    current_games = _game_count(current_stats) or 0.0
    current_production, current_efficiency = _season_signal(record, current_stats, current_games) if current_games else (0.0, 0.0)

    prior_production: list[tuple[int, float]] = []
    prior_efficiency: list[tuple[int, float]] = []
    for year, stats in history.items():
        if year >= season:
            continue
        games = _game_count(stats)
        if games is None or games <= 0:
            continue
        production, efficiency = _season_signal(record, stats, games)
        prior_production.append((year, production))
        prior_efficiency.append((year, efficiency))

    recent_prior_production = _weighted_recent_prior(prior_production)
    recent_prior_efficiency = _weighted_recent_prior(prior_efficiency)

    career = item.get("career") if isinstance(item.get("career"), dict) else {}
    career_games = _game_count(career, record.get("professionalGames"))
    career_production = None
    career_efficiency = None
    if career and career_games:
        career_production, career_efficiency = _season_signal(record, career, career_games)

    def durable_baseline(recent_prior: float | None, career_value: float | None) -> float | None:
        if recent_prior is not None and career_value is not None:
            return recent_prior * 0.70 + career_value * 0.30
        return recent_prior if recent_prior is not None else career_value

    production_baseline = durable_baseline(recent_prior_production, career_production)
    efficiency_baseline = durable_baseline(recent_prior_efficiency, career_efficiency)
    if production_baseline is None and efficiency_baseline is None:
        return None

    production_weight = current_games / (current_games + 10.0) if current_games > 0 else 0.0
    opportunities, opportunity_prior = _efficiency_opportunities(record, current_stats, current_games)
    efficiency_weight = opportunities / (opportunities + opportunity_prior) if opportunities > 0 else 0.0

    stable_production = (
        current_production
        if production_baseline is None
        else production_baseline * (1.0 - production_weight) + current_production * production_weight
    )
    stable_efficiency = (
        current_efficiency
        if efficiency_baseline is None
        else efficiency_baseline * (1.0 - efficiency_weight) + current_efficiency * efficiency_weight
    )

    signals = dict(item.get("signals") or {})
    signals["recentProduction"] = max(0.0, stable_production)
    signals["efficiency"] = stable_efficiency
    detail = {
        "version": NFL_FUNDAMENTAL_EVIDENCE_VERSION,
        "window": "established-multiseason",
        "season": season,
        "currentSeasonGames": int(round(current_games)),
        "currentSeasonProductionWeight": round(production_weight, 4),
        "efficiencyOpportunities": round(opportunities, 2),
        "efficiencyOpportunityPrior": round(opportunity_prior, 2),
        "currentSeasonEfficiencyWeight": round(efficiency_weight, 4),
        "priorCompletedSeasonProduction": round(recent_prior_production, 4) if recent_prior_production is not None else None,
        "careerPerGameProduction": round(career_production, 4) if career_production is not None else None,
        "stableRecentProduction": round(stable_production, 4),
        "priorCompletedSeasonEfficiency": round(recent_prior_efficiency, 4) if recent_prior_efficiency is not None else None,
        "careerEfficiency": round(career_efficiency, 4) if career_efficiency is not None else None,
        "stableEfficiency": round(stable_efficiency, 4),
        "principle": "career/multi-season fundamentals update gradually; verified games move market price separately",
    }
    return signals, detail


def nfl_aware_fetch_hourly_evidence(record: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Keep normal evidence collection, then replace NFL projections with actual season history."""
    item = _original_fetch_hourly_evidence(record, timeout)
    if (
        str(record.get("leagueOrMedium") or "") != "NFL"
        or str(record.get("sourceNamespace") or "") != "espn"
        or not isinstance(item, dict)
        or not item.get("ok")
    ):
        return item

    athlete_id = str(record.get("sourceRecordId") or "").strip()
    if not athlete_id:
        return item
    season = _nfl_season_year()
    url = NFL_STATS_HISTORY.format(athlete_id=athlete_id, season=season)
    try:
        payload = refresh.fetch_json(url, timeout)
        history = parse_nfl_season_history(payload)
    except Exception as exc:  # noqa: BLE001
        warnings = list(item.get("errors") or [])
        warnings.append(f"NFL season history {type(exc).__name__}")
        item["errors"] = warnings
        return item

    if not history:
        return item

    current_stats = history.get(season) or history[max(history)]
    prior_signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
    award_points = float(prior_signals.get("awardPoints") or 0.0)
    record_copy = dict(item.get("record") or record)
    total_games = _season_history_games(history)
    if total_games > 0:
        record_copy["professionalGames"] = total_games
        item["professionalGames"] = total_games
    item["record"] = record_copy
    item["recent"] = current_stats
    item["signals"] = refresh.signal_bundle(record_copy, current_stats, item.get("career") or {}, award_points)

    stable = _established_fundamental_signals(record_copy, item, history, season)
    if stable is not None:
        item["signals"], item["nflFundamentalEvidence"] = stable
    else:
        item["nflFundamentalEvidence"] = {
            "version": NFL_FUNDAMENTAL_EVIDENCE_VERSION,
            "window": "rookie-ipo-transition",
            "season": season,
            "principle": "rookie and second-year players use the IPO transition instead of veteran multi-season stabilization",
        }

    item["nflSeasonStats"] = history
    item["nflSeasonHistorySource"] = url
    evidence_urls = list(item.get("evidenceUrls") or [])
    if url not in evidence_urls:
        evidence_urls.append(url)
    item["evidenceUrls"] = evidence_urls
    return item


def _blend_stat_maps(first: dict[str, float], second: dict[str, float], first_weight: float) -> dict[str, float]:
    output: dict[str, float] = {}
    second_weight = 1.0 - first_weight
    for key in set(first) | set(second):
        a = _finite(first.get(key))
        b = _finite(second.get(key))
        if a is not None and b is not None:
            output[key] = a * first_weight + b * second_weight
        elif a is not None:
            output[key] = a
        elif b is not None:
            output[key] = b
    return output


def nfl_expected_baseline_stats(record: dict[str, Any], item: dict[str, Any]) -> dict[str, float]:
    """Return the per-game stat map representing the NFL pre-game expectation."""
    history = item.get("nflSeasonStats") if isinstance(item.get("nflSeasonStats"), dict) else {}
    normalized_history: dict[int, dict[str, float]] = {}
    for raw_year, raw_stats in history.items():
        if not isinstance(raw_stats, dict):
            continue
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            continue
        normalized_history[year] = raw_stats

    if normalized_history:
        years = sorted(normalized_history)
        current_year = years[-1]
        current_raw = normalized_history[current_year]
        current_games = _game_count(current_raw)
        current_pg = nfl_per_game_stats(current_raw, current_games) if current_games else {}
        prior_years = [year for year in years if year < current_year]
        prior_pg: dict[str, float] = {}
        if prior_years:
            prior_raw = normalized_history[max(prior_years)]
            prior_games = _game_count(prior_raw)
            if prior_games:
                prior_pg = nfl_per_game_stats(prior_raw, prior_games)

        if prior_pg:
            if current_pg and current_games is not None and current_games >= 8:
                return _blend_stat_maps(current_pg, prior_pg, 0.70)
            if current_pg and current_games is not None and current_games >= 4:
                return _blend_stat_maps(current_pg, prior_pg, 0.50)
            return prior_pg
        if current_pg:
            return current_pg

    recent = item.get("recent") if isinstance(item.get("recent"), dict) else {}
    career = item.get("career") if isinstance(item.get("career"), dict) else {}
    recent_games = _game_count(recent)
    career_games = _game_count(career, record.get("professionalGames"))
    recent_pg = nfl_per_game_stats(recent, recent_games) if recent_games else {}
    career_pg = nfl_per_game_stats(career, career_games) if career_games else {}
    return career_pg or recent_pg


def nfl_aware_expected_game_signal(record: dict[str, Any], item: dict[str, Any]) -> float:
    if str(record.get("leagueOrMedium") or "") != "NFL":
        return _original_expected_game_signal(record, item)
    baseline = nfl_expected_baseline_stats(record, item)
    if baseline:
        signals = refresh.signal_bundle(record, baseline, {}, 0)
        score = _finite(signals.get("recentProduction"))
        if score is not None and score > 0:
            return score
    return _original_expected_game_signal(record, item)


def _nfl_game_evidence(record: dict[str, Any], item: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
    normalized_stats: dict[str, float] = {}
    for key, value in stats.items():
        parsed = refresh.numeric_box_value(value)
        if parsed is not None:
            normalized_stats[refresh.norm_key(key)] = parsed

    if "yardsperpassattempt" not in normalized_stats and "battingaverage" in normalized_stats:
        normalized_stats["yardsperpassattempt"] = normalized_stats["battingaverage"]
    if "passerrating" in normalized_stats:
        normalized_stats["qbrating"] = normalized_stats["passerrating"]

    actual_signals = refresh.signal_bundle(record, normalized_stats, {}, 0)
    actual_production = float(actual_signals.get("recentProduction") or 0)
    baseline_stats = nfl_expected_baseline_stats(record, item)
    expected_signals = refresh.signal_bundle(record, baseline_stats, {}, 0) if baseline_stats else {}
    expected_production = float(expected_signals.get("recentProduction") or 0)

    if expected_production <= 0:
        return {
            "comparable": False,
            "reason": "No source-backed one-game NFL baseline was available for this box score",
            "actualPerformanceScore": round(actual_production, 3),
            "expectedPerformanceScore": 0.0,
        }

    production_delta = (actual_production / expected_production - 1.0) * 100.0
    efficiency_delta: float | None = None
    role = str(record.get("role") or "").lower()
    if "quarterback" in role or role.strip() == "qb":
        actual_rating = refresh.stat_value(normalized_stats, "passerRating", "QBRating")
        expected_rating = refresh.stat_value(baseline_stats, "QBRating", "passerRating")
        if actual_rating is not None and expected_rating is not None and actual_rating > 0 and expected_rating > 0:
            efficiency_delta = (actual_rating / expected_rating - 1.0) * 100.0
    else:
        actual_efficiency = float(actual_signals.get("efficiency") or 0)
        expected_efficiency = float(expected_signals.get("efficiency") or 0)
        if abs(actual_efficiency) > 0.01 and abs(expected_efficiency) > 0.01:
            efficiency_delta = (actual_efficiency / expected_efficiency - 1.0) * 100.0

    performance_delta = production_delta if efficiency_delta is None else production_delta * 0.80 + efficiency_delta * 0.20
    return {
        "comparable": True,
        "actualPerformanceScore": round(actual_production, 3),
        "expectedPerformanceScore": round(expected_production, 3),
        "performanceDeltaPct": round(performance_delta, 2),
        "productionDeltaPct": round(production_delta, 2),
        "efficiencyDeltaPct": round(efficiency_delta, 2) if efficiency_delta is not None else None,
        "expectationSource": "ESPN prior/current season history per game",
    }


def nfl_results_based_game_event_move(record, item, event, legacy_max_game_move_pct):
    """Keep shared Sports behavior, with corrected NFL expectations and sensitivity."""
    if str(record.get("leagueOrMedium") or "") != "NFL":
        if _original_reliable_results_move is None:
            return refresh.game_event_move(record, item, event, legacy_max_game_move_pct)
        return _original_reliable_results_move(record, item, event, legacy_max_game_move_pct)

    evidence = _nfl_game_evidence(record, item, event)
    started = _parse_time(event.get("startedAt"))
    if not evidence.get("comparable"):
        if started is not None and started.month in {7, 8} and _original_reliable_results_move is not None:
            return _original_reliable_results_move(record, item, event, legacy_max_game_move_pct)
        return 0.0, evidence

    delta = float(evidence.get("performanceDeltaPct") or 0.0)
    sensitivity_record = dict(record)
    if item.get("professionalGames"):
        sensitivity_record["professionalGames"] = item["professionalGames"]
    tier, sensitivity = result_sensitivity(sensitivity_record)
    scale = 0.80 if started is not None and started.month in {7, 8} else NFL_RESULT_SCALE
    performance_move = result_move_from_delta(delta, scale=scale) * sensitivity
    outcome_move = 0.06 if event.get("teamWon") is True else -0.05 if event.get("teamWon") is False else 0.0
    tuned_move = performance_move + outcome_move
    return round(tuned_move, 3), {
        **evidence,
        "performanceMovePct": round(performance_move, 3),
        "outcomeMovePct": outcome_move,
        "volatilityTier": tier,
        "resultSensitivity": round(sensitivity, 3),
        "pricingBasis": f"{RESULTS_MODEL_VERSION}-{NFL_EXPECTATION_MODEL_VERSION}",
        "nflExpectationModelVersion": NFL_EXPECTATION_MODEL_VERSION,
        "hardMoveCapPct": None,
    }


def _latest_nfl_game_event(record: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or str(event.get("eventType") or "").lower() != "game":
            continue
        league = str(event.get("league") or record.get("leagueOrMedium") or "").lower()
        if league not in {"nfl", ""}:
            continue
        if not isinstance(event.get("stats"), dict) or not event.get("stats"):
            continue
        candidates.append((index, event))
    return max(candidates, key=lambda pair: str(pair[1].get("startedAt") or "")) if candidates else None


def migrate_latest_nfl_expectations(
    catalog_path: Path = Path("data/current_catalog.json"),
    *, timeout: float = 10.0,
    now: datetime | None = None,
) -> int:
    """Reprice the latest recent NFL game once under the corrected expectation."""
    if not catalog_path.exists():
        return 0
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=NFL_MIGRATION_LOOKBACK_DAYS)
    records = load_records(catalog_path)
    changed = 0

    for record in records:
        if str(record.get("leagueOrMedium") or "") != "NFL":
            continue
        if str(record.get("nflExpectationModelVersion") or "") == NFL_EXPECTATION_MODEL_VERSION:
            continue
        selected = _latest_nfl_game_event(record)
        if selected is None:
            continue
        index, old_event = selected
        event_time = _parse_time(old_event.get("startedAt"))
        if event_time is None or event_time < cutoff:
            continue
        event_key = str(old_event.get("eventKey") or old_event.get("eventId") or "").strip()
        if not event_key or str(record.get("lastPriceEventId") or "") != event_key:
            continue
        before = _finite(old_event.get("priceBefore"))
        old_after = _finite(old_event.get("priceAfter"))
        if before is None or before <= 0 or old_after is None or old_after <= 0:
            continue

        evidence_item = refresh.fetch_hourly_evidence(record, timeout)
        if not isinstance(evidence_item, dict) or not evidence_item.get("ok"):
            continue
        new_move, evidence = nfl_results_based_game_event_move(record, evidence_item, old_event, None)
        if not evidence.get("comparable") or new_move <= -100.0:
            continue

        new_after = max(0.01, round(before * (1.0 + new_move / 100.0), 2))
        actual_move = round((new_after / before - 1.0) * 100.0, 3)
        updated_event = {
            **old_event, **evidence,
            "movePct": actual_move,
            "modelMovePct": round(new_move, 3),
            "priceBefore": round(before, 2),
            "priceAfter": new_after,
            "nflExpectationModelVersion": NFL_EXPECTATION_MODEL_VERSION,
        }
        events = [dict(value) if isinstance(value, dict) else value for value in record.get("priceEvents", [])]
        events[index] = updated_event
        record["priceEvents"] = events
        record["previousMarketPrice"] = round(before, 2)
        record["marketPrice"] = new_after
        record["lastGameMovePct"] = actual_move
        record["lastGamePerformanceDeltaPct"] = evidence.get("performanceDeltaPct")
        record["lastGameStats"] = updated_event.get("stats", {})
        record["dailyChange"] = actual_move
        record["hourlyChangePct"] = actual_move
        record["nflExpectationModelVersion"] = NFL_EXPECTATION_MODEL_VERSION

        trend = [float(value) for value in record.get("trend", []) if isinstance(value, (int, float))]
        if trend:
            trend[-1] = new_after
            record["trend"] = [round(value, 2) for value in trend]
        history = [dict(value) for value in record.get("priceHistory", []) if isinstance(value, dict)]
        for point in history:
            if str(point.get("eventId") or "") != event_key:
                continue
            phase = str(point.get("phase") or "").lower()
            if phase == "open":
                point["price"] = round(before, 2)
            elif phase == "close":
                point["price"] = new_after
        if history:
            record["priceHistory"] = history
        changed += 1

    if changed:
        write_records(catalog_path, records)
        print(f"Repriced {changed:,} recent NFL latest-game event(s) with collision-safe season expectations.")
    return changed


def install_nfl_layer():
    """Install NFL-only hooks after loading the existing reliability wrapper."""
    global _original_reliable_results_move
    refresh.expected_game_signal = nfl_aware_expected_game_signal
    refresh.fetch_hourly_evidence = nfl_aware_fetch_hourly_evidence
    import hourly_price_refresh_reliable as reliable

    _original_reliable_results_move = reliable.results_based_game_event_move
    reliable.results_based_game_event_move = nfl_results_based_game_event_move
    refresh.game_event_move = nfl_results_based_game_event_move
    return reliable


if __name__ == "__main__":
    reliability = install_nfl_layer()
    reliability.normalize_sports_tickers()
    reliability.repair_sports_price_integrity()
    reliability.seed_rookie_ipo_history()
    migrate_latest_nfl_expectations()
    raise SystemExit(refresh.main())
