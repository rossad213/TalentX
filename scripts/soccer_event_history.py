#!/usr/bin/env python3
"""Build verified Soccer price-event history without changing today's price.

Baseball works well in TalentX because the market can attach dense player-level
box scores directly to dated games. Soccer now follows the same principle.

Primary path:
* start from the verified ESPN athlete ID already stored on each Soccer record;
* read that player's ESPN match log for the seasons intersecting the lookback;
* turn each verified appearance into the same durable ``priceEvents`` contract
  used by MLB/NBA/NFL/NHL charts.

Priority-player fallback:
* if a high-priority Soccer name still has no player-log rows, use the verified
  current-team schedule plus ESPN match summaries to recover player box scores.

Historical events are reconstructed backward from the unchanged current
``marketPrice``. This script never reprices today's live market.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from backfill_price_history import (
    evaluated_game_events,
    existing_events,
    history_from_events,
    merge_event_evidence,
    reconstruct_chain,
)
from enrich_current_catalog import fetch_json, session
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

ROOT = Path(__file__).resolve().parents[1]
PRIORITY_NAMES = ROOT / "data" / "priority_soccer_names.json"
ESPN_PLAYER_MATCHES = "https://www.espn.com/soccer/player/matches/_/id/{athlete_id}/league/{league}/season/{season}"
ESPN_TEAM_SCHEDULE = (
    "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/{team_id}/schedule"
    "?season={season}"
)
MAX_DIRECT_PLAYERS_PER_PASS = 1800

# Most European/Middle-Eastern leagues use a fall-to-spring season label. ESPN's
# season=2025 page can therefore contain Jan-May 2026 matches. Calendar-year
# leagues such as MLS keep the season year unchanged.
FALL_SPRING_PREFIXES = {
    "eng", "esp", "ger", "ita", "fra", "ned", "por", "bel", "sco", "tur",
    "gre", "aut", "sui", "den", "cze", "pol", "rou", "hun", "cro", "srb",
    "svn", "svk", "bul", "ukr", "isr", "cyp", "ksa", "qat", "uae", "irn",
}
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def team_id_for(record: dict[str, Any]) -> str:
    explicit = str(record.get("sourceTeamId") or "").strip()
    if explicit:
        return explicit
    source_url = str(record.get("sourceUrl") or "")
    match = re.search(r"/teams/([^/?#]+)/roster(?:[/?#]|$)", source_url)
    return match.group(1) if match else ""


def schedule_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
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
    output = dict(stats)
    if "st" in output and "shotsOnTarget" not in output:
        output["shotsOnTarget"] = output["st"]
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


def has_verified_game_history(record: dict[str, Any]) -> bool:
    for event in existing_events(record):
        if event.get("verified") is False:
            continue
        if str(event.get("eventType") or "game") == "game":
            return True
    return False


def priority_names() -> set[str]:
    try:
        payload = json.loads(PRIORITY_NAMES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {norm(value) for value in payload if str(value).strip()} if isinstance(payload, list) else set()


class TableParser(HTMLParser):
    """Small dependency-free HTML table parser for ESPN's server-rendered logs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", " ".join(self._cell)).strip()
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def season_calendar_year(month: int, season_value: int, league: str) -> int:
    prefix = str(league or "").split(".", 1)[0].lower()
    return season_value + 1 if prefix in FALL_SPRING_PREFIXES and month <= 6 else season_value


