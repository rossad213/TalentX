#!/usr/bin/env python3
"""Repair persisted NFL reserve-opportunity price spikes without repricing normal players.

Some NFL reserve players received a large one-game workload because of an injury or
short-term replacement role. Older TalentX event records could compare that full-game
production with a tiny historical per-game workload, creating an outsized price jump.
Later legitimate moves then compounded from the inflated price.

This repair is deliberately narrow:
* only NFL game events carrying the reserve/opportunity signature are recalculated;
* corrected result moves use the same NFL result curve and volatility sensitivity as
  the live pricing engine;
* later stored event percentages are replayed from the corrected base price;
* normal NFL listings and non-NFL records are untouched;
* priceHistory, trend, and latest displayed movement are rebased with the price chain.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import hourly_price_refresh_nfl as nfl
import hourly_price_refresh_nfl_opportunity as opportunity
from category_market_store import load_records, write_records
from results_event_pricing import result_move_from_delta, result_sensitivity

REPAIR_VERSION = "1.0-nfl-persisted-opportunity-history-rebase"


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


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


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_aliases(event: dict[str, Any]) -> set[str]:
    return {
        str(value).strip()
        for value in (event.get("eventKey"), event.get("eventId"))
        if str(value or "").strip()
    }


def stored_move(event: dict[str, Any]) -> float | None:
    move = finite(event.get("movePct"))
    if move is not None and move > -100.0:
        return move
    before = finite(event.get("priceBefore"))
    after = finite(event.get("priceAfter"))
    if before is None or before <= 0 or after is None or after <= 0:
        return None
    return (after / before - 1.0) * 100.0


def opportunity_signature(record: dict[str, Any], event: dict[str, Any]) -> bool:
    if str(record.get("leagueOrMedium") or "").upper() != "NFL":
        return False
    if str(event.get("eventType") or "").lower() != "game":
        return False

    migrated_at = parse_time(record.get("nflMarketMigratedAt"))
    event_time = parse_time(event.get("startedAt"))
    if migrated_at is not None and event_time is not None and event_time <= migrated_at:
        return False

    role_floor = opportunity.replacement_baseline_score(record)
    personal_expected = finite(event.get("personalExpectedPerformanceScore"))
    expected = finite(event.get("expectedPerformanceScore"))
    delta = abs(finite(event.get("performanceDeltaPct")) or 0.0)
    games = max(0.0, finite(record.get("professionalGames")) or 0.0)

    # Strongest signal: an earlier protection pass already identified a tiny
    # reserve denominator. Revisit it so an old inflated priceBefore/priceAfter
    # chain can be rebased even when the evidence fields themselves were fixed.
    if event.get("opportunityFloorApplied") is True:
        if personal_expected is not None and 0 < personal_expected < role_floor:
            return True
        if expected is not None and 0 < expected <= role_floor:
            return True

    # Legacy events may predate the explicit opportunity markers.
    if expected is not None and 0 < expected < role_floor and delta >= 150.0 and games < 100.0:
        return True
    return delta >= 400.0 and games < 80.0 and expected is not None and expected < role_floor


def corrected_delta(record: dict[str, Any], event: dict[str, Any]) -> float | None:
    role_floor = opportunity.replacement_baseline_score(record)
    actual = finite(event.get("actualPerformanceScore"))
    personal_expected = finite(event.get("personalExpectedPerformanceScore"))
    current_delta = finite(event.get("performanceDeltaPct"))

    if actual is not None and actual > 0 and personal_expected is not None and personal_expected > 0:
        effective_expected = max(personal_expected, min(role_floor, actual))
        if effective_expected > 0:
            return (actual / effective_expected - 1.0) * 100.0

    # If a prior protection pass already rewrote the event's performance delta,
    # that corrected delta is safe to reuse for the deterministic price rebase.
    if event.get("opportunityFloorApplied") is True and current_delta is not None:
        return current_delta
    return None


def corrected_move(record: dict[str, Any], event: dict[str, Any]) -> tuple[float, float] | None:
    delta = corrected_delta(record, event)
    if delta is None:
        return None
    started = parse_time(event.get("startedAt"))
    scale = 0.80 if started is not None and started.month in {7, 8} else nfl.NFL_RESULT_SCALE
    _, sensitivity = result_sensitivity(record)
    performance_move = result_move_from_delta(delta, scale=scale) * sensitivity
    outcome_move = 0.06 if event.get("teamWon") is True else -0.05 if event.get("teamWon") is False else 0.0
    return round(performance_move + outcome_move, 3), round(delta, 2)


def rebase_record(record: dict[str, Any], repaired_at: str) -> tuple[dict[str, Any], int]:
    if str(record.get("leagueOrMedium") or "").upper() != "NFL":
        return record, 0

    events = [dict(value) if isinstance(value, dict) else value for value in record.get("priceEvents", [])]
    chronological = sorted(
        (
            (index, event)
            for index, event in enumerate(events)
            if isinstance(event, dict) and parse_time(event.get("startedAt")) is not None
        ),
        key=lambda pair: parse_time(pair[1].get("startedAt")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    suspect = [(index, event) for index, event in chronological if opportunity_signature(record, event)]
    if not suspect:
        return record, 0

    replacements: dict[int, tuple[float, float]] = {}
    for index, event in suspect:
        repaired = corrected_move(record, event)
        if repaired is not None and repaired[0] > -100.0:
            replacements[index] = repaired
    if not replacements:
        return record, 0

    first_index = min(
        replacements,
        key=lambda idx: parse_time(events[idx].get("startedAt")) or datetime.max.replace(tzinfo=timezone.utc),
    )
    first_time = parse_time(events[first_index].get("startedAt"))
    rolling_price = finite(events[first_index].get("priceBefore"))
    if first_time is None or rolling_price is None or rolling_price <= 0:
        return record, 0

    repaired_price_map: dict[str, tuple[float, float]] = {}
    latest_event: dict[str, Any] | None = None
    latest_move: float | None = None
    changed = 0

    for index, event in chronological:
        event_time = parse_time(event.get("startedAt"))
        if event_time is None or event_time < first_time:
            continue
        move = stored_move(event)
        if index in replacements:
            move, delta = replacements[index]
            event["performanceDeltaPct"] = delta
            event["modelMovePct"] = move
            event["opportunityFloorApplied"] = True
            event["opportunityModelVersion"] = opportunity.OPPORTUNITY_MODEL_VERSION
            event["nflPersistedOpportunityRepairVersion"] = REPAIR_VERSION
            changed += 1
        if move is None or move <= -100.0:
            continue

        before = round(float(rolling_price), 2)
        after = max(0.01, round(before * (1.0 + float(move) / 100.0), 2))
        actual_move = round((after / before - 1.0) * 100.0, 3)
        event["priceBefore"] = before
        event["priceAfter"] = after
        event["movePct"] = actual_move
        events[index] = event
        for alias in event_aliases(event):
            repaired_price_map[alias] = (before, after)
        rolling_price = after
        latest_event = event
        latest_move = actual_move

    if changed == 0 or latest_event is None:
        return record, 0

    result = dict(record)
    result["priceEvents"] = events
    latest_before = finite(latest_event.get("priceBefore"))
    latest_after = finite(latest_event.get("priceAfter"))
    if latest_before is not None and latest_after is not None:
        result["previousMarketPrice"] = round(latest_before, 2)
        result["marketPrice"] = round(latest_after, 2)
        result["dailyChange"] = round(latest_move or 0.0, 3)
        result["hourlyChangePct"] = round(latest_move or 0.0, 3)
        if str(latest_event.get("eventType") or "").lower() == "game":
            result["lastGameMovePct"] = round(latest_move or 0.0, 3)
            result["lastPriceEventId"] = event_key(latest_event)
            result["lastPriceEventAt"] = latest_event.get("startedAt")
            result["lastPriceEvent"] = latest_event.get("name") or result.get("lastPriceEvent")
            if latest_event.get("performanceDeltaPct") is not None:
                result["lastGamePerformanceDeltaPct"] = latest_event.get("performanceDeltaPct")
            if isinstance(latest_event.get("stats"), dict):
                result["lastGameStats"] = latest_event.get("stats", {})

    history = [dict(value) for value in result.get("priceHistory", []) if isinstance(value, dict)]
    for point in history:
        alias = str(point.get("eventId") or point.get("eventKey") or "").strip()
        prices = repaired_price_map.get(alias)
        if prices is None:
            continue
        phase = str(point.get("phase") or "").lower()
        if phase == "open":
            point["price"] = prices[0]
        elif phase == "close":
            point["price"] = prices[1]
    if history:
        result["priceHistory"] = history
        valid_prices = [finite(point.get("price")) for point in history]
        valid_prices = [value for value in valid_prices if value is not None and value > 0]
        if valid_prices:
            result["trend"] = [round(value, 2) for value in valid_prices[-18:]]
    elif latest_after is not None:
        trend = [finite(value) for value in result.get("trend", [])]
        trend = [value for value in trend if value is not None and value > 0]
        result["trend"] = [round(value, 2) for value in (trend[-17:] + [latest_after])]

    result["nflPersistedOpportunityRepairVersion"] = REPAIR_VERSION
    result["nflPersistedOpportunityRepairedAt"] = repaired_at
    result["nflPersistedOpportunityRepairCount"] = changed
    return result, changed


def repair_catalog(path: Path, repaired_at: str | None = None) -> tuple[int, int]:
    records = load_records(path)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output: list[dict[str, Any]] = []
    repaired_records = 0
    repaired_events = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        updated, count = rebase_record(record, stamp)
        output.append(updated)
        if count:
            repaired_records += 1
            repaired_events += count
    if repaired_records:
        write_records(path, output)
    return repaired_records, repaired_events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    records, events = repair_catalog(args.catalog)
    print(f"Rebased {events:,} NFL opportunity event(s) across {records:,} listing(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
