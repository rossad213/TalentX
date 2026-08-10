#!/usr/bin/env python3
"""Build one-year Soccer chart history from ESPN league scoreboards.

Team schedule endpoints proved unreliable for historical discovery in GitHub
Actions. ESPN scoreboards support date ranges, so this collector discovers a
league's completed matches for the whole lookback window in one request, then
uses the Soccer-aware summary/lineup parser to attach verified player events.

Historical reconstruction does not change today's live market price.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
from typing import Any

import soccer_json_history as base
import soccer_json_history_runner as soccer_runner

ESPN_SCOREBOARD_RANGE = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
    "?limit=1000&dates={start_date}-{end_date}"
)


def soccer_completed(event: dict[str, Any]) -> bool:
    status = event.get("status") if isinstance(event.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    if status_type.get("completed") is True:
        return True
    values = [
        status_type.get("state"),
        status_type.get("name"),
        status_type.get("description"),
        status_type.get("detail"),
        status_type.get("shortDetail"),
        event.get("statusType"),
    ]
    joined = " ".join(str(value or "") for value in values).lower().replace("_", " ").replace("-", " ")
    if base.completed_event(str(status_type.get("state") or status_type.get("name") or "")):
        return True
    return any(
        token in joined
        for token in (
            "full time",
            "status full time",
            "final",
            "completed",
            "complete",
            "after extra time",
            "after penalties",
            "penalties",
        )
    )


def event_info(event: dict[str, Any], league: str, start, end) -> dict[str, Any] | None:
    event_id = str(event.get("id") or "").strip()
    started = base.parse_datetime(event.get("date"))
    if not event_id or started is None or started < start or started > end or not soccer_completed(event):
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
        "eventKey": f"espn:{event_id}",
        "eventId": event_id,
        "eventType": "game",
        "provider": "ESPN",
        "league": league,
        "sport": "soccer",
        "name": str(event.get("name") or event.get("shortName") or event_id),
        "startedAt": base.iso_utc(started),
        "teamIds": team_ids,
        "winningTeamIds": winners,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--max-players", type=int, default=3000)
    parser.add_argument("--max-leagues", type=int, default=0)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    parser.add_argument("--minimum-total-covered", type=int, default=20)
    parser.add_argument("--minimum-anchor-covered", type=int, default=5)
    args = parser.parse_args()

    records = base.load_records(args.catalog)
    now = base.utc_now()
    start = now - timedelta(days=max(1, args.days))
    priorities = base.priority_names()
    anchors = {base.norm(name) for name in base.ANCHOR_NAMES}

    eligible = [
        index for index, record in enumerate(records)
        if str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "") == "Soccer"
        and str(record.get("sourceNamespace") or "") == "espn"
        and str(record.get("sourceRecordId") or "").strip()
        and str(record.get("sourceLeagueSlug") or "").strip()
    ]
    eligible.sort(key=lambda index: (
        1 if base.norm(records[index].get("name")) in anchors else 0,
        1 if base.norm(records[index].get("name")) in priorities else 0,
        1 if not base.has_verified_game_history(records[index]) else 0,
        float(records[index].get("pricingConfidence") or 0),
        float(records[index].get("marketPrice") or 0),
    ), reverse=True)
    if args.max_players > 0:
        eligible = eligible[: args.max_players]
    print(f"Eligible Soccer players for scoreboard history: {len(eligible):,}")
    if not eligible:
        raise SystemExit("No eligible ESPN Soccer records found")

    athlete_ids = {str(records[index].get("sourceRecordId") or "") for index in eligible}
    league_indexes: dict[str, list[int]] = defaultdict(list)
    for index in eligible:
        league_indexes[str(records[index].get("sourceLeagueSlug") or "")].append(index)

    def league_priority(item: tuple[str, list[int]]) -> tuple[int, int, int, float]:
        _league, indexes = item
        return (
            sum(1 for index in indexes if base.norm(records[index].get("name")) in anchors),
            sum(1 for index in indexes if base.norm(records[index].get("name")) in priorities),
            sum(1 for index in indexes if not base.has_verified_game_history(records[index])),
            max((float(records[index].get("pricingConfidence") or 0) for index in indexes), default=0.0),
        )

    ordered_leagues = sorted(league_indexes.items(), key=league_priority, reverse=True)
    if args.max_leagues > 0:
        ordered_leagues = ordered_leagues[: args.max_leagues]
    leagues = [league for league, _indexes in ordered_leagues]
    print(f"Soccer leagues selected: {len(leagues):,}")

    range_start = start.strftime("%Y%m%d")
    range_end = now.strftime("%Y%m%d")

    def fetch_scoreboard(league: str) -> tuple[str, list[dict[str, Any]], str | None]:
        url = ESPN_SCOREBOARD_RANGE.format(league=league, start_date=range_start, end_date=range_end)
        try:
            payload = base.fetch_json(url, args.request_timeout)
            events = payload.get("events") if isinstance(payload, dict) else []
            return league, [event for event in (events or []) if isinstance(event, dict)], None
        except Exception as exc:  # noqa: BLE001
            return league, [], f"scoreboard {league}: {type(exc).__name__}: {exc}"

    raw_count = 0
    match_info: dict[tuple[str, str], dict[str, Any]] = {}
    scoreboard_warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(leagues) or 1))) as executor:
        futures = [executor.submit(fetch_scoreboard, league) for league in leagues]
        for future in as_completed(futures):
            league, events, warning = future.result()
            raw_count += len(events)
            if warning:
                scoreboard_warnings.append(warning)
            for event in events:
                info = event_info(event, league, start, now)
                if info is not None:
                    match_info[(league, str(info["eventId"]))] = info

    print(f"Raw Soccer scoreboard events discovered: {raw_count:,}")
    print(f"Completed Soccer matches discovered: {len(match_info):,}")
    if scoreboard_warnings:
        print(f"Scoreboard warnings: {len(scoreboard_warnings):,}")
        for warning in scoreboard_warnings[:12]:
            print(f"WARNING {warning}")
    if not match_info:
        raise SystemExit("Soccer scoreboard collector found zero completed matches")

    player_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    match_warnings: list[str] = []
    infos = list(match_info.values())
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(infos) or 1))) as executor:
        futures = {executor.submit(soccer_runner.fetch_match_stats, info, args.request_timeout): info for info in infos}
        for future in as_completed(futures):
            info = futures[future]
            stats_by_player, warning = future.result()
            if warning:
                match_warnings.append(warning)
            for athlete_id, performance in stats_by_player.items():
                athlete_id = str(athlete_id)
                if athlete_id not in athlete_ids:
                    continue
                stats = base.normalized_soccer_stats(performance.get("stats", {}))
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
                    "sourceUrl": base.ESPN_SUMMARY.format(
                        sport="soccer", league=info["league"], event_id=info["eventId"]
                    ),
                })

    for athlete_id, events in list(player_events.items()):
        deduped = {str(event.get("eventKey")): event for event in events if event.get("eventKey")}
        player_events[athlete_id] = sorted(deduped.values(), key=lambda event: str(event.get("startedAt") or ""))
    print(f"Soccer players with verified JSON match appearances: {len(player_events):,}")
    if match_warnings:
        print(f"Summary/lineup warnings: {len(match_warnings):,}")

    candidates = [index for index in eligible if player_events.get(str(records[index].get("sourceRecordId") or ""))]
    baselines: dict[int, dict[str, Any] | None] = {}

    def get_baseline(index: int) -> tuple[int, dict[str, Any] | None]:
        saved = base.saved_baseline(records[index])
        if saved is not None:
            return index, saved
        try:
            item = base.fetch_hourly_evidence(records[index], args.request_timeout)
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
            if (event := base.priced_event(record, baselines.get(index), source_event, args.max_game_move_pct)) is not None
        ]
        if not generated:
            continue
        result = dict(record)
        combined = base.merge_event_evidence(base.existing_events(result), generated)[-base.MAX_EVENTS_PER_RECORD:]
        rebuilt = base.reconstruct_chain(result, combined)
        result["priceEvents"] = rebuilt
        result["priceHistory"] = base.history_from_events(rebuilt)
        result["priceHistoryStatus"] = "verified-event-backfill"
        result["priceHistoryBackfilledAt"] = base.iso_utc(now)
        result["priceHistoryBackfillDays"] = max(int(result.get("priceHistoryBackfillDays") or 0), args.days)
        result["priceHistoryBackfillModel"] = "soccer-scoreboard-range-v1"
        result["soccerVerifiedGameEvents"] = sum(
            1 for event in rebuilt
            if isinstance(event, dict)
            and str(event.get("eventType") or "game") == "game"
            and event.get("verified") is not False
            and abs(float(event.get("movePct") or 0)) >= 0.001
        )
        updated[index] = result
        touched += 1
        generated_total += len(generated)

    base.write_records(args.catalog, updated)

    soccer_records = [record for record in updated if str(record.get("discipline") or "") == "Soccer"]
    covered = [record for record in soccer_records if base.has_verified_game_history(record)]
    anchor_records = [record for record in soccer_records if base.norm(record.get("name")) in anchors]
    anchor_covered = [record for record in anchor_records if base.has_verified_game_history(record)]
    print(f"Soccer chart coverage: {len(covered):,}/{len(soccer_records):,} records with verified game history.")
    print("Anchor coverage: " + ", ".join(
        f"{record.get('name')}={'yes' if base.has_verified_game_history(record) else 'NO'}" for record in anchor_records
    ))
    print(f"This pass added {generated_total:,} priced verified match events to {touched:,} Soccer records.")

    if len(covered) < args.minimum_total_covered:
        raise SystemExit(
            f"Soccer history quality gate failed: only {len(covered)} records have verified game history; "
            f"minimum is {args.minimum_total_covered}."
        )
    required_anchor = min(args.minimum_anchor_covered, len(anchor_records))
    if len(anchor_covered) < required_anchor:
        missing = [str(record.get("name") or "") for record in anchor_records if not base.has_verified_game_history(record)]
        raise SystemExit(
            f"Soccer anchor quality gate failed: {len(anchor_covered)}/{len(anchor_records)} anchors covered; "
            f"minimum is {required_anchor}. Missing: {', '.join(missing)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
