#!/usr/bin/env python3
"""Build browser-friendly TalentX data from the authoritative current catalog.

The full current_catalog.json remains untouched as the source of truth and
fallback. This script creates:
  * catalog_index.json: compact fields needed for Dashboard/Market/search.
  * profile_shards/*.json: full records split across a fixed number of shards.

This keeps initial browser memory bounded while preserving the exact same
profile data, pricing evidence, and chart/event history on demand.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SHARD_COUNT = 128

INDEX_FIELDS = (
    "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
    "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
    "careerStage", "marketPrice", "dailyChange", "careerScore",
    "fundamentalValue", "pricingConfidence", "dataConfidence", "avatar",
    "searchText", "modelType", "demandPremiumPct", "momentumPct", "volume",
    "lastPriceEventId", "lastGameMovePct", "lastPriceEventAt", "lastPriceEvent",
    "verificationStatus", "lastVerifiedAt", "statusSource", "sourceName",
    "pricingDataStatus", "pricingModelVersion", "priceHistoryStatus",
)


def fnv1a_bucket(value: str) -> int:
    h = 2166136261
    for ch in value:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h % SHARD_COUNT


def load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    if not records:
        raise ValueError(f"{path} contains no records")
    return records


def compact_record(record: dict[str, Any]) -> dict[str, Any]:
    item = {field: record.get(field) for field in INDEX_FIELDS if field in record}
    trend = record.get("trend")
    if isinstance(trend, list) and trend:
        # Dashboard only needs a small sparkline; the full profile gets the full
        # event/chart history from its shard.
        item["trend"] = trend[-8:]
    item["__compact"] = True
    return item


def patch_manifest(manifest_path: Path, records: list[dict[str, Any]]) -> None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            manifest = {}
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}

    current_count = len(records)
    legacy = int(manifest.get("legacyRecords") or 0)
    review = int(manifest.get("underReviewRecords") or 0)
    automated = sum(1 for r in records if r.get("lastVerifiedAt"))
    enriched = sum(
        1 for r in records
        if str(r.get("pricingDataStatus") or "").lower() not in {"", "provisional", "fallback"}
    )
    manifest["currentCatalogRecords"] = current_count
    manifest["currentSeedRecords"] = current_count
    manifest["totalRecords"] = current_count + legacy + review
    manifest["automatedRosterVerifiedRecords"] = automated
    manifest["pricingEnrichedRecords"] = enriched
    manifest["clientCatalogMode"] = "compact-index-plus-profile-shards"
    manifest["clientProfileShardCount"] = SHARD_COUNT
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(catalog_path: Path, index_path: Path, shards_dir: Path, manifest_path: Path) -> None:
    records = load_catalog(catalog_path)
    index = [compact_record(record) for record in records]

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    shards_dir.mkdir(parents=True, exist_ok=True)
    for old in shards_dir.glob("*.json"):
        old.unlink()

    buckets: list[dict[str, dict[str, Any]]] = [dict() for _ in range(SHARD_COUNT)]
    for record in records:
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            raise ValueError("Every current record must have an id")
        buckets[fnv1a_bucket(record_id)][record_id] = record

    for number, payload in enumerate(buckets):
        (shards_dir / f"{number:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    patch_manifest(manifest_path, records)

    full_bytes = catalog_path.stat().st_size
    index_bytes = index_path.stat().st_size
    print(f"Built compact index for {len(records):,} records")
    print(f"Full catalog: {full_bytes / 1024 / 1024:.2f} MiB")
    print(f"Compact index: {index_bytes / 1024 / 1024:.2f} MiB ({index_bytes / full_bytes:.1%} of full)")
    print(f"Profile shards: {SHARD_COUNT}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    parser.add_argument("--index", type=Path, default=Path("data/catalog_index.json"))
    parser.add_argument("--shards", type=Path, default=Path("data/profile_shards"))
    parser.add_argument("--manifest", type=Path, default=Path("data/catalog_manifest.json"))
    args = parser.parse_args()
    build(args.catalog, args.index, args.shards, args.manifest)


if __name__ == "__main__":
    main()
