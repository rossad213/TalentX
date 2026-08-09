#!/usr/bin/env python3
"""Expand verified Soccer game history without changing today's TalentX price.

The generic historical Sports scanner is efficient for leagues such as MLB/NBA/NFL,
but Soccer is spread across many ESPN league slugs. Scanning every league for every
calendar day is wasteful and can leave Soccer under-covered. This adapter instead
starts from each verified player's current ESPN roster team, fetches the team's
season schedules, verifies player participation from ESPN match summaries, and
stores the resulting games in the same durable ``priceEvents`` contract used by
all TalentX charts.

Only completed matches with a player-level box-score entry are eligible. Historical
prices are reconstructed backward from the unchanged current market price. Each
pass prioritizes teams whose players still lack durable verified game history so
signings or other career events cannot make an unfilled match chart look covered.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backfill_price_history import (
    evaluated_game_events,
    existing_events,
    history_from_events,
    merge_event_evidence,
    reconstruct_chain,
)
from hourly_price_refresh import (
    ESPN_SUMMARY,
    completed_event,
    extract_espn_game_stats,
    fetch_hourly_evidence,
    iso_utc,
    numeric_box_value,
    parse_datetime,
    utc_now,
)
from enrich_current_catalog import fetch_json

ESPN_TEAM_SCHEDULE = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/{team_id}/schedule"
    "?season={season}"
)


def load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def team_id_for(record: dict[str, Any]) -> str:
    explicit = str(record.get("sourceTeamId") or "").strip()
    if explicit:
        return explicit
    source_url = str(record.get("sourceUrl") or "")
    match = re.search(r"/teams/([^/?#]+)/roster(?:[/?#]|$)", source_url)
    return match.group(1) if match else ""


def schedule_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect event-like dictionaries from slightly different ESPN schedule shapes."""
    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            event_id = str(node.get("id") or "").strip()
            has_competition = isinstance(node.get("competitions"), list)
            if event_id and node.get("date") and (has_competition or "status" in node):
                found.setdefault(event_id, node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return list(found.values())


def schedule_event_info(event: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any] | None:
    event_id = str(event.get("id") or "").strip()
    started = parse_datetime(event.get("date"))
    status = event.get("status") if isinstance(event.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    state = str(status_type.get("state") or status_type.get("name") or event.get("statusType") or "")
    if not event_id or started is None or started < start or started > end or not completed_event(state):
        return None

    team_ids: list[str] = []
    winners: list[str] = []
    for competition in event.get("competitions") or []:
        if not isinstance(competition, dict):
            continue
        for competitor in competition.get("competitors") or []:
            if not isinstance(competitor, dict):
                continue
            team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
            team_id = str(team.get("id") or "").strip()
            if team_id:
                team_ids.append(team_id)
                if competitor.get("winner") is True:
                    winners.append(team_id)

    return {
        "provider": "ESPN",
        "eventId": event_id,
        "eventKey": f"espn:{event_id}",
        "league": "",
        "sport": "soccer",
        "name": str(event.get("name") or event.get("shortName") or event_id),
        "state": state,
        "startedAt": iso_utc(started),
        "teamIds": team_ids,
        "winningTeamIds": winners,
    }


def normalized_soccer_game_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """Add one verified appearance when the box score proves participation."""
    output = dict(stats)
    minutes = numeric_box_value(output.get("minutes"))
    participated = minutes is not None and minutes > 0
    if not participated:
        participated = any(
            numeric_box_value(output.get(key)) not in (None, 0)
            for key in ("goals", "assists", "shots", "shotsOnTarget", "saves")
        )
    if participated:
        output.setdefault("appearances", 1.0)
        output.setdefault("gamesPlayed", 1.0)
    return output


def saved_baseline(record: dict[str, Any]) -> dict[str, Any] | None:
    """Use already-enriched season signals when a live overview is temporarily thin."""
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    raw = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
    recent_prod = float(raw.get("recentProduction") or 0)
    if recent_prod <= 0:
        return None
    games = int(record.get("professionalGames") or summary.get("professionalGames") or 0)
    # Treat the saved production score as a season aggregate unless no game count
    # was recorded; expected_game_signal will convert it to a per-game baseline.
    recent = {"gamesPlayed": max(1, games)} if games else {"avgProduction": recent_prod}
    return {
        "record": dict(record),
        "ok": True,
        "professionalGames": games,
        "recent": recent,
        "career": {},
        "signals": {
            "recentProduction": recent_prod,
            "careerProduction": float(raw.get("careerProduction") or 0),
            "efficiency": float(raw.get("efficiency") or 0),
            "usage": float(raw.get("usage") or 0),
            "careerUsage": float(raw.get("careerUsage") or 0),
            "awardPoints": float(raw.get("awardPoints") or 0),
        },
        "awards": summary.get("awardNames", []),
        "newsCount": 0,
        "evidenceUrls": list(record.get("pricingEvidence") or []),
        "errors": ["Used previously verified season baseline for historical Soccer comparison"],
    }


def has_verified_game_history(record: dict[str, Any]) -> bool:
    """Return True only when the record has a durable verified game event."""
    for event in existing_events(record):
        if event.get("verified") is False:
            continue
        if str(event.get("eventType") or "game") == "game":
            return True
    return False


def team_priority(records: list[dict[str, Any]], indexes: list[int]) -> tuple[int, int, float]:
    missing = sum(1 for index in indexes if not has_verified_game_history(records[index]))
    confidence = max((float(records[index].get("pricingConfidence") or 0) for index in indexes), default=0.0)
    return missing, len(indexes), confidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--max-players", type=int, default=3500)
    parser.add_argument("--max-teams", type=int, default=350)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    args = parser.parse_args()

    records = load_catalog(args.catalog)
    now = utc_now()
    start = now - timedelta(days=max(1, args.days))
    soccer_indexes = [
        index for index, record in enumerate(records)
        if str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "") == "Soccer"
        and str(record.get("sourceNamespace") or "") == "espn"
        and str(record.get("sourceLeagueSlug") or "").strip()
        and team_id_for(record)
    ]
    soccer_indexes.sort(
        key=lambda index: (
            1 if not has_verified_game_history(records[index]) else 0,
            float(records[index].get("pricingConfidence") or 0),
            str(records[index].get("name") or ""),
        ),
        reverse=True,
    )
    if args.max_players > 0:
        soccer_indexes = soccer_indexes[: args.max_players]
    print(f"Soccer players eligible for team-schedule history: {len(soccer_indexes):,}")
    if not soccer_indexes:
        return 0

    all_groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index in soccer_indexes:
        record = records[index]
        all_groups[(str(record.get("sourceLeagueSlug")), team_id_for(record))].append(index)
    ordered_groups = sorted(
        all_groups.items(),
        key=lambda item: team_priority(records, item[1]),
        reverse=True,
    )
    if args.max_teams > 0:
        ordered_groups = ordered_groups[: args.max_teams]
    groups = dict(ordered_groups)
    print(f"Soccer teams selected for this coverage pass: {len(groups):,} of {len(all_groups):,}")

    seasons = range(start.year, now.year + 1)
    schedule_jobs = [(league, team_id, season) for (league, team_id) in groups for season in seasons]
    match_info: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    def fetch_schedule(job: tuple[str, str, int]) -> tuple[tuple[str, str, int], list[dict[str, Any]], str | None]:
        league, team_id, season = job
        url = ESPN_TEAM_SCHEDULE.format(league=league, team_id=team_id, season=season)
        try:
            return job, schedule_events(fetch_json(url, args.request_timeout)), None
        except Exception as exc:  # noqa: BLE001
            return job, [], f"schedule {league}/{team_id}/{season}: {type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(schedule_jobs)))) as executor:
        futures = [executor.submit(fetch_schedule, job) for job in schedule_jobs]
        for future in as_completed(futures):
            (league, team_id, _season), events, warning = future.result()
            if warning:
                warnings.append(warning)
            for raw in events:
                info = schedule_event_info(raw, start, now)
                if info is None or team_id not in set(info.get("teamIds") or []):
                    continue
                info["league"] = league
                match_info.setdefault(str(info["eventId"]), info)

    print(f"Completed Soccer matches discovered from team schedules: {len(match_info):,}")
    if warnings:
        print(f"Soccer schedule warnings: {len(warnings):,}")
    if not match_info:
        return 0

    player_events: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def fetch_summary(info: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str | None]:
        try:
            payload = fetch_json(
                ESPN_SUMMARY.format(sport="soccer", league=info["league"], event_id=info["eventId"]),
                args.request_timeout,
            )
            stats = extract_espn_game_stats(payload, set(info.get("winningTeamIds") or []))
            return info, stats, None if stats else f"no player box score {info['league']}/{info['eventId']}"
        except Exception as exc:  # noqa: BLE001
            return info, {}, f"summary {info['league']}/{info['eventId']}: {type(exc).__name__}"

    infos = list(match_info.values())
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(infos)))) as executor:
        futures = [executor.submit(fetch_summary, info) for info in infos]
        for future in as_completed(futures):
            info, stats_by_player, warning = future.result()
            if warning:
                warnings.append(warning)
            for athlete_id, performance in stats_by_player.items():
                stats = normalized_soccer_game_stats(performance.get("stats", {}))
                if not stats.get("appearances"):
                    continue
                player_events[str(athlete_id)].append({
                    "eventKey": info["eventKey"],
                    "eventId": info["eventId"],
                    "eventType": "game",
                    "provider": "ESPN",
                    "league": info["league"],
                    "name": info["name"],
                    "startedAt": info["startedAt"],
                    "stats": stats,
                    "teamWon": performance.get("teamWon"),
                })

    candidates = [index for index in soccer_indexes if str(records[index].get("sourceRecordId")) in player_events]
    evidence: dict[int, dict[str, Any]] = {}

    def fetch_baseline(index: int) -> tuple[int, dict[str, Any]]:
        record = records[index]
        try:
            item = fetch_hourly_evidence(record, args.request_timeout)
        except Exception as exc:  # noqa: BLE001
            item = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if not item.get("ok"):
            item = saved_baseline(record) or item
        return index, item

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(candidates) or 1))) as executor:
        futures = [executor.submit(fetch_baseline, index) for index in candidates]
        for future in as_completed(futures):
            index, item = future.result()
            evidence[index] = item

    updated = list(records)
    touched = 0
    generated_count = 0
    for index in candidates:
        record = records[index]
        item = evidence.get(index, {})
        if not item.get("ok"):
            continue
        athlete_id = str(record.get("sourceRecordId") or "")
        generated = evaluated_game_events(
            record,
            item,
            player_events.get(athlete_id, []),
            args.max_game_move_pct,
        )
        if not generated:
            continue
        result = dict(record)
        combined = merge_event_evidence(existing_events(result), generated)
        rebuilt = reconstruct_chain(result, combined)
        result["priceEvents"] = rebuilt
        result["priceHistory"] = history_from_events(rebuilt)
        result["priceHistoryStatus"] = "verified-event-backfill"
        result["priceHistoryBackfilledAt"] = iso_utc(now)
        result["priceHistoryBackfillDays"] = max(int(result.get("priceHistoryBackfillDays") or 0), args.days)
        result["priceHistoryBackfillModel"] = "soccer-team-schedule-game-events-v3"
        updated[index] = result
        touched += 1
        generated_count += len(generated)

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Expanded {touched:,} Soccer charts with {generated_count:,} verified completed-match events.")
    if warnings:
        print(f"Total Soccer source warnings: {len(warnings):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
