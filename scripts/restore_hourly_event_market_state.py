#!/usr/bin/env python3
"""Restore event-driven market state after recalculating v2 fair values.

The hourly game processor owns the observable market price. Pricing engine v2 may
recalculate fair/fundamental value, but it must not erase a sequence of verified
game moves that was just applied in the same refresh.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EVENT_STATE_FIELDS = (
    "marketPrice",
    "previousMarketPrice",
    "modelTargetPrice",
    "trend",
    "priceEvents",
    "priceHistory",
    "dailyChange",
    "hourlyChangePct",
    "lastPriceRefreshAt",
    "lastPriceEventAt",
    "lastPriceEvent",
    "lastPriceEventId",
    "lastGameMovePct",
    "lastGamePerformanceDeltaPct",
    "lastGameStats",
)


def load(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def restore(base: list[dict[str, Any]], snapshot: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    prior = {str(item.get("id")): item for item in snapshot if item.get("id")}
    output: list[dict[str, Any]] = []
    restored = 0
    for item in base:
        record = dict(item)
        saved = prior.get(str(record.get("id") or ""))
        if saved and (saved.get("lastPriceEventId") or saved.get("priceEvents")):
            for field in EVENT_STATE_FIELDS:
                if field in saved:
                    record[field] = saved[field]
            restored += 1
        output.append(record)
    return output, restored


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    base = load(args.base)
    snapshot = load(args.snapshot)
    restored_records, count = restore(base, snapshot)
    args.base.write_text(json.dumps(restored_records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Restored event-driven market state for {count:,} records after fair-value recalculation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