def parse_match_date(value: str, season_value: int, league: str) -> datetime | None:
    text = re.sub(r"\s+", " ", str(value or "").replace(",", " ")).strip()
    numeric = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text)
    if numeric:
        month = int(numeric.group(1))
        day = int(numeric.group(2))
        raw_year = numeric.group(3)
        if raw_year:
            year = int(raw_year)
            if year < 100:
                year += 2000
        else:
            year = season_calendar_year(month, season_value, league)
        try:
            return datetime(year, month, day, 12, tzinfo=timezone.utc)
        except ValueError:
            return None

    month_match = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:\s+(\d{4}))?\b", text, re.I)
    if month_match:
        month = MONTHS[month_match.group(1)[:3].lower()]
        day = int(month_match.group(2))
        year = int(month_match.group(3)) if month_match.group(3) else season_calendar_year(month, season_value, league)
        try:
            return datetime(year, month, day, 12, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def number_text(value: str) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"--", "-", "—"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def player_log_events(
    record: dict[str, Any],
    html: str,
    *,
    season_value: int,
    start: datetime,
    end: datetime,
    source_url: str,
) -> list[dict[str, Any]]:
    parser = TableParser()
    parser.feed(html)
    output: list[dict[str, Any]] = []
    league = str(record.get("sourceLeagueSlug") or "")
    athlete_id = str(record.get("sourceRecordId") or "")

    for table in parser.tables:
        header_index = -1
        headers: list[str] = []
        for index, row in enumerate(table):
            normalized = [norm(cell) for cell in row]
            if "date" in normalized and "opp" in normalized and "result" in normalized:
                header_index = index
                headers = normalized
                break
        if header_index < 0:
            continue

        for row in table[header_index + 1:]:
            if len(row) < 3:
                continue
            values = {headers[index]: row[index] for index in range(min(len(headers), len(row)))}
            when = parse_match_date(values.get("date", ""), season_value, league)
            if when is None or when < start or when > end:
                continue
            opponent = str(values.get("opp") or "Opponent").strip()
            result_text = str(values.get("result") or "").strip()
            result_code = result_text[:1].upper()
            team_won = True if result_code == "W" else False if result_code == "L" else None

            stats: dict[str, float] = {"appearances": 1.0, "gamesPlayed": 1.0}
            aliases = {
                "g": "goals", "goals": "goals",
                "a": "assists", "assists": "assists",
                "sh": "shots", "shots": "shots",
                "st": "shotsOnTarget", "sog": "shotsOnTarget", "shotsontarget": "shotsOnTarget",
                "sv": "saves", "saves": "saves",
                "min": "minutes", "minutes": "minutes",
            }
            for header, target in aliases.items():
                if header not in values:
                    continue
                parsed = number_text(values[header])
                if parsed is not None:
                    stats[target] = parsed

            stamp = when.date().isoformat()
            key = f"espn-player-log:{athlete_id}:{league}:{stamp}:{norm(opponent)}"
            output.append({
                "eventKey": key,
                "eventId": key,
                "eventType": "game",
                "provider": "ESPN",
                "sourceUrl": source_url,
                "league": league,
                "name": f"{record.get('teamOrPlatform') or 'Club'} {opponent}".strip(),
                "startedAt": iso_utc(when),
                "datePrecision": "day",
                "stats": stats,
                "teamWon": team_won,
                "verifiedParticipation": True,
            })

    deduped: dict[str, dict[str, Any]] = {}
    for event in output:
        deduped[str(event["eventKey"])] = event
    return sorted(deduped.values(), key=lambda event: str(event.get("startedAt") or ""))


def saved_baseline(record: dict[str, Any]) -> dict[str, Any] | None:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    raw = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
    recent_prod = float(raw.get("recentProduction") or 0)
    if recent_prod <= 0:
        return None
    games = int(record.get("professionalGames") or summary.get("professionalGames") or 0)
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
        "errors": ["Used previously verified season baseline for Soccer match-log comparison"],
    }


