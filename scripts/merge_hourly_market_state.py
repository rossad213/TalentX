#!/usr/bin/env python3
"""Carry event-driven market history onto a newly rebuilt fundamental catalog."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MARKET_STATE_FIELDS = (
    "marketPrice",
    "previousMarketPrice",
    "modelTargetPrice",
    "trend",
    "priceHistory",
    "priceExplanation",
    "volatilityTier",
    "lastPriceRefreshAt",
    "lastPriceEventAt",
    "lastPriceEvent",
    "lastPriceEventId",
    "lastGameMovePct",
    "lastGamePerformanceDeltaPct",
    "lastGameStats",
)


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def merge_market_state(
    rebuilt: list[dict[str, Any]],
    prior_hourly: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    prior_by_id = {
        str(record.get("id")): record
        for record in prior_hourly
        if record.get("id") and (record.get("lastPriceEventId") or record.get("priceHistory"))
    }
    merged: list[dict[str, Any]] = []
    carried = 0
    for record in rebuilt:
        result = dict(record)
        prior = prior_by_id.get(str(result.get("id") or ""))
        prior_price = prior.get("marketPrice") if prior else None
        if prior and isinstance(prior_price, (int, float)) and float(prior_price) > 0:
            for field in MARKET_STATE_FIELDS:
                if field in prior:
                    result[field] = prior[field]
            # The historical event remains available through its explanation;
            # the rebuild itself must not manufacture another price move.
            result["dailyChange"] = 0.0
            result["hourlyChangePct"] = 0.0
            carried += 1
        merged.append(result)
    return merged, carried


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    args = parser.parse_args()

    rebuilt = load_records(args.base)
    prior_hourly = load_records(args.overlay)
    merged, carried = merge_market_state(rebuilt, prior_hourly)
    args.base.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Carried {carried:,} event-driven market histories onto {len(merged):,} rebuilt records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
