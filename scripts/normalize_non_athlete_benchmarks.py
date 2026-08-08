#!/usr/bin/env python3
"""Keep source-discovered non-athletes outside curated editorial benchmark ranks.

The top-100 Music, Actor, and Creator rosters use benchmarkRank as a temporary
editorial ordering. Records discovered from public sources are evidence-backed
catalog additions, not members of those curated rankings. Removing benchmark
fields prevents pricing validation from treating discovery order as editorial
rank order.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "data" / "current_seed.json"
DISCOVERY_NAMESPACES = {
    "wikidata-non-athlete",
    "wikidata-music-expanded",
    "wikidata-music-strict",
    "wikidata-actor-only",
}


def is_source_discovered_non_athlete(record: dict[str, Any]) -> bool:
    namespace = str(record.get("sourceNamespace") or "")
    category = str(record.get("primaryCategory") or "")
    return namespace in DISCOVERY_NAMESPACES and category in {"Music", "Actor", "Creator"}


def normalize(records: list[dict[str, Any]]) -> int:
    changed = 0
    for record in records:
        if not is_source_discovered_non_athlete(record):
            continue
        removed = False
        for key in ("benchmarkRank", "benchmarkPoolSize"):
            if key in record:
                record.pop(key, None)
                removed = True
        if removed:
            changed += 1
        record["rankingStatus"] = "Source-discovered; not part of curated benchmark ranking"
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    args = parser.parse_args()

    payload = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")
    records = [record for record in payload if isinstance(record, dict)]
    changed = normalize(records)
    args.seed.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Removed curated benchmark fields from {changed:,} source-discovered records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
