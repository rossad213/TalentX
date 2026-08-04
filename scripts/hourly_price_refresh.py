#!/usr/bin/env python3
"""Lightweight hourly TalentX price refresh.

This job starts from the latest successful full-catalog artifact, detects
unprocessed completed games, reads player-level box scores, refreshes only those
athletes' season/career evidence, and applies bounded game-level price moves.
The weekly workflow remains responsible for full roster, identity, award, and
status verification.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from enrich_current_catalog import (
    ESPN_OVERVIEW,
    ESPN_ATHLETE_PROFILE,
    ESPN_CORE_ATHLETE,
    NHL_LANDING,
    SPORT_PATH,
    cohort_key,
    extract_profile_evidence,
    merge_profile_evidence,
    norm_key,
    extract_stat_maps,
    fetch_json,
    number,
    percentile,
    potential_prior,
    professional_games_from_stats,
    recursively_collect_numbers,
    signal_bundle,
)
from pricing_model import apply_pricing_to_records, clamp, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CATALOG = DATA / "current_catalog.json"
CATALOG_CSV = DATA / "current_catalog.csv"
CATALOG_MANIFEST = DATA / "catalog_manifest.json"
PRICING_OVERRIDES = DATA / "pricing_overrides.json"
HOURLY_MANIFEST = DATA / "hourly_refresh_manifest.json"
CURRENT_SEED = DATA / "current_seed.json"

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates={date}&limit=1000"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary?event={event_id}"
NHL_SCORE = "https://api-web.nhle.com/v1/score/{date}"
NHL_BOXSCORE = "https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"

SIGNAL_KEYS = (
    "recentProduction",
    "careerProduction",
    "efficiency",
    "usage",
    "careerUsage",
    "awardPoints",
)

PROCESSED_EVENT_RETENTION_DAYS = 30
COMPLETED_EVENT_STATES = {
    "post", "final", "completed", "complete", "off", "closed", "official",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
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


def safe_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def event_key(provider: str, event_id: str) -> str:
    return f"{provider.lower()}:{event_id}"


def player_event_key(athlete_key: tuple[str, str], game_key: str) -> str:
    return f"{game_key}|{athlete_key[0]}:{athlete_key[1]}"


def completed_event(state: str) -> bool:
    normalized = str(state or "").lower().strip()
    return normalized in COMPLETED_EVENT_STATES or normalized.startswith("status_final")


def prior_processed_events(manifest: dict[str, Any], now: datetime) -> dict[str, dict[str, Any]]:
    cutoff = now - timedelta(days=PROCESSED_EVENT_RETENTION_DAYS)
    output: dict[str, dict[str, Any]] = {}
    items = manifest.get("processedEvents") if isinstance(manifest.get("processedEvents"), list) else []
    for item in items:
        if isinstance(item, str):
            output[item] = {"key": item}
            continue
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        started_at = parse_datetime(item.get("startedAt"))
        if key and (started_at is None or started_at >= cutoff):
            output[key] = dict(item)
    return output


def prior_processed_player_events(manifest: dict[str, Any]) -> set[str]:
    items = manifest.get("processedPlayerEvents") if isinstance(manifest.get("processedPlayerEvents"), list) else []
    return {
        str(item.get("key") if isinstance(item, dict) else item)
        for item in items
        if str(item.get("key") if isinstance(item, dict) else item)
    }


def numeric_box_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"--", "-", "DNP", "DND"}:
        return None
    try:
        parsed = float(text.rstrip("%"))
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def made_attempted(value: Any) -> tuple[float, float] | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", str(value or ""))
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def normalized_box_stats(labels: Iterable[Any], values: Iterable[Any], section: str = "") -> dict[str, float]:
    """Normalize common ESPN box-score columns into pricing-model stat names."""
    output: dict[str, float] = {}
    group = norm_key(section)
    basketball = {
        "min": "minutes", "pts": "points", "reb": "rebounds", "ast": "assists",
        "stl": "steals", "blk": "blocks", "to": "turnovers", "tov": "turnovers",
        "oreb": "offensiveRebounds", "dreb": "defensiveRebounds", "pf": "personalFouls",
    }
    baseball = {
        "ab": "atBats", "r": "runs", "h": "hits", "rbi": "runsBattedIn",
        "bb": "walks", "k": "strikeouts", "so": "strikeouts", "hr": "homeRuns",
        "sb": "stolenBases", "ip": "inningsPitched", "er": "earnedRuns",
        "era": "earnedRunAverage", "avg": "battingAverage", "obp": "onBasePct",
        "ops": "ops", "sv": "saves", "w": "wins",
    }
    soccer = {
        "min": "minutes", "g": "goals", "gls": "goals", "a": "assists",
        "ast": "assists", "sh": "shots", "sog": "shotsOnTarget", "sv": "saves",
    }
    for label, raw_value in zip(labels, values):
        label_text = str(label or "").strip()
        key = norm_key(label_text)
        pair = made_attempted(raw_value)
        if pair and key in {"fg", "fieldgoals", "3pt", "3p", "threepointfieldgoals", "ft", "freethrows"}:
            made, attempted = pair
            prefix = "fieldGoal" if key in {"fg", "fieldgoals"} else "threePointFieldGoal" if key in {"3pt", "3p", "threepointfieldgoals"} else "freeThrow"
            output[f"{prefix}sMade"] = made
            output[f"{prefix}sAttempted"] = attempted
            output[f"{prefix}Pct"] = (made / attempted * 100.0) if attempted else 0.0
            continue
        parsed = numeric_box_value(raw_value)
        if parsed is None:
            continue
        mapped = basketball.get(key) or baseball.get(key) or soccer.get(key)
        if group:
            if "pass" in group:
                mapped = {
                    "yds": "passingYards", "td": "passingTouchdowns", "int": "interceptions",
                    "qbr": "QBRating", "rtg": "passerRating",
                }.get(key, mapped)
            elif "rush" in group:
                mapped = {"yds": "rushingYards", "td": "rushingTouchdowns", "avg": "yardsPerRushAttempt"}.get(key, mapped)
            elif "receiv" in group:
                mapped = {"rec": "receptions", "yds": "receivingYards", "td": "receivingTouchdowns", "avg": "yardsPerReception"}.get(key, mapped)
            elif "defen" in group:
                mapped = {"tot": "totalTackles", "tkl": "totalTackles", "sack": "sacks", "int": "interceptions", "pd": "passesDefended", "ff": "forcedFumbles"}.get(key, mapped)
        output[mapped or key] = parsed
    return output


def extract_espn_game_stats(payload: dict[str, Any], winning_team_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Return player-level stats from ESPN summary box scores."""
    found: dict[str, dict[str, Any]] = {}
    boxscore = payload.get("boxscore") if isinstance(payload.get("boxscore"), dict) else {}
    teams = boxscore.get("players") if isinstance(boxscore.get("players"), list) else []
    for team_block in teams:
        if not isinstance(team_block, dict):
            continue
        team = team_block.get("team") if isinstance(team_block.get("team"), dict) else {}
        team_id = str(team.get("id") or "")
        groups = team_block.get("statistics") if isinstance(team_block.get("statistics"), list) else []
        for group in groups:
            if not isinstance(group, dict):
                continue
            labels = group.get("labels") or group.get("names") or group.get("displayNames") or []
            if not isinstance(labels, list):
                continue
            section = str(group.get("name") or group.get("displayName") or group.get("type") or "")
            athletes = group.get("athletes") if isinstance(group.get("athletes"), list) else []
            for entry in athletes:
                if not isinstance(entry, dict):
                    continue
                athlete = entry.get("athlete") if isinstance(entry.get("athlete"), dict) else {}
                athlete_id = str(athlete.get("id") or entry.get("athleteId") or "")
                values = entry.get("stats") or entry.get("values") or []
                if not athlete_id or not isinstance(values, list):
                    continue
                normalized = normalized_box_stats(labels, values, section)
                if not normalized:
                    continue
                item = found.setdefault(
                    athlete_id,
                    {"stats": {}, "teamId": team_id, "teamWon": team_id in winning_team_ids if team_id else None},
                )
                item["stats"].update(normalized)
    return found


