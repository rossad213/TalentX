#!/usr/bin/env python3
"""Build and maintain dated TalentX price history.

Verified game or market events are appended as permanent dated points. When a
record has an older undated ``trend`` but no dated history yet, the trend is
mapped across the prior six months as a clearly marked reconstructed baseline.
This gives the range filters useful history immediately without pretending the
legacy points were observed on exact dates.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MAX_HISTORY_POINTS = 2500
MAX_HISTORY_AGE_DAYS = 3650
RECONSTRUCTION_DAYS = 182


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
                "historyType": str(item.get("historyType") or "verified"),
                "eventType": str(item.get("eventType") or "market"),
            }
        )
    return output


def reconstructed_baseline(record: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    raw = record.get("trend")
    if not isinstance(raw, list):
        return []
    values = [number(value) for value in raw]
    values = [value for value in values if value is not None]
    if len(values) < 2:
        return []

    current = number(record.get("marketPrice"))
    if current is not None:
        values[-1] = current

    end = parse_time(record.get("lastPriceRefreshAt")) or now
    start = end - timedelta(days=RECONSTRUCTION_DAYS)
    span = max(1, len(values) - 1)
    return [
        {
            "time": iso_time(start + (end - start) * (index / span)),
            "price": round(value, 2),
            "eventId": f"reconstructed:{index}",
            "label": "Reconstructed from saved TalentX market trend",
            "phase": "close",
            "historyType": "reconstructed",
            "eventType": "historical-baseline",
        }
        for index, value in enumerate(values)
    ]


def append_supported_major_events(record: dict[str, Any], history: list[dict[str, Any]]) -> bool:
    """Append dated non-game events only when an explicit price or impact exists.

    Supported inputs may come from future injury, trade, award, suspension,
    retirement, draft, free-agency, record, playoff, or championship feeds.
    No movement is invented merely from a headline.
    """
    sources = [record.get("majorEvents"), record.get("careerEvents"), record.get("marketEvents")]
    events = next((source for source in sources if isinstance(source, list)), [])
    if not events:
        return False

    changed = False
    known_ids = {str(item.get("eventId")) for item in history}
    prior_price = history[-1]["price"] if history else number(record.get("previousMarketPrice") or record.get("marketPrice"))
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        when = parse_time(event.get("time") or event.get("date") or event.get("occurredAt"))
        if when is None:
            continue
        event_id = str(event.get("eventId") or event.get("id") or f"major:{iso_time(when)}:{index}")
        if event_id in known_ids:
            continue
        explicit_price = number(event.get("price") or event.get("priceAfter"))
        try:
            impact = float(event.get("impactPct"))
        except (TypeError, ValueError):
            impact = 0.0
        if explicit_price is None and (prior_price is None or abs(impact) < 0.0001):
            continue
        next_price = explicit_price or round(float(prior_price) * (1 + max(-10.0, min(10.0, impact)) / 100), 2)
        history.append(
            {
                "time": iso_time(when),
                "price": round(next_price, 2),
                "eventId": event_id,
                "label": str(event.get("label") or event.get("headline") or event.get("type") or "Major career event"),
                "phase": "close",
                "historyType": "verified",
                "eventType": str(event.get("type") or "career-event"),
            }
        )
        known_ids.add(event_id)
        prior_price = next_price
        changed = True
    return changed


def append_record_history(record: dict[str, Any], now: datetime) -> tuple[dict[str, Any], bool]:
    result = dict(record)
    history = existing_history(result)
    seeded = False
    if not history:
        history = reconstructed_baseline(result, now)
        seeded = bool(history)

    event_id = str(result.get("lastPriceEventId") or "").strip()
    event_time = parse_time(result.get("lastPriceEventAt"))
    current_price = number(result.get("marketPrice"))
    previous_price = number(result.get("previousMarketPrice"))
    label = str(result.get("lastPriceEvent") or "Completed game")

    changed = seeded
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
                        "historyType": "verified",
                        "eventType": "game",
                    }
                )
            history.append(
                {
                    "time": iso_time(event_time),
                    "price": round(current_price, 2),
                    "eventId": event_id,
                    "label": label,
                    "phase": "close",
                    "historyType": "verified",
                    "eventType": "game",
                }
            )
            changed = True

    changed = append_supported_major_events(result, history) or changed

    if current_price is not None:
        latest_time = parse_time(result.get("lastPriceRefreshAt")) or now
        history.append(
            {
                "time": iso_time(latest_time),
                "price": round(current_price, 2),
                "eventId": "current-market-price",
                "label": "Current TalentX market price",
                "phase": "close",
                "historyType": "verified",
                "eventType": "market",
            }
        )

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
    kinds = {str(item.get("historyType")) for item in result["priceHistory"]}
    result["priceHistoryStatus"] = "verified-and-reconstructed" if len(kinds) > 1 else next(iter(kinds), "unavailable")
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
    changed_count = 0
    with_history = 0
    reconstructed = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        record, changed = append_record_history(item, now)
        updated.append(record)
        changed_count += int(changed)
        with_history += int(bool(record.get("priceHistory")))
        reconstructed += int(any(point.get("historyType") == "reconstructed" for point in record.get("priceHistory", [])))

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"Updated {changed_count:,} histories; {with_history:,} records contain dated history; "
        f"{reconstructed:,} include a reconstructed six-month baseline."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
