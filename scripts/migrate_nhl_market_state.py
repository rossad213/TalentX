#!/usr/bin/env python3
"""One-time NHL market reset after repairing the event-price pathway.

Historical priceEvents/priceHistory remain intact for audit. Only the current
market epoch is reset to the corrected NHL fair value so previously compounded
game moves cannot keep dominating the live ranking.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pricing_engine_v2 import apply_v2, is_nhl

MIGRATION_VERSION = "1.0-nhl-event-ledger-repair"
MIGRATION_EVENT_ID = "model:nhl-event-ledger-repair-v1"

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
)


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def migration_point(history: list[dict[str, Any]], price: float, stamp: str) -> list[dict[str, Any]]:
    if any(str(item.get("eventId") or "") == MIGRATION_EVENT_ID for item in history if isinstance(item, dict)):
        return history
    return [
        *history,
        {
            "time": stamp,
            "eventId": MIGRATION_EVENT_ID,
            "eventType": "model_migration",
            "phase": "close",
            "name": "NHL event-ledger and goalie-cohort repair",
            "price": round(price, 2),
        },
    ]


def migrate_record(record: dict[str, Any], stamp: str) -> tuple[dict[str, Any], bool]:
    if not is_nhl(record):
        return dict(record), False
    if str(record.get("nhlMarketMigrationVersion") or "") == MIGRATION_VERSION:
        return dict(record), False

    prior_market = finite(record.get("marketPrice"))
    prior_game_move = finite(record.get("lastGameMovePct"))

    clean = dict(record)
    clean["lastGameMovePct"] = 0.0
    clean["dailyChange"] = 0.0
    clean["hourlyChangePct"] = 0.0
    repriced = apply_v2(clean)
    target = finite(repriced.get("fairValue"))
    if target is None or target <= 0:
        return dict(record), False
    target = round(target, 2)

    result = dict(record)
    for field in _V2_FIELDS:
        if field in repriced:
            result[field] = repriced[field]
    history = [dict(item) for item in result.get("priceHistory", []) if isinstance(item, dict)]
    result["priceHistory"] = migration_point(history, target, stamp)
    result["marketPrice"] = target
    result["previousMarketPrice"] = target
    result["modelTargetPrice"] = target
    result["dailyChange"] = 0.0
    result["hourlyChangePct"] = 0.0
    result["trend"] = [target] * 18
    result["lastGameMovePct"] = 0.0
    result["lastPriceRefreshAt"] = stamp
    result["nhlMarketMigrationVersion"] = MIGRATION_VERSION
    result["nhlMarketMigratedAt"] = stamp
    result["nhlMarketMigrationPriorMarketPrice"] = round(prior_market, 2) if prior_market is not None else None
    result["nhlMarketMigrationPriorLastGameMovePct"] = round(prior_game_move, 3) if prior_game_move is not None else None
    result["nhlMarketMigrationTargetPrice"] = target
    result["nhlMarketMigrationReason"] = (
        "Reset NHL current market state after goalie-cohort correction and removal of repeated prior-game compounding"
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
    path.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if path.name == "current_catalog.json":
        fields = sorted({
            key for record in output for key, value in record.items()
            if not isinstance(value, (dict, list))
        })
        with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    changed = migrate_catalog(args.catalog)
    print(f"Migrated {changed:,} NHL listing(s) to the repaired market epoch; non-NHL records were unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
