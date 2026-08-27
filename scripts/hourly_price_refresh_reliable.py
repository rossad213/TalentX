#!/usr/bin/env python3
"""Reliability wrapper for the TalentX hourly game refresh.

This wrapper keeps the original retry behavior, adds durable game-by-game
market events, normalizes inherited Sports ticker collisions, repairs thin
Soccer box-score participation before pricing, and prevents repeated game-event
moves from compounding Sports prices away from fair value indefinitely.
"""
from __future__ import annotations

from pathlib import Path

import hourly_price_refresh as refresh
from category_market_store import dedupe_tickers, load_records, write_records
from game_event_history import attach_price_events

_original_discover = refresh.discover_recent_events
_original_apply_game_market_moves = refresh.apply_game_market_moves

SPORTS_EVENT_PRICE_BAND_PCT = 30.0
EXTREME_PRICE_RATIO_LOW = 0.50
EXTREME_PRICE_RATIO_HIGH = 2.00


def _number(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def fair_value_anchor(record) -> float | None:
    """Return the best saved fair-value anchor for Sports event pricing."""
    target = _number(record.get("modelTargetPrice"))
    fundamental = _number(record.get("fundamentalValue"))
    if target is not None and fundamental is not None:
        # Reject a stale/corrupt target that is itself detached from fundamentals.
        ratio = target / fundamental
        if 0.70 <= ratio <= 1.30:
            return target
        return fundamental
    return target or fundamental


def bounded_event_price(price: float, anchor: float) -> float:
    """Keep cumulative game-event pricing inside a durable fair-value band."""
    band = SPORTS_EVENT_PRICE_BAND_PCT / 100.0
    lower = anchor * (1.0 - band)
    upper = anchor * (1.0 + band)
    return round(max(lower, min(upper, price)), 2)


def repair_sports_price_integrity(catalog_path: Path = Path("data/current_catalog.json")) -> int:
    """Repair inherited Sports prices that escaped the event-pricing band.

    Severe outliers are reset to the current fair-value anchor. Lesser outliers
    are clipped to the allowed event band. This is a data-integrity repair, not a
    new market event, so the displayed hourly/daily move is reset to zero.
    """
    if not catalog_path.exists():
        return 0
    records = load_records(catalog_path)
    repairs = 0
    for record in records:
        price = _number(record.get("marketPrice"))
        anchor = fair_value_anchor(record)
        if price is None or anchor is None:
            continue
        lower = anchor * (1.0 - SPORTS_EVENT_PRICE_BAND_PCT / 100.0)
        upper = anchor * (1.0 + SPORTS_EVENT_PRICE_BAND_PCT / 100.0)
        if lower <= price <= upper:
            continue

        ratio = price / anchor
        repaired = anchor if ratio < EXTREME_PRICE_RATIO_LOW or ratio > EXTREME_PRICE_RATIO_HIGH else bounded_event_price(price, anchor)
        old_price = round(price, 2)
        repaired = round(repaired, 2)
        record["previousMarketPrice"] = old_price
        record["marketPrice"] = repaired
        record["dailyChange"] = 0.0
        record["hourlyChangePct"] = 0.0
        record["trend"] = [repaired] * 18
        record["priceIntegrityRepair"] = {
            "reason": "cumulative Sports game-event price escaped fair-value band",
            "oldPrice": old_price,
            "repairedPrice": repaired,
            "fairValueAnchor": round(anchor, 2),
            "allowedBandPct": SPORTS_EVENT_PRICE_BAND_PCT,
        }
        repairs += 1

    if repairs:
        write_records(catalog_path, records)
        print(f"Repaired {repairs:,} Sports price outlier(s) before game refresh.")
    return repairs


def normalize_sports_tickers(catalog_path: Path = Path("data/current_catalog.json")) -> int:
    """Repair duplicate Sports tickers deterministically before hourly refresh."""
    if not catalog_path.exists():
        return 0
    records = load_records(catalog_path)
    repairs = dedupe_tickers(records)
    if repairs:
        write_records(catalog_path, records)
        print(f"Normalized {len(repairs):,} duplicate Sports ticker(s) before game refresh.")
    return len(repairs)


def add_soccer_participation(records, athlete_events) -> None:
    """Give a verified Soccer appearance a one-game participation baseline.

    ESPN Soccer summaries often expose minutes/goals/assists/shots but omit an
    explicit appearances column. TalentX's season model includes appearances, so
    a player who logs minutes must receive ``appearances=1`` for the one-game
    comparison. Without this normalization, a scoreless 90-minute appearance can
    be mistaken for zero production.
    """
    soccer_keys = {
        (str(record.get("sourceNamespace") or ""), str(record.get("sourceRecordId") or ""))
        for record in records
        if str(record.get("discipline") or "") == "Soccer"
    }
    for athlete_key, events in athlete_events.items():
        if athlete_key not in soccer_keys:
            continue
        for event in events:
            stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
            minutes = refresh.numeric_box_value(stats.get("minutes"))
            if minutes is not None and minutes > 0:
                stats.setdefault("appearances", 1.0)
                stats.setdefault("gamesPlayed", 1.0)


def discover_recent_events_reliably(
    records,
    *,
    now,
    lookback_hours,
    timeout,
    workers,
    processed_keys,
    processed_player_keys=None,
):
    # Do not let an event-level marker suppress players that were missed during
    # an earlier partial run. The underlying function still checks
    # processed_player_keys before returning each athlete/event pair, so an
    # already-priced player cannot be priced twice for the same game.
    result = _original_discover(
        records,
        now=now,
        lookback_hours=lookback_hours,
        timeout=timeout,
        workers=workers,
        processed_keys=set(),
        processed_player_keys=processed_player_keys,
    )
    participant_ids, athlete_events, events, warnings = result
    add_soccer_participation(records, athlete_events)
    return participant_ids, athlete_events, events, warnings


def apply_game_market_moves_with_history(
    old_record,
    new_record,
    item,
    events,
    max_game_move_pct,
    refreshed_at,
):
    result, change_pct, event_results = _original_apply_game_market_moves(
        old_record,
        new_record,
        item,
        events,
        max_game_move_pct,
        refreshed_at,
    )

    anchor = fair_value_anchor(result)
    price = _number(result.get("marketPrice"))
    if anchor is not None and price is not None:
        bounded = bounded_event_price(price, anchor)
        if abs(bounded - price) >= 0.01:
            old_price = max(0.01, float(old_record.get("marketPrice") or price))
            result["marketPrice"] = bounded
            result["dailyChange"] = round((bounded / old_price - 1.0) * 100.0, 2)
            result["hourlyChangePct"] = result["dailyChange"]
            trend = [float(value) for value in result.get("trend", []) if isinstance(value, (int, float))]
            result["trend"] = ([round(value, 2) for value in trend[-17:]] + [bounded]) if trend else [bounded] * 18
            result["eventPriceBand"] = {
                "fairValueAnchor": round(anchor, 2),
                "allowedBandPct": SPORTS_EVENT_PRICE_BAND_PCT,
                "clamped": True,
            }
            change_pct = result["dailyChange"]
        else:
            result["eventPriceBand"] = {
                "fairValueAnchor": round(anchor, 2),
                "allowedBandPct": SPORTS_EVENT_PRICE_BAND_PCT,
                "clamped": False,
            }

    result = attach_price_events(old_record, result, event_results)
    return result, change_pct, event_results


refresh.discover_recent_events = discover_recent_events_reliably
refresh.apply_game_market_moves = apply_game_market_moves_with_history

if __name__ == "__main__":
    normalize_sports_tickers()
    repair_sports_price_integrity()
    raise SystemExit(refresh.main())
