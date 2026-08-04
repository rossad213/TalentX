#!/usr/bin/env python3
"""Append verified event-driven prices to each TalentX player record.

This script runs after the hourly pricing refresh. It records the price before
and after each newly processed event, deduplicates by event ID, and keeps the
history sorted so chart filters can display real dated movement.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MAX_HISTORY_POINTS = 2500
MAX_HISTORY_AGE_DAYS = 3650


def parse_time(value: Any) -> datetime | None:
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


def iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def existing_history(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("priceHistory")
    if not isinstance(raw, list):
        return []
    output: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        timestamp = parse_time(item.get("time") or item.get("date") or item.get("timestamp"))
        price = number(item.get("price") or item.get("value") or item.get("marketPrice"))
        if timestamp is None or price is None:
            continue
        output.append(
            {
                "time": iso_time(timestamp),
                "price": round(price, 2),
                "eventId": str(item.get("eventId") or item.get("eventKey") or ""),
                "label": str(item.get("label") or item.get("event") or ""),
                "phase": str(item.get("phase") or "close"),
            }
        )
    return output


def append_record_history(record: dict[str, Any], now: datetime) -> tuple[dict[str, Any], bool]:
    result = dict(record)
    history = existing_history(result)
    event_id = str(result.get("lastPriceEventId") or "").strip()
    event_time = parse_time(result.get("lastPriceEventAt"))
    current_price = number(result.get("marketPrice"))
    previous_price = number(result.get("previousMarketPrice"))
    label = str(result.get("lastPriceEvent") or "Completed game")

    changed = False
    if event_id and event_time is not None and current_price is not None:
        already_recorded = any(item.get("eventId") == event_id and item.get("phase") == "close" for item in history)
        if not already_recorded:
            if previous_price is not None:
                history.append(
                    {
                        "time": iso_time(event_time - timedelta(seconds=1)),
                        "price": round(previous_price, 2),
                        "eventId": event_id,
                        "label": label,
                        "phase": "open",
                    }
                )
            history.append(
                {
                    "time": iso_time(event_time),
                    "price": round(current_price, 2),
                    "eventId": event_id,
                    "label": label,
                    "phase": "close",
                }
            )
            changed = True

    cutoff = now - timedelta(days=MAX_HISTORY_AGE_DAYS)
    history = [item for item in history if (parse_time(item.get("time")) or now) >= cutoff]
    history.sort(key=lambda item: item.get("time", ""))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in history:
        key = (str(item.get("time")), str(item.get("eventId")), str(item.get("phase")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    result["priceHistory"] = deduped[-MAX_HISTORY_POINTS:]
    return result, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")

    now = datetime.now(timezone.utc)
    updated: list[dict[str, Any]] = []
    appended = 0
    with_history = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        record, changed = append_record_history(item, now)
        updated.append(record)
        appended += int(changed)
        with_history += int(bool(record.get("priceHistory")))

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Appended {appended:,} new dated price events; {with_history:,} records now contain price history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
