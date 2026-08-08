#!/usr/bin/env python3
"""Convert durable TalentX price events into permanent dated priceHistory points."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


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


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def price(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 2) if parsed > 0 else None


def append_events(record: dict[str, Any]) -> tuple[dict[str, Any], int]:
    result = dict(record)
    history = [dict(item) for item in result.get("priceHistory", []) if isinstance(item, dict)]
    events = [dict(item) for item in result.get("priceEvents", []) if isinstance(item, dict)]
    existing = {
        (str(item.get("eventId") or item.get("eventKey") or ""), str(item.get("phase") or ""))
        for item in history
    }
    added = 0
    for event in events:
        event_key = str(event.get("eventKey") or event.get("eventId") or "").strip()
        when = parse_time(event.get("startedAt") or event.get("time") or event.get("date"))
        before = price(event.get("priceBefore"))
        after = price(event.get("priceAfter"))
        if not event_key or when is None or after is None:
            continue
        label = str(event.get("name") or "Verified career event")
        event_type = str(event.get("eventType") or "game")
        if before is not None and (event_key, "open") not in existing:
            history.append({
                "time": iso(when - timedelta(seconds=1)),
                "price": before,
                "eventId": event_key,
                "label": label,
                "phase": "open",
                "historyType": "verified",
                "eventType": event_type,
            })
            existing.add((event_key, "open"))
            added += 1
        if (event_key, "close") not in existing:
            history.append({
                "time": iso(when),
                "price": after,
                "eventId": event_key,
                "label": label,
                "phase": "close",
                "historyType": "verified",
                "eventType": event_type,
            })
            existing.add((event_key, "close"))
            added += 1
    history.sort(key=lambda item: str(item.get("time") or ""))
    result["priceHistory"] = history[-2500:]
    return result, added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    output = []
    total = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        record, added = append_events(item)
        output.append(record)
        total += added
    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Appended {total:,} verified event price-history points.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
