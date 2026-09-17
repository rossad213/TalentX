#!/usr/bin/env python3
"""Sync source-backed NFL career game counts for recent draft classes.

TalentX's hourly NFL collector already uses ESPN season history, but only athletes
selected by a recent completed event receive that refresh. Rookie IPO decay needs
durable career exposure even when a player has no new game in the lookback window.
This job fills that narrow data-quality gap without writing or hardcoding prices.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from category_market_store import load_records, write_records

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
ESPN_HISTORY_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/"
    "athletes/{athlete_id}/stats?season={season}"
)
SYNC_VERSION = "2026-09-16-recent-draft-career-games-v1"
MAX_DRAFT_AGE = 4
CACHE_HOURS = 20
GAME_KEYS = {"gamesplayed", "games", "appearances", "gp"}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


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


def career_games_from_payload(payload: dict[str, Any]) -> int:
    """Sum the maximum reported Games Played for each season in ESPN history."""
    season_games: dict[int, float] = {}
    categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
    for category in categories:
        if not isinstance(category, dict):
            continue
        names = category.get("names") if isinstance(category.get("names"), list) else []
        game_indexes = [index for index, name in enumerate(names) if _norm(name) in GAME_KEYS]
        if not game_indexes:
            continue
        rows = category.get("statistics") if isinstance(category.get("statistics"), list) else []
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
            candidates = []
            for index in game_indexes:
                if index < len(values):
                    parsed = _number(values[index])
                    if parsed is not None and parsed >= 0:
                        candidates.append(parsed)
            if candidates:
                season_games[year] = max(season_games.get(year, 0.0), max(candidates))
    return max(0, int(round(sum(season_games.values()))))


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "TalentX-NFL-games-sync/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _eligible(record: dict[str, Any], *, current_year: int) -> bool:
    if str(record.get("leagueOrMedium") or "").strip().upper() != "NFL":
        return False
    if str(record.get("sourceNamespace") or "").strip().lower() != "espn":
        return False
    if not str(record.get("sourceRecordId") or "").strip():
        return False
    draft_year = _number(record.get("draftYear"))
    if draft_year is None:
        return False
    age = max(0, current_year - int(round(draft_year)))
    return age <= MAX_DRAFT_AGE


def _fresh(record: dict[str, Any], *, now: datetime, cache_hours: float) -> bool:
    if str(record.get("nflCareerGamesSyncVersion") or "") != SYNC_VERSION:
        return False
    synced = _parse_time(record.get("nflCareerGamesSyncedAt"))
    if synced is None:
        return False
    return now - synced < timedelta(hours=max(0.0, cache_hours))


def sync_records(
    records: list[dict[str, Any]],
    *,
    fetcher: Callable[[str, float], dict[str, Any]] = fetch_json,
    workers: int = 16,
    timeout: float = 8.0,
    cache_hours: float = CACHE_HOURS,
    now: datetime | None = None,
) -> dict[str, int]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = current.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    season = current.year
    selected: list[int] = []
    counts = {"eligible": 0, "requested": 0, "updated": 0, "unchanged": 0, "failed": 0, "cached": 0}

    for index, record in enumerate(records):
        if not _eligible(record, current_year=season):
            continue
        counts["eligible"] += 1
        if _fresh(record, now=current, cache_hours=cache_hours):
            counts["cached"] += 1
            continue
        selected.append(index)

    counts["requested"] = len(selected)

    def request(index: int) -> tuple[int, int | None, str]:
        record = records[index]
        athlete_id = str(record.get("sourceRecordId") or "").strip()
        url = ESPN_HISTORY_URL.format(athlete_id=athlete_id, season=season)
        try:
            payload = fetcher(url, timeout)
            games = career_games_from_payload(payload)
            return index, games, url
        except Exception:  # noqa: BLE001 - one provider miss must not take Sports offline
            return index, None, url

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(request, index) for index in selected]
        for future in as_completed(futures):
            index, games, url = future.result()
            record = records[index]
            if games is None:
                counts["failed"] += 1
                continue
            prior = int(round(max(0.0, _number(record.get("professionalGames")) or 0.0)))
            # Never erase stronger durable evidence when a provider returns a
            # partial history. Career games are monotonic for an active player.
            resolved = max(prior, games)
            if resolved != prior:
                record["professionalGames"] = resolved
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            record["nflCareerGamesSource"] = url
            record["nflCareerGamesSyncVersion"] = SYNC_VERSION
            record["nflCareerGamesSyncedAt"] = stamp
            summary = dict(record.get("pricingEvidenceSummary") or {})
            summary["professionalGames"] = resolved
            record["pricingEvidenceSummary"] = summary

    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--request-timeout", type=float, default=8.0)
    parser.add_argument("--cache-hours", type=float, default=CACHE_HOURS)
    args = parser.parse_args()

    records = load_records(args.catalog)
    counts = sync_records(
        records,
        workers=args.workers,
        timeout=args.request_timeout,
        cache_hours=args.cache_hours,
    )
    write_records(args.catalog, records)
    print(
        "NFL career-games sync: "
        f"{counts['updated']:,} updated, {counts['unchanged']:,} unchanged, "
        f"{counts['failed']:,} failed, {counts['cached']:,} cached; "
        f"{counts['eligible']:,} recent drafted NFL players eligible."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
