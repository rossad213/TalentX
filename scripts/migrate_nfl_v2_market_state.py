#!/usr/bin/env python3
"""One-time NFL market-state migration onto the shared v2/NBA-style price base.

This migration is intentionally NFL-only. It does not alter NBA, WNBA, NHL, MLB,
Soccer, Tennis, Golf, Music, Actor, or Creator records.

Why this exists:
The Sports workflow recalculates v2 fair value and then restores durable event
market state. That is normally correct, but it also preserved legacy NFL market
prices created before the NFL adopted the NBA-style architecture. This migration
moves each NFL listing onto its clean v2 fair value exactly once, keeps the old
price/game history for audit, and lets future verified NFL games compound from
that new market base.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pricing_engine_v2 import apply_v2

MIGRATION_VERSION = "1.1-nfl-v2-market-state-reset"
MIGRATION_EVENT_ID = "model:nfl-v2-market-state-reset-v1-1"

_V2_FIELDS = (
    "talentScore",
    "marketScore",
    "confidenceScore",
    "situationScore",
    "expectedValueScore",
    "fairValue",
    "fundamentalValue",
    "pricingModelVersion",
    "pricingEngine",
    "pricingV2",
    "rookiePricing",
)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _is_nfl(record: dict[str, Any]) -> bool:
    return str(record.get("leagueOrMedium") or "").strip().upper() == "NFL"


def _migration_point(history: list[dict[str, Any]], price: float, stamp: str) -> list[dict[str, Any]]:
    if any(str(item.get("eventId") or "") == MIGRATION_EVENT_ID for item in history if isinstance(item, dict)):
        return history
    return [
        *history,
        {
            "time": stamp,
            "eventId": MIGRATION_EVENT_ID,
            "eventType": "model_migration",
            "phase": "close",
            "name": "NFL v2 market-state migration",
            "price": round(price, 2),
        },
    ]


def migrate_record(record: dict[str, Any], stamp: str) -> tuple[dict[str, Any], bool]:
    """Reset one NFL listing to a clean v2 price exactly once."""
    if not _is_nfl(record):
        return dict(record), False
    if str(record.get("nflMarketMigrationVersion") or "") == MIGRATION_VERSION:
        return dict(record), False

    prior_market = _finite(record.get("marketPrice"))
    prior_game_move = _finite(record.get("lastGameMovePct"))

    # Build the new fundamental without allowing a stale pre-migration game move
    # to contaminate the reset price. Future verified games will populate this
    # field normally and compound from the migrated market price.
    clean = dict(record)
    clean["lastGameMovePct"] = 0.0
    clean["dailyChange"] = 0.0
    clean["hourlyChangePct"] = 0.0
    repriced = apply_v2(clean)
    target = _finite(repriced.get("fairValue"))
    if target is None or target <= 0:
        return dict(record), False
    target = round(target, 2)

    result = dict(record)
    for field in _V2_FIELDS:
        if field in repriced:
            result[field] = repriced[field]

    # The historical priceEvents ledger remains untouched for audit/history,
    # but NFL repair code treats nflMarketMigratedAt as a hard market-epoch
    # boundary and will never replay events that predate this reset.
    history = [
        dict(item)
        for item in result.get("priceHistory", [])
        if isinstance(item, dict)
    ]
    result["priceHistory"] = _migration_point(history, target, stamp)
    result["marketPrice"] = target
    result["previousMarketPrice"] = target
    result["modelTargetPrice"] = target
    result["dailyChange"] = 0.0
    result["hourlyChangePct"] = 0.0
    result["trend"] = [target] * 18
    result["lastGameMovePct"] = 0.0
    result["lastPriceRefreshAt"] = stamp

    result["nflMarketMigrationVersion"] = MIGRATION_VERSION
    result["nflMarketMigratedAt"] = stamp
    result["nflMarketMigrationPriorMarketPrice"] = round(prior_market, 2) if prior_market is not None else None
    result["nflMarketMigrationPriorLastGameMovePct"] = (
        round(prior_game_move, 3) if prior_game_move is not None else None
    )
    result["nflMarketMigrationTargetPrice"] = target
    result["nflMarketMigrationReason"] = (
        "Reset legacy NFL market state to clean v2 fair value before future NBA-style event compounding"
    )
    return result, True


def migrate_catalog(path: Path, *, migrated_at: str | None = None) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")

    stamp = migrated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output: list[dict[str, Any]] = []
    changed = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        updated, did_change = migrate_record(item, stamp)
        output.append(updated)
        changed += int(did_change)

    path.write_text(
        json.dumps(output, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    if path.name == "current_catalog.json":
        csv_path = path.with_suffix(".csv")
        fields = sorted({
            key
            for record in output
            for key, value in record.items()
            if not isinstance(value, (dict, list))
        })
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    changed = migrate_catalog(args.catalog)
    print(
        f"Migrated {changed:,} NFL listing(s) to clean v2 market state; "
        "all non-NFL records were left unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
