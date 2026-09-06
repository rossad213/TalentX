#!/usr/bin/env python3
"""Reliability wrapper for the TalentX hourly game refresh.

This wrapper keeps the original retry behavior, adds durable game-by-game
market events, normalizes inherited Sports ticker collisions, repairs thin
Soccer box-score participation before pricing, prevents duplicate event moves,
and ensures current NFL rookies receive durable IPO/preseason chart history.

Sports event pricing is results-proportional and has no fixed percentage ceiling.
Routine variance produces small moves; increasingly exceptional verified results
are allowed to produce increasingly large moves.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import hourly_price_refresh as refresh
from category_market_store import dedupe_tickers, load_records, write_records
from game_event_history import attach_price_events
from results_event_pricing import MODEL_VERSION as RESULTS_MODEL_VERSION
from results_event_pricing import result_move_from_delta, result_sensitivity

_original_discover = refresh.discover_recent_events
_original_game_event_move = refresh.game_event_move

# These are corruption-detection thresholds only. They are never applied to a
# price with durable verified event history and are not market-movement caps.
EXTREME_UNSUPPORTED_PRICE_RATIO_LOW = 0.10
EXTREME_UNSUPPORTED_PRICE_RATIO_HIGH = 10.0
ROOKIE_HISTORY_BACKFILL_DAYS = 45
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
    return parsed if math.isfinite(parsed) and parsed > 0 else None


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


def _has_supported_price_history(record) -> bool:
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    return any(
        isinstance(event, dict)
        and event.get("verified") is not False
        and (event.get("priceAfter") is not None or event.get("movePct") is not None)
        for event in events
    )


def _draft_timestamp(record) -> str | None:
    try:
        year = int(record.get("draftYear") or 0)
        round_number = int(record.get("draftRound") or 0)
    except (TypeError, ValueError):
        return None
    return NFL_DRAFT_STARTS_UTC.get(year, {}).get(round_number)


def seed_rookie_ipo_history(catalog_path: Path = Path("data/current_catalog.json")) -> int:
    """Persist a verified NFL Draft/IPO event so rookie charts have a true origin."""
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
    """Return the best saved fair-value anchor for integrity checks only."""
    target = _number(record.get("modelTargetPrice"))
    fundamental = _number(record.get("fundamentalValue"))
    if target is not None and fundamental is not None:
        ratio = target / fundamental
        if 0.70 <= ratio <= 1.30:
            return target
        return fundamental
    return target or fundamental


def repair_sports_price_integrity(catalog_path: Path = Path("data/current_catalog.json")) -> int:
    """Repair only clearly unsupported/corrupt inherited Sports prices.

    A valid positive price with verified event history is never clipped to a fair-
    value band. This preserves legitimate cumulative result-driven movement.
    """
    if not catalog_path.exists():
        return 0
    records = load_records(catalog_path)
    repairs = 0
    for record in records:
        price = _number(record.get("marketPrice"))
        anchor = fair_value_anchor(record)
        if anchor is None:
            continue

        reason = ""
        if price is None:
            reason = "invalid or non-positive Sports price"
        elif not _has_supported_price_history(record):
            ratio = price / anchor
            if ratio < EXTREME_UNSUPPORTED_PRICE_RATIO_LOW or ratio > EXTREME_UNSUPPORTED_PRICE_RATIO_HIGH:
                reason = "extreme inherited Sports price had no verified event history"
        if not reason:
            record.pop("eventPriceBand", None)
            continue

        old_price = round(price, 2) if price is not None else None
        repaired = round(anchor, 2)
        record["previousMarketPrice"] = old_price if old_price is not None else repaired
        record["marketPrice"] = repaired
        record["dailyChange"] = 0.0
        record["hourlyChangePct"] = 0.0
        record["trend"] = [repaired] * 18
        record["priceIntegrityRepair"] = {
            "reason": reason,
            "oldPrice": old_price,
            "repairedPrice": repaired,
            "fairValueAnchor": round(anchor, 2),
            "verifiedEventHistoryPresent": False,
        }
        record.pop("eventPriceBand", None)
        repairs += 1

    if repairs:
        write_records(catalog_path, records)
        print(f"Repaired {repairs:,} unsupported Sports price outlier(s) before game refresh.")
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
    """Give a verified Soccer appearance a one-game participation baseline."""
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


def results_based_game_event_move(record, item, event, _legacy_max_game_move_pct):
    """Price a verified game from performance surprise with no hard move cap."""
    _legacy_move, evidence = _original_game_event_move(record, item, event, float("inf"))
    if evidence.get("comparable"):
        delta = float(evidence.get("performanceDeltaPct") or 0.0)
        tier, sensitivity = result_sensitivity(record)
        performance_move = result_move_from_delta(delta) * sensitivity
        outcome_move = 0.06 if event.get("teamWon") is True else -0.05 if event.get("teamWon") is False else 0.0
        move_pct = performance_move + outcome_move
        return round(move_pct, 3), {
            **evidence,
            "outcomeMovePct": outcome_move,
            "performanceMovePct": round(performance_move, 3),
            "volatilityTier": tier,
            "resultSensitivity": round(sensitivity, 3),
            "pricingBasis": RESULTS_MODEL_VERSION,
            "hardMoveCapPct": None,
        }

    if not _is_nfl_preseason_event(record, event):
        return 0.0, evidence

    expected = _rookie_preseason_expected_signal(record)
    actual = evidence.get("actualPerformanceScore")
    try:
        actual = float(actual)
    except (TypeError, ValueError):
        actual = None
    if expected is None or actual is None:
        return 0.0, evidence

    production_delta = (actual / expected - 1.0) * 100.0
    performance_move = result_move_from_delta(
        production_delta,
        scale=0.48,
        reference_pct=25.0,
        exponent=1.45,
        dead_zone_pct=4.0,
    )
    outcome_move = 0.04 if event.get("teamWon") is True else -0.03 if event.get("teamWon") is False else 0.0
    move_pct = performance_move + outcome_move
    return round(move_pct, 3), {
        **evidence,
        "comparable": True,
        "reason": "Verified NFL rookie preseason box score compared with a conservative position baseline",
        "expectedPerformanceScore": round(expected, 3),
        "performanceDeltaPct": round(production_delta, 2),
        "productionDeltaPct": round(production_delta, 2),
        "efficiencyDeltaPct": None,
        "performanceMovePct": round(performance_move, 3),
        "outcomeMovePct": outcome_move,
        "rookiePreseason": True,
        "pricingBasis": f"{RESULTS_MODEL_VERSION}-rookie-preseason",
        "hardMoveCapPct": None,
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
    # an earlier partial run. Player/event keys still prevent double-pricing.
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
    _legacy_max_game_move_pct,
    refreshed_at,
):
    """Apply distinct verified game results without a hard event or daily cap."""
    old_price = max(0.01, float(old_record.get("marketPrice") or new_record.get("marketPrice") or 0.01))
    model_target = max(0.01, float(new_record.get("marketPrice") or old_price))
    price = old_price
    prior_trend = [float(value) for value in old_record.get("trend", []) if isinstance(value, (int, float))]
    trend = [round(value, 2) for value in prior_trend] or [round(old_price, 2)] * 18
    event_results = []
    seen_keys: set[str] = set()

    for event in sorted(events, key=lambda value: str(value.get("startedAt") or "")):
        key = str(event.get("eventKey") or event.get("eventId") or "").strip()
        if key and key in seen_keys:
            continue
        if key:
            seen_keys.add(key)

        event_move, evidence = results_based_game_event_move(old_record, item, event, None)
        if not evidence.get("comparable"):
            event_results.append({**event, **evidence, "movePct": 0.0})
            continue
        if event_move <= -100.0:
            event_results.append({**event, **evidence, "comparable": False, "reason": "Invalid move would make price non-positive", "movePct": 0.0})
            continue

        before = price
        next_price = max(0.01, round(before * (1.0 + event_move / 100.0), 2))
        actual_move = round((next_price / before - 1.0) * 100.0, 3)
        price = next_price
        trend = trend[-17:] + [price]
        event_results.append({
            **event,
            **evidence,
            "modelMovePct": round(event_move, 3),
            "movePct": actual_move,
            "priceBefore": round(before, 2),
            "priceAfter": price,
        })

    change_pct = round((price / old_price - 1.0) * 100.0, 2)
    result = dict(new_record)
    result["modelTargetPrice"] = round(model_target, 2)
    result["previousMarketPrice"] = round(old_price, 2)
    result["marketPrice"] = round(price, 2)
    result["dailyChange"] = change_pct
    result["hourlyChangePct"] = change_pct
    result["lastPriceRefreshAt"] = refreshed_at
    result["trend"] = trend
    result["eventPricingModel"] = RESULTS_MODEL_VERSION
    result.pop("eventPriceBand", None)

    comparable = [event for event in event_results if event.get("comparable")]
    if comparable:
        latest = comparable[-1]
        result["lastPriceEventAt"] = latest.get("startedAt") or refreshed_at
        result["lastPriceEvent"] = str(latest.get("name") or "Completed game")
        result["lastPriceEventId"] = latest.get("eventKey") or latest.get("eventId")
        result["lastGameMovePct"] = latest.get("movePct")
        result["lastGamePerformanceDeltaPct"] = latest.get("performanceDeltaPct")
        result["lastGameStats"] = latest.get("stats", {})
        result["volatilityTier"] = latest.get("volatilityTier") or result.get("volatilityTier")

    result = attach_price_events(old_record, result, event_results)
    return result, change_pct, event_results


refresh.discover_recent_events = discover_recent_events_reliably
refresh.game_event_move = results_based_game_event_move
refresh.apply_game_market_moves = apply_game_market_moves_with_history

if __name__ == "__main__":
    normalize_sports_tickers()
    repair_sports_price_integrity()
    seed_rookie_ipo_history()
    raise SystemExit(refresh.main())
