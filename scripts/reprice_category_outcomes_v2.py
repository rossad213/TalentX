#!/usr/bin/env python3
"""One-time/idempotent repricing of existing verified category outcome events.

The live adapters now create events with the v2 calibration directly. This script
migrates older durable outcome events in place, rescales later event prices, and
updates matching chart points. Events are stamped so reruns cannot reprice them.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market_outcome_calibration_v2 import (
    MODEL_VERSION,
    actor_box_office_target,
    amplify_legacy_direct_actor_target,
    music_chart_target,
    music_movement_target,
)


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


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


def category_event(category: str, event_type: str) -> bool:
    if category == "music":
        return event_type == "music-chart-outcome"
    if category == "actors":
        return event_type in {"actor-box-office-outcome", "actor-streaming-outcome"}
    if category == "creators":
        return event_type == "creator-youtube-outcome"
    return False


def calibrated_target(event: dict[str, Any], now: datetime) -> float | None:
    event_type = str(event.get("eventType") or "")
    old_target = number(event.get("targetOutcomeMovePct"), 0.0)

    if event_type == "music-chart-outcome":
        rank = int(number(event.get("chartRank"), 0))
        if rank <= 0:
            return None
        previous_raw = event.get("previousChartRank")
        previous = int(number(previous_raw, 0)) if previous_raw not in {None, ""} else None
        status = str(event.get("chartStatus") or "ranked").lower()
        if previous is not None or status in {"new", "re-entry"}:
            return music_movement_target(rank, previous, status)
        return music_chart_target(rank)[1]

    if event_type == "actor-box-office-outcome":
        ratio = number(event.get("boxOfficeToCostRatio"), 0.0)
        if ratio > 0:
            occurred = parse_time(event.get("startedAt"))
            age_days = max(0.0, (now - occurred).total_seconds() / 86400.0) if occurred else 30.0
            result = actor_box_office_target(ratio, age_days)
            return result[1] if result else None
        return amplify_legacy_direct_actor_target(old_target)

    if event_type == "actor-streaming-outcome":
        return amplify_legacy_direct_actor_target(old_target)

    if event_type == "creator-youtube-outcome":
        if old_target > 0:
            return old_target * 2.0
        if old_target < 0:
            return old_target * 1.5
        return None

    return None


def reprice_record(record: dict[str, Any], category: str, now: datetime) -> tuple[dict[str, Any], int]:
    result = dict(record)
    raw_events = result.get("priceEvents") if isinstance(result.get("priceEvents"), list) else []
    events = [dict(item) for item in raw_events if isinstance(item, dict)]
    if not events:
        return result, 0

    migrated: set[str] = set()
    desired_moves: dict[str, float] = {}
    for event in events:
        event_type = str(event.get("eventType") or "")
        if not category_event(category, event_type):
            continue
        if str(event.get("pricingCalibrationVersion") or "") == MODEL_VERSION:
            continue
        if event.get("verified") is False:
            continue
        target = calibrated_target(event, now)
        if target is None:
            continue
        old_target = number(event.get("targetOutcomeMovePct"), 0.0)
        old_move = number(event.get("movePct"), 0.0)
        effective_multiplier = old_move / old_target if abs(old_target) >= 0.001 else 1.0
        effective_multiplier = min(1.35, max(0.65, effective_multiplier))
        desired = target * effective_multiplier
        key = str(event.get("eventKey") or event.get("eventId") or "")
        if not key:
            continue
        desired_moves[key] = desired
        migrated.add(key)
        event["targetOutcomeMovePct"] = round(target, 4)
        event["pricingCalibrationVersion"] = MODEL_VERSION

    if not migrated:
        return result, 0

    indexed = sorted(
        events,
        key=lambda item: (str(item.get("startedAt") or ""), str(item.get("eventKey") or item.get("eventId") or "")),
    )
    scale = 1.0
    for event in indexed:
        key = str(event.get("eventKey") or event.get("eventId") or "")
        old_before = number(event.get("priceBefore"), 0.0)
        old_after = number(event.get("priceAfter"), 0.0)
        old_move = number(event.get("movePct"), 0.0)
        if old_before <= 0 or old_after <= 0:
            if key in desired_moves:
                event["movePct"] = round(desired_moves[key], 3)
            continue

        new_before = max(0.01, round(old_before * scale, 2))
        move = desired_moves.get(key, old_move)
        new_after = max(0.01, round(new_before * (1.0 + move / 100.0), 2))
        event["priceBefore"] = new_before
        event["priceAfter"] = new_after
        event["movePct"] = round((new_after / new_before - 1.0) * 100.0, 3)
        scale = new_after / old_after

    result["priceEvents"] = indexed
    old_market = number(result.get("marketPrice"), 0.01)
    result["marketPrice"] = max(0.01, round(old_market * scale, 2))
    result["pricingCalibrationVersion"] = MODEL_VERSION

    event_map = {
        str(event.get("eventKey") or event.get("eventId") or ""): event
        for event in indexed
        if str(event.get("eventKey") or event.get("eventId") or "")
    }
    history = [dict(item) for item in result.get("priceHistory", []) if isinstance(item, dict)]
    for point in history:
        event = event_map.get(str(point.get("eventId") or ""))
        if not event:
            continue
        phase = str(point.get("phase") or "")
        if phase == "open" and number(event.get("priceBefore"), 0) > 0:
            point["price"] = round(number(event.get("priceBefore")), 2)
        elif phase == "close" and number(event.get("priceAfter"), 0) > 0:
            point["price"] = round(number(event.get("priceAfter")), 2)
    result["priceHistory"] = history

    latest = indexed[-1] if indexed else None
    if latest:
        result["previousMarketPrice"] = round(number(latest.get("priceBefore"), result.get("previousMarketPrice", old_market)), 2)
        result["lastPriceEventAt"] = latest.get("startedAt") or result.get("lastPriceEventAt")
        result["lastPriceEvent"] = latest.get("name") or result.get("lastPriceEvent")
        result["lastPriceEventId"] = latest.get("eventKey") or latest.get("eventId") or result.get("lastPriceEventId")
        result["hourlyChangePct"] = round(number(latest.get("movePct"), result.get("hourlyChangePct", 0)), 3)
        result["dailyChange"] = round(number(latest.get("movePct"), result.get("dailyChange", 0)), 3)

    return result, len(migrated)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--category", choices=("music", "actors", "creators"), required=True)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")

    now = datetime.now(timezone.utc)
    output: list[dict[str, Any]] = []
    records_changed = 0
    events_changed = 0
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        record, changed = reprice_record(raw, args.category, now)
        output.append(record)
        if changed:
            records_changed += 1
            events_changed += changed

    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"Category calibration {MODEL_VERSION}: repriced {events_changed:,} existing verified "
        f"outcome events across {records_changed:,} {args.category} records."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
