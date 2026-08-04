#!/usr/bin/env python3
"""Backfill dated TalentX price history from completed historical games.

This does not invent market noise. For each supported athlete it evaluates
completed games with the same bounded game-pricing model used by the hourly
refresh, then works backward from today's known price to reconstruct the price
immediately before and after each game.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
from typing import Any

from hourly_price_refresh import (
    discover_recent_events,
    fetch_hourly_evidence,
    game_event_move,
    iso_utc,
    player_event_key,
    select_records,
    utc_now,
)


def load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def existing_history(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("priceHistory")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def backfilled_history(
    record: dict[str, Any],
    item: dict[str, Any],
    events: list[dict[str, Any]],
    max_move_pct: float,
) -> list[dict[str, Any]]:
    current = max(0.01, float(record.get("marketPrice") or 0.01))
    evaluated: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda value: str(value.get("startedAt") or "")):
        move_pct, evidence = game_event_move(record, item, event, max_move_pct)
        if not evidence.get("comparable") or abs(move_pct) < 0.001:
            continue
        evaluated.append({**event, **evidence, "movePct": move_pct})

    after = current
    points: list[dict[str, Any]] = []
    for event in reversed(evaluated):
        move = float(event.get("movePct") or 0.0)
        denominator = 1.0 + move / 100.0
        before = after / denominator if abs(denominator) > 0.0001 else after
        started = str(event.get("startedAt") or "")
        if not started:
            continue
        event_id = str(event.get("eventKey") or "")
        label = str(event.get("name") or "Completed game")
        points.extend(
            [
                {
                    "time": iso_utc((__import__("datetime").datetime.fromisoformat(started.replace("Z", "+00:00")) - timedelta(seconds=1))),
                    "price": round(before, 2),
                    "eventId": event_id,
                    "label": label,
                    "phase": "open",
                    "source": "historical-game-backfill",
                    "movePct": round(move, 3),
                },
                {
                    "time": started,
                    "price": round(after, 2),
                    "eventId": event_id,
                    "label": label,
                    "phase": "close",
                    "source": "historical-game-backfill",
                    "movePct": round(move, 3),
                },
            ]
        )
        after = before

    points.sort(key=lambda value: str(value.get("time") or ""))
    return points


def merge_history(record: dict[str, Any], generated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined = [*existing_history(record), *generated]
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for point in combined:
        key = (
            str(point.get("time") or point.get("date") or ""),
            str(point.get("eventId") or point.get("eventKey") or ""),
            str(point.get("phase") or "close"),
        )
        if key[0]:
            deduped[key] = point
    return sorted(deduped.values(), key=lambda value: str(value.get("time") or value.get("date") or ""))[-2500:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--days", type=int, default=182)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--max-athletes", type=int, default=2500)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    args = parser.parse_args()

    records = load_catalog(args.catalog)
    now = utc_now()
    participant_ids, athlete_events, events, warnings = discover_recent_events(
        records,
        now=now,
        lookback_hours=max(24.0, float(args.days) * 24.0),
        timeout=args.request_timeout,
        workers=args.workers,
        processed_keys=set(),
        processed_player_keys=set(),
    )
    indexes = select_records(records, participant_ids, max_athletes=args.max_athletes)
    print(f"Historical events discovered: {len(events):,}")
    print(f"Matched athletes selected: {len(indexes):,}")
    if warnings:
        print(f"Discovery warnings: {len(warnings):,}")

    evidence: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(fetch_hourly_evidence, records[index], args.request_timeout): index for index in indexes}
        for future in as_completed(futures):
            index = futures[future]
            try:
                evidence[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                evidence[index] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    updated = list(records)
    histories = 0
    points = 0
    for index in indexes:
        record = records[index]
        item = evidence.get(index, {})
        if not item.get("ok"):
            continue
        athlete_key = (str(record.get("sourceNamespace") or ""), str(record.get("sourceRecordId") or ""))
        generated = backfilled_history(
            record,
            item,
            athlete_events.get(athlete_key, []),
            args.max_game_move_pct,
        )
        if not generated:
            continue
        result = dict(record)
        result["priceHistory"] = merge_history(result, generated)
        result["priceHistoryBackfilledAt"] = iso_utc(now)
        result["priceHistoryBackfillDays"] = args.days
        result["priceHistoryBackfillModel"] = "historical-game-events-v1"
        updated[index] = result
        histories += 1
        points += len(generated)

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Backfilled {histories:,} athletes with {points:,} dated price points.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
