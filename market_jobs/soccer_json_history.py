#!/usr/bin/env python3
"""Build reliable verified Soccer price history from ESPN JSON game data.

This job deliberately does not scrape ESPN player HTML. GitHub-hosted runners can
receive bot/JavaScript interstitials from player pages, which previously allowed a
history job to finish with zero useful Soccer events.

Instead TalentX uses the same kind of source that makes MLB history reliable:
verified team schedules plus JSON game summaries/box scores. Every stored event
requires an ESPN athlete ID to appear in a completed match's player data. If the
normal season-baseline comparison is available, the shared TalentX game policy is
used. If that comparison is unavailable, a smaller Soccer-specific verified-stat
fallback is used so a real appearance/result does not disappear merely because a
season aggregate endpoint is thin.

Historical reconstruction never changes today's marketPrice or current change.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from backfill_price_history import (  # noqa: E402
    existing_events,
    history_from_events,
    merge_event_evidence,
    reconstruct_chain,
)
from enrich_current_catalog import fetch_json  # noqa: E402
from hourly_price_refresh import (  # noqa: E402
    ESPN_SUMMARY,
    completed_event,
    extract_espn_game_stats,
    fetch_hourly_evidence,
    game_event_move,
    iso_utc,
    numeric_box_value,
    parse_datetime,
    utc_now,
)

PRIORITY_NAMES_PATH = ROOT / "data" / "priority_soccer_names.json"
ESPN_TEAMS = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams?limit=1000"
ESPN_TEAM_SCHEDULE = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/{team_id}/schedule"
    "?season={season}"
)
ESPN_CDN_BOXSCORE = "https://cdn.espn.com/core/soccer/boxscore?xhr=1&gameId={event_id}&league={league}"
ESPN_CDN_GAME = "https://cdn.espn.com/core/soccer/game?xhr=1&gameId={event_id}&league={league}"

# These are the names the user specifically surfaced while diagnosing empty
# Soccer charts. The workflow quality gate requires several of these anchors to
# have real verified match events before the artifact can be called healthy.
ANCHOR_NAMES = {
    "Lionel Messi",
    "Cristiano Ronaldo",
    "Mohamed Salah",
    "Harry Kane",
    "Luis Díaz",
}

MAX_EVENTS_PER_RECORD = 2500


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def priority_names() -> set[str]:
    output = {norm(name) for name in ANCHOR_NAMES}
    try:
        payload = json.loads(PRIORITY_NAMES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = []
    if isinstance(payload, list):
        output.update(norm(name) for name in payload if str(name).strip())
    return output


def source_team_id(record: dict[str, Any]) -> str:
    explicit = str(record.get("sourceTeamId") or "").strip()
    if explicit:
        return explicit
    candidates: list[str] = []
    for key in ("sourceUrl", "statusSourceUrl", "rosterSourceUrl"):
        if record.get(key):
            candidates.append(str(record[key]))
    evidence = record.get("pricingEvidence")
    if isinstance(evidence, list):
        candidates.extend(str(value) for value in evidence if isinstance(value, str))
    for value in candidates:
        match = re.search(r"/teams/([^/?#]+)/roster(?:[/?#]|$)", value)
        if match:
            return match.group(1)
    return ""


def team_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}

    def add(candidate: Any) -> None:
        if not isinstance(candidate, dict):
            return
        team = candidate.get("team") if isinstance(candidate.get("team"), dict) else candidate
        team_id = str(team.get("id") or "").strip()
        if team_id:
            output[team_id] = team

    for wrapper in payload.get("teams") or []:
        add(wrapper)
    for sport in payload.get("sports") or []:
        if not isinstance(sport, dict):
            continue
        for league in sport.get("leagues") or []:
            if not isinstance(league, dict):
                continue
            for wrapper in league.get("teams") or []:
                add(wrapper)
    return list(output.values())


def team_names(team: dict[str, Any]) -> set[str]:
    values = {
        str(team.get(key) or "").strip()
        for key in ("displayName", "name", "shortDisplayName", "abbreviation", "location", "nickname")
    }
    location = str(team.get("location") or "").strip()
    nickname = str(team.get("name") or team.get("nickname") or "").strip()
    if location and nickname:
        values.add(f"{location} {nickname}")
    return {norm(value) for value in values if value}


def resolve_team_ids(
    records: list[dict[str, Any]],
    indexes: list[int],
    timeout: float,
    workers: int,
) -> dict[int, str]:
    resolved = {index: source_team_id(records[index]) for index in indexes}
    missing_by_league: dict[str, list[int]] = defaultdict(list)
    for index in indexes:
        if resolved[index]:
            continue
        league = str(records[index].get("sourceLeagueSlug") or "").strip()
        if league:
            missing_by_league[league].append(index)

    def fetch_league(league: str) -> tuple[str, list[dict[str, Any]], str | None]:
        try:
            payload = fetch_json(ESPN_TEAMS.format(league=league), timeout)
            return league, team_entries(payload), None
        except Exception as exc:  # noqa: BLE001
            return league, [], f"teams {league}: {type(exc).__name__}"

    team_maps: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(missing_by_league) or 1))) as executor:
        futures = [executor.submit(fetch_league, league) for league in missing_by_league]
        for future in as_completed(futures):
            league, teams, warning = future.result()
            if warning:
                warnings.append(warning)
            aliases: dict[str, str] = {}
            for team in teams:
                team_id = str(team.get("id") or "")
                for alias in team_names(team):
                    aliases.setdefault(alias, team_id)
            team_maps[league] = aliases

    for league, league_indexes in missing_by_league.items():
        aliases = team_maps.get(league, {})
        for index in league_indexes:
            wanted = norm(records[index].get("teamOrPlatform"))
            if wanted in aliases:
                resolved[index] = aliases[wanted]

    if warnings:
        print(f"Team-resolution warnings: {len(warnings):,}")
    return resolved


def schedule_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            event_id = str(node.get("id") or "").strip()
            if event_id and node.get("date") and (isinstance(node.get("competitions"), list) or "status" in node):
                found.setdefault(event_id, node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return list(found.values())


def schedule_event_info(
    event: dict[str, Any],
    *,
    league: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any] | None:
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
            team_id = str(team.get("id") or competitor.get("id") or "").strip()
            if not team_id:
                continue
            team_ids.append(team_id)
            if competitor.get("winner") is True:
                winners.append(team_id)

    return {
        "eventId": event_id,
        "eventKey": f"espn:{event_id}",
        "provider": "ESPN",
        "eventType": "game",
        "league": league,
        "sport": "soccer",
        "name": str(event.get("name") or event.get("shortName") or event_id),
        "startedAt": iso_utc(started),
        "teamIds": team_ids,
        "winningTeamIds": winners,
    }


def unwrap_gamepackage(payload: dict[str, Any]) -> dict[str, Any]:
    package = payload.get("gamepackageJSON")
    return package if isinstance(package, dict) else payload


def fetch_match_stats(info: dict[str, Any], timeout: float) -> tuple[dict[str, dict[str, Any]], str | None]:
    winners = set(str(value) for value in info.get("winningTeamIds") or [])
    sources = [
        ESPN_SUMMARY.format(sport="soccer", league=info["league"], event_id=info["eventId"]),
        ESPN_CDN_BOXSCORE.format(league=info["league"], event_id=info["eventId"]),
        ESPN_CDN_GAME.format(league=info["league"], event_id=info["eventId"]),
    ]
    errors: list[str] = []
    for source in sources:
        try:
            payload = unwrap_gamepackage(fetch_json(source, timeout))
            stats = extract_espn_game_stats(payload, winners)
            if stats:
                return stats, None
            errors.append("empty")
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)
    return {}, f"no player JSON for {info['league']}/{info['eventId']} ({'/'.join(errors)})"


def normalized_soccer_stats(stats: dict[str, Any]) -> dict[str, float]:
    output: dict[str, float] = {}
    aliases = {
        "minutes": "minutes", "min": "minutes",
        "goals": "goals", "g": "goals", "gls": "goals",
        "assists": "assists", "a": "assists", "ast": "assists",
        "shots": "shots", "sh": "shots",
        "shotsontarget": "shotsOnTarget", "sog": "shotsOnTarget", "st": "shotsOnTarget",
        "saves": "saves", "sv": "saves",
        "yellowcards": "yellowCards", "yc": "yellowCards",
        "redcards": "redCards", "rc": "redCards",
    }
    for key, value in stats.items():
        parsed = numeric_box_value(value)
        if parsed is None:
            continue
        normalized = norm(key)
        output[aliases.get(normalized, str(key))] = parsed

    participated = (output.get("minutes") or 0) > 0 or any(
        abs(output.get(key, 0)) > 0 for key in ("goals", "assists", "shots", "shotsOnTarget", "saves")
    )
    if participated:
        output["appearances"] = 1.0
        output["gamesPlayed"] = 1.0
    return output


def saved_baseline(record: dict[str, Any]) -> dict[str, Any] | None:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    raw = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
    recent_production = float(raw.get("recentProduction") or 0)
    if recent_production <= 0:
        return None
    games = int(record.get("professionalGames") or summary.get("professionalGames") or 0)
    return {
        "record": dict(record),
        "ok": True,
        "professionalGames": games,
        "recent": {"gamesPlayed": max(1, games)},
        "career": {},
        "signals": {
            "recentProduction": recent_production,
            "careerProduction": float(raw.get("careerProduction") or 0),
            "efficiency": float(raw.get("efficiency") or 0),
            "usage": float(raw.get("usage") or 0),
            "careerUsage": float(raw.get("careerUsage") or 0),
            "awardPoints": float(raw.get("awardPoints") or 0),
        },
        "awards": summary.get("awardNames", []),
        "newsCount": 0,
        "evidenceUrls": list(record.get("pricingEvidence") or []),
        "errors": ["Used stored verified Soccer season baseline"],
    }


def stat(stats: dict[str, Any], key: str) -> float:
    wanted = norm(key)
    for name, value in stats.items():
        if norm(name) == wanted:
            parsed = numeric_box_value(value)
            return float(parsed) if parsed is not None else 0.0
    return 0.0


def direct_soccer_move(event: dict[str, Any], max_move: float) -> tuple[float, dict[str, Any]]:
    del max_move  # legacy compatibility; no fixed percentage ceiling
    stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
    appearance = stat(stats, "appearances") > 0 or stat(stats, "minutes") > 0
    if not appearance:
        return 0.0, {"comparable": False, "reason": "No verified player appearance"}

    goals = stat(stats, "goals")
    assists = stat(stats, "assists")
    shots_on_target = stat(stats, "shotsOnTarget")
    saves = stat(stats, "saves")
    yellow = stat(stats, "yellowCards")
    red = stat(stats, "redCards")
    performance = goals * 0.55 + assists * 0.35 + shots_on_target * 0.04 + saves * 0.025 - yellow * 0.05 - red * 0.60
    outcome = 0.12 if event.get("teamWon") is True else -0.08 if event.get("teamWon") is False else 0.0
    move = performance + outcome
    if abs(move) < 0.05 and event.get("teamWon") is not None:
        move = 0.05 if event.get("teamWon") is True else -0.05
    return round(move, 3), {
        "comparable": True,
        "pricingBasis": "verified-soccer-box-score-fallback",
        "performanceDeltaPct": None,
        "productionDeltaPct": None,
        "efficiencyDeltaPct": None,
        "outcomeMovePct": outcome,
        "reason": "Verified Soccer appearance/result priced with conservative box-score fallback because a comparable season baseline was unavailable.",
    }


def priced_event(
    record: dict[str, Any],
    baseline: dict[str, Any] | None,
    event: dict[str, Any],
    max_move: float,
) -> dict[str, Any] | None:
    move = 0.0
    evidence: dict[str, Any] = {"comparable": False}
    if baseline and baseline.get("ok"):
        try:
            move, evidence = game_event_move(record, baseline, event, max_move)
        except Exception:  # noqa: BLE001
            move, evidence = 0.0, {"comparable": False, "reason": "Shared game comparison failed"}
    if not evidence.get("comparable"):
        move, evidence = direct_soccer_move(event, max_move)
    if not evidence.get("comparable") or abs(move) < 0.001:
        return None
    return {
        **event,
        **evidence,
        "movePct": round(move, 3),
        "verified": True,
        "historicalBackfill": True,
        "verifiedParticipation": True,
        "backfillModel": "soccer-json-team-summary-v5",
    }


def has_verified_game_history(record: dict[str, Any]) -> bool:
    return any(
        isinstance(event, dict)
        and event.get("verified") is not False
        and str(event.get("eventType") or "game") == "game"
        and abs(float(event.get("movePct") or 0)) >= 0.001
        for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--max-players", type=int, default=3000)
    parser.add_argument("--max-teams", type=int, default=120)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    parser.add_argument("--minimum-total-covered", type=int, default=20)
    parser.add_argument("--minimum-anchor-covered", type=int, default=3)
    args = parser.parse_args()

    records = load_records(args.catalog)
    now = utc_now()
    start = now - timedelta(days=max(1, args.days))
    priorities = priority_names()
    anchors = {norm(name) for name in ANCHOR_NAMES}

    eligible = [
        index for index, record in enumerate(records)
        if str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "") == "Soccer"
        and str(record.get("sourceNamespace") or "") == "espn"
        and str(record.get("sourceRecordId") or "").strip()
        and str(record.get("sourceLeagueSlug") or "").strip()
    ]
    eligible.sort(key=lambda index: (
        1 if norm(records[index].get("name")) in anchors else 0,
        1 if norm(records[index].get("name")) in priorities else 0,
        1 if not has_verified_game_history(records[index]) else 0,
        float(records[index].get("pricingConfidence") or 0),
        float(records[index].get("marketPrice") or 0),
    ), reverse=True)
    if args.max_players > 0:
        eligible = eligible[: args.max_players]
    print(f"Eligible Soccer players for JSON history: {len(eligible):,}")
    if not eligible:
        raise SystemExit("No eligible ESPN Soccer records found")

    resolved_team = resolve_team_ids(records, eligible, args.request_timeout, args.workers)
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    unresolved: list[str] = []
    for index in eligible:
        league = str(records[index].get("sourceLeagueSlug") or "")
        team_id = resolved_team.get(index, "")
        if not team_id:
            unresolved.append(str(records[index].get("name") or ""))
            continue
        groups[(league, team_id)].append(index)

    def group_priority(item: tuple[tuple[str, str], list[int]]) -> tuple[int, int, int, float]:
        _key, indexes = item
        return (
            sum(1 for index in indexes if norm(records[index].get("name")) in anchors),
            sum(1 for index in indexes if norm(records[index].get("name")) in priorities),
            sum(1 for index in indexes if not has_verified_game_history(records[index])),
            max((float(records[index].get("pricingConfidence") or 0) for index in indexes), default=0.0),
        )

    ordered_groups = sorted(groups.items(), key=group_priority, reverse=True)
    if args.max_teams > 0:
        ordered_groups = ordered_groups[: args.max_teams]
    selected_groups = dict(ordered_groups)
    selected_indexes = sorted({index for indexes in selected_groups.values() for index in indexes})
    selected_athlete_ids = {str(records[index].get("sourceRecordId") or "") for index in selected_indexes}
    print(f"Soccer teams selected: {len(selected_groups):,}; players on selected teams: {len(selected_indexes):,}")
    if unresolved:
        print(f"Unresolved current team IDs: {len(unresolved):,}")

    schedule_jobs = [
        (league, team_id, season)
        for league, team_id in selected_groups
        for season in range(start.year, now.year + 1)
    ]
    match_info: dict[tuple[str, str], dict[str, Any]] = {}
    schedule_warnings: list[str] = []

    def fetch_schedule(job: tuple[str, str, int]) -> tuple[tuple[str, str, int], list[dict[str, Any]], str | None]:
        league, team_id, season = job
        try:
            payload = fetch_json(ESPN_TEAM_SCHEDULE.format(league=league, team_id=team_id, season=season), args.request_timeout)
            return job, schedule_events(payload), None
        except Exception as exc:  # noqa: BLE001
            return job, [], f"schedule {league}/{team_id}/{season}: {type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(schedule_jobs) or 1))) as executor:
        futures = [executor.submit(fetch_schedule, job) for job in schedule_jobs]
        for future in as_completed(futures):
            (league, team_id, _season), raw_events, warning = future.result()
            if warning:
                schedule_warnings.append(warning)
            for raw in raw_events:
                info = schedule_event_info(raw, league=league, start=start, end=now)
                if info is None or team_id not in set(info.get("teamIds") or []):
                    continue
                match_info.setdefault((league, str(info["eventId"])), info)

    print(f"Completed Soccer matches discovered: {len(match_info):,}")
    if schedule_warnings:
        print(f"Schedule warnings: {len(schedule_warnings):,}")
    if not match_info:
        raise SystemExit("Soccer JSON collector found zero completed matches")

    player_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    summary_warnings: list[str] = []
    infos = list(match_info.values())
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(infos)))) as executor:
        futures = {executor.submit(fetch_match_stats, info, args.request_timeout): info for info in infos}
        for future in as_completed(futures):
            info = futures[future]
            stats_by_player, warning = future.result()
            if warning:
                summary_warnings.append(warning)
            for athlete_id, performance in stats_by_player.items():
                athlete_id = str(athlete_id)
                if athlete_id not in selected_athlete_ids:
                    continue
                stats = normalized_soccer_stats(performance.get("stats", {}))
                if not stats.get("appearances"):
                    continue
                player_events[athlete_id].append({
                    "eventKey": info["eventKey"],
                    "eventId": info["eventId"],
                    "eventType": "game",
                    "provider": "ESPN",
                    "league": info["league"],
                    "name": info["name"],
                    "startedAt": info["startedAt"],
                    "stats": stats,
                    "teamWon": performance.get("teamWon"),
                    "sourceUrl": ESPN_SUMMARY.format(sport="soccer", league=info["league"], event_id=info["eventId"]),
                })

    for athlete_id, events in list(player_events.items()):
        deduped = {str(event.get("eventKey")): event for event in events if event.get("eventKey")}
        player_events[athlete_id] = sorted(deduped.values(), key=lambda event: str(event.get("startedAt") or ""))
    print(f"Soccer players with verified JSON match appearances: {len(player_events):,}")
    if summary_warnings:
        print(f"Summary/CDN warnings: {len(summary_warnings):,}")

    candidates = [index for index in selected_indexes if player_events.get(str(records[index].get("sourceRecordId") or ""))]
    baselines: dict[int, dict[str, Any] | None] = {}

    def get_baseline(index: int) -> tuple[int, dict[str, Any] | None]:
        saved = saved_baseline(records[index])
        if saved is not None:
            return index, saved
        try:
            item = fetch_hourly_evidence(records[index], args.request_timeout)
            return index, item if item.get("ok") else None
        except Exception:  # noqa: BLE001
            return index, None

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(candidates) or 1))) as executor:
        futures = [executor.submit(get_baseline, index) for index in candidates]
        for future in as_completed(futures):
            index, item = future.result()
            baselines[index] = item

    updated = list(records)
    touched = 0
    generated_total = 0
    for index in candidates:
        record = records[index]
        athlete_id = str(record.get("sourceRecordId") or "")
        generated = [
            event
            for source_event in player_events.get(athlete_id, [])
            if (event := priced_event(record, baselines.get(index), source_event, args.max_game_move_pct)) is not None
        ]
        if not generated:
            continue
        result = dict(record)
        combined = merge_event_evidence(existing_events(result), generated)[-MAX_EVENTS_PER_RECORD:]
        rebuilt = reconstruct_chain(result, combined)
        result["priceEvents"] = rebuilt
        result["priceHistory"] = history_from_events(rebuilt)
        result["priceHistoryStatus"] = "verified-event-backfill"
        result["priceHistoryBackfilledAt"] = iso_utc(now)
        result["priceHistoryBackfillDays"] = max(int(result.get("priceHistoryBackfillDays") or 0), args.days)
        result["priceHistoryBackfillModel"] = "soccer-json-team-summary-v5"
        result["soccerVerifiedGameEvents"] = sum(
            1 for event in rebuilt
            if isinstance(event, dict) and str(event.get("eventType") or "game") == "game" and event.get("verified") is not False
        )
        updated[index] = result
        touched += 1
        generated_total += len(generated)

    write_records(args.catalog, updated)

    soccer_records = [record for record in updated if str(record.get("discipline") or "") == "Soccer"]
    covered = [record for record in soccer_records if has_verified_game_history(record)]
    anchor_records = [record for record in soccer_records if norm(record.get("name")) in anchors]
    anchor_covered = [record for record in anchor_records if has_verified_game_history(record)]
    print(f"Soccer chart coverage: {len(covered):,}/{len(soccer_records):,} records with verified game history.")
    print("Anchor coverage: " + ", ".join(
        f"{record.get('name')}={'yes' if has_verified_game_history(record) else 'NO'}" for record in anchor_records
    ))
    print(f"This pass added {generated_total:,} priced verified match events to {touched:,} Soccer records.")

    if len(covered) < args.minimum_total_covered:
        raise SystemExit(
            f"Soccer history quality gate failed: only {len(covered)} records have verified game history; "
            f"minimum is {args.minimum_total_covered}."
        )
    required_anchor = min(args.minimum_anchor_covered, len(anchor_records))
    if len(anchor_covered) < required_anchor:
        missing = [str(record.get("name") or "") for record in anchor_records if not has_verified_game_history(record)]
        raise SystemExit(
            f"Soccer anchor quality gate failed: {len(anchor_covered)}/{len(anchor_records)} anchors covered; "
            f"minimum is {required_anchor}. Missing: {', '.join(missing)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
