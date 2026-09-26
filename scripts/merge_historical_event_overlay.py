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


def is_point_in_time_nfl_event(event: dict[str, Any]) -> bool:
    return (
        event.get("historicalBackfill") is True
        and str(event.get("historicalExpectationMode") or "") == "point-in-time-pre-game"
    )


def is_full_nfl_chart_replay(record: dict[str, Any]) -> bool:
    return (
        str(record.get("leagueOrMedium") or "").strip().upper() == "NFL"
        and str(record.get("priceHistoryStatus") or "") == "source-backed-full-point-in-time-nfl-replay"
    )


def merge_full_nfl_chart_history(
    base_history: list[dict[str, Any]],
    overlay_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay the complete NFL chart replay without altering live market fields."""
    preserved = [
        dict(point)
        for point in base_history
        if isinstance(point, dict)
        and str(point.get("source") or "") != "verified-nfl-event-replay"
        and str(point.get("historyType") or "") != "verified-event-replay"
    ]
    replay = [
        dict(point)
        for point in overlay_history
        if isinstance(point, dict)
        and (
            str(point.get("source") or "") == "verified-nfl-event-replay"
            or str(point.get("historyType") or "") == "verified-event-replay"
        )
    ]
    combined = preserved + replay
    combined.sort(key=lambda item: str(item.get("time") or ""))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for point in combined:
        key = (
            str(point.get("time") or ""),
            str(point.get("eventId") or point.get("eventKey") or ""),
            str(point.get("phase") or ""),
            str(point.get("source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(point)
    return deduped[-5000:]


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    text = event_time(event)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def reconstruct_point_in_time_nfl_history(
    current_price: float,
    historical: list[dict[str, Any]],
    live_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Re-anchor only simulated NFL history; recorded live-event prices stay exact."""
    anchors: list[tuple[datetime, float]] = []
    for event in live_events:
        started = _event_datetime(event)
        before = number(event.get("priceBefore"), 0.0)
        if started is not None and before > 0:
            anchors.append((started, before))
    if anchors:
        anchor_time, after = min(anchors, key=lambda item: item[0])
        candidates = [
            dict(event)
            for event in historical
            if _event_datetime(event) is not None and _event_datetime(event) < anchor_time
        ]
        anchor_type = "earliest-recorded-live-event"
    else:
        after = max(0.01, current_price)
        candidates = [dict(event) for event in historical]
        anchor_type = "current-market-price"

    rebuilt: list[dict[str, Any]] = []
    for event in reversed(sorted(candidates, key=event_time)):
        result = dict(event)
        model_move = number(result.get("modelMovePct"), float("nan"))
        move = model_move if model_move == model_move else move_for(result)
        denominator = 1.0 + move / 100.0
        if denominator <= 0:
            continue
        before = max(0.01, round(after / denominator, 2))
        after_rounded = max(0.01, round(after, 2))
        chart_move = round((after_rounded / before - 1.0) * 100.0, 3)
        result["priceBefore"] = before
        result["priceAfter"] = after_rounded
        result["movePct"] = chart_move
        result["chartMovePct"] = chart_move
        result["chartCorrelationVerified"] = True
        result["verified"] = result.get("verified") is not False
        rebuilt.append(result)
        after = before
    rebuilt.reverse()
    return rebuilt[-MAX_PRICE_EVENTS:], anchor_type


def merge_point_in_time_history_points(
    base_history: list[dict[str, Any]],
    historical_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    historical_keys = {event_key(event) for event in historical_events if event_key(event)}
    preserved: list[dict[str, Any]] = []
    for point in base_history:
        if not isinstance(point, dict):
            continue
        source = str(point.get("source") or "")
        point_key = str(point.get("eventId") or point.get("eventKey") or "")
        if point_key in historical_keys:
            continue
        if source in {"verified-nfl-event-replay", "verified-historical-game-backfill"}:
            continue
        preserved.append(dict(point))
    combined = preserved + history_from_events(historical_events)
    combined.sort(key=lambda item: str(item.get("time") or ""))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for point in combined:
        key = (
            str(point.get("time") or ""),
            str(point.get("eventId") or ""),
            str(point.get("phase") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(point)
    return deduped[-5000:]


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

        base_events = [dict(event) for event in result.get("priceEvents", []) if isinstance(event, dict)]
        current_price = max(0.01, number(result.get("marketPrice"), 0.01))
        full_nfl_replay = is_full_nfl_chart_replay(prior)
        specialized_nfl = (
            str(result.get("leagueOrMedium") or "").strip().upper() == "NFL"
            and any(is_point_in_time_nfl_event(event) for event in historical)
        )

        if full_nfl_replay:
            live_events = [event for event in base_events if event.get("historicalBackfill") is not True]
            live_keys = {event_key(event) for event in live_events if event_key(event)}
            missing_history_events = [
                dict(event)
                for event in historical
                if event_key(event) and event_key(event) not in live_keys
            ]
            combined = sorted([*missing_history_events, *live_events], key=event_time)
            result["priceEvents"] = combined[-MAX_PRICE_EVENTS:]
            base_history = result.get("priceHistory") if isinstance(result.get("priceHistory"), list) else []
            overlay_history = prior.get("priceHistory") if isinstance(prior.get("priceHistory"), list) else []
            result["priceHistory"] = merge_full_nfl_chart_history(base_history, overlay_history)
            result["priceHistoryStatus"] = "source-backed-full-point-in-time-nfl-replay"
            if prior.get("priceHistoryDisclosure"):
                result["priceHistoryDisclosure"] = prior.get("priceHistoryDisclosure")
            added = len(missing_history_events)
            for field in (
                "nflHistoricalBackfillVersion",
                "nflHistoricalBackfilledAt",
                "nflHistoricalBackfillDays",
                "nflHistoricalBackfillEventCount",
                "nflHistoricalBackfillImportedEventCount",
                "nflHistoricalBackfillChartPointCount",
                "nflHistoricalBackfillModel",
                "nflHistoricalBackfillFirstEventAt",
                "nflHistoricalBackfillLastEventAt",
                "nflHistoricalBackfillAnchor",
            ):
                if field in prior:
                    result[field] = prior[field]
        elif specialized_nfl:
            # Current Sports state owns recorded live events. Historical overlays
            # may extend farther back, but they must never reprice those live events.
            live_events = [event for event in base_events if event.get("historicalBackfill") is not True]
            base_specialized = [event for event in base_events if is_point_in_time_nfl_event(event)]
            historical_by_key = {
                event_key(event): dict(event)
                for event in historical
                if is_point_in_time_nfl_event(event) and event_key(event)
            }
            # Prefer current-base replay metadata for overlapping events; overlay
            # contributes only missing/older verified history.
            for event in base_specialized:
                key = event_key(event)
                if key:
                    historical_by_key[key] = dict(event)
            historical_union = sorted(historical_by_key.values(), key=event_time)
            base_hist_keys = {event_key(event) for event in base_specialized if event_key(event)}
            replay, anchor_type = reconstruct_point_in_time_nfl_history(
                current_price,
                historical_union,
                live_events,
            )
            replay_keys = {event_key(event) for event in replay if event_key(event)}
            added = len(replay_keys - base_hist_keys)
            if not added and len(replay) <= len(base_specialized):
                output.append(result)
                continue
            combined = sorted([*replay, *live_events], key=event_time)
            result["priceEvents"] = combined[-MAX_PRICE_EVENTS:]
            base_history = result.get("priceHistory") if isinstance(result.get("priceHistory"), list) else []
            result["priceHistory"] = merge_point_in_time_history_points(base_history, replay)
            result["priceHistoryStatus"] = "source-backed-point-in-time-nfl-replay"
            result["nflHistoricalBackfillAnchor"] = anchor_type
            for field in (
                "nflHistoricalBackfillVersion",
                "nflHistoricalBackfilledAt",
                "nflHistoricalBackfillDays",
                "nflHistoricalBackfillEventCount",
                "nflHistoricalBackfillChartPointCount",
                "nflHistoricalBackfillModel",
                "nflHistoricalBackfillFirstEventAt",
                "nflHistoricalBackfillLastEventAt",
            ):
                if field in prior:
                    result[field] = prior[field]
        else:
            before_keys = {event_key(event) for event in base_events if event_key(event)}
            merged = merge_events(base_events, historical)
            after_keys = {event_key(event) for event in merged if event_key(event)}
            added = len(after_keys - before_keys)
            if not added:
                output.append(result)
                continue
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
