#!/usr/bin/env python3
"""One-time Tennis market rebase after verified-match confidence calibration.

The previous generic confidence pathway treated established tennis players with
hundreds of verified matches as low-sample athletes because professionalGames was
usually absent. Reprice only Tennis to the corrected v2 fair value once while
preserving the full verified match ledger and dated chart history.
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

MIGRATION_VERSION = "1.0-tennis-verified-match-confidence"
MIGRATION_EVENT_ID = "model:tennis-verified-match-confidence-v1"

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


def is_tennis(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() == "tennis"
    )


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def migration_point(history: list[dict[str, Any]], price: float, stamp: str) -> list[dict[str, Any]]:
    if any(
        isinstance(item, dict)
        and str(item.get("eventId") or "") == MIGRATION_EVENT_ID
        for item in history
    ):
        return history
    return [
        *history,
        {
            "time": stamp,
            "eventId": MIGRATION_EVENT_ID,
            "label": "Tennis verified-match confidence market rebase",
            "phase": "close",
            "price": round(price, 2),
            "historyType": "verified",
            "eventType": "model_migration",
            "priceBasis": "one-time Tennis market-scale correction from verified match evidence",
        },
    ]


def migrate_record(record: dict[str, Any], stamp: str) -> tuple[dict[str, Any], bool]:
    if not is_tennis(record):
        return dict(record), False
    if str(record.get("tennisMarketMigrationVersion") or "") == MIGRATION_VERSION:
        return dict(record), False

    prior_market = finite(record.get("marketPrice"))
    prior_fundamental = finite(record.get("fundamentalValue"))

    repriced = apply_v2(dict(record))
    target = finite(repriced.get("fairValue"))
    if target is None or target <= 0:
        return dict(record), False
    target = round(target, 2)

    result = dict(record)
    for field in _V2_FIELDS:
        if field in repriced:
            result[field] = repriced[field]

    history = [
        dict(item)
        for item in result.get("priceHistory", [])
        if isinstance(item, dict)
    ]
    result["priceHistory"] = migration_point(history, target, stamp)
    result["marketPrice"] = target
    result["previousMarketPrice"] = target
    result["modelTargetPrice"] = target
    result["dailyChange"] = 0.0
    result["hourlyChangePct"] = 0.0
    result["trend"] = [target] * 18
    result["lastGameMovePct"] = 0.0
    result["lastPriceRefreshAt"] = stamp
    result["tennisMarketMigrationVersion"] = MIGRATION_VERSION
    result["tennisMarketMigratedAt"] = stamp
    result["tennisMarketMigrationPriorMarketPrice"] = (
        round(prior_market, 2) if prior_market is not None else None
    )
    result["tennisMarketMigrationPriorFundamentalValue"] = (
        round(prior_fundamental, 2) if prior_fundamental is not None else None
    )
    result["tennisMarketMigrationTargetPrice"] = target
    result["tennisMarketMigrationReason"] = (
        "Rebase Tennis after counting verified ATP/WTA matches as professional evidence"
    )
    return result, True


def migrate_catalog(path: Path, *, migrated_at: str | None = None) -> tuple[int, list[dict[str, Any]], str]:
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
            key
            for record in output
            for key, value in record.items()
            if not isinstance(value, (dict, list))
        })
        with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output)
    return changed, output, stamp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    changed, _, _ = migrate_catalog(args.catalog)
    print(
        f"Migrated {changed:,} Tennis listing(s) to verified-match confidence pricing; "
        "non-Tennis records were unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
