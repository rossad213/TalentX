#!/usr/bin/env python3
"""Ensure a curated set of notable soccer players is present in the current catalog.

Identity and club/status fields are taken from the same point-in-time ESPN roster
feeds used by the main catalog builder. Names that cannot be verified in a live
roster response are skipped rather than inserted with guessed current details.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_current_catalog import (
    ESPN_LEAGUES,
    build_market_fields,
    collect_espn_league,
    normalize,
)
from pricing_model import apply_pricing_to_records, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CATALOG = DATA / "current_catalog.json"
CATALOG_CSV = DATA / "current_catalog.csv"
MANIFEST = DATA / "catalog_manifest.json"
PRIORITY = DATA / "priority_soccer_names.json"
OVERRIDES = DATA / "pricing_overrides.json"


def read_array(path: Path) -> list[Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must contain a JSON array")
    return payload


def write_csv(records: list[dict[str, Any]]) -> None:
    fields = [
        "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
        "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
        "careerStage", "lastVerifiedAt", "verificationStatus", "sourceName",
        "sourceUrl", "sourceRecordId", "dataConfidence", "pricingConfidence",
        "pricingDataStatus", "pricingModelVersion", "marketPrice", "fundamentalValue",
        "careerScore", "talentScore", "marketScore", "confidenceScore", "fairValue",
    ]
    with CATALOG_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def sync_manifest(records: list[dict[str, Any]], verified_at: str) -> None:
    manifest: dict[str, Any] = {}
    if MANIFEST.exists():
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            manifest = payload

    category_counts = Counter(str(r.get("primaryCategory") or "Unknown") for r in records)
    sport_counts = Counter(
        str(r.get("discipline") or "Unknown")
        for r in records
        if r.get("primaryCategory") == "Athlete"
    )
    league_counts = Counter(
        str(r.get("leagueOrMedium") or "Unknown")
        for r in records
        if r.get("primaryCategory") == "Athlete"
    )
    automated = sum(1 for r in records if r.get("sourceNamespace") in {"espn", "nhl"})

    manifest.update({
        "generatedAt": verified_at,
        "currentSeedRecords": len(records),
        "currentCatalogRecords": len(records),
        "automatedRosterVerifiedRecords": automated,
        "categoryCounts": dict(sorted(category_counts.items())),
        "sportCounts": dict(sorted(sport_counts.items())),
        "leagueCounts": dict(league_counts.most_common()),
        "prioritySoccerMergeAt": verified_at,
    })
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def persist(records: list[dict[str, Any]], verified_at: str) -> None:
    CATALOG.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_csv(records)
    sync_manifest(records, verified_at)


def main() -> int:
    records = [item for item in read_array(CATALOG) if isinstance(item, dict)]
    requested = [str(name).strip() for name in read_array(PRIORITY) if str(name).strip()]
    wanted = {normalize(name): name for name in requested}
    existing = {
        normalize(str(record.get("name") or ""))
        for record in records
        if normalize(str(record.get("discipline") or "")) == normalize("Soccer")
    }
    remaining = set(wanted) - existing
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if not remaining:
        persist(records, verified_at)
        print("All priority soccer players are already present; manifest and CSV synchronized.")
        return 0

    found: dict[str, dict[str, Any]] = {}
    source_errors: list[str] = []
    soccer_leagues = [cfg for cfg in ESPN_LEAGUES if cfg.get("discipline") == "Soccer"]
    for cfg in soccer_leagues:
        result = collect_espn_league(cfg, workers=12)
        if result.error:
            source_errors.append(f"{cfg['label']}: {result.error}")
        for raw in result.records:
            key = normalize(str(raw.get("name") or ""))
            if key in remaining and key not in found:
                found[key] = raw
        remaining -= set(found)
        if not remaining:
            break

    used_tickers = {str(record.get("ticker") or "") for record in records if record.get("ticker")}
    additions = [build_market_fields(raw, verified_at, used_tickers) for raw in found.values()]

    by_key = {
        (normalize(str(record.get("name") or "")), normalize(str(record.get("discipline") or ""))): record
        for record in records
    }
    for record in additions:
        by_key[(normalize(record["name"]), normalize(record["discipline"]))] = record

    merged = list(by_key.values())
    merged = apply_pricing_to_records(merged, load_overrides(OVERRIDES))
    persist(merged, verified_at)

    print(f"Added or refreshed {len(additions)} priority soccer players.")
    if remaining:
        print("Not found in current roster feeds: " + ", ".join(wanted[key] for key in sorted(remaining)))
    if source_errors:
        print(f"Roster source warnings: {len(source_errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
