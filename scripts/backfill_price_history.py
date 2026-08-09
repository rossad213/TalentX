#!/usr/bin/env python3
"""Backfill verified Sports event history without changing today's market price.

Completed historical games are evaluated with the same bounded game-pricing
model used by the live Sports refresh. The resulting events are stored in the
same durable ``priceEvents`` format trusted by TalentX charts. Prices are
reconstructed backward from today's known market price, so the backfill can
extend historical charts without repricing the current market.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from hourly_price_refresh import (
    discover_recent_events,
    fetch_hourly_evidence,
    game_event_move,
    iso_utc,
    select_records,
    utc_now,
)

MAX_PRICE_EVENTS = 2500


def load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_time(event: dict[str, Any]) -> str:
    return str(event.get("startedAt") or event.get("time") or event.get("date") or "").strip()


def existing_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("priceEvents")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def evaluated_game_events(
    record: dict[str, Any],
    item: dict[str, Any],
    events: list[dict[str, Any]],
    max_move_pct: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in sorted(events, key=event_time):
        started = event_time(event)
        key = event_key(event)
        if not started or not key:
            continue
        move_pct, evidence = game_event_move(record, item, event, max_move_pct)
        if not evidence.get("comparable") or abs(move_pct) < 0.001:
            continue
        output.append({
            **event,
            **evidence,
            "eventKey": key,
            "eventId": str(event.get("eventId") or key),
            "eventType": str(event.get("eventType") or "game"),
            "startedAt": started,
            "movePct": round(move_pct, 3),
            "verified": True,
            "historicalBackfill": True,
            "backfillModel": "historical-game-events-v2",
        })
    return output


def merge_event_evidence(
    existing: list[dict[str, Any]],
    generated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in existing:
        key = event_key(event)
        if key:
            by_key[key] = dict(event)
        else:
            anonymous.append(dict(event))
    for event in generated:
        key = event_key(event)
        if not key:
            continue
        if key in by_key:
            # Preserve live-event metadata/move when the same game was already
            # priced in production; historical discovery only fills missing data.
            merged = dict(event)
            merged.update(by_key[key])
            by_key[key] = merged
        else:
            by_key[key] = dict(event)
    combined = [*anonymous, *by_key.values()]
    combined = [event for event in combined if event_time(event)]
    combined.sort(key=event_time)
    return combined[-MAX_PRICE_EVENTS:]


def reconstruct_chain(record: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-anchor event prices backward from the unchanged current market price."""
    current = max(0.01, number(record.get("marketPrice"), 0.01))
    after = current
    rebuilt: list[dict[str, Any]] = []
    for event in reversed(events):
        result = dict(event)
        move = number(result.get("movePct"), 0.0)
        if abs(move) < 0.001:
            # Events without a price move remain durable metadata but do not
            # participate in the price chain used by charts.
            rebuilt.append(result)
            continue
        denominator = 1.0 + move / 100.0
        before = after / denominator if abs(denominator) > 0.0001 else after
        before_rounded = max(0.01, round(before, 2))
        after_rounded = max(0.01, round(after, 2))
        actual_move = round((after_rounded / before_rounded - 1.0) * 100.0, 3)
        result["priceBefore"] = before_rounded
        result["priceAfter"] = after_rounded
        result["movePct"] = actual_move
        result["verified"] = result.get("verified") is not False
        rebuilt.append(result)
        after = before_rounded
    rebuilt.reverse()
    return rebuilt


def history_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for event in events:
        started = event_time(event)
        key = event_key(event)
        before = number(event.get("priceBefore"), 0.0)
        after = number(event.get("priceAfter"), 0.0)
        if not started or not key or before <= 0 or after <= 0:
            continue
        try:
            parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            continue
        points.extend([
            {
                "time": iso_utc(parsed - timedelta(seconds=1)),
                "price": round(before, 2),
                "eventId": key,
                "label": str(event.get("name") or "Completed game"),
                "phase": "open",
                "source": "verified-historical-game-backfill",
                "historyType": "verified",
                "movePct": event.get("movePct"),
            },
            {
                "time": started,
                "price": round(after, 2),
                "eventId": key,
                "label": str(event.get("name") or "Completed game"),
                "phase": "close",
                "source": "verified-historical-game-backfill",
                "historyType": "verified",
                "movePct": event.get("movePct"),
            },
        ])
    points.sort(key=lambda value: str(value.get("time") or ""))
    return points[-5000:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--max-athletes", type=int, default=3000)
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
    print(f"Historical games discovered: {len(events):,}")
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
    generated_count = 0
    for index in indexes:
        record = records[index]
        item = evidence.get(index, {})
        if not item.get("ok"):
            continue
        athlete_key = (str(record.get("sourceNamespace") or ""), str(record.get("sourceRecordId") or ""))
        generated = evaluated_game_events(
            record,
            item,
            athlete_events.get(athlete_key, []),
            args.max_game_move_pct,
        )
        if not generated and not existing_events(record):
            continue

        result = dict(record)
        combined = merge_event_evidence(existing_events(result), generated)
        rebuilt = reconstruct_chain(result, combined)
        result["priceEvents"] = rebuilt
        result["priceHistory"] = history_from_events(rebuilt)
        result["priceHistoryStatus"] = "verified-event-backfill"
        result["priceHistoryBackfilledAt"] = iso_utc(now)
        result["priceHistoryBackfillDays"] = args.days
        result["priceHistoryBackfillModel"] = "historical-game-events-v2"
        # marketPrice, previousMarketPrice, dailyChange and live event pointers are
        # intentionally left exactly as they were before this backfill.
        updated[index] = result
        histories += 1
        generated_count += len(generated)

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Backfilled {histories:,} athletes with {generated_count:,} verified historical game events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
