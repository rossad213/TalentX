#!/usr/bin/env python3
"""Repair MLB market state onto the current clean TalentX fundamental model.

Older MLB safety code intentionally restored pricingV1.marketPrice after a
generic baseball game-pricing path produced unstable repeated moves. That
protection stopped runaway prices, but it also froze MLB on an obsolete market
scale while the rest of TalentX moved to newer fundamentals.

This module now performs an MLB-only, idempotent market-epoch migration:

* first clean migration: rebase current MLB market price to the latest v2
  fairValue / fundamentalValue rather than the legacy v1 anchor;
* preserve legitimate non-game events and plausible dated history;
* remove invalid generic MLB game-price events/history;
* record the prior market price and migration target for audit;
* on later runs, leave already-migrated clean MLB market state untouched;
* if invalid MLB game repricing reappears while live baseball pricing is still
  disabled, re-anchor only that contaminated MLB listing to current fair value;
* leave every non-MLB record byte-for-byte untouched.

Until a baseball-specific hitter/pitcher per-game expectation model is
calibrated and explicitly enabled, MLB game results remain informational only.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPAIR_VERSION = "2.0-mlb-current-fundamental-market-epoch"
OLD_REPAIR_EVENT_IDS = {"mlb-market-repair", "mlb-market-rebase-v2"}
REPAIR_EVENT_ID = "mlb-market-rebase-v2"
HISTORY_RATIO_LOW = 0.25
HISTORY_RATIO_HIGH = 4.0


def finite_positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def is_mlb(record: dict[str, Any]) -> bool:
    return str(record.get("leagueOrMedium") or "").strip().upper() == "MLB"


def current_fundamental_price(record: dict[str, Any]) -> tuple[float | None, str]:
    """Return the cleanest current anchor, falling back to v1 only if necessary."""
    for field in ("fairValue", "fundamentalValue", "modelTargetPrice"):
        parsed = finite_positive(record.get(field))
        if parsed is not None:
            return round(parsed, 2), field

    pricing_v1 = record.get("pricingV1") if isinstance(record.get("pricingV1"), dict) else {}
    parsed = finite_positive(pricing_v1.get("marketPrice"))
    if parsed is not None:
        return round(parsed, 2), "pricingV1.marketPrice fallback"
    return None, ""


def event_key(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def game_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    event_type = str(event.get("eventType") or "").strip().lower()
    key = event_key(event).lower()
    non_game_types = {
        "career", "signing", "trade", "award", "injury", "milestone",
        "team-change", "model-migration", "market-repair",
    }
    return (
        event_type == "game"
        or (key.startswith("espn:") and event_type not in non_game_types)
    )


def history_is_game_point(point: Any, game_ids: set[str]) -> bool:
    if not isinstance(point, dict):
        return False
    event_id = str(point.get("eventId") or point.get("eventKey") or "").strip()
    event_type = str(point.get("eventType") or "").strip().lower()
    return event_type == "game" or (event_id and event_id in game_ids)


def safe_history(
    record: dict[str, Any],
    anchor: float,
    repaired_at: str,
    *,
    removed_game_ids: set[str],
) -> list[dict[str, Any]]:
    history = record.get("priceHistory") if isinstance(record.get("priceHistory"), list) else []
    kept: list[dict[str, Any]] = []
    low = anchor * HISTORY_RATIO_LOW
    high = anchor * HISTORY_RATIO_HIGH

    for point in history:
        if not isinstance(point, dict):
            continue
        event_id = str(point.get("eventId") or point.get("eventKey") or "")
        if event_id in OLD_REPAIR_EVENT_IDS:
            continue
        if history_is_game_point(point, removed_game_ids):
            continue

        price = finite_positive(point.get("price"))
        if price is None or not (low <= price <= high):
            continue
        kept.append(dict(point))

    kept.append({
        "time": repaired_at,
        "price": round(anchor, 2),
        "eventId": REPAIR_EVENT_ID,
        "label": "MLB market rebased to current clean TalentX fundamental",
        "phase": "close",
        "historyType": "verified",
        "eventType": "model-migration",
        "priceBasis": "current MLB fairValue/fundamentalValue; live MLB game repricing disabled",
    })
    kept.sort(key=lambda item: str(item.get("time") or ""))
    return kept[-730:]


def safe_trend(history: list[dict[str, Any]], anchor: float) -> list[float]:
    prices = [
        round(price, 2)
        for point in history
        if isinstance(point, dict)
        for price in [finite_positive(point.get("price"))]
        if price is not None
    ]
    if not prices:
        return [round(anchor, 2)]
    if abs(prices[-1] - anchor) >= 0.005:
        prices.append(round(anchor, 2))
    return prices[-18:]


def has_invalid_game_state(record: dict[str, Any]) -> bool:
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    if any(game_event(event) for event in events):
        return True
    last_move = finite_number(record.get("lastGameMovePct"))
    return last_move is not None and abs(last_move) >= 0.0005


def repair_record(record: dict[str, Any], *, repaired_at: str) -> tuple[dict[str, Any], bool]:
    if not is_mlb(record):
        return record, False

    already_current = str(record.get("mlbMarketRepairVersion") or "") == REPAIR_VERSION
    contaminated = has_invalid_game_state(record)
    if already_current and not contaminated:
        return record, False

    anchor, anchor_source = current_fundamental_price(record)
    if anchor is None:
        return record, False

    result = dict(record)
    old_price = finite_positive(result.get("marketPrice"))
    old_fundamental = finite_positive(result.get("fundamentalValue"))

    existing_events = result.get("priceEvents") if isinstance(result.get("priceEvents"), list) else []
    game_ids = {
        key
        for event in existing_events
        if isinstance(event, dict) and game_event(event)
        for key in (event_key(event), str(event.get("eventId") or "").strip())
        if key
    }
    preserved_events = [
        dict(event)
        for event in existing_events
        if isinstance(event, dict) and not game_event(event)
    ]

    repaired_history = safe_history(
        result,
        anchor,
        repaired_at,
        removed_game_ids=game_ids,
    )

    result["marketPrice"] = anchor
    result["previousMarketPrice"] = anchor
    result["modelTargetPrice"] = anchor
    result["dailyChange"] = 0.0
    result["hourlyChangePct"] = 0.0
    result["lastGameMovePct"] = 0.0
    result["priceEvents"] = preserved_events
    result["priceHistory"] = repaired_history
    result["trend"] = safe_trend(repaired_history, anchor)
    result["priceHistoryStatus"] = "verified"

    for key in (
        "lastPriceEventAt",
        "lastPriceEvent",
        "lastPriceEventId",
        "lastGamePerformanceDeltaPct",
        "eventPricingModel",
        "priceExplanation",
    ):
        result.pop(key, None)

    result["mlbGamePricingStatus"] = "protected-clean-fundamental-anchor-no-live-game-repricing"
    result["mlbMarketRepairVersion"] = REPAIR_VERSION
    result["mlbMarketRepairedAt"] = repaired_at
    result["mlbMarketMigrationPriorMarketPrice"] = round(old_price, 2) if old_price is not None else None
    result["mlbMarketMigrationPriorFundamentalValue"] = round(old_fundamental, 2) if old_fundamental is not None else None
    result["mlbMarketMigrationTargetPrice"] = anchor
    result["mlbMarketMigrationReason"] = (
        "Rebased MLB from legacy v1 protection to the current clean TalentX fundamental model"
        if not already_current
        else "Removed invalid MLB game repricing while baseball live-game pricing remains disabled"
    )
    result["mlbMarketRepair"] = {
        "reason": result["mlbMarketMigrationReason"],
        "oldPrice": round(old_price, 2) if old_price is not None else None,
        "restoredPrice": anchor,
        "source": anchor_source,
        "removedGameEvents": len(existing_events) - len(preserved_events),
        "safeHistoryPoints": len(repaired_history),
    }
    return result, True


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> int:
    if not path.exists():
        return 0
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{path} must contain a JSON array")

    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    repaired: list[dict[str, Any]] = []
    count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        updated, changed = repair_record(record, repaired_at=stamp)
        repaired.append(updated)
        count += int(changed)

    compact = path.name == "current_catalog.json"
    path.write_text(
        json.dumps(repaired, ensure_ascii=False, separators=(",", ":"))
        if compact
        else json.dumps(repaired, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()

    count = repair_catalog(args.catalog)
    if count:
        print(f"Migrated/repaired {count:,} MLB market price(s) onto the current clean fundamental epoch.")
    else:
        print("No MLB market prices required migration or safety repair.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
