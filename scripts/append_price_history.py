#!/usr/bin/env python3
"""Maintain dated TalentX price history from actual recorded events/observations.

Only dated TalentX observations and explicitly supported dated events are stored.
Legacy undated trend arrays are never mapped onto invented calendar dates. If a
profile lacks dated history, it remains without dated history until a real,
source-backed event or a recorded TalentX observation exists.
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
        # Purge legacy reconstructed/synthetic dated points as they pass through.
        history_type = str(item.get("historyType") or "verified").strip().lower()
        if history_type in {"reconstructed", "synthetic"} or item.get("reconstructed") is True or item.get("synthetic") is True:
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
                **({"source": item.get("source")} if item.get("source") else {}),
                **({"provider": item.get("provider")} if item.get("provider") else {}),
                **({"priceBasis": item.get("priceBasis")} if item.get("priceBasis") else {}),
            }
        )
    return output


def append_supported_major_events(record: dict[str, Any], history: list[dict[str, Any]]) -> bool:
    """Append dated non-game events only when explicit supported impact exists.

    The upstream event itself must already carry a real date. This utility does
    not infer dates or fabricate movement merely from a headline.
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
        if event.get("verified") is False or event.get("synthetic") is True or event.get("reconstructed") is True:
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
                **({"source": event.get("sourceUrl")} if event.get("sourceUrl") else {}),
                **({"provider": event.get("provider")} if event.get("provider") else {}),
            }
        )
        known_ids.add(event_id)
        prior_price = next_price
        changed = True
    return changed


def append_record_history(record: dict[str, Any], now: datetime) -> tuple[dict[str, Any], bool]:
    result = dict(record)
    original_history = result.get("priceHistory") if isinstance(result.get("priceHistory"), list) else []
    history = existing_history(result)
    removed_legacy = len(history) != len(original_history)

    event_id = str(result.get("lastPriceEventId") or "").strip()
    event_time = parse_time(result.get("lastPriceEventAt"))
    current_price = number(result.get("marketPrice"))
    previous_price = number(result.get("previousMarketPrice"))
    label = str(result.get("lastPriceEvent") or "Completed event")

    changed = removed_legacy
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
                        "eventType": "recorded-event",
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
                    "eventType": "recorded-event",
                }
            )
            changed = True

    changed = append_supported_major_events(result, history) or changed

    # A current-market observation is a real TalentX observation at a real time;
    # it is not historical backfill and does not imply a past event.
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
                "eventType": "market-observation",
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
    result["priceHistoryStatus"] = "verified" if result["priceHistory"] else "unavailable"
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
    for item in payload:
        if not isinstance(item, dict):
            continue
        record, changed = append_record_history(item, now)
        updated.append(record)
        changed_count += int(changed)
        with_history += int(bool(record.get("priceHistory")))

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"Updated {changed_count:,} histories; {with_history:,} records contain dated history; "
        "0 reconstructed dated baselines retained."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
