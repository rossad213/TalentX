#!/usr/bin/env python3
"""Remove duplicate Actor price events that were applied more than once.

Older non-athlete outcome refreshes could discover the same real-world Actor
outcome through more than one base event in a single run. The duplicated event
key was then compounded more than once. This repair keeps one canonical event,
reverses only the extra multiplicative moves, rebases the retained event ledger,
and removes invalid post-duplicate market-observation history.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPAIR_VERSION = "1.0-actor-duplicate-outcome-ledger"


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_move(event: dict[str, Any]) -> float:
    move = number(event.get("movePct"), float("nan"))
    if math.isfinite(move):
        return move
    before = number(event.get("priceBefore"), 0)
    after = number(event.get("priceAfter"), 0)
    if before > 0 and after > 0:
        return (after / before - 1.0) * 100.0
    return 0.0


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


def repair_actor_record(record: dict[str, Any]) -> tuple[dict[str, Any], int]:
    if str(record.get("primaryCategory") or "") != "Actor":
        return dict(record), 0
    events = [dict(item) for item in record.get("priceEvents", []) if isinstance(item, dict)]
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, event in enumerate(events):
        key = event_key(event)
        if key:
            groups.setdefault(key, []).append((index, event))

    duplicate_groups = {key: values for key, values in groups.items() if len(values) > 1}
    if not duplicate_groups:
        return dict(record), 0

    keep_indexes: set[int] = set(range(len(events)))
    removed: list[dict[str, Any]] = []
    earliest_duplicate: datetime | None = None
    for values in duplicate_groups.values():
        winner_index, _winner = max(
            values,
            key=lambda pair: (
                abs(event_move(pair[1])),
                str(pair[1].get("startedAt") or pair[1].get("time") or ""),
                pair[0],
            ),
        )
        for index, event in values:
            when = parse_time(event.get("startedAt") or event.get("time"))
            if when is not None and (earliest_duplicate is None or when < earliest_duplicate):
                earliest_duplicate = when
            if index == winner_index:
                continue
            keep_indexes.discard(index)
            removed.append(event)

    correction = 1.0
    for event in removed:
        move = event_move(event)
        factor = 1.0 + move / 100.0
        if factor > 0:
            correction *= factor

    result = dict(record)
    current = max(0.01, number(result.get("marketPrice"), 0.01))
    corrected_current = round(current / correction, 2) if correction > 0 else round(current, 2)
    retained = [events[index] for index in sorted(keep_indexes)]
    retained.sort(key=lambda item: str(item.get("startedAt") or item.get("time") or ""))

    # Rebase retained event prices backward from the corrected current market
    # price so the durable ledger remains continuous after duplicate removal.
    after = corrected_current
    for event in reversed(retained):
        move = event_move(event)
        denominator = 1.0 + move / 100.0
        if denominator <= 0:
            continue
        before = after / denominator
        event["priceAfter"] = round(after, 2)
        event["priceBefore"] = round(before, 2)
        if event["priceBefore"] > 0:
            event["movePct"] = round((event["priceAfter"] / event["priceBefore"] - 1.0) * 100.0, 3)
        after = before

    result["priceEvents"] = retained
    result["marketPrice"] = corrected_current
    result["dailyChange"] = 0.0
    result["hourlyChangePct"] = 0.0
    result["actorOutcomeLedgerRepairVersion"] = REPAIR_VERSION
    result["actorDuplicateOutcomeEventsRemoved"] = int(result.get("actorDuplicateOutcomeEventsRemoved") or 0) + len(removed)

    if retained:
        latest = retained[-1]
        result["previousMarketPrice"] = round(number(latest.get("priceBefore"), corrected_current), 2)
        result["lastPriceEventAt"] = latest.get("startedAt") or latest.get("time")
        result["lastPriceEvent"] = latest.get("name")
        result["lastPriceEventId"] = event_key(latest)
        result["lastEventMovePct"] = latest.get("movePct")
        result["lastEventType"] = latest.get("eventType")
        result["lastEventSource"] = latest.get("provider")
        result["trend"] = [
            round(number(event.get("priceAfter"), corrected_current), 2)
            for event in retained[-18:]
            if number(event.get("priceAfter"), 0) > 0
        ] or [corrected_current]
    else:
        result["previousMarketPrice"] = corrected_current
        result["trend"] = [corrected_current]
        for field in ("lastPriceEventAt", "lastPriceEvent", "lastPriceEventId", "lastEventMovePct", "lastEventType", "lastEventSource"):
            result.pop(field, None)

    retained_by_key = {event_key(event): event for event in retained if event_key(event)}
    repaired_history: list[dict[str, Any]] = []
    seen_history: set[tuple[str, str, str]] = set()
    for raw in result.get("priceHistory", []) if isinstance(result.get("priceHistory"), list) else []:
        if not isinstance(raw, dict):
            continue
        point = dict(raw)
        when = parse_time(point.get("time") or point.get("date"))
        if (
            earliest_duplicate is not None
            and when is not None
            and when >= earliest_duplicate
            and str(point.get("eventId") or "") == "current-market-price"
        ):
            continue
        key = str(point.get("eventId") or point.get("eventKey") or "")
        linked = retained_by_key.get(key)
        if linked is not None:
            if str(point.get("phase") or "") == "open":
                point["price"] = linked.get("priceBefore")
            else:
                point["price"] = linked.get("priceAfter")
        history_key = (str(point.get("time") or point.get("date") or ""), key, str(point.get("phase") or ""))
        if history_key in seen_history:
            continue
        seen_history.add(history_key)
        repaired_history.append(point)
    result["priceHistory"] = repaired_history
    return result, len(removed)


def repair_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    output: list[dict[str, Any]] = []
    changed_records = 0
    removed_events = 0
    for record in records:
        repaired, removed = repair_actor_record(record)
        output.append(repaired)
        if removed:
            changed_records += 1
            removed_events += removed
    return output, changed_records, removed_events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    repaired, changed, removed = repair_records(records)
    args.catalog.write_text(json.dumps(repaired, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Actor duplicate outcome repair: {changed:,} record(s) repaired; {removed:,} duplicate event(s) removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
