#!/usr/bin/env python3
"""Reliability wrapper for the TalentX hourly game refresh.

A completed game may be marked processed even when one or more player records
failed to match during that run. The original discovery function then skipped
the whole game on every retry. This wrapper always re-opens completed games in
the lookback window and relies on the existing per-player event keys to prevent
duplicate price changes.
"""
from __future__ import annotations

import hourly_price_refresh as refresh

_original_discover = refresh.discover_recent_events


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
    return _original_discover(
        records,
        now=now,
        lookback_hours=lookback_hours,
        timeout=timeout,
        workers=workers,
        processed_keys=set(),
        processed_player_keys=processed_player_keys,
    )


refresh.discover_recent_events = discover_recent_events_reliably

if __name__ == "__main__":
    raise SystemExit(refresh.main())
