#!/usr/bin/env python3
"""Apply verified PGA/LPGA tournament results to TalentX Golf prices.

Golf is an individual event sport, so it does not fit the team-game adapter used
by MLB/NBA/NFL/NHL/Soccer. ESPN's PGA and LPGA scoreboards expose completed
tournaments with athlete names/IDs, leaderboard order, scores, round cards and
final status. This module converts each completed tournament into one durable
TalentX price event per matched golfer.

Live mode changes marketPrice only for newly discovered completed tournaments.
Historical consumers import the same parsing/pricing helpers but reconstruct the
past without changing today's live price.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/golf/{tour}/scoreboard"
    "?limit=1000&dates={start_date}-{end_date}"
)
TOURS = ("pga", "lpga")
USER_AGENT = "TalentX-Golf-Event-Refresh/1.0 (+https://github.com/rossad213/TalentX)"
MAX_EVENTS_PER_RECORD = 2500

MAJOR_TOKENS = (
    "masterstournament",
    "pgachampionship",
    "usopen",
    "theopen",
    "chevronchampionship",
    "uswomensopen",
    "kpmgwomenspgachampionship",
    "amundievianchampionship",
    "aigwomensopen",
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


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def numeric_score(value: Any) -> float | None:
    text = str(value or "").strip().upper().replace("−", "-")
    if not text:
        return None
    if text in {"E", "EVEN"}:
        return 0.0
    if text in {"CUT", "MC", "WD", "W/D", "DQ", "MDF", "DNS"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.35,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def fetch_json(url: str, timeout: float, http: requests.Session | None = None) -> dict[str, Any]:
    client = http or session()
    response = client.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def completed(competition: dict[str, Any], event: dict[str, Any] | None = None) -> bool:
    event = event or {}
    statuses = []
    for source in (competition, event):
        status = source.get("status") if isinstance(source.get("status"), dict) else {}
        status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
        if status_type.get("completed") is True:
            return True
        statuses.extend([
            status_type.get("state"), status_type.get("name"), status_type.get("description"),
            status_type.get("detail"), status_type.get("shortDetail"),
        ])
    joined = " ".join(str(value or "") for value in statuses).lower().replace("_", " ").replace("-", " ")
    return "final" in joined or "completed" in joined or "complete" in joined or "post" in joined


def is_major(name: str) -> bool:
    key = norm(name)
    return any(token in key for token in MAJOR_TOKENS)


def rounds_played(competitor: dict[str, Any]) -> int:
    lines = competitor.get("linescores") if isinstance(competitor.get("linescores"), list) else []
    count = 0
    for item in lines:
        if not isinstance(item, dict):
            continue
        value = number(item.get("value"), 0)
        display = str(item.get("displayValue") or "").strip()
        if value > 0 or (display and display not in {"-", "--"}):
            count += 1
    return count


def competitor_status(competitor: dict[str, Any], expected_rounds: int) -> str:
    values = [competitor.get("status"), competitor.get("score")]
    for item in competitor.get("statistics") or []:
        if isinstance(item, dict):
            values.extend([item.get("name"), item.get("displayValue")])
    joined = " ".join(str(value or "") for value in values).upper()
    for token in ("DQ", "W/D", "WD", "DNS", "MDF", "CUT", "MC"):
        if token in joined:
            return token
    played = rounds_played(competitor)
    if expected_rounds >= 4 and 0 < played <= 2:
        return "CUT_OR_INCOMPLETE"
    return "FINISHED"


def competitor_name(competitor: dict[str, Any]) -> str:
    athlete = competitor.get("athlete") if isinstance(competitor.get("athlete"), dict) else {}
    return str(
        athlete.get("displayName")
        or athlete.get("fullName")
        or competitor.get("displayName")
        or competitor.get("name")
        or ""
    ).strip()


def flatten_scoreboard(payload: dict[str, Any], tour: str, source_url: str = "") -> list[dict[str, Any]]:
    tournaments: dict[str, dict[str, Any]] = {}
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        tournament_name = str(event.get("name") or event.get("shortName") or "Golf tournament")
        for competition in event.get("competitions") or []:
            if not isinstance(competition, dict) or not completed(competition, event):
                continue
            competition_id = str(competition.get("id") or event_id).strip()
            if not competition_id:
                continue
            completion_time = parse_datetime(
                competition.get("endDate") or event.get("endDate") or competition.get("date") or event.get("date")
            )
            if completion_time is None:
                continue
            status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
            expected_rounds = int(number(status.get("period"), 4)) or 4
            competitors = [item for item in (competition.get("competitors") or []) if isinstance(item, dict)]
            field_size = len(competitors)
            parsed: list[dict[str, Any]] = []
            for competitor in competitors:
                name = competitor_name(competitor)
                if not name:
                    continue
                athlete = competitor.get("athlete") if isinstance(competitor.get("athlete"), dict) else {}
                finish = int(number(competitor.get("order"), 0))
                if finish <= 0:
                    continue
                parsed.append({
                    "name": name,
                    "normalizedName": norm(name),
                    "athleteId": str(competitor.get("id") or athlete.get("id") or "").strip(),
                    "finish": finish,
                    "score": numeric_score(competitor.get("score")),
                    "scoreDisplay": str(competitor.get("score") or ""),
                    "roundsPlayed": rounds_played(competitor),
                    "status": competitor_status(competitor, expected_rounds),
                })
            if not parsed:
                continue
            key = f"{tour}:{competition_id}"
            tournaments[key] = {
                "tournamentKey": key,
                "competitionId": competition_id,
                "eventId": event_id or competition_id,
                "tour": tour.upper(),
                "tournament": tournament_name,
                "major": bool(event.get("major")) or is_major(tournament_name),
                "completedAt": iso_utc(completion_time),
                "startedAt": iso_utc(completion_time),
                "sourceUrl": source_url,
                "fieldSize": field_size,
                "expectedRounds": expected_rounds,
                "competitors": parsed,
            }
    return sorted(tournaments.values(), key=lambda item: str(item.get("completedAt") or ""))


def player_rank(record: dict[str, Any] | None) -> int | None:
    if not isinstance(record, dict):
        return None
    for key in ("sourceRank", "rosterSourceRank", "rosterPriority"):
        value = int(number(record.get(key), 0))
        if 0 < value <= 300:
            return value
    return None


def golf_tournament_move(
    *,
    finish: int,
    field_size: int,
    score_to_par: float | None,
    status: str,
    major: bool,
    player_record: dict[str, Any] | None = None,
    max_move_pct: float = 2.5,
) -> float:
    """Conservative verified move based on finish relative to expectation."""
    field = max(2, int(field_size or 0))
    position = max(1, min(int(finish or field), field))
    own_rank = player_rank(player_record)
    expected = min(field, own_rank) if own_rank else max(1, round(field * 0.50))

    # Beating or missing the golfer's ranking/roster expectation is the main
    # continuous signal. Tournament placement adds a modest absolute achievement
    # component so wins/top finishes matter even for elite players.
    expectation = max(-0.55, min(0.85, ((expected - position) / field) * 1.25))
    if position == 1:
        placement = 0.85
    elif position <= 3:
        placement = 0.55
    elif position <= 10:
        placement = 0.30
    elif position <= 25:
        placement = 0.12
    elif position <= 50:
        placement = 0.02
    else:
        placement = -0.08

    status_key = str(status or "").upper()
    if status_key in {"DQ", "WD", "W/D", "DNS"}:
        placement -= 0.42
    elif status_key in {"CUT", "MC", "MDF", "CUT_OR_INCOMPLETE"}:
        placement -= 0.28

    score_component = 0.0
    if score_to_par is not None and math.isfinite(float(score_to_par)):
        score_component = max(-0.15, min(0.15, -float(score_to_par) * 0.012))

    move = expectation + placement + score_component
    if major:
        move *= 1.35

    cap = max(0.05, min(float(max_move_pct), 2.5))
    move = max(-cap, min(cap, move))
    if abs(move) < 0.05:
        move = 0.05 if position <= expected else -0.05
    return round(move, 3)


def existing_event_keys(record: dict[str, Any]) -> set[str]:
    return {
        str(event.get("eventKey") or event.get("eventId") or "")
        for event in record.get("priceEvents", [])
        if isinstance(event, dict) and str(event.get("eventKey") or event.get("eventId") or "")
    }


def build_name_index(records: list[dict[str, Any]]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for position, record in enumerate(records):
        if str(record.get("primaryCategory") or "") != "Athlete" or str(record.get("discipline") or "") != "Golf":
            continue
        key = norm(record.get("name"))
        if key:
            index.setdefault(key, []).append(position)
    return index


def record_strength(record: dict[str, Any]) -> tuple[float, float, str]:
    return (
        float(record.get("pricingConfidence") or record.get("dataConfidence") or 0),
        float(record.get("marketPrice") or 0),
        str(record.get("id") or ""),
    )


def choose_record(records: list[dict[str, Any]], indexes: list[int]) -> int:
    return max(indexes, key=lambda index: record_strength(records[index]))


def matched_competitors(
    records: list[dict[str, Any]],
    tournaments: Iterable[dict[str, Any]],
) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    name_index = build_name_index(records)
    output: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for tournament in tournaments:
        for competitor in tournament.get("competitors") or []:
            if not isinstance(competitor, dict):
                continue
            indexes = name_index.get(str(competitor.get("normalizedName") or ""), [])
            if not indexes:
                continue
            output.append((choose_record(records, indexes), tournament, competitor))
    return output


def event_for_record(
    record: dict[str, Any],
    tournament: dict[str, Any],
    competitor: dict[str, Any],
    *,
    historical_backfill: bool,
    max_move_pct: float,
) -> dict[str, Any]:
    finish = int(number(competitor.get("finish"), 0))
    field_size = int(number(tournament.get("fieldSize"), 0))
    score = competitor.get("score")
    score_value = float(score) if isinstance(score, (int, float)) else None
    status = str(competitor.get("status") or "FINISHED")
    move = golf_tournament_move(
        finish=finish,
        field_size=field_size,
        score_to_par=score_value,
        status=status,
        major=bool(tournament.get("major")),
        player_record=record,
        max_move_pct=max_move_pct,
    )
    tournament_key = str(tournament.get("tournamentKey") or tournament.get("competitionId") or "")
    finish_label = f"T{finish}" if finish > 1 else "1st"
    return {
        "eventKey": f"espn-golf:{tournament_key}",
        "eventId": str(tournament.get("competitionId") or tournament_key),
        "eventType": "game",
        "sport": "golf",
        "provider": "ESPN",
        "tour": str(tournament.get("tour") or ""),
        "league": str(tournament.get("tour") or ""),
        "name": f"{tournament.get('tournament') or 'Golf tournament'} · {finish_label}",
        "tournament": tournament.get("tournament"),
        "major": bool(tournament.get("major")),
        "startedAt": tournament.get("completedAt") or tournament.get("startedAt"),
        "sourceUrl": tournament.get("sourceUrl"),
        "movePct": move,
        "verified": True,
        "verifiedParticipation": True,
        "historicalBackfill": bool(historical_backfill),
        "backfillModel": "golf-espn-scoreboard-v1" if historical_backfill else None,
        "stats": {
            "finishPosition": finish,
            "fieldSize": field_size,
            "scoreToPar": score_value,
            "roundsPlayed": int(number(competitor.get("roundsPlayed"), 0)),
            "status": status,
            "tournamentWin": 1 if finish == 1 else 0,
        },
        "teamWon": finish == 1,
        "pricingBasis": "verified-golf-finish-vs-expectation",
        "reason": (
            f"Verified {tournament.get('tour') or 'golf'} finish of {finish_label} at "
            f"{tournament.get('tournament') or 'a completed tournament'}"
            + (" (major)." if tournament.get("major") else ".")
        ),
        "golfEspnAthleteId": competitor.get("athleteId"),
    }


def discover_tournaments(
    start: datetime,
    end: datetime,
    *,
    timeout: float,
    tours: Iterable[str] = TOURS,
    http: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    start_date = start.strftime("%Y%m%d")
    end_date = end.strftime("%Y%m%d")
    found: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    client = http or session()
    for tour in tours:
        url = ESPN_SCOREBOARD.format(tour=tour, start_date=start_date, end_date=end_date)
        try:
            payload = fetch_json(url, timeout, client)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{tour} scoreboard: {type(exc).__name__}: {exc}")
            continue
        for tournament in flatten_scoreboard(payload, tour, url):
            completed_at = parse_datetime(tournament.get("completedAt"))
            if completed_at is None or completed_at < start or completed_at > end + timedelta(days=1):
                continue
            found[str(tournament.get("tournamentKey"))] = tournament
    return sorted(found.values(), key=lambda item: str(item.get("completedAt") or "")), warnings


def apply_live_tournaments(
    records: list[dict[str, Any]],
    tournaments: list[dict[str, Any]],
    *,
    max_move_pct: float,
) -> tuple[list[dict[str, Any]], int, int]:
    updated = [dict(record) for record in records]
    rows = matched_competitors(updated, tournaments)
    grouped: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for index, tournament, competitor in rows:
        grouped.setdefault(index, []).append((tournament, competitor))

    touched = 0
    added_total = 0
    for index, entries in grouped.items():
        result = dict(updated[index])
        existing = [dict(event) for event in result.get("priceEvents", []) if isinstance(event, dict)]
        known = existing_event_keys(result)
        price = max(0.01, float(result.get("marketPrice") or 0.01))
        added: list[dict[str, Any]] = []
        for tournament, competitor in sorted(entries, key=lambda item: str(item[0].get("completedAt") or "")):
            event = event_for_record(
                result,
                tournament,
                competitor,
                historical_backfill=False,
                max_move_pct=max_move_pct,
            )
            key = str(event.get("eventKey") or "")
            if not key or key in known:
                continue
            before = price
            price = max(0.01, before * (1.0 + float(event["movePct"]) / 100.0))
            event["priceBefore"] = round(before, 2)
            event["priceAfter"] = round(price, 2)
            event["movePct"] = round((event["priceAfter"] / event["priceBefore"] - 1.0) * 100.0, 3)
            added.append(event)
            known.add(key)
        if not added:
            continue
        all_events = [*existing, *added]
        all_events = [event for event in all_events if str(event.get("startedAt") or "")]
        all_events.sort(key=lambda event: str(event.get("startedAt") or ""))
        all_events = all_events[-MAX_EVENTS_PER_RECORD:]
        latest = added[-1]
        result["priceEvents"] = all_events
        result["previousMarketPrice"] = round(float(latest.get("priceBefore") or result.get("marketPrice") or price), 2)
        result["marketPrice"] = round(price, 2)
        result["hourlyChangePct"] = round(float(latest.get("movePct") or 0), 3)
        result["dailyChange"] = round(float(latest.get("movePct") or 0), 3)
        result["lastGameMovePct"] = round(float(latest.get("movePct") or 0), 3)
        result["lastGameStats"] = dict(latest.get("stats") or {})
        result["lastPriceEventAt"] = latest.get("startedAt")
        result["lastPriceEvent"] = latest.get("name")
        result["lastPriceEventId"] = latest.get("eventKey")
        result["priceExplanation"] = latest.get("reason")
        result["golfLastVerifiedAt"] = latest.get("startedAt")
        result["golfEventModel"] = "golf-espn-scoreboard-v1"
        if latest.get("golfEspnAthleteId"):
            result["golfEspnAthleteId"] = latest.get("golfEspnAthleteId")
        updated[index] = result
        touched += 1
        added_total += len(added)
    return updated, touched, added_total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--lookback-hours", type=float, default=192.0)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    args = parser.parse_args()

    records = load_records(args.catalog)
    now = utc_now()
    start = now - timedelta(hours=max(1.0, args.lookback_hours))
    tournaments, warnings = discover_tournaments(start, now, timeout=args.request_timeout)
    print(f"Verified completed PGA/LPGA tournaments discovered: {len(tournaments):,}")
    for warning in warnings[:10]:
        print(f"WARNING {warning}")
    updated, touched, added = apply_live_tournaments(records, tournaments, max_move_pct=args.max_game_move_pct)
    write_records(args.catalog, updated)
    print(f"Applied {added:,} new verified Golf price events to {touched:,} records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
