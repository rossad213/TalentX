#!/usr/bin/env python3
"""Refresh verified Formula 1 evidence and race-driven TalentX market events.

F1 uses the Jolpica F1 API's Ergast-compatible endpoints for current standings
and race results. The first successful run backfills completed races as verified
ledger events without moving today's price; the Motorsport migration then
normalizes that historical path to the corrected current fair value. Subsequent
new races move the existing market price exactly once.

Broad Wikidata Motorsport discoveries are not assumed to be current competitors.
Until a championship-specific provider verifies participation they are routed to
Under Review instead of receiving a live-market valuation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from results_event_pricing import result_move_from_delta, valid_move

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DEFAULT_MANIFEST = ROOT / "data" / "motorsport_event_refresh_manifest.json"
API_ROOT = "https://api.jolpi.ca/ergast/f1"
USER_AGENT = "TalentX-Motorsport-Refresh/1.0 (+https://github.com/rossad213/TalentX)"
EVENT_MODEL = "f1-jolpica-race-surprise-v1"
MAX_EVENTS_PER_RECORD = 2500

F1_ALIASES = {
    "andreakimiantonelli": "kimiantonelli",
    "kimiantonelli": "kimiantonelli",
    "alexalbon": "alexanderalbon",
    "alexanderalbon": "alexanderalbon",
    "checoperez": "sergioperez",
    "sergioperez": "sergioperez",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def identity_name(value: Any) -> str:
    key = normalize(value)
    return F1_ALIASES.get(key, key)


def slug(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def is_motorsport(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() == "motorsport"
    )


def session() -> requests.Session:
    client = requests.Session()
    retry = Retry(
        total=3, connect=3, read=3, backoff_factor=0.75,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=6, pool_maxsize=6)
    client.mount("https://", adapter)
    client.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return client


def fetch_json(url: str, timeout: float, client: requests.Session) -> dict[str, Any]:
    response = client.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected response shape from {url}")
    return payload


def driver_name(driver: dict[str, Any]) -> str:
    return " ".join(part for part in (
        str(driver.get("givenName") or "").strip(),
        str(driver.get("familyName") or "").strip(),
    ) if part).strip()


def standings_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    table = payload.get("MRData", {}).get("StandingsTable", {})
    lists = table.get("StandingsLists") if isinstance(table, dict) else []
    if not isinstance(lists, list) or not lists:
        return []
    rows = lists[0].get("DriverStandings") if isinstance(lists[0], dict) else []
    output: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        driver = row.get("Driver") if isinstance(row.get("Driver"), dict) else {}
        name = driver_name(driver)
        if not name:
            continue
        constructors = row.get("Constructors") if isinstance(row.get("Constructors"), list) else []
        team = ""
        if constructors and isinstance(constructors[-1], dict):
            team = str(constructors[-1].get("name") or "")
        output.append({
            "name": name,
            "identity": identity_name(name),
            "driverId": str(driver.get("driverId") or ""),
            "permanentNumber": str(driver.get("permanentNumber") or ""),
            "nationality": str(driver.get("nationality") or ""),
            "position": int(number(row.get("position"), len(output) + 1)),
            "points": number(row.get("points")),
            "wins": int(number(row.get("wins"))),
            "team": team,
        })
    return output


def races_from_results_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for payload in pages:
        table = payload.get("MRData", {}).get("RaceTable", {})
        races = table.get("Races") if isinstance(table, dict) else []
        for race in races if isinstance(races, list) else []:
            if not isinstance(race, dict):
                continue
            key = (str(race.get("season") or ""), str(race.get("round") or ""))
            current = merged.setdefault(key, {k: v for k, v in race.items() if k != "Results"})
            by_driver = {
                str(item.get("Driver", {}).get("driverId") or identity_name(driver_name(item.get("Driver", {})))): dict(item)
                for item in current.get("Results", []) if isinstance(item, dict)
            }
            for item in race.get("Results") or []:
                if not isinstance(item, dict):
                    continue
                driver = item.get("Driver") if isinstance(item.get("Driver"), dict) else {}
                dkey = str(driver.get("driverId") or identity_name(driver_name(driver)))
                if dkey:
                    by_driver[dkey] = dict(item)
            current["Results"] = list(by_driver.values())
    return sorted(merged.values(), key=lambda race: int(number(race.get("round"), 0)))


def fetch_f1(season: int, timeout: float, client: requests.Session | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    http = client or session()
    warnings: list[str] = []
    standings_url = f"{API_ROOT}/{season}/driverstandings/?limit=100"
    standings = standings_from_payload(fetch_json(standings_url, timeout, http))

    pages: list[dict[str, Any]] = []
    offset = 0
    for _ in range(20):
        url = f"{API_ROOT}/{season}/results/?limit=100&offset={offset}"
        payload = fetch_json(url, timeout, http)
        pages.append(payload)
        meta = payload.get("MRData", {})
        total = int(number(meta.get("total"), 0))
        limit = max(1, int(number(meta.get("limit"), 100)))
        offset += limit
        if total <= 0 or offset >= total:
            break
    races = races_from_results_pages(pages)
    if not standings:
        warnings.append("Current F1 standings endpoint returned no drivers; current-market verification was not changed.")
    if not races:
        warnings.append("F1 results endpoint returned no completed races; event ledger was not changed.")
    return standings, races, warnings


def result_finished_status(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "").lower()
    position_text = str(result.get("positionText") or "").lower()
    if position_text in {"r", "d", "e", "w", "f", "n"}:
        return False
    if status == "finished" or status.startswith("+"):
        return True
    return not any(token in status for token in (
        "retired", "accident", "collision", "disqual", "engine", "gearbox", "hydraulic", "electrical",
        "brakes", "suspension", "transmission", "puncture", "overheating", "spun", "damage", "did not",
    ))


def race_timestamp(race: dict[str, Any]) -> str:
    date = str(race.get("date") or "").strip()
    time = str(race.get("time") or "00:00:00Z").strip() or "00:00:00Z"
    if not date:
        return ""
    return f"{date}T{time}" if "T" not in date else date


def race_result_move(*, actual_position: int, expected_position: float, field_size: int, grid: int, status: str, fastest_lap_rank: int | None, prior_starts: int) -> tuple[float, float]:
    field = max(2, field_size)
    actual = max(1.0, min(float(field), float(actual_position)))
    expected = max(1.0, min(float(field), float(expected_position)))
    actual_score = 100.0 * (field - actual) / (field - 1.0)
    expected_score = 100.0 * (field - expected) / (field - 1.0)
    delta = actual_score - expected_score
    move = result_move_from_delta(delta, scale=1.15, reference_pct=16.0, exponent=1.35, dead_zone_pct=2.5)

    if actual_position == 1:
        move += 0.65
    elif actual_position <= 3:
        move += 0.25
    if fastest_lap_rank == 1:
        move += 0.10

    lower_status = str(status or "").lower()
    if not (lower_status == "finished" or lower_status.startswith("+")):
        if any(token in lower_status for token in ("collision", "accident", "spun", "disqual")):
            move -= 0.75
        elif any(token in lower_status for token in ("engine", "gearbox", "hydraulic", "electrical", "transmission", "overheating")):
            move -= 0.15
        else:
            move -= 0.35

    if prior_starts < 5:
        move *= 1.08
    elif prior_starts >= 50:
        move *= 0.92
    checked = valid_move(move)
    return round(checked if checked is not None else 0.0, 3), round(delta, 3)


def build_season_evidence(races: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    stats: dict[str, dict[str, Any]] = {}
    events: dict[str, list[dict[str, Any]]] = {}
    cumulative_points: dict[str, float] = {}
    starts_before: dict[str, int] = {}

    for race in sorted(races, key=lambda item: int(number(item.get("round"), 0))):
        results = [item for item in (race.get("Results") or []) if isinstance(item, dict)]
        field_size = max(2, len(results))
        pre_order = sorted(cumulative_points, key=lambda key: (-cumulative_points.get(key, 0.0), key))
        pre_rank = {key: index + 1 for index, key in enumerate(pre_order)}
        timestamp = race_timestamp(race)
        race_name = str(race.get("raceName") or f"Round {race.get('round') or ''}").strip()
        round_number = int(number(race.get("round"), 0))

        for item in results:
            driver = item.get("Driver") if isinstance(item.get("Driver"), dict) else {}
            name = driver_name(driver)
            key = identity_name(name)
            if not key:
                continue
            position = max(1, int(number(item.get("position"), field_size)))
            grid = int(number(item.get("grid"), 0))
            prior = int(starts_before.get(key, 0))
            expected_rank = float(pre_rank.get(key, grid if grid > 0 else (field_size + 1) / 2.0))
            if grid > 0 and key in pre_rank:
                expected_rank = expected_rank * 0.72 + float(grid) * 0.28
            fastest = item.get("FastestLap") if isinstance(item.get("FastestLap"), dict) else {}
            fastest_rank = int(number(fastest.get("rank"), 0)) or None
            status = str(item.get("status") or "")
            move, delta = race_result_move(
                actual_position=position,
                expected_position=expected_rank,
                field_size=field_size,
                grid=grid,
                status=status,
                fastest_lap_rank=fastest_rank,
                prior_starts=prior,
            )
            event_key = f"jolpica-f1:{race.get('season')}:{round_number}:{str(driver.get('driverId') or key)}"
            event = {
                "eventKey": event_key,
                "eventId": event_key,
                "eventType": "game",
                "sport": "motorsport",
                "provider": "Jolpica F1",
                "league": "Formula 1",
                "series": "Formula 1",
                "season": int(number(race.get("season"), 0)),
                "round": round_number,
                "name": f"{race_name} · P{position}",
                "raceName": race_name,
                "startedAt": timestamp,
                "sourceUrl": f"{API_ROOT}/{race.get('season')}/{round_number}/results/",
                "movePct": move,
                "performanceDeltaPct": delta,
                "verified": True,
                "verifiedParticipation": True,
                "pricingBasis": "verified-f1-finish-vs-prerace-expectation",
                "reason": f"Verified Formula 1 result: P{position} at {race_name} versus pre-race expected position {expected_rank:.1f}.",
                "stats": {
                    "finishPosition": position,
                    "gridPosition": grid,
                    "points": number(item.get("points")),
                    "status": status,
                    "fastestLapRank": fastest_rank,
                    "fieldSize": field_size,
                    "expectedPosition": round(expected_rank, 2),
                },
                "teamWon": position == 1,
                "motorsportEventModel": EVENT_MODEL,
            }
            events.setdefault(key, []).append(event)

            row = stats.setdefault(key, {
                "name": name, "starts": 0, "classifiedFinishes": 0, "wins": 0, "podiums": 0,
                "top10": 0, "dnfs": 0, "points": 0.0, "finishSum": 0.0, "gridSum": 0.0,
            })
            row["starts"] += 1
            row["wins"] += int(position == 1)
            row["podiums"] += int(position <= 3)
            row["top10"] += int(position <= 10)
            row["points"] += number(item.get("points"))
            row["finishSum"] += position
            row["gridSum"] += grid if grid > 0 else field_size
            finished = result_finished_status(item)
            row["classifiedFinishes"] += int(finished)
            row["dnfs"] += int(not finished)

            cumulative_points[key] = cumulative_points.get(key, 0.0) + number(item.get("points"))
            starts_before[key] = prior + 1
    return stats, events


def metric_bundle(existing: dict[str, Any], season_stats: dict[str, Any], standing: dict[str, Any], field_size: int, leader_points: float, completed_races: int) -> dict[str, float]:
    starts = max(1, int(season_stats.get("starts") or 0))
    rank = max(1, int(standing.get("position") or field_size))
    rank_pct = 1.0 if field_size <= 1 else 1.0 - (rank - 1) / (field_size - 1)
    avg_finish = number(season_stats.get("finishSum")) / starts
    finish_pct = 1.0 - max(0.0, min(1.0, (avg_finish - 1.0) / max(1.0, field_size - 1.0)))
    points_ratio = number(standing.get("points")) / max(1.0, leader_points)
    performance = 55.0 + 45.0 * (0.48 * rank_pct + 0.30 * math.sqrt(max(0.0, points_ratio)) + 0.22 * finish_pct)

    wins = int(season_stats.get("wins") or 0)
    podiums = int(season_stats.get("podiums") or 0)
    top10 = int(season_stats.get("top10") or 0)
    achievement_season = 48.0 + 18.0 * min(1.0, wins / 4.0) + 14.0 * min(1.0, podiums / 8.0) + 12.0 * rank_pct
    finish_rate = number(season_stats.get("classifiedFinishes")) / starts
    top10_rate = top10 / starts
    consistency = 52.0 + 28.0 * finish_rate + 20.0 * top10_rate
    availability = 70.0 + 30.0 * min(1.0, starts / max(1, completed_races))

    old = existing if isinstance(existing, dict) else {}
    achievements = max(achievement_season, number(old.get("achievements"), 50.0))
    potential = number(old.get("potential"), 72.0)
    audience = number(old.get("audience"), 70.0)
    return {
        "performance": round(max(0.0, min(100.0, performance)), 1),
        "achievements": round(max(0.0, min(100.0, achievements)), 1),
        "consistency": round(max(0.0, min(100.0, consistency)), 1),
        "potential": round(max(0.0, min(100.0, potential)), 1),
        "availability": round(max(0.0, min(100.0, availability)), 1),
        "audience": round(max(0.0, min(100.0, audience)), 1),
    }


def unique_ticker(name: str, used: set[str]) -> str:
    initials = "".join(part[0] for part in re.findall(r"[A-Za-z0-9]+", name)[:4]).upper() or "F1"
    base = (initials + "F1")[:5]
    candidate = base
    if candidate not in used:
        used.add(candidate)
        return candidate
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest().upper()
    for width in range(1, 5):
        candidate = (base[: max(1, 5 - width)] + digest[:width])[:5]
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError(f"Unable to create ticker for {name}")


def make_f1_record(standing: dict[str, Any], used_ids: set[str], used_tickers: set[str]) -> dict[str, Any]:
    name = str(standing.get("name") or "Formula 1 Driver")
    base_id = f"athlete-motorsport-{slug(name)}"
    record_id = base_id
    index = 2
    while record_id in used_ids:
        record_id = f"{base_id}-{index}"
        index += 1
    used_ids.add(record_id)
    return {
        "id": record_id,
        "name": name,
        "ticker": unique_ticker(name, used_tickers),
        "primaryCategory": "Athlete",
        "discipline": "Motorsport",
        "leagueOrMedium": "Formula 1",
        "teamOrPlatform": standing.get("team") or "Formula 1",
        "role": "Formula 1 Driver",
        "country": standing.get("nationality") or "—",
        "careerStatus": "Active",
        "marketSegment": "Current",
        "avatar": "".join(part[0] for part in name.split()[:2]).upper(),
        "activeMetrics": {"performance": 70.0, "achievements": 55.0, "consistency": 65.0, "potential": 72.0, "availability": 80.0, "audience": 65.0},
        "pricingConfidence": 0.72,
        "dataConfidence": 0.72,
        "marketPrice": 62.0,
        "fundamentalValue": 62.0,
        "priceEvents": [],
        "priceHistory": [],
    }


def record_strength(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(str(record.get("sourceNamespace") or "") == "curated-individual-sport-roster"),
        int(str(record.get("leagueOrMedium") or "").strip().lower() == "formula 1"),
        int(bool(record.get("professionEvidenceVerified"))),
        number(record.get("pricingConfidence", record.get("dataConfidence", 0))),
        len(record.get("priceEvents") or []) if isinstance(record.get("priceEvents"), list) else 0,
    )


def apply_refresh(records: list[dict[str, Any]], standings: list[dict[str, Any]], races: list[dict[str, Any]], *, season: int, refreshed_at: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    updated = [dict(record) for record in records]
    stats, events_by_identity = build_season_evidence(races)
    standings_by_identity = {str(item.get("identity") or ""): item for item in standings if item.get("identity")}
    field_size = max(2, len(standings))
    leader_points = max([number(item.get("points")) for item in standings] or [1.0])
    completed_races = len(races)

    indexes: dict[str, list[int]] = {}
    for index, record in enumerate(updated):
        if is_motorsport(record):
            indexes.setdefault(identity_name(record.get("name")), []).append(index)

    used_ids = {str(record.get("id") or "") for record in updated if record.get("id")}
    used_tickers = {str(record.get("ticker") or "").upper() for record in updated if record.get("ticker")}
    created = 0
    verified = 0
    event_added = 0
    live_applied = 0

    approved_current_f1 = {
        identity_name(record.get("name"))
        for record in updated
        if is_motorsport(record)
        and str(record.get("leagueOrMedium") or "").strip().lower() == "formula 1"
        and (
            str(record.get("sourceNamespace") or "").strip().lower() == "curated-individual-sport-roster"
            or str(record.get("sourceType") or "").strip().lower() == "official-ranking-roster"
            or bool(record.get("motorsportCurrentRosterVerified"))
        )
        and str(record.get("marketSegment") or "Current").strip().lower() != "under review"
    }

    # Driver standings include anyone who scored/participated during the season, not
    # necessarily the current race-seat roster. Do not create/promote a live asset
    # solely because a name appears in season standings (e.g. a replaced driver).
    active_standings = {
        identity: standing
        for identity, standing in standings_by_identity.items()
        if identity in approved_current_f1
    }

    for identity, standing in active_standings.items():
        candidates = indexes.get(identity, [])
        if not candidates:
            continue
        index = max(candidates, key=lambda i: record_strength(updated[i]))

        record = dict(updated[index])
        season_stats = stats.get(identity, {})
        starts = int(season_stats.get("starts") or 0)
        confidence = min(0.96, 0.80 + 0.035 * math.sqrt(max(0, starts))) if starts else 0.74
        source_url = f"{API_ROOT}/{season}/driverstandings/"
        record.update({
            "name": standing.get("name") or record.get("name"),
            "primaryCategory": "Athlete",
            "discipline": "Motorsport",
            "leagueOrMedium": "Formula 1",
            "teamOrPlatform": standing.get("team") or record.get("teamOrPlatform") or "Formula 1",
            "role": "Formula 1 Driver",
            "careerStatus": "Active",
            "marketSegment": "Current",
            "verificationStatus": "Verified current Formula 1 season results and standings",
            "statusSource": "Jolpica F1 API (Ergast-compatible)",
            "sourceName": "Jolpica F1 current Formula 1 standings/results",
            "sourceUrl": source_url,
            "sourceNamespace": "verified-formula1-jolpica",
            "sourceType": "verified-championship-results",
            "sourceAsOf": refreshed_at[:10],
            "lastVerifiedAt": refreshed_at,
            "sourceRank": int(standing.get("position") or 0) or None,
            "rosterSourceRank": int(standing.get("position") or 0) or None,
            "sourceRecordId": standing.get("driverId") or record.get("sourceRecordId"),
            "professionEvidenceVerified": bool(starts),
            "pricingDataStatus": "Evidence enriched · verified Formula 1 season results and current standings" if starts else "Verified Formula 1 current roster; race evidence pending",
            "pricingConfidence": round(confidence, 3),
            "dataConfidence": round(confidence, 3),
            "motorsportSeries": "Formula 1",
            "motorsportSeason": season,
            "motorsportSeasonStarts": starts,
            "motorsportSeasonWins": int(season_stats.get("wins") or 0),
            "motorsportSeasonPodiums": int(season_stats.get("podiums") or 0),
            "motorsportSeasonDnfs": int(season_stats.get("dnfs") or 0),
            "motorsportSeasonPoints": round(number(standing.get("points")), 2),
            "motorsportChampionshipRank": int(standing.get("position") or 0) or None,
            "motorsportLastVerifiedAt": refreshed_at,
            "motorsportEventModel": EVENT_MODEL,
        })
        if starts:
            record["activeMetrics"] = metric_bundle(record.get("activeMetrics") or {}, season_stats, standing, field_size, leader_points, completed_races)
            record["professionalGames"] = starts
            verified += 1

        existing = [dict(item) for item in record.get("priceEvents", []) if isinstance(item, dict)]
        known = {str(item.get("eventKey") or item.get("eventId") or "") for item in existing}
        f1_existing = [item for item in existing if str(item.get("provider") or "") == "Jolpica F1"]
        first_backfill = not f1_existing
        current_price = max(0.01, number(record.get("marketPrice"), 0.01))
        newly_added: list[dict[str, Any]] = []
        newly_live: list[dict[str, Any]] = []
        for event in sorted(events_by_identity.get(identity, []), key=lambda item: str(item.get("startedAt") or "")):
            event = dict(event)
            key = str(event.get("eventKey") or "")
            if not key or key in known:
                continue
            if first_backfill:
                event["historicalBackfill"] = True
                event["backfillModel"] = EVENT_MODEL
            else:
                before = current_price
                current_price = max(0.01, before * (1.0 + number(event.get("movePct")) / 100.0))
                event["priceBefore"] = round(before, 2)
                event["priceAfter"] = round(current_price, 2)
                event["movePct"] = round((event["priceAfter"] / event["priceBefore"] - 1.0) * 100.0, 3)
                newly_live.append(event)
            newly_added.append(event)
            known.add(key)

        if newly_added:
            all_events = [*existing, *newly_added]
            all_events.sort(key=lambda item: str(item.get("startedAt") or ""))
            record["priceEvents"] = all_events[-MAX_EVENTS_PER_RECORD:]
            event_added += len(newly_added)
        if newly_live:
            latest = newly_live[-1]
            record["previousMarketPrice"] = round(number(latest.get("priceBefore"), current_price), 2)
            record["marketPrice"] = round(current_price, 2)
            record["hourlyChangePct"] = number(latest.get("movePct"))
            record["dailyChange"] = number(latest.get("movePct"))
            record["lastGameMovePct"] = number(latest.get("movePct"))
            record["lastGamePerformanceDeltaPct"] = number(latest.get("performanceDeltaPct"))
            record["lastGameStats"] = dict(latest.get("stats") or {})
            record["lastPriceEventAt"] = latest.get("startedAt")
            record["lastPriceEvent"] = latest.get("name")
            record["lastPriceEventId"] = latest.get("eventKey")
            record["priceExplanation"] = latest.get("reason")
            live_applied += len(newly_live)
        updated[index] = record

    under_review = 0
    if standings_by_identity:
        current_identities = set(approved_current_f1)
        for index, record in enumerate(updated):
            if not is_motorsport(record):
                continue
            identity = identity_name(record.get("name"))
            if identity in current_identities:
                continue
            namespace = str(record.get("sourceNamespace") or "").strip().lower()
            if namespace == "wikidata-individual-sport" and not bool(record.get("professionEvidenceVerified")):
                result = dict(record)
                result["marketSegment"] = "Under Review"
                result["verificationStatus"] = "Under Review — current championship participation not verified"
                result["pricingDataStatus"] = "Under Review — generic racing occupation evidence only; current series participation not verified"
                result["pricingConfidence"] = min(0.45, number(result.get("pricingConfidence", result.get("dataConfidence", 0.45)), 0.45))
                result["dataConfidence"] = min(0.45, number(result.get("dataConfidence", result.get("pricingConfidence", 0.45)), 0.45))
                updated[index] = result
                under_review += 1

    return updated, {
        "createdF1Records": created,
        "verifiedF1Records": verified,
        "eventsAdded": event_added,
        "liveEventsApplied": live_applied,
        "movedToUnderReview": under_review,
    }


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if path.name == "current_catalog.json":
        fields = sorted({key for record in records for key, value in record.items() if not isinstance(value, (dict, list))})
        with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--season", type=int, default=utc_now().year)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    args = parser.parse_args()

    records = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{args.catalog} must contain a JSON array")
    stamp = iso_utc(utc_now())
    manifest: dict[str, Any] = {"refreshedAt": stamp, "season": args.season, "provider": "Jolpica F1", "model": EVENT_MODEL}
    try:
        standings, races, warnings = fetch_f1(args.season, args.request_timeout)
        if not standings:
            manifest.update({"status": "degraded", "warnings": warnings, "standingsCount": 0, "racesCount": len(races)})
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print("WARNING Motorsport refresh skipped mutations because current F1 standings were unavailable.")
            return 0
        updated, counts = apply_refresh([dict(item) for item in records if isinstance(item, dict)], standings, races, season=args.season, refreshed_at=stamp)
        write_records(args.catalog, updated)
        manifest.update({"status": "ok", "standingsCount": len(standings), "racesCount": len(races), "warnings": warnings, **counts})
        print(
            f"Motorsport F1 refresh: {len(standings)} current drivers, {len(races)} completed races, "
            f"{counts['eventsAdded']} event(s) added, {counts['liveEventsApplied']} live event(s) applied, "
            f"{counts['movedToUnderReview']} unverified discovery listing(s) moved to Under Review."
        )
    except Exception as exc:  # noqa: BLE001
        manifest.update({"status": "degraded", "warnings": [f"{type(exc).__name__}: {exc}"]})
        print(f"WARNING Motorsport F1 refresh unavailable; preserving last-known-good market: {type(exc).__name__}: {exc}")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
