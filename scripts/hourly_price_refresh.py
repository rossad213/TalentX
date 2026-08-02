#!/usr/bin/env python3
"""Lightweight hourly TalentX price refresh.

This job starts from the latest successful full-catalog artifact, detects games
that are live or recently completed, identifies participating athletes, refreshes
only those athletes' available career/statistical evidence, and recalculates
only their prices. The weekly workflow remains responsible for full roster,
identity, award, and status verification.
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


def team_id_from_record(record: dict[str, Any]) -> str:
    source_url = str(record.get("sourceUrl") or "")
    match = re.search(r"/teams/([^/]+)/roster", source_url)
    return match.group(1) if match else ""


def nhl_abbreviation_from_record(record: dict[str, Any]) -> str:
    source_url = str(record.get("sourceUrl") or "")
    match = re.search(r"/roster/([^/]+)/current", source_url)
    return match.group(1).upper() if match else ""


def translated_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or value.get("en") or next(iter(value.values()), ""))
    return str(value or "")


def extract_espn_athlete_ids(payload: Any) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            athlete = node.get("athlete")
            if isinstance(athlete, dict) and athlete.get("id") is not None:
                found.add(str(athlete["id"]))
            athlete_id = node.get("athleteId")
            if athlete_id is not None:
                found.add(str(athlete_id))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return {value for value in found if value}


def extract_nhl_player_ids(payload: Any) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            player_id = node.get("playerId")
            if player_id is not None:
                found.add(str(player_id))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return {value for value in found if value}


def event_is_recent(start: datetime | None, state: str, now: datetime, cutoff: datetime) -> bool:
    if start is None or start < cutoff or start > now + timedelta(hours=1):
        return False
    normalized = state.lower().strip()
    return normalized in {"in", "post", "live", "crit", "off", "final", "completed"} or start <= now


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
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]], set[str], list[dict[str, Any]], list[str]]:
    """Return participant IDs and fallback team targets from recent events."""
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
    fallback_teams: set[tuple[str, str, str]] = set()
    nhl_teams: set[str] = set()
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
                if not event_id or not event_is_recent(start, state, now, cutoff):
                    continue
                team_ids: list[str] = []
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
                info = {
                    "provider": "ESPN",
                    "eventId": event_id,
                    "league": league,
                    "sport": sport,
                    "name": str(event.get("name") or event.get("shortName") or event_id),
                    "state": state,
                    "startedAt": iso_utc(start) if start else None,
                    "teamIds": team_ids,
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
            if not game_id or not event_is_recent(start, state, now, cutoff):
                continue
            teams: list[str] = []
            for side in ("awayTeam", "homeTeam"):
                team = game.get(side) if isinstance(game.get(side), dict) else {}
                abbreviation = translated_text(team.get("abbrev")).upper()
                if abbreviation:
                    teams.append(abbreviation)
            info = {
                "provider": "NHL",
                "eventId": game_id,
                "league": "NHL",
                "sport": "hockey",
                "name": f"{' vs '.join(teams)}" if teams else game_id,
                "state": state,
                "startedAt": iso_utc(start) if start else None,
                "teamIds": teams,
            }
            events.append(info)
            nhl_jobs.append((game_id, info))

    def fetch_espn_summary(job: tuple[str, str, str, dict[str, Any]]) -> tuple[set[tuple[str, str]], str | None]:
        sport, league, event_id, _ = job
        try:
            payload = fetch_json(ESPN_SUMMARY.format(sport=sport, league=league, event_id=event_id), timeout)
            return {("espn", athlete_id) for athlete_id in extract_espn_athlete_ids(payload)}, None
        except Exception as exc:  # noqa: BLE001
            return set(), f"summary {sport}/{league}/{event_id}: {type(exc).__name__}"

    def fetch_nhl_boxscore(job: tuple[str, dict[str, Any]]) -> tuple[set[tuple[str, str]], str | None]:
        game_id, _ = job
        try:
            payload = fetch_json(NHL_BOXSCORE.format(game_id=game_id), timeout)
            return {("nhl", athlete_id) for athlete_id in extract_nhl_player_ids(payload)}, None
        except Exception as exc:  # noqa: BLE001
            return set(), f"NHL boxscore {game_id}: {type(exc).__name__}"

    all_jobs: list[tuple[str, Any]] = [("espn", job) for job in summary_jobs] + [("nhl", job) for job in nhl_jobs]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(all_jobs) or 1))) as executor:
        futures = {}
        for kind, job in all_jobs:
            future = executor.submit(fetch_espn_summary if kind == "espn" else fetch_nhl_boxscore, job)
            futures[future] = (kind, job)
        for future in as_completed(futures):
            kind, job = futures[future]
            ids, warning = future.result()
            if ids:
                participant_ids.update(ids)
            elif kind == "espn":
                sport, league, _, info = job
                for team_id in info.get("teamIds", []):
                    fallback_teams.add((sport, league, str(team_id)))
            else:
                _, info = job
                for abbreviation in info.get("teamIds", []):
                    if abbreviation:
                        nhl_teams.add(str(abbreviation).upper())
            if warning:
                warnings.append(warning)

    return participant_ids, fallback_teams, nhl_teams, events, warnings


def select_records(
    records: list[dict[str, Any]],
    participant_ids: set[tuple[str, str]],
    fallback_teams: set[tuple[str, str, str]],
    nhl_teams: set[str],
    *,
    max_athletes: int,
) -> list[int]:
    exact: list[int] = []
    fallback: list[int] = []
    for index, record in enumerate(records):
        namespace = str(record.get("sourceNamespace") or "")
        athlete_id = str(record.get("sourceRecordId") or "")
        if (namespace, athlete_id) in participant_ids:
            exact.append(index)
            continue
        if namespace == "espn":
            sport = SPORT_PATH.get(str(record.get("discipline") or ""))
            league = str(record.get("sourceLeagueSlug") or "")
            team_id = team_id_from_record(record)
            if sport and league and team_id and (sport, league, team_id) in fallback_teams:
                fallback.append(index)
        elif namespace == "nhl":
            abbreviation = nhl_abbreviation_from_record(record)
            if abbreviation and abbreviation in nhl_teams:
                fallback.append(index)

    def priority(index: int) -> tuple[int, float, float, str]:
        record = records[index]
        evidence = str(record.get("pricingDataStatus") or "").startswith("Evidence enriched")
        starter = bool(record.get("starter"))
        confidence = float(record.get("pricingConfidence") or 0)
        return (1 if evidence else 0, 1 if starter else 0, confidence, str(record.get("name") or ""))

    exact = sorted(set(exact), key=priority, reverse=True)
    remaining = max(0, max_athletes - len(exact)) if max_athletes > 0 else len(fallback)
    fallback = sorted(set(fallback) - set(exact), key=priority, reverse=True)[:remaining]
    selected = exact + fallback
    if max_athletes > 0:
        selected = selected[:max_athletes]
    return selected


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
    career_pct = max(pcts["careerProduction"], pcts["careerUsage"] * 0.82)
    efficiency_pct = pcts["efficiency"]
    usage_pct = pcts["usage"]
    award_pct = pcts["awardPoints"]

    performance = clamp(24 + 72 * (recent_pct * 0.56 + efficiency_pct * 0.24 + usage_pct * 0.20), 20, 98)
    achievements = clamp(8 + 88 * (career_pct * 0.63 + award_pct * 0.27 + pcts["careerUsage"] * 0.10), 8, 99)
    potential = potential_prior(record, recent_pct)

    current_metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    base_audience = number(current_metrics.get("audience")) or 40.0
    news_boost = min(12.0, math.log1p(item.get("newsCount", 0)) * 4.0)
    audience = clamp(base_audience * 0.68 + recent_pct * 18 + award_pct * 10 + news_boost, 20, 97)

    if signals.get("usage", 0) > 0:
        availability = clamp(52 + usage_pct * 42, 48, 96)
    else:
        availability = float(current_metrics.get("availability") or (72 if record.get("careerStatus") == "Active" else 55))
    consistency = clamp(30 + career_pct * 35 + recent_pct * 25 + availability * 0.10, 28, 96)

    completeness = sum(
        1
        for condition in (
            bool(item.get("recent")),
            bool(item.get("career")),
            signals.get("usage", 0) > 0,
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


def cap_hourly_market_move(old_record: dict[str, Any], new_record: dict[str, Any], max_move_pct: float, refreshed_at: str) -> tuple[dict[str, Any], float]:
    old_price = max(0.01, float(old_record.get("marketPrice") or new_record.get("marketPrice") or 0.01))
    model_target = max(0.01, float(new_record.get("marketPrice") or old_price))
    raw_move = model_target / old_price - 1
    capped_move = clamp(raw_move, -max_move_pct / 100.0, max_move_pct / 100.0)
    new_price = round(old_price * (1 + capped_move), 2)
    change_pct = round((new_price / old_price - 1) * 100, 2)

    result = dict(new_record)
    result["modelTargetPrice"] = round(model_target, 2)
    result["previousMarketPrice"] = round(old_price, 2)
    result["marketPrice"] = new_price
    result["dailyChange"] = change_pct
    result["hourlyChangePct"] = change_pct
    result["lastPriceEventAt"] = refreshed_at
    result["lastPriceEvent"] = "Recent game/statistics refresh"
    prior_trend = [float(value) for value in old_record.get("trend", []) if isinstance(value, (int, float))]
    result["trend"] = [round(value, 2) for value in prior_trend[-17:]] + [new_price]
    return result, change_pct


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
    parser.add_argument("--lookback-hours", type=float, default=10.0)
    parser.add_argument("--max-athletes", type=int, default=1800)
    parser.add_argument("--max-hourly-move-pct", type=float, default=8.0)
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
    participant_ids, fallback_teams, nhl_teams, events, discovery_warnings = discover_recent_events(
        records,
        now=now,
        lookback_hours=args.lookback_hours,
        timeout=args.request_timeout,
        workers=args.workers,
    )
    selected_indexes = select_records(
        records,
        participant_ids,
        fallback_teams,
        nhl_teams,
        max_athletes=args.max_athletes,
    )

    print(f"Recent events found: {len(events):,}")
    print(f"Exact participants found: {len(participant_ids):,}")
    print(f"Athletes selected for hourly evidence refresh: {len(selected_indexes):,}")

    cohorts, leagues = stored_signal_pools(records)
    overrides = load_overrides(PRICING_OVERRIDES)
    updated_records = list(records)
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
    for index in selected_indexes:
        item = results_by_index.get(index, {"record": records[index], "ok": False, "reason": "missing worker result"})
        old_record = records[index]
        if not item.get("ok"):
            failure_reason = str(item.get("reason") or "unknown")
            failures[failure_reason] += 1
            retained = dict(old_record)
            retained["hourlyEvidenceCheckedAt"] = refreshed_at
            retained["hourlyEvidenceWarning"] = failure_reason
            updated_records[index] = retained
            continue

        usable += 1
        metrics_record = apply_hourly_metrics(item, cohorts, leagues, refreshed_at)
        repriced = apply_pricing_to_records([metrics_record], overrides)[0]
        repriced, change_pct = cap_hourly_market_move(old_record, repriced, args.max_hourly_move_pct, refreshed_at)
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
                }
            )
        else:
            unchanged += 1

    validate_catalog(updated_records, len(records))
    CATALOG.write_text(json.dumps(updated_records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    rewrite_csv(updated_records)

    manifest = safe_json(CATALOG_MANIFEST, {})
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.update(
        {
            "hourlyPriceRefreshAt": refreshed_at,
            "hourlyBaselineRunId": str(args.baseline_run_id or ""),
            "hourlyEventsFound": len(events),
            "hourlyAthletesSelected": len(selected_indexes),
            "hourlyEvidenceUsable": usable,
            "hourlyPricesChanged": changed,
            "hourlyRefreshMode": "Recent games and participating-athlete statistics only; full catalog remains weekly.",
            "hourlyMaximumMovePct": args.max_hourly_move_pct,
            "hourlyRefreshManifest": "data/hourly_refresh_manifest.json",
        }
    )
    CATALOG_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    hourly_manifest = {
        "version": "1.1-hourly-rookie-transition",
        "generatedAt": refreshed_at,
        "weeklyBaselineRunId": str(args.baseline_run_id or ""),
        "elapsedSeconds": round(time.time() - started, 1),
        "lookbackHours": args.lookback_hours,
        "maximumAthletes": args.max_athletes,
        "maximumHourlyMovePct": args.max_hourly_move_pct,
        "eventsFound": len(events),
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
            "Scoreboards identify live/recently completed games; box scores identify participants; only those athletes receive "
            "updated recent-stat evidence and repricing. Drafted rookies automatically reduce their IPO influence as professional games accumulate. "
            "Existing cohort distributions provide normalization until the next full weekly rebuild."
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
