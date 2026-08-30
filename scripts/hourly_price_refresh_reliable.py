#!/usr/bin/env python3
"""Reliability wrapper for the TalentX hourly game refresh.

This wrapper keeps the original retry behavior, adds durable game-by-game
market events, normalizes inherited Sports ticker collisions, repairs thin
Soccer box-score participation before pricing, prevents repeated game-event
moves from compounding Sports prices away from fair value indefinitely, and
ensures current NFL rookies receive durable IPO/preseason chart history.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import hourly_price_refresh as refresh
from category_market_store import dedupe_tickers, load_records, write_records
from game_event_history import attach_price_events

_original_discover = refresh.discover_recent_events
_original_game_event_move = refresh.game_event_move
_original_apply_game_market_moves = refresh.apply_game_market_moves

SPORTS_EVENT_PRICE_BAND_PCT = 30.0
EXTREME_PRICE_RATIO_LOW = 0.50
EXTREME_PRICE_RATIO_HIGH = 2.00
ROOKIE_HISTORY_BACKFILL_DAYS = 45
ROOKIE_PRESEASON_MOVE_CAP_PCT = 1.50
NFL_2026_DRAFT_SOURCE = "https://www.nfl.com/news/2026-nfl-draft-order-for-all-seven-rounds"
NFL_DRAFT_STARTS_UTC = {
    2026: {
        1: "2026-04-24T00:00:00Z",  # Apr 23, 8 p.m. ET
        2: "2026-04-24T23:00:00Z",  # Apr 24, 7 p.m. ET
        3: "2026-04-24T23:00:00Z",
        4: "2026-04-25T16:00:00Z",  # Apr 25, noon ET
        5: "2026-04-25T16:00:00Z",
        6: "2026-04-25T16:00:00Z",
        7: "2026-04-25T16:00:00Z",
    }
}


def _number(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _iso_now(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_nfl_rookie(record) -> bool:
    if str(record.get("leagueOrMedium") or "") != "NFL":
        return False
    if not record.get("draftPick") or not record.get("draftYear"):
        return False
    status = str(record.get("pricingDataStatus") or "")
    stage = str(record.get("careerStage") or "").lower()
    experience = _number(record.get("experienceYears")) or 0
    return "Rookie IPO" in status or "rookie" in stage or experience <= 1


def _has_game_price_event(record) -> bool:
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    return any(isinstance(event, dict) and str(event.get("eventType") or "").lower() == "game" for event in events)


def _draft_timestamp(record) -> str | None:
    try:
        year = int(record.get("draftYear") or 0)
        round_number = int(record.get("draftRound") or 0)
    except (TypeError, ValueError):
        return None
    return NFL_DRAFT_STARTS_UTC.get(year, {}).get(round_number)


def seed_rookie_ipo_history(catalog_path: Path = Path("data/current_catalog.json")) -> int:
    """Persist a verified NFL Draft/IPO event so rookie charts have a true origin.

    The event date is used only when TalentX has an explicit, source-backed draft
    schedule for that year/round. No ingestion or verification timestamp is ever
    substituted for the actual draft date.
    """
    if not catalog_path.exists():
        return 0
    records = load_records(catalog_path)
    seeded = 0
    for record in records:
        if not _is_nfl_rookie(record):
            continue
        started_at = _draft_timestamp(record)
        if not started_at:
            continue
        pricing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else {}
        opening = _number(pricing.get("calibratedIpoPrice")) or _number(pricing.get("ipoPrice")) or _number(record.get("fundamentalValue"))
        if opening is None:
            continue
        source_id = str(record.get("sourceRecordId") or record.get("id") or record.get("name") or "rookie")
        event_key = f"rookie-ipo:nfl:{int(record.get('draftYear'))}:{source_id}"
        existing = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
        if any(isinstance(event, dict) and str(event.get("eventKey") or "") == event_key for event in existing):
            continue
        event = {
            "eventKey": event_key,
            "eventId": event_key,
            "provider": "NFL",
            "league": "NFL",
            "name": f"{int(record.get('draftYear'))} NFL Draft · Round {int(record.get('draftRound'))}, Pick {int(record.get('draftPick'))}",
            "startedAt": started_at,
            "priceBefore": round(opening, 2),
            "priceAfter": round(opening, 2),
            "movePct": 0.0,
            "eventType": "rookie-ipo",
            "verified": True,
            "sourceUrl": NFL_2026_DRAFT_SOURCE if int(record.get("draftYear")) == 2026 else record.get("draftMetadataSource"),
        }
        record["priceEvents"] = [*existing, event]
        record["rookieIpoHistorySeededAt"] = _iso_now()
        seeded += 1
    if seeded:
        write_records(catalog_path, records)
        print(f"Seeded {seeded:,} verified NFL rookie IPO chart event(s).")
    return seeded


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


def _event_datetime(event) -> datetime | None:
    value = str(event.get("startedAt") or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_nfl_preseason_event(record, event) -> bool:
    """Identify the current NFL preseason window without inventing game dates."""
    if not _is_nfl_rookie(record):
        return False
    league = str(event.get("league") or "").lower()
    if league not in {"nfl", ""}:
        return False
    started = _event_datetime(event)
    if started is None:
        return False
    try:
        draft_year = int(record.get("draftYear") or 0)
    except (TypeError, ValueError):
        return False
    # NFL preseason games are played in July/August before the September regular
    # season. The event timestamp itself still comes directly from ESPN.
    return started.year == draft_year and started.month in {7, 8}


def _rookie_preseason_expected_signal(record) -> float | None:
    role = str(record.get("role") or "").lower()
    if "quarterback" in role or role.strip() == "qb":
        return 1.5
    if "running back" in role or "fullback" in role or role.strip() in {"rb", "fb"}:
        return 2.5
    if any(token in role for token in ("wide receiver", "receiver", "tight end")) or role.strip() in {"wr", "te"}:
        return 2.5
    if any(token in role for token in ("kicker", "punter")):
        return 1.0
    if any(token in role for token in ("tackle", "guard", "center", "offensive line")):
        return None
    return 1.2


def game_event_move_with_rookie_preseason(record, item, event, max_game_move_pct):
    """Allow verified rookie preseason games to move price before a season baseline exists."""
    move, evidence = _original_game_event_move(record, item, event, max_game_move_pct)
    if evidence.get("comparable") or not _is_nfl_preseason_event(record, event):
        return move, evidence

    expected = _rookie_preseason_expected_signal(record)
    actual = evidence.get("actualPerformanceScore")
    try:
        actual = float(actual)
    except (TypeError, ValueError):
        actual = None
    if expected is None or actual is None:
        return move, evidence

    production_delta = refresh.clamp((actual / expected - 1.0) * 100.0, -100.0, 250.0)
    performance_move = refresh.clamp(production_delta / 100.0 * 0.90, -ROOKIE_PRESEASON_MOVE_CAP_PCT, ROOKIE_PRESEASON_MOVE_CAP_PCT)
    outcome_move = 0.05 if event.get("teamWon") is True else -0.03 if event.get("teamWon") is False else 0.0
    move_pct = refresh.clamp(
        performance_move + outcome_move,
        -min(max_game_move_pct, ROOKIE_PRESEASON_MOVE_CAP_PCT),
        min(max_game_move_pct, ROOKIE_PRESEASON_MOVE_CAP_PCT),
    )
    if abs(move_pct) < 0.05 and abs(production_delta) >= 1.0:
        move_pct = 0.05 if production_delta > 0 else -0.05
    return round(move_pct, 3), {
        **evidence,
        "comparable": True,
        "reason": "Verified NFL rookie preseason box score compared with a conservative position baseline",
        "expectedPerformanceScore": round(expected, 3),
        "performanceDeltaPct": round(production_delta, 2),
        "productionDeltaPct": round(production_delta, 2),
        "efficiencyDeltaPct": None,
        "outcomeMovePct": outcome_move,
        "rookiePreseason": True,
    }


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

    # A rebuilt baseline can contain a current drafted rookie but no durable game
    # events even though that player already appeared in preseason. Rehydrate a
    # short NFL-only window once, then normal 48-hour processing owns new games.
    backfill_records = [
        record for record in records
        if _is_nfl_rookie(record)
        and not _has_game_price_event(record)
        and not record.get("rookieHistoryBackfillCheckedAt")
    ]
    if backfill_records:
        candidate_by_key = {
            (str(record.get("sourceNamespace") or ""), str(record.get("sourceRecordId") or "")): record
            for record in backfill_records
        }
        backfill = _original_discover(
            backfill_records,
            now=now,
            lookback_hours=max(float(lookback_hours), ROOKIE_HISTORY_BACKFILL_DAYS * 24.0),
            timeout=timeout,
            workers=workers,
            processed_keys=set(),
            # Rehydrate durable history even if an older manifest says the game
            # was processed before a later baseline rebuild discarded the event.
            processed_player_keys=set(),
        )
        _, backfill_athlete_events, backfill_events, backfill_warnings = backfill
        accepted_event_keys: set[str] = set()
        for athlete_key, candidate in candidate_by_key.items():
            existing = {str(event.get("eventKey") or ""): event for event in athlete_events.get(athlete_key, [])}
            for event in backfill_athlete_events.get(athlete_key, []):
                if not _is_nfl_preseason_event(candidate, event):
                    continue
                key = str(event.get("eventKey") or "")
                if key:
                    existing[key] = event
                    accepted_event_keys.add(key)
            if existing:
                athlete_events[athlete_key] = sorted(existing.values(), key=lambda item: str(item.get("startedAt") or ""))
                participant_ids.add(athlete_key)
            candidate["rookieHistoryBackfillCheckedAt"] = _iso_now(now)

        event_by_key = {str(event.get("eventKey") or ""): event for event in events if event.get("eventKey")}
        for event in backfill_events:
            key = str(event.get("eventKey") or "")
            if key in accepted_event_keys:
                event_by_key[key] = event
        events = list(event_by_key.values())
        warnings.extend(backfill_warnings)
        if accepted_event_keys:
            print(f"Backfilled {len(accepted_event_keys):,} NFL rookie preseason event(s) for durable chart history.")

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
refresh.game_event_move = game_event_move_with_rookie_preseason
refresh.apply_game_market_moves = apply_game_market_moves_with_history

if __name__ == "__main__":
    normalize_sports_tickers()
    repair_sports_price_integrity()
    seed_rookie_ipo_history()
    raise SystemExit(refresh.main())
