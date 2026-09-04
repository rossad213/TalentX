#!/usr/bin/env python3
"""Validate the generated TalentX current catalog before Pages deployment."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from category_market_store import CSV_FIELDS
from same_category_identity_dedupe import dedupe_same_category_identities

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# This validator also repairs safely provable same-category duplicate identities
# after category overlays are composed, so stale market overlays cannot re-add them.


def write_csv(records: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def repair_same_category_duplicates(records: list[dict], catalog_path: Path, csv_path: Path, manifest_path: Path) -> tuple[list[dict], int]:
    repaired, repairs = dedupe_same_category_identities(records)
    if not repairs:
        return records, 0

    catalog_path.write_text(json.dumps(repaired, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_csv(repaired, csv_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    category_counts = Counter(str(record.get("primaryCategory") or "") for record in repaired)
    manifest["totalRecords"] = len(repaired)
    manifest["currentCatalogRecords"] = len(repaired)
    manifest["categories"] = dict(category_counts)
    manifest["sameCategoryIdentityRepairs"] = int(manifest.get("sameCategoryIdentityRepairs") or 0) + len(repairs)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    market_dir = DATA / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    category_files = {
        "Athlete": "sports.json",
        "Music": "music.json",
        "Actor": "actors.json",
        "Creator": "creators.json",
    }
    for category, filename in category_files.items():
        category_records = [record for record in repaired if record.get("primaryCategory") == category]
        (market_dir / filename).write_text(
            json.dumps(category_records, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    print(f"Repaired {len(repairs):,} safe same-category duplicate listing(s) before validation.")
    for repair in repairs[:20]:
        print(
            f"  {repair.get('category')}: {repair.get('name')} — kept {repair.get('primaryId')}, "
            f"suppressed {repair.get('suppressedId')}"
        )
    return repaired, len(repairs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum", type=int, default=10_000)
    parser.add_argument("--minimum-automated", type=int, default=10_000)
    args = parser.parse_args()

    catalog_path = DATA / "current_catalog.json"
    csv_path = DATA / "current_catalog.csv"
    manifest_path = DATA / "catalog_manifest.json"
    source_manifest_path = DATA / "current_source_manifest.json"
    errors: list[str] = []

    for path in (catalog_path, csv_path, manifest_path, source_manifest_path):
        if not path.exists():
            errors.append(f"Missing {path.relative_to(ROOT)}")
    if errors:
        print("\n".join(errors))
        return 1

    records = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        errors.append("current_catalog.json is not an array")
        records = []
    else:
        records, _ = repair_same_category_duplicates(records, catalog_path, csv_path, manifest_path)

    if len(records) < args.minimum:
        errors.append(f"Catalog has {len(records):,} records; expected at least {args.minimum:,}")

    ids = [record.get("id") for record in records]
    tickers = [record.get("ticker") for record in records]
    if len(ids) != len(set(ids)):
        errors.append("Profile IDs are not unique")
    if len(tickers) != len(set(tickers)):
        errors.append("Ticker symbols are not unique")

    required = (
        "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
        "teamOrPlatform", "role", "careerStatus", "marketSegment", "marketPrice",
        "careerScore", "dataConfidence",
    )
    excluded_current_statuses = {
        "Retired — Legacy", "Legacy artist", "Group inactive", "Legacy",
        "Status under review", "Retirement announced",
    }
    for index, record in enumerate(records):
        missing = [key for key in required if record.get(key) in (None, "")]
        if missing:
            errors.append(f"Record {index} ({record.get('name')!r}) is missing {missing}")
            if len(errors) >= 30:
                break
        if record.get("marketSegment") != "Current":
            errors.append(f"Non-current record in current catalog: {record.get('name')}")
        status = str(record.get("careerStatus") or "")
        if status in excluded_current_statuses:
            errors.append(f"Legacy or unresolved record in current catalog: {record.get('name')} ({status})")

    automated = [record for record in records if record.get("sourceNamespace") in {"espn", "nhl"}]
    if len(automated) < args.minimum_automated:
        errors.append(
            f"Only {len(automated):,} records have automated current-roster verification; "
            f"expected at least {args.minimum_automated:,}"
        )
    for record in automated:
        if not record.get("lastVerifiedAt") or not record.get("sourceUrl") or not record.get("sourceRecordId"):
            errors.append(f"Automated record lacks source metadata: {record.get('name')}")
            if len(errors) >= 30:
                break

    category_counts = Counter(str(record.get("primaryCategory")) for record in records)
    discipline_counts = Counter(
        str(record.get("discipline"))
        for record in records
        if record.get("primaryCategory") == "Athlete"
    )

    with csv_path.open(encoding="utf-8", newline="") as handle:
        csv_count = sum(1 for _ in csv.DictReader(handle))
    if csv_count != len(records):
        errors.append(f"CSV count {csv_count:,} does not match JSON count {len(records):,}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("currentCatalogRecords") != len(records):
        errors.append("Manifest currentCatalogRecords does not match catalog")

    print(f"Current records: {len(records):,}")
    print(f"Automated roster verified: {len(automated):,}")
    print("Categories:")
    for category, count in category_counts.most_common():
        print(f"  {category}: {count:,}")
    print("Athlete disciplines:")
    for discipline, count in discipline_counts.most_common():
        print(f"  {discipline}: {count:,}")

    if errors:
        print("\nVALIDATION ERRORS")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())