#!/usr/bin/env python3
"""Merge verified historical event evidence into a live TalentX catalog.

Only events explicitly marked ``historicalBackfill`` are imported from the
overlay. Live category records remain authoritative for identity, current price,
current change, explanations and latest-event pointers. After merging evidence,
the durable event price chain is re-anchored backward from the unchanged current
market price so historical charts stay on the current pricing scale.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CATEGORY_ALIASES = {
    "sports": "Athlete",
    "athlete": "Athlete",
    "athletes": "Athlete",
    "music": "Music",
    "actor": "Actor",
    "actors": "Actor",
}
MAX_PRICE_EVENTS = 2500


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_time(event: dict[str, Any]) -> str:
    return str(event.get("startedAt") or event.get("time") or event.get("date") or "").strip()


def move_for(event: dict[str, Any]) -> float:
    explicit = number(event.get("movePct"), float("nan"))
    if explicit == explicit:  # NaN-safe finite-enough check for normal JSON numbers.
        return explicit
    before = number(event.get("priceBefore"), 0.0)
    after = number(event.get("priceAfter"), 0.0)
    if before > 0 and after > 0:
        return (after / before - 1.0) * 100.0
    return 0.0


def merge_events(live: list[dict[str, Any]], overlay: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in live:
        if not isinstance(event, dict):
            continue
        key = event_key(event)
        if key:
            by_key[key] = dict(event)
        else:
            anonymous.append(dict(event))
    for event in overlay:
        if not isinstance(event, dict) or event.get("historicalBackfill") is not True:
            continue
        key = event_key(event)
        if not key or key in by_key:
            continue
        by_key[key] = dict(event)
    combined = [*anonymous, *by_key.values()]
    combined = [event for event in combined if event_time(event)]
    combined.sort(key=event_time)
    return combined[-MAX_PRICE_EVENTS:]


def reconstruct_chain(current_price: float, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    after = max(0.01, current_price)
    rebuilt: list[dict[str, Any]] = []
    for event in reversed(events):
        result = dict(event)
        move = move_for(result)
        denominator = 1.0 + move / 100.0
        before = after / denominator if abs(denominator) > 0.0001 else after
        before_rounded = max(0.01, round(before, 2))
        after_rounded = max(0.01, round(after, 2))
        result["priceBefore"] = before_rounded
        result["priceAfter"] = after_rounded
        result["movePct"] = round((after_rounded / before_rounded - 1.0) * 100.0, 3)
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
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        open_time = (parsed - timedelta(seconds=1)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        points.extend([
            {
                "time": open_time,
                "price": round(before, 2),
                "eventId": key,
                "label": str(event.get("name") or "Verified event"),
                "phase": "open",
                "source": "verified-event-chain",
                "historyType": "verified",
                "movePct": event.get("movePct"),
            },
            {
                "time": started,
                "price": round(after, 2),
                "eventId": key,
                "label": str(event.get("name") or "Verified event"),
                "phase": "close",
                "source": "verified-event-chain",
                "historyType": "verified",
                "movePct": event.get("movePct"),
            },
        ])
    points.sort(key=lambda value: str(value.get("time") or ""))
    return points[-5000:]


def merge_catalog(base: list[dict[str, Any]], overlay: list[dict[str, Any]], category: str) -> tuple[list[dict[str, Any]], int, int]:
    expected = CATEGORY_ALIASES.get(category.lower(), category)
    overlay_by_id = {
        str(record.get("id") or ""): record
        for record in overlay
        if str(record.get("id") or "") and str(record.get("primaryCategory") or "") == expected
    }
    output: list[dict[str, Any]] = []
    touched = 0
    imported = 0
    for record in base:
        result = dict(record)
        if str(result.get("primaryCategory") or "") != expected:
            output.append(result)
            continue
        prior = overlay_by_id.get(str(result.get("id") or ""))
        if prior is None:
            output.append(result)
            continue
        historical = [
            dict(event)
            for event in prior.get("priceEvents", [])
            if isinstance(event, dict) and event.get("historicalBackfill") is True
        ]
        if not historical:
            output.append(result)
            continue
        live_events = [dict(event) for event in result.get("priceEvents", []) if isinstance(event, dict)]
        before_keys = {event_key(event) for event in live_events if event_key(event)}
        merged = merge_events(live_events, historical)
        after_keys = {event_key(event) for event in merged if event_key(event)}
        added = len(after_keys - before_keys)
        if not added:
            output.append(result)
            continue
        current_price = max(0.01, number(result.get("marketPrice"), 0.01))
        result["priceEvents"] = reconstruct_chain(current_price, merged)
        result["priceHistory"] = history_from_events(result["priceEvents"])
        result["priceHistoryStatus"] = "verified-event-backfill"
        result["priceHistoryBackfillDays"] = max(
            int(number(result.get("priceHistoryBackfillDays"), 0)),
            int(number(prior.get("priceHistoryBackfillDays"), 0)),
        )
        result["priceHistoryBackfilledAt"] = prior.get("priceHistoryBackfilledAt") or result.get("priceHistoryBackfilledAt")
        result["priceHistoryBackfillModel"] = prior.get("priceHistoryBackfillModel") or result.get("priceHistoryBackfillModel")
        # Intentionally do not copy marketPrice, previousMarketPrice, dailyChange,
        # hourlyChangePct, trend, lastPriceEvent* or priceExplanation from overlay.
        output.append(result)
        touched += 1
        imported += added
    return output, touched, imported


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--category", required=True)
    args = parser.parse_args()

    base = load_records(args.base)
    overlay = load_records(args.overlay)
    merged, touched, imported = merge_catalog(base, overlay, args.category)
    args.base.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Merged {imported:,} historical events into {touched:,} {args.category} records without changing live market fields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
