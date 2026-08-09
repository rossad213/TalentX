#!/usr/bin/env python3
"""Reliability wrapper for the TalentX hourly game refresh.

This wrapper keeps the original retry behavior, adds durable game-by-game
market events, normalizes inherited Sports ticker collisions, and repairs thin
Soccer box-score participation before pricing. A completed game can therefore
affect both price and dated chart history exactly once per athlete without an
unrelated catalog issue breaking the refresh.
"""
from __future__ import annotations

from pathlib import Path

import hourly_price_refresh as refresh
from category_market_store import dedupe_tickers, load_records, write_records
from game_event_history import attach_price_events

_original_discover = refresh.discover_recent_events
_original_apply_game_market_moves = refresh.apply_game_market_moves


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
    result = attach_price_events(old_record, result, event_results)
    return result, change_pct, event_results


refresh.discover_recent_events = discover_recent_events_reliably
refresh.apply_game_market_moves = apply_game_market_moves_with_history

if __name__ == "__main__":
    normalize_sports_tickers()
    raise SystemExit(refresh.main())