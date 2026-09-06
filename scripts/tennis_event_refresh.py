#!/usr/bin/env python3
"""Apply verified ATP/WTA singles results to TalentX Tennis prices.

Tennis is an individual sport and does not fit the team-sport ESPN adapter used by
MLB/NBA/NFL/NHL/Soccer. ESPN's Tennis scoreboard already exposes completed
competitions with stable athlete IDs, player names, winners, set scores,
tournament/round metadata and major status. This module converts those verified
results into durable TalentX ``priceEvents``.

Live mode changes ``marketPrice`` only for newly discovered completed matches.
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
    "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard"
    "?limit=1000&dates={start_date}-{end_date}"
)
TOURS = ("atp", "wta")
USER_AGENT = "TalentX-Tennis-Event-Refresh/1.0 (+https://github.com/rossad213/TalentX)"
MAX_EVENTS_PER_RECORD = 2500


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


def tennis_completed(competition: dict[str, Any]) -> bool:
    status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    if status_type.get("completed") is True:
        return True
    values = [
        status_type.get("state"),
        status_type.get("name"),
        status_type.get("description"),
        status_type.get("detail"),
        status_type.get("shortDetail"),
    ]
    joined = " ".join(str(value or "") for value in values).lower().replace("_", " ").replace("-", " ")
    return "final" in joined or "completed" in joined or str(status_type.get("state") or "").lower() == "post"


def singles_competition(competition: dict[str, Any], grouping_slug: str = "") -> bool:
    type_info = competition.get("type") if isinstance(competition.get("type"), dict) else {}
    slug = str(type_info.get("slug") or grouping_slug or "").lower()
    text = str(type_info.get("text") or "").lower()
    if "double" in slug or "double" in text or "mixed" in slug or "mixed" in text:
        return False
    return "single" in slug or "single" in text or not slug


def competitor_name(competitor: dict[str, Any]) -> str:
    athlete = competitor.get("athlete") if isinstance(competitor.get("athlete"), dict) else {}
    return str(
        athlete.get("displayName")
        or athlete.get("fullName")
        or competitor.get("displayName")
        or competitor.get("name")
        or ""
    ).strip()


def sets_won(competitor: dict[str, Any]) -> int:
    lines = competitor.get("linescores") if isinstance(competitor.get("linescores"), list) else []
    explicit = sum(1 for item in lines if isinstance(item, dict) and item.get("winner") is True)
    if explicit:
        return explicit
    return 0


def flatten_scoreboard(payload: dict[str, Any], tour: str, source_url: str = "") -> list[dict[str, Any]]:
    """Flatten ESPN tournament/grouping payloads into completed singles matches."""
    matches: dict[str, dict[str, Any]] = {}
    for tournament in payload.get("events") or []:
        if not isinstance(tournament, dict):
            continue
        tournament_name = str(tournament.get("name") or tournament.get("shortName") or "Tennis event")
        major = bool(tournament.get("major"))
        tournament_id = str(tournament.get("id") or "")
        for grouping_block in tournament.get("groupings") or []:
            if not isinstance(grouping_block, dict):
                continue
            grouping = grouping_block.get("grouping") if isinstance(grouping_block.get("grouping"), dict) else {}
            grouping_slug = str(grouping.get("slug") or "")
            for competition in grouping_block.get("competitions") or []:
                if not isinstance(competition, dict) or not tennis_completed(competition):
                    continue
                if not singles_competition(competition, grouping_slug):
                    continue
                competitors = [item for item in (competition.get("competitors") or []) if isinstance(item, dict)]
                if len(competitors) != 2:
                    continue
                started = parse_datetime(competition.get("date") or competition.get("startDate"))
                competition_id = str(competition.get("id") or "").strip()
                if not competition_id or started is None:
                    continue
                round_info = competition.get("round") if isinstance(competition.get("round"), dict) else {}
                round_name = str(round_info.get("displayName") or round_info.get("name") or "Match")
                parsed_competitors: list[dict[str, Any]] = []
                for competitor in competitors:
                    name = competitor_name(competitor)
                    if not name:
                        continue
                    athlete = competitor.get("athlete") if isinstance(competitor.get("athlete"), dict) else {}
                    parsed_competitors.append({
                        "name": name,
                        "normalizedName": norm(name),
                        "athleteId": str(competitor.get("id") or athlete.get("id") or "").strip(),
                        "winner": competitor.get("winner") is True,
                        "setsWon": sets_won(competitor),
                        "linescores": [dict(item) for item in (competitor.get("linescores") or []) if isinstance(item, dict)],
                    })
                if len(parsed_competitors) != 2:
                    continue
                key = f"{tour}:{competition_id}"
                matches[key] = {
                    "matchKey": key,
                    "competitionId": competition_id,
                    "tournamentId": tournament_id,
                    "tour": tour.upper(),
                    "tournament": tournament_name,
                    "round": round_name,
                    "major": major,
                    "startedAt": iso_utc(started),
                    "sourceUrl": source_url,
                    "competitors": parsed_competitors,
                }
    return sorted(matches.values(), key=lambda item: str(item.get("startedAt") or ""))


def round_importance(round_name: str) -> float:
    text = norm(round_name)
    if "final" in text and "semifinal" not in text and "quarterfinal" not in text and "qualifying" not in text:
        return 0.62
    if "semifinal" in text or text in {"sf", "semis"}:
        return 0.42
    if "quarterfinal" in text or text in {"qf", "quarters"}:
        return 0.30
    if "roundof16" in text or text in {"r16", "4thround", "fourthround"}:
        return 0.22
    if "roundof32" in text or text in {"r32", "3rdround", "thirdround"}:
        return 0.16
    if "roundof64" in text or text in {"r64", "2ndround", "secondround"}:
        return 0.12
    if "roundof128" in text or text in {"r128", "1stround", "firstround"}:
        return 0.09
    if "qualifying" in text:
        return 0.06
    return 0.10


def player_rank(record: dict[str, Any] | None) -> int | None:
    if not isinstance(record, dict):
        return None
    for key in ("sourceRank", "rosterSourceRank"):
        value = number(record.get(key), 0)
        if value > 0:
            return int(value)
    return None


def tennis_match_move(
    *,
    winner: bool,
    round_name: str,
    major: bool,
    sets_for: int,
    sets_against: int,
    player_record: dict[str, Any] | None = None,
    opponent_record: dict[str, Any] | None = None,
    max_move_pct: float = 2.5,
) -> float:
    """Verified Tennis result priced by importance and surprise, without a fixed ceiling."""
    del max_move_pct
    importance = round_importance(round_name)
    move = 0.06 + importance * 1.65 if winner else -(0.07 + importance * 0.22)
    if major:
        move *= 1.75 if winner else 1.30
    if sets_for or sets_against:
        if winner and sets_against == 0:
            move += 0.04
        elif not winner and sets_for == 0:
            move -= 0.03
    own_rank = player_rank(player_record)
    opponent_rank = player_rank(opponent_record)
    if own_rank and opponent_rank:
        if winner and own_rank > opponent_rank:
            move += 0.22 * math.log1p((own_rank - opponent_rank) / 20.0)
        elif not winner and own_rank < opponent_rank:
            move -= 0.18 * math.log1p((opponent_rank - own_rank) / 20.0)
        elif winner and own_rank < opponent_rank:
            move -= 0.02 * math.log1p((opponent_rank - own_rank) / 100.0)
    if abs(move) < 0.03:
        move = 0.03 if winner else -0.03
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
        if str(record.get("primaryCategory") or "") != "Athlete" or str(record.get("discipline") or "") != "Tennis":
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
    matches: Iterable[dict[str, Any]],
) -> list[tuple[int, dict[str, Any], dict[str, Any], dict[str, Any] | None]]:
    name_index = build_name_index(records)
    output: list[tuple[int, dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
    for match in matches:
        competitors = match.get("competitors") if isinstance(match.get("competitors"), list) else []
        if len(competitors) != 2:
            continue
        chosen: list[int | None] = []
        for competitor in competitors:
            indexes = name_index.get(str(competitor.get("normalizedName") or ""), []) if isinstance(competitor, dict) else []
            chosen.append(choose_record(records, indexes) if indexes else None)
        for side, index in enumerate(chosen):
            if index is None:
                continue
            competitor = competitors[side]
            opponent_index = chosen[1 - side]
            opponent_record = records[opponent_index] if opponent_index is not None else None
            output.append((index, match, competitor, opponent_record))
    return output


def event_for_record(
    record: dict[str, Any],
    match: dict[str, Any],
    competitor: dict[str, Any],
    opponent_record: dict[str, Any] | None,
    *,
    historical_backfill: bool,
    max_move_pct: float,
) -> dict[str, Any]:
    competitors = match.get("competitors") if isinstance(match.get("competitors"), list) else []
    opponent = next((item for item in competitors if item is not competitor), {})
    sets_for = int(number(competitor.get("setsWon"), 0))
    sets_against = int(number(opponent.get("setsWon"), 0)) if isinstance(opponent, dict) else 0
    move = tennis_match_move(
        winner=competitor.get("winner") is True,
        round_name=str(match.get("round") or "Match"),
        major=bool(match.get("major")),
        sets_for=sets_for,
        sets_against=sets_against,
        player_record=record,
        opponent_record=opponent_record,
        max_move_pct=max_move_pct,
    )
    opponent_name = str(opponent.get("name") or "Opponent") if isinstance(opponent, dict) else "Opponent"
    match_key = str(match.get("matchKey") or match.get("competitionId") or "")
    return {
        "eventKey": f"espn-tennis:{match_key}",
        "eventId": str(match.get("competitionId") or match_key),
        "eventType": "game",
        "sport": "tennis",
        "provider": "ESPN",
        "tour": str(match.get("tour") or ""),
        "league": str(match.get("tour") or ""),
        "name": f"{match.get('tournament') or 'Tennis'} · {match.get('round') or 'Match'} · vs {opponent_name}",
        "tournament": match.get("tournament"),
        "round": match.get("round"),
        "major": bool(match.get("major")),
        "startedAt": match.get("startedAt"),
        "sourceUrl": match.get("sourceUrl"),
        "movePct": move,
        "verified": True,
        "verifiedParticipation": True,
        "historicalBackfill": bool(historical_backfill),
        "backfillModel": "tennis-espn-scoreboard-v1" if historical_backfill else None,
        "stats": {
            "matchWin": 1 if competitor.get("winner") is True else 0,
            "matchLoss": 0 if competitor.get("winner") is True else 1,
            "setsWon": sets_for,
            "setsLost": sets_against,
        },
        "teamWon": competitor.get("winner") is True,
        "pricingBasis": "verified-tennis-result-round-major",
        "reason": (
            f"Verified {match.get('tour') or 'tennis'} singles {'win' if competitor.get('winner') is True else 'loss'} "
            f"in {match.get('round') or 'match'} at {match.get('tournament') or 'tournament'}."
        ),
    }


def discover_matches(
    start: datetime,
    end: datetime,
    *,
    timeout: float,
    tours: Iterable[str] = TOURS,
    http: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    start_date = start.strftime("%Y%m%d")
    end_date = end.strftime("%Y%m%d")
    matches: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    client = http or session()
    for tour in tours:
        url = ESPN_SCOREBOARD.format(tour=tour, start_date=start_date, end_date=end_date)
        try:
            payload = fetch_json(url, timeout, client)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{tour} scoreboard: {type(exc).__name__}: {exc}")
            continue
        for match in flatten_scoreboard(payload, tour, url):
            matches[str(match.get("matchKey"))] = match
    return sorted(matches.values(), key=lambda item: str(item.get("startedAt") or "")), warnings


def apply_live_matches(
    records: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    *,
    max_move_pct: float,
) -> tuple[list[dict[str, Any]], int, int]:
    updated = [dict(record) for record in records]
    rows = matched_competitors(updated, matches)
    by_record: dict[int, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]]] = {}
    for index, match, competitor, opponent_record in rows:
        by_record.setdefault(index, []).append((match, competitor, opponent_record))

    touched = 0
    added_total = 0
    for index, entries in by_record.items():
        result = dict(updated[index])
        existing = [dict(event) for event in result.get("priceEvents", []) if isinstance(event, dict)]
        known = existing_event_keys(result)
        price = max(0.01, float(result.get("marketPrice") or 0.01))
        added: list[dict[str, Any]] = []
        for match, competitor, opponent_record in sorted(entries, key=lambda item: str(item[0].get("startedAt") or "")):
            event = event_for_record(
                result,
                match,
                competitor,
                opponent_record,
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
        result["tennisLastVerifiedAt"] = latest.get("startedAt")
        result["tennisEventModel"] = "tennis-espn-scoreboard-v1"
        updated[index] = result
        touched += 1
        added_total += len(added)
    return updated, touched, added_total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--lookback-hours", type=float, default=96.0)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    args = parser.parse_args()

    records = load_records(args.catalog)
    now = utc_now()
    start = now - timedelta(hours=max(1.0, args.lookback_hours))
    matches, warnings = discover_matches(start, now, timeout=args.request_timeout)
    print(f"Verified completed ATP/WTA singles matches discovered: {len(matches):,}")
    for warning in warnings[:10]:
        print(f"WARNING {warning}")
    updated, touched, added = apply_live_matches(records, matches, max_move_pct=args.max_game_move_pct)
    write_records(args.catalog, updated)
    print(f"Applied {added:,} new verified Tennis price events to {touched:,} records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