def extract_nhl_game_stats(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return player-level stats from the NHL gamecenter box score."""
    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            player_id = str(node.get("playerId") or "")
            if player_id:
                stats: dict[str, float] = {}
                for key, value in node.items():
                    if key in {"playerId", "headshot", "name", "position"} or isinstance(value, (dict, list)):
                        continue
                    parsed = numeric_box_value(value)
                    if parsed is not None:
                        mapped = {
                            "savePctg": "savePct", "sweaterNumber": "jerseyNumber",
                            "toi": "timeOnIce", "pim": "penaltyMinutes",
                        }.get(key, key)
                        stats[mapped] = parsed
                if stats:
                    found[player_id] = {"stats": stats, "teamId": "", "teamWon": None}
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload.get("playerByGameStats") or payload)
    return found


def translated_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or value.get("en") or next(iter(value.values()), ""))
    return str(value or "")


def event_in_window(start: datetime | None, state: str, now: datetime, cutoff: datetime) -> bool:
    return bool(start and cutoff <= start <= now + timedelta(hours=1) and completed_event(state))


def event_dates(now: datetime, lookback_hours: float) -> list[str]:
    first = (now - timedelta(hours=lookback_hours)).date()
    last = now.date()
    days = (last - first).days
    return [(first + timedelta(days=offset)).isoformat() for offset in range(days + 1)]


def discover_recent_events(
    records: list[dict[str, Any]],
    *,
    now: datetime,
    lookback_hours: float,
    timeout: float,
    workers: int,
    processed_keys: set[str],
    processed_player_keys: set[str] | None = None,
) -> tuple[
    set[tuple[str, str]],
    dict[tuple[str, str], list[dict[str, Any]]],
    list[dict[str, Any]],
    list[str],
]:
    """Return player-level box scores for completed, not-yet-processed games."""
    cutoff = now - timedelta(hours=lookback_hours)
    espn_leagues = sorted(
        {
            (SPORT_PATH.get(str(record.get("discipline") or "")), str(record.get("sourceLeagueSlug") or ""))
            for record in records
            if record.get("sourceNamespace") == "espn"
        }
        - {(None, "")}
    )
    espn_leagues = [(sport, league) for sport, league in espn_leagues if sport and league]

    participant_ids: set[tuple[str, str]] = set()
    athlete_events: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    processed_player_keys = processed_player_keys or set()
    seen_event_keys: set[str] = set()
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    summary_jobs: list[tuple[str, str, str, dict[str, Any]]] = []
    nhl_jobs: list[tuple[str, dict[str, Any]]] = []

    dates = event_dates(now, lookback_hours)
    for sport, league in espn_leagues:
        for date in dates:
            url = ESPN_SCOREBOARD.format(sport=sport, league=league, date=date.replace("-", ""))
            try:
                payload = fetch_json(url, timeout)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"scoreboard {sport}/{league}: {type(exc).__name__}")
                continue
            for event in payload.get("events") or []:
                if not isinstance(event, dict):
                    continue
                event_id = str(event.get("id") or "")
                start = parse_datetime(event.get("date"))
                status_type = ((event.get("status") or {}).get("type") or {}) if isinstance(event.get("status"), dict) else {}
                state = str(status_type.get("state") or status_type.get("name") or "")
                key = event_key("ESPN", event_id)
                if not event_id or key in processed_keys or key in seen_event_keys or not event_in_window(start, state, now, cutoff):
                    continue
                seen_event_keys.add(key)
                team_ids: list[str] = []
                winning_team_ids: list[str] = []
                for competition in event.get("competitions") or []:
                    if not isinstance(competition, dict):
                        continue
                    for competitor in competition.get("competitors") or []:
                        if not isinstance(competitor, dict):
                            continue
                        team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
                        team_id = str(team.get("id") or "")
                        if team_id:
                            team_ids.append(team_id)
                            if competitor.get("winner") is True:
                                winning_team_ids.append(team_id)
                info = {
                    "provider": "ESPN",
                    "eventId": event_id,
                    "eventKey": key,
                    "league": league,
                    "sport": sport,
                    "name": str(event.get("name") or event.get("shortName") or event_id),
                    "state": state,
                    "startedAt": iso_utc(start) if start else None,
                    "teamIds": team_ids,
                    "winningTeamIds": winning_team_ids,
                    "ready": False,
                }
                events.append(info)
                summary_jobs.append((sport, league, event_id, info))

    for date in dates:
        url = NHL_SCORE.format(date=date)
        try:
            payload = fetch_json(url, timeout)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"NHL score {date}: {type(exc).__name__}")
            continue
        for game in payload.get("games") or []:
            if not isinstance(game, dict):
                continue
            game_id = str(game.get("id") or "")
            start = parse_datetime(game.get("startTimeUTC"))
            state = str(game.get("gameState") or "")
            key = event_key("NHL", game_id)
            if not game_id or key in processed_keys or key in seen_event_keys or not event_in_window(start, state, now, cutoff):
                continue
            seen_event_keys.add(key)
            teams: list[str] = []
            for side in ("awayTeam", "homeTeam"):
                team = game.get(side) if isinstance(game.get(side), dict) else {}
                abbreviation = translated_text(team.get("abbrev")).upper()
                if abbreviation:
                    teams.append(abbreviation)
            info = {
                "provider": "NHL",
                "eventId": game_id,
                "eventKey": key,
                "league": "NHL",
                "sport": "hockey",
                "name": f"{' vs '.join(teams)}" if teams else game_id,
                "state": state,
                "startedAt": iso_utc(start) if start else None,
                "teamIds": teams,
                "ready": False,
            }
            events.append(info)
            nhl_jobs.append((game_id, info))

    def fetch_espn_summary(job: tuple[str, str, str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], str | None]:
        sport, league, event_id, info = job
        try:
            payload = fetch_json(ESPN_SUMMARY.format(sport=sport, league=league, event_id=event_id), timeout)
            stats = extract_espn_game_stats(payload, set(info.get("winningTeamIds") or []))
            if not stats:
                return {}, f"box score not ready {sport}/{league}/{event_id}"
            return stats, None
        except Exception as exc:  # noqa: BLE001
            return {}, f"summary {sport}/{league}/{event_id}: {type(exc).__name__}"

    def fetch_nhl_boxscore(job: tuple[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], str | None]:
        game_id, _ = job
        try:
            payload = fetch_json(NHL_BOXSCORE.format(game_id=game_id), timeout)
            stats = extract_nhl_game_stats(payload)
            if not stats:
                return {}, f"NHL box score not ready {game_id}"
            return stats, None
        except Exception as exc:  # noqa: BLE001
            return {}, f"NHL boxscore {game_id}: {type(exc).__name__}"

    all_jobs: list[tuple[str, Any]] = [("espn", job) for job in summary_jobs] + [("nhl", job) for job in nhl_jobs]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(all_jobs) or 1))) as executor:
        futures = {}
        for kind, job in all_jobs:
            future = executor.submit(fetch_espn_summary if kind == "espn" else fetch_nhl_boxscore, job)
            futures[future] = (kind, job)
        for future in as_completed(futures):
            kind, job = futures[future]
            stats_by_player, warning = future.result()
            info = job[3] if kind == "espn" else job[1]
            namespace = "espn" if kind == "espn" else "nhl"
            if stats_by_player:
                info["ready"] = True
                info["playersWithStats"] = len(stats_by_player)
                for athlete_id, performance in stats_by_player.items():
                    key = (namespace, str(athlete_id))
                    if player_event_key(key, str(info["eventKey"])) in processed_player_keys:
                        continue
                    participant_ids.add(key)
                    athlete_events[key].append(
                        {
                            "eventKey": info["eventKey"],
                            "eventId": info["eventId"],
                            "provider": info["provider"],
                            "league": info["league"],
                            "name": info["name"],
                            "startedAt": info["startedAt"],
                            "stats": performance.get("stats", {}),
                            "teamWon": performance.get("teamWon"),
                        }
                    )
            if warning:
                warnings.append(warning)

    for performances in athlete_events.values():
        performances.sort(key=lambda item: str(item.get("startedAt") or ""))
    return participant_ids, dict(athlete_events), events, warnings


def select_records(
    records: list[dict[str, Any]],
    participant_ids: set[tuple[str, str]],
    *,
    max_athletes: int,
) -> list[int]:
    exact: list[int] = []
    for index, record in enumerate(records):
        namespace = str(record.get("sourceNamespace") or "")
        athlete_id = str(record.get("sourceRecordId") or "")
        if (namespace, athlete_id) in participant_ids:
            exact.append(index)

    def priority(index: int) -> tuple[int, float, float, str]:
        record = records[index]
        evidence = str(record.get("pricingDataStatus") or "").startswith("Evidence enriched")
        starter = bool(record.get("starter"))
        confidence = float(record.get("pricingConfidence") or 0)
        return (1 if evidence else 0, 1 if starter else 0, confidence, str(record.get("name") or ""))

    exact = sorted(set(exact), key=priority, reverse=True)
    return exact[:max_athletes] if max_athletes > 0 else exact


def prior_award_data(record: dict[str, Any]) -> tuple[float, list[str]]:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    signals = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
    award_points = float(signals.get("awardPoints") or 0)
    names = summary.get("awardNames") if isinstance(summary.get("awardNames"), list) else []
    return award_points, [str(name) for name in names[:12]]


def fetch_hourly_evidence(record: dict[str, Any], timeout: float) -> dict[str, Any]:
    result = dict(record)
    namespace = str(result.get("sourceNamespace") or "")
    athlete_id = str(result.get("sourceRecordId") or "").strip()
    award_score, award_names = prior_award_data(result)
    recent: dict[str, float] = {}
    career: dict[str, float] = {}
    evidence_urls: list[str] = []
    errors: list[str] = []
    news_count = 0

    if namespace == "espn":
        sport = SPORT_PATH.get(str(result.get("discipline") or ""))
        league = str(result.get("sourceLeagueSlug") or "").strip()
        if not sport or not league or not athlete_id:
            return {"record": result, "ok": False, "reason": "unsupported ESPN record"}
        url = ESPN_OVERVIEW.format(sport=sport, league=league, athlete_id=athlete_id)
        try:
            payload = fetch_json(url, timeout)
            recent, career = extract_stat_maps(payload)
            merge_profile_evidence(result, extract_profile_evidence(payload))
            news = payload.get("news")
            news_count = len(news) if isinstance(news, list) else 0
            evidence_urls.append(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"overview {type(exc).__name__}")
        if result.get("draftPick") is None and float(result.get("experienceYears") or 0) <= 1:
            profile_url = ESPN_ATHLETE_PROFILE.format(sport=sport, league=league, athlete_id=athlete_id)
            try:
                profile_payload = fetch_json(profile_url, timeout)
                merge_profile_evidence(result, extract_profile_evidence(profile_payload))
                evidence_urls.append(profile_url)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"profile {type(exc).__name__}")
            if result.get("draftPick") is None:
                core_url = ESPN_CORE_ATHLETE.format(sport=sport, league=league, athlete_id=athlete_id)
                try:
                    core_payload = fetch_json(core_url, timeout)
                    merge_profile_evidence(result, extract_profile_evidence(core_payload))
                    evidence_urls.append(core_url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"core profile {type(exc).__name__}")
    elif namespace == "nhl":
        if not athlete_id:
            return {"record": result, "ok": False, "reason": "missing NHL player id"}
        url = NHL_LANDING.format(athlete_id=athlete_id)
        try:
            payload = fetch_json(url, timeout)
            merge_profile_evidence(result, extract_profile_evidence(payload))
            flattened: dict[str, float] = {}
            recursively_collect_numbers(payload, flattened)
            recent = {key: value for key, value in flattened.items() if "seasontotals" in key or "featuredstats" in key}
            career = {key: value for key, value in flattened.items() if "careertotals" in key}
            evidence_urls.append(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"NHL landing {type(exc).__name__}")
    else:
        return {"record": result, "ok": False, "reason": "not an automated sports record"}

    if not recent and not career:
        return {"record": result, "ok": False, "reason": "; ".join(errors) or "no updated statistics"}

    games = professional_games_from_stats(recent, career)
    result["professionalGames"] = games
    return {
        "record": result,
        "ok": True,
        "professionalGames": games,
        "recent": recent,
        "career": career,
        "signals": signal_bundle(result, recent, career, award_score),
        "awards": award_names,
        "newsCount": news_count,
        "evidenceUrls": evidence_urls,
        "errors": errors,
    }


def stored_signal_pools(records: Iterable[dict[str, Any]]) -> tuple[dict[tuple[str, str], list[dict[str, float]]], dict[str, list[dict[str, float]]]]:
    cohorts: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
    leagues: dict[str, list[dict[str, float]]] = defaultdict(list)
    for record in records:
        summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
        raw = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
        signals = {key: float(raw.get(key) or 0) for key in SIGNAL_KEYS}
        if not any(signals.values()):
            continue
        cohorts[cohort_key(record)].append(signals)
        leagues[str(record.get("leagueOrMedium") or "Unknown")].append(signals)
    return cohorts, leagues


def apply_hourly_metrics(
    item: dict[str, Any],
    cohorts: dict[tuple[str, str], list[dict[str, float]]],
    leagues: dict[str, list[dict[str, float]]],
    refreshed_at: str,
) -> dict[str, Any]:
    record = dict(item["record"])
    signals = item["signals"]
    peer_signals = cohorts.get(cohort_key(record), [])
    if len(peer_signals) < 8:
        peer_signals = leagues.get(str(record.get("leagueOrMedium") or "Unknown"), peer_signals)
    if not peer_signals:
        peer_signals = [signals]

    pcts = {
        key: percentile(float(signals.get(key, 0)), [float(peer.get(key, 0)) for peer in peer_signals])
        for key in SIGNAL_KEYS
    }
    recent_pct = pcts["recentProduction"]
    career_pct = pcts["careerProduction"]
    efficiency_pct = pcts["efficiency"]
    award_pct = pcts["awardPoints"]

    performance = clamp(24 + 72 * (recent_pct * 0.70 + efficiency_pct * 0.30), 20, 98)
    achievements = clamp(8 + 88 * (career_pct * 0.70 + award_pct * 0.30), 8, 99)
    potential = potential_prior(record, recent_pct)

    current_metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    base_audience = number(current_metrics.get("audience")) or 40.0
    news_boost = min(12.0, math.log1p(item.get("newsCount", 0)) * 4.0)
    audience = clamp(base_audience * 0.68 + recent_pct * 18 + award_pct * 10 + news_boost, 20, 97)

    availability = 75.0 if record.get("careerStatus") == "Active" else 55.0
    consistency = clamp(24 + 72 * (career_pct * 0.65 + recent_pct * 0.35), 24, 97)

    completeness = sum(
        1
        for condition in (
            bool(item.get("recent")),
            bool(item.get("career")),
            number(record.get("professionalGames")) is not None,
            signals.get("awardPoints", 0) > 0,
            number(record.get("age")) is not None,
        )
        if condition
    )
    confidence = clamp(max(float(record.get("pricingConfidence") or 0), 0.62 + completeness * 0.055), 0.62, 0.92)

    record["professionalGames"] = int(item.get("professionalGames") or 0)
    record["activeMetrics"] = {
        "performance": round(performance, 1),
        "achievements": round(achievements, 1),
        "potential": round(potential, 1),
        "audience": round(audience, 1),
        "availability": round(availability, 1),
        "consistency": round(consistency, 1),
    }
    record["pricingDataStatus"] = "Evidence enriched — hourly recent-stat refresh"
    record["pricingConfidence"] = round(confidence, 2)
    existing_evidence = record.get("pricingEvidence") if isinstance(record.get("pricingEvidence"), list) else []
    record["pricingEvidence"] = list(dict.fromkeys([*existing_evidence, *item.get("evidenceUrls", [])]))[-8:]
    record["pricingEvidenceSummary"] = {
        "cohort": f"{cohort_key(record)[0]} · {cohort_key(record)[1]}",
        "recentStatFields": len(item.get("recent", {})),
        "careerStatFields": len(item.get("career", {})),
        "awardNames": item.get("awards", []),
        "draftYear": record.get("draftYear"),
        "draftRound": record.get("draftRound"),
        "draftPick": record.get("draftPick"),
        "professionalGames": record.get("professionalGames", 0),
        "percentiles": {key: round(value, 4) for key, value in pcts.items()},
        "rawSignals": {key: round(float(signals.get(key, 0)), 4) for key in SIGNAL_KEYS},
    }
    record["hourlyEvidenceCheckedAt"] = refreshed_at
    if item.get("errors"):
        record["pricingEvidenceWarnings"] = item["errors"]
    else:
        record.pop("pricingEvidenceWarnings", None)
    return record


def stat_value(stats: dict[str, Any], *aliases: str) -> float | None:
    normalized = {norm_key(key): numeric_box_value(value) for key, value in stats.items()}
    wanted = [norm_key(alias) for alias in aliases]
    for alias in wanted:
        if normalized.get(alias) is not None:
            return normalized[alias]
    for key, value in normalized.items():
        if value is not None and any(key.endswith(alias) for alias in wanted):
            return value
    return None


def expected_game_signal(record: dict[str, Any], item: dict[str, Any]) -> float:
    """Convert saved season evidence to an expected one-game production signal."""
    signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
    season_signal = max(0.0, float(signals.get("recentProduction") or 0))
    recent = item.get("recent") if isinstance(item.get("recent"), dict) else {}
    league = str(record.get("leagueOrMedium") or "")
    if league in {"NBA", "WNBA"}:
        # ESPN basketball evidence is already expressed through per-game fields.
        return season_signal
    has_per_game_fields = any("pergame" in norm_key(key) or norm_key(key).startswith("avg") for key in recent)
    if has_per_game_fields:
        return season_signal
    games = stat_value(recent, "gamesPlayed", "games", "appearances", "gp")
    return season_signal / games if games and games > 0 else season_signal


def game_event_move(
    record: dict[str, Any],
    item: dict[str, Any],
    event: dict[str, Any],
    max_game_move_pct: float,
) -> tuple[float, dict[str, Any]]:
    stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
    actual_signal = float(signal_bundle(record, stats, {}, 0).get("recentProduction") or 0)
    expected_signal = expected_game_signal(record, item)
    if expected_signal <= 0:
        return 0.0, {
            "comparable": False,
            "reason": "No season baseline was available for this box score",
            "actualPerformanceScore": round(actual_signal, 3),
            "expectedPerformanceScore": 0.0,
        }

    performance_delta = (actual_signal / expected_signal - 1.0) * 100.0
    performance_move = clamp(performance_delta / 100.0 * 1.75, -2.25, 2.25)
    outcome_move = 0.15 if event.get("teamWon") is True else -0.10 if event.get("teamWon") is False else 0.0
    move_pct = clamp(performance_move + outcome_move, -max_game_move_pct, max_game_move_pct)
    if abs(move_pct) < 0.05 and (abs(performance_delta) >= 1.0 or event.get("teamWon") is not None):
        direction = move_pct if abs(move_pct) > 1e-9 else performance_delta or (1 if event.get("teamWon") else -1)
        move_pct = 0.05 if direction > 0 else -0.05
    return round(move_pct, 3), {
        "comparable": True,
        "actualPerformanceScore": round(actual_signal, 3),
        "expectedPerformanceScore": round(expected_signal, 3),
        "performanceDeltaPct": round(performance_delta, 2),
        "outcomeMovePct": outcome_move,
    }


def apply_game_market_moves(
    old_record: dict[str, Any],
    new_record: dict[str, Any],
    item: dict[str, Any],
    events: list[dict[str, Any]],
    max_game_move_pct: float,
    refreshed_at: str,
) -> tuple[dict[str, Any], float, list[dict[str, Any]]]:
    """Move market price once per completed game while keeping fundamentals anchored."""
    old_price = max(0.01, float(old_record.get("marketPrice") or new_record.get("marketPrice") or 0.01))
    model_target = max(0.01, float(new_record.get("marketPrice") or old_price))
    anchor_gap_pct = (model_target / old_price - 1.0) * 100.0
    anchor_budget = clamp(anchor_gap_pct * 0.15, -0.50, 0.50)
    per_event_anchor = anchor_budget / max(1, len(events))
    price = old_price
    prior_trend = [float(value) for value in old_record.get("trend", []) if isinstance(value, (int, float))]
    trend = [round(value, 2) for value in prior_trend] or [round(old_price, 2)] * 18
    event_results: list[dict[str, Any]] = []

    for event in sorted(events, key=lambda value: str(value.get("startedAt") or "")):
        event_move, evidence = game_event_move(old_record, item, event, max_game_move_pct)
        if not evidence.get("comparable"):
            event_results.append({**event, **evidence, "movePct": 0.0})
            continue
        combined_move = clamp(event_move + per_event_anchor, -max_game_move_pct, max_game_move_pct)
        next_price = round(price * (1.0 + combined_move / 100.0), 2)
        actual_move = round((next_price / price - 1.0) * 100.0, 2)
        price = next_price
        trend = trend[-17:] + [price]
        event_results.append({**event, **evidence, "movePct": actual_move, "priceAfter": price})

    change_pct = round((price / old_price - 1.0) * 100.0, 2)
    result = dict(new_record)
    result["modelTargetPrice"] = round(model_target, 2)
    result["previousMarketPrice"] = round(old_price, 2)
    result["marketPrice"] = round(price, 2)
    result["dailyChange"] = change_pct
    result["hourlyChangePct"] = change_pct
    result["lastPriceRefreshAt"] = refreshed_at
    result["trend"] = trend
    comparable = [event for event in event_results if event.get("comparable")]
    if comparable:
        latest = comparable[-1]
        result["lastPriceEventAt"] = latest.get("startedAt") or refreshed_at
        result["lastPriceEvent"] = str(latest.get("name") or "Completed game")
        result["lastPriceEventId"] = latest.get("eventKey")
        result["lastGameMovePct"] = latest.get("movePct")
        result["lastGamePerformanceDeltaPct"] = latest.get("performanceDeltaPct")
        result["lastGameStats"] = latest.get("stats", {})
    return result, change_pct, event_results


def rewrite_csv(records: list[dict[str, Any]]) -> None:
    fields = [
        "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
        "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
        "careerStage", "lastVerifiedAt", "verificationStatus", "sourceName",
        "sourceUrl", "sourceRecordId", "dataConfidence", "pricingConfidence",
        "pricingDataStatus", "pricingModelVersion", "marketPrice", "careerScore",
        "fundamentalValue", "draftYear", "draftRound", "draftPick",
        "professionalGames", "hourlyChangePct", "lastPriceEventAt",
    ]
    with CATALOG_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def validate_catalog(records: list[dict[str, Any]], original_count: int) -> None:
    errors: list[str] = []
    if len(records) != original_count:
        errors.append(f"record count changed from {original_count:,} to {len(records):,}")
    ids = [record.get("id") for record in records]
    tickers = [record.get("ticker") for record in records]
    if len(ids) != len(set(ids)):
        errors.append("duplicate profile IDs")
    if len(tickers) != len(set(tickers)):
        errors.append("duplicate tickers")
    for record in records:
        price = number(record.get("marketPrice"))
        if price is None or price <= 0:
            errors.append(f"invalid price for {record.get('name')}")
            break
    if errors:
        raise SystemExit("Hourly catalog validation failed: " + "; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--lookback-hours", type=float, default=48.0)
    parser.add_argument("--max-athletes", type=int, default=1800)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    parser.add_argument("--baseline-run-id", default="")
    args = parser.parse_args()

    if not CATALOG.exists():
        raise SystemExit("data/current_catalog.json is missing; download the latest full catalog artifact first")
    records = safe_json(CATALOG, [])
    if not isinstance(records, list) or not records:
        raise SystemExit("data/current_catalog.json must be a non-empty array")

    started = time.time()
    now = utc_now()
    refreshed_at = iso_utc(now)
    prior_manifest = safe_json(HOURLY_MANIFEST, {})
    if not isinstance(prior_manifest, dict):
        prior_manifest = {}
    processed_history = prior_processed_events(prior_manifest, now)
    processed_player_history = prior_processed_player_events(prior_manifest)
    participant_ids, athlete_events, events, discovery_warnings = discover_recent_events(
        records,
        now=now,
        lookback_hours=args.lookback_hours,
        timeout=args.request_timeout,
        workers=args.workers,
        processed_keys=set(processed_history),
        processed_player_keys=processed_player_history,
    )
    selected_indexes = select_records(
        records,
        participant_ids,
        max_athletes=args.max_athletes,
    )

    print(f"Unprocessed completed events found: {len(events):,}")
    print(f"Players with box-score statistics: {len(participant_ids):,}")
    print(f"Athletes selected for game-level evidence refresh: {len(selected_indexes):,}")

    cohorts, leagues = stored_signal_pools(records)
    overrides = load_overrides(PRICING_OVERRIDES)
    # A displayed change describes this refresh, not a permanent random drift.
    # Untouched records keep their price and history but return to a 0.00% move.
    updated_records = []
    for record in records:
        retained = dict(record)
        retained["dailyChange"] = 0.0
        retained["hourlyChangePct"] = 0.0
        updated_records.append(retained)
    results_by_index: dict[int, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(fetch_hourly_evidence, records[index], args.request_timeout): index
            for index in selected_indexes
        }
        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            try:
                results_by_index[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                results_by_index[index] = {"record": records[index], "ok": False, "reason": f"worker {type(exc).__name__}: {exc}"}
            completed += 1
            if completed % 200 == 0 or completed == len(futures):
                usable = sum(1 for item in results_by_index.values() if item.get("ok"))
                print(f"Hourly evidence requests: {completed:,}/{len(futures):,}; usable: {usable:,}", flush=True)

    usable = 0
    changed = 0
    unchanged = 0
    failures: Counter[str] = Counter()
    changes: list[dict[str, Any]] = []
    evaluated_player_events: set[tuple[tuple[str, str], str]] = set()
    for index in selected_indexes:
        item = results_by_index.get(index, {"record": records[index], "ok": False, "reason": "missing worker result"})
        old_record = records[index]
        athlete_key = (str(old_record.get("sourceNamespace") or ""), str(old_record.get("sourceRecordId") or ""))
        player_events = athlete_events.get(athlete_key, [])
        if not item.get("ok"):
            failure_reason = str(item.get("reason") or "unknown")
            failures[failure_reason] += 1
            retained = dict(updated_records[index])
            retained["hourlyEvidenceCheckedAt"] = refreshed_at
            retained["hourlyEvidenceWarning"] = failure_reason
            updated_records[index] = retained
            continue

        usable += 1
        metrics_record = apply_hourly_metrics(item, cohorts, leagues, refreshed_at)
        benchmark_records: list[dict[str, Any]] = []
        if CURRENT_SEED.exists():
            loaded_seed = safe_json(CURRENT_SEED, [])
            if isinstance(loaded_seed, list):
                benchmark_records = loaded_seed
        repriced = apply_pricing_to_records(
            [metrics_record],
            overrides,
            benchmark_records=benchmark_records,
            calibration_reference=records,
        )[0]
        repriced, change_pct, event_results = apply_game_market_moves(
            old_record,
            repriced,
            item,
            player_events,
            args.max_game_move_pct,
            refreshed_at,
        )
        for result in event_results:
            game_key = str(result.get("eventKey") or "")
            evaluated_player_events.add((athlete_key, game_key))
            processed_player_history.add(player_event_key(athlete_key, game_key))
        repriced.pop("hourlyEvidenceWarning", None)
        updated_records[index] = repriced
        if abs(change_pct) >= 0.01:
            changed += 1
            changes.append(
                {
                    "id": repriced.get("id"),
                    "name": repriced.get("name"),
                    "league": repriced.get("leagueOrMedium"),
                    "oldPrice": round(float(old_record.get("marketPrice") or 0), 2),
                    "newPrice": round(float(repriced.get("marketPrice") or 0), 2),
                    "changePct": change_pct,
                    "modelTargetPrice": repriced.get("modelTargetPrice"),
                    "games": [
                        {
                            "eventKey": result.get("eventKey"),
                            "name": result.get("name"),
                            "startedAt": result.get("startedAt"),
                            "movePct": result.get("movePct"),
                            "performanceDeltaPct": result.get("performanceDeltaPct"),
                        }
                        for result in event_results
                    ],
                }
            )
        else:
            unchanged += 1

    validate_catalog(updated_records, len(records))
    CATALOG.write_text(json.dumps(updated_records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    rewrite_csv(updated_records)

    catalog_athlete_keys = {
        (str(record.get("sourceNamespace") or ""), str(record.get("sourceRecordId") or ""))
        for record in records
        if record.get("sourceRecordId")
    }
    processed_now: list[dict[str, Any]] = []
    for event in events:
        if not event.get("ready"):
            continue
        key = str(event.get("eventKey") or "")
        matching_players = {
            athlete_key
            for athlete_key, player_events in athlete_events.items()
            if athlete_key in catalog_athlete_keys
            and any(str(item.get("eventKey") or "") == key for item in player_events)
        }
        if matching_players and not all((athlete_key, key) in evaluated_player_events for athlete_key in matching_players):
            continue
        processed_item = {
            "key": key,
            "provider": event.get("provider"),
            "eventId": event.get("eventId"),
            "league": event.get("league"),
            "name": event.get("name"),
            "startedAt": event.get("startedAt"),
            "processedAt": refreshed_at,
            "playersWithStats": event.get("playersWithStats", 0),
            "matchedCatalogPlayers": len(matching_players),
        }
        processed_history[key] = processed_item
        processed_now.append(processed_item)

    # Player-level markers prevent a successful athlete from being moved twice
    # when a teammate's evidence request fails and the game must be retried.
    processed_player_history = {
        key for key in processed_player_history
        if key.split("|", 1)[0] not in processed_history
    }

    manifest = safe_json(CATALOG_MANIFEST, {})
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.update(
        {
            "hourlyPriceRefreshAt": refreshed_at,
            "hourlyBaselineRunId": str(args.baseline_run_id or ""),
            "hourlyEventsFound": len(events),
            "hourlyEventsProcessed": len(processed_now),
            "hourlyAthletesSelected": len(selected_indexes),
            "hourlyEvidenceUsable": usable,
            "hourlyPricesChanged": changed,
            "hourlyRefreshMode": "Completed games are processed once from player box scores; season and career evidence remains the valuation anchor.",
            "hourlyMaximumGameMovePct": args.max_game_move_pct,
            "hourlyRefreshManifest": "data/hourly_refresh_manifest.json",
        }
    )
    CATALOG_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    hourly_manifest = {
        "version": "1.3-game-level-event-pricing",
        "generatedAt": refreshed_at,
        "weeklyBaselineRunId": str(args.baseline_run_id or ""),
        "elapsedSeconds": round(time.time() - started, 1),
        "lookbackHours": args.lookback_hours,
        "maximumAthletes": args.max_athletes,
        "maximumGameMovePct": args.max_game_move_pct,
        "eventsFound": len(events),
        "eventsProcessedNow": len(processed_now),
        "processedEvents": sorted(
            processed_history.values(),
            key=lambda item: str(item.get("startedAt") or item.get("processedAt") or ""),
        )[-2000:],
        "processedPlayerEvents": sorted(processed_player_history)[-50000:],
        "exactParticipantsFound": len(participant_ids),
        "athletesSelected": len(selected_indexes),
        "evidenceUsable": usable,
        "pricesChanged": changed,
        "pricesUnchanged": unchanged,
        "failures": sum(failures.values()),
        "topFailureReasons": dict(failures.most_common(12)),
        "discoveryWarnings": discovery_warnings[:30],
        "events": events[:150],
        "largestMoves": sorted(changes, key=lambda item: abs(float(item["changePct"])), reverse=True)[:100],
        "method": (
            "Completed games are identified by stable provider event IDs and are processed only once. Player-level box scores are compared with "
            "the athlete's saved season baseline; the resulting bounded game move is blended with a small pull toward the season/career model target. "
            "Drafted rookies automatically reduce their IPO influence as professional games accumulate."
        ),
    }
    HOURLY_MANIFEST.write_text(json.dumps(hourly_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    prices = [float(record.get("marketPrice") or 0) for record in updated_records]
    print(f"Usable hourly evidence: {usable:,}")
    print(f"Prices changed: {changed:,}; unchanged: {unchanged:,}; failures: {sum(failures.values()):,}")
    if prices:
        print(f"Catalog price median after refresh: ${statistics.median(prices):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
