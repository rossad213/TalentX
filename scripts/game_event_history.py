#!/usr/bin/env python3
"""Helpers for durable, game-by-game TalentX market history."""
from __future__ import annotations

from typing import Any

MAX_PRICE_EVENTS = 1000


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def attach_price_events(
    old_record: dict[str, Any],
    result: dict[str, Any],
    event_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist every comparable priced game instead of only the latest game."""
    updated = dict(result)
    existing = old_record.get("priceEvents") if isinstance(old_record.get("priceEvents"), list) else []
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in existing:
        if not isinstance(item, dict):
            continue
        key = str(item.get("eventKey") or item.get("eventId") or "").strip()
        if not key:
            continue
        by_key[key] = dict(item)
        order.append(key)

    running_price = number(old_record.get("marketPrice")) or number(result.get("previousMarketPrice")) or number(result.get("marketPrice")) or 1.0
    for event in sorted(event_results, key=lambda item: str(item.get("startedAt") or "")):
        if not isinstance(event, dict) or not event.get("comparable"):
            continue
        after = number(event.get("priceAfter"))
        if after is None:
            continue
        key = str(event.get("eventKey") or event.get("eventId") or "").strip()
        if not key:
            continue
        before = running_price
        move = event.get("movePct")
        try:
            move_pct = float(move)
        except (TypeError, ValueError):
            move_pct = round((after / before - 1.0) * 100.0, 3) if before else 0.0
        stored = {
            "eventKey": key,
            "eventId": str(event.get("eventId") or key),
            "provider": event.get("provider"),
            "league": event.get("league"),
            "name": event.get("name") or "Completed game",
            "startedAt": event.get("startedAt"),
            "priceBefore": round(float(before), 2),
            "priceAfter": round(float(after), 2),
            "movePct": round(float(move_pct), 3),
            "performanceDeltaPct": event.get("performanceDeltaPct"),
            "productionDeltaPct": event.get("productionDeltaPct"),
            "efficiencyDeltaPct": event.get("efficiencyDeltaPct"),
            "outcomeMovePct": event.get("outcomeMovePct"),
            "stats": event.get("stats") if isinstance(event.get("stats"), dict) else {},
            "teamWon": event.get("teamWon"),
            "eventType": "game",
            "verified": True,
        }
        if key not in by_key:
            order.append(key)
        by_key[key] = stored
        running_price = after

    updated["priceEvents"] = [by_key[key] for key in order if key in by_key][-MAX_PRICE_EVENTS:]
    return updated