def priority_team_fallback(
    records: list[dict[str, Any]],
    indexes: list[int],
    player_events: dict[str, list[dict[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    workers: int,
    timeout: float,
) -> list[str]:
    """Recover priority names through team schedules when player HTML is thin."""
    missing = [
        index for index in indexes
        if not player_events.get(str(records[index].get("sourceRecordId") or ""))
        and team_id_for(records[index])
    ]
    if not missing:
        return []

    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index in missing:
        record = records[index]
        groups[(str(record.get("sourceLeagueSlug") or ""), team_id_for(record))].append(index)

    seasons = range(start.year, end.year + 1)
    jobs = [(league, team_id, season_value) for league, team_id in groups for season_value in seasons]
    match_info: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []

    def fetch_schedule(job: tuple[str, str, int]) -> tuple[tuple[str, str, int], list[dict[str, Any]], str | None]:
        league, team_id, season_value = job
        url = ESPN_TEAM_SCHEDULE.format(league=league, team_id=team_id, season=season_value)
        try:
            return job, schedule_events(fetch_json(url, timeout)), None
        except Exception as exc:  # noqa: BLE001
            return job, [], f"priority schedule {league}/{team_id}/{season_value}: {type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs) or 1))) as executor:
        futures = [executor.submit(fetch_schedule, job) for job in jobs]
        for future in as_completed(futures):
            (league, team_id, _season), events, warning = future.result()
            if warning:
                warnings.append(warning)
            for raw in events:
                info = schedule_event_info(raw, start, end)
                if info is None or team_id not in set(info.get("teamIds") or []):
                    continue
                info["league"] = league
                match_info.setdefault((league, str(info["eventId"])), info)

    def fetch_summary(info: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str | None]:
        try:
            payload = fetch_json(
                ESPN_SUMMARY.format(sport="soccer", league=info["league"], event_id=info["eventId"]),
                timeout,
            )
            stats = extract_espn_game_stats(payload, set(info.get("winningTeamIds") or []))
            return info, stats, None if stats else f"priority summary empty {info['league']}/{info['eventId']}"
        except Exception as exc:  # noqa: BLE001
            return info, {}, f"priority summary {info['league']}/{info['eventId']}: {type(exc).__name__}"

    infos = list(match_info.values())
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(infos) or 1))) as executor:
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
                    "verifiedParticipation": True,
                })
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--max-players", type=int, default=1800)
    parser.add_argument("--max-teams", type=int, default=80)  # compatibility; priority fallback is intentionally small
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    args = parser.parse_args()

    records = load_catalog(args.catalog)
    now = utc_now()
    start = now - timedelta(days=max(1, args.days))
    priorities = priority_names()

    eligible = [
        index for index, record in enumerate(records)
        if str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "") == "Soccer"
        and str(record.get("sourceNamespace") or "") == "espn"
        and str(record.get("sourceRecordId") or "").strip()
        and str(record.get("sourceLeagueSlug") or "").strip()
    ]
    eligible.sort(
        key=lambda index: (
            1 if not has_verified_game_history(records[index]) else 0,
            1 if norm(records[index].get("name")) in priorities else 0,
            float(records[index].get("pricingConfidence") or 0),
            float(records[index].get("marketPrice") or 0),
        ),
        reverse=True,
    )
    limit = min(MAX_DIRECT_PLAYERS_PER_PASS, args.max_players) if args.max_players > 0 else MAX_DIRECT_PLAYERS_PER_PASS
    selected = eligible[:limit]
    print(f"Soccer player-match-log coverage pass: {len(selected):,} of {len(eligible):,} eligible players.")
    if not selected:
        return 0

    seasons = range(start.year, now.year + 1)
    player_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    warnings: list[str] = []

    def fetch_log(index: int, season_value: int) -> tuple[int, list[dict[str, Any]], str | None]:
        record = records[index]
        athlete_id = str(record.get("sourceRecordId") or "")
        league = str(record.get("sourceLeagueSlug") or "")
        url = ESPN_PLAYER_MATCHES.format(athlete_id=athlete_id, league=league, season=season_value)
        try:
            response = session().get(
                url,
                timeout=args.request_timeout,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Mozilla/5.0 (compatible; TalentX/1.0; +https://rossad213.github.io/TalentX/)",
                },
            )
            response.raise_for_status()
            events = player_log_events(
                record,
                response.text,
                season_value=season_value,
                start=start,
                end=now,
                source_url=url,
            )
            return index, events, None
        except Exception as exc:  # noqa: BLE001
            return index, [], f"player matches {record.get('name')} {season_value}: {type(exc).__name__}"

    jobs = [(index, season_value) for index in selected for season_value in seasons]
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(jobs)))) as executor:
        futures = [executor.submit(fetch_log, index, season_value) for index, season_value in jobs]
        for future in as_completed(futures):
            index, events, warning = future.result()
            athlete_id = str(records[index].get("sourceRecordId") or "")
            if events:
                player_events[athlete_id].extend(events)
            if warning:
                warnings.append(warning)

    # For the high-profile names that motivated this fix, never let a thin HTML
    # response be the end of the road. Their verified team schedule/summary path
    # is small enough to use as a second source in the same run.
    priority_indexes = [index for index in eligible if norm(records[index].get("name")) in priorities]
    warnings.extend(priority_team_fallback(
        records,
        priority_indexes,
        player_events,
        start=start,
        end=now,
        workers=args.workers,
        timeout=args.request_timeout,
    ))

    for athlete_id, events in player_events.items():
        deduped = {str(event.get("eventKey")): event for event in events if event.get("eventKey")}
        player_events[athlete_id] = sorted(deduped.values(), key=lambda event: str(event.get("startedAt") or ""))

    candidates = [index for index in eligible if player_events.get(str(records[index].get("sourceRecordId") or ""))]
    evidence: dict[int, dict[str, Any]] = {}

    def baseline(index: int) -> tuple[int, dict[str, Any]]:
        record = records[index]
        saved = saved_baseline(record)
        if saved is not None:
            return index, saved
        try:
            return index, fetch_hourly_evidence(record, args.request_timeout)
        except Exception as exc:  # noqa: BLE001
            return index, {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(candidates) or 1))) as executor:
        futures = [executor.submit(baseline, index) for index in candidates]
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
        result["priceHistoryBackfillModel"] = "soccer-player-match-log-events-v4"
        updated[index] = result
        touched += 1
        generated_count += len(generated)

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    covered = sum(1 for index in eligible if has_verified_game_history(updated[index]))
    print(f"Expanded {touched:,} Soccer charts with {generated_count:,} verified player-match events.")
    print(f"Soccer records with durable verified game history after this pass: {covered:,} / {len(eligible):,}.")
    if warnings:
        print(f"Soccer source warnings (non-fatal): {len(warnings):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
