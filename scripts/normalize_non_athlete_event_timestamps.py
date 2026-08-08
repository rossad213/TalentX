#!/usr/bin/env python3
"""Normalize newly verified future-project events to their verification time."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
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


def normalize_record(record: dict[str, Any], now: datetime) -> tuple[dict[str, Any], int]:
    result = dict(record)
    raw_events = result.get("priceEvents")
    if not isinstance(raw_events, list):
        return result, 0
    events: list[dict[str, Any]] = []
    changed = 0
    latest_id = str(result.get("lastPriceEventId") or "")
    verified_at = parse_time(result.get("lastPriceRefreshAt")) or now
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        event = dict(item)
        when = parse_time(event.get("startedAt"))
        if event.get("eventType") == "actor-upcoming-project" and when and when > now:
            event["scheduledFor"] = event.get("startedAt")
            event["startedAt"] = verified_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if str(event.get("eventKey") or "") == latest_id:
                result["lastPriceEventAt"] = event["startedAt"]
                explanation = result.get("priceExplanation")
                if isinstance(explanation, dict):
                    explanation = dict(explanation)
                    explanation["eventAt"] = event["startedAt"]
                    explanation["scheduledFor"] = event["scheduledFor"]
                    result["priceExplanation"] = explanation
            changed += 1
        events.append(event)
    result["priceEvents"] = events
    return result, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    now = datetime.now(timezone.utc)
    output = []
    changed = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        record, count = normalize_record(item, now)
        output.append(record)
        changed += count
    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Normalized {changed:,} upcoming-project event timestamp(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
