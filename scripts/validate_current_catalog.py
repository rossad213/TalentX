#!/usr/bin/env python3
"""Validate the generated TalentX current catalog before Pages deployment."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum", type=int, default=19_500)
    parser.add_argument("--minimum-automated", type=int, default=10_000)
    parser.add_argument("--minimum-music", type=int, default=5_100)
    parser.add_argument("--minimum-actors", type=int, default=400)
    parser.add_argument("--minimum-creators", type=int, default=200)
    parser.add_argument("--minimum-baseball", type=int, default=500)
    parser.add_argument("--minimum-tennis", type=int, default=400)
    parser.add_argument("--minimum-golf", type=int, default=300)
    parser.add_argument("--minimum-motorsport", type=int, default=300)
    parser.add_argument("--minimum-combat", type=int, default=300)
    parser.add_argument("--minimum-cricket", type=int, default=200)
    parser.add_argument("--minimum-soccer", type=int, default=2_000)
    args = parser.parse_args()

    catalog_path = DATA / "current_catalog.json"
    csv_path = DATA / "current_catalog.csv"
    manifest_path = DATA / "catalog_manifest.json"
    source_manifest_path = DATA / "current_source_manifest.json"
    expansion_manifest_path = DATA / "catalog_expansion_manifest.json"
    errors: list[str] = []

    for path in (catalog_path, csv_path, manifest_path, source_manifest_path, expansion_manifest_path):
        if not path.exists():
            errors.append(f"Missing {path.relative_to(ROOT)}")
    if errors:
        print("\n".join(errors))
        return 1

    records = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        errors.append("current_catalog.json is not an array")
        records = []
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

    expansion = [record for record in records if record.get("sourceNamespace") == "wikipedia-wikidata"]
    if len(expansion) < 9_400:
        errors.append(f"Only {len(expansion):,} Wikipedia/Wikidata expansion records found; expected 9,400")
    for record in expansion:
        if not record.get("sourceUrl") or not record.get("sourceRecordId") or not record.get("lastVerifiedAt"):
            errors.append(f"Expansion record lacks source metadata: {record.get('name')}")
            if len(errors) >= 30:
                break
        if not str(record.get("pricingDataStatus") or "").startswith("Provisional"):
            errors.append(f"Expansion record is not provisional: {record.get('name')}")
            break
        fundamental = float(record.get("fundamentalValue") or record.get("fundamental") or 0)
        if fundamental > 62.01:
            errors.append(f"Expansion record exceeded limited-evidence cap: {record.get('name')} (${fundamental:.2f})")
            break

    category_counts = Counter(str(record.get("primaryCategory")) for record in records)
    category_minimums = {
        "Music": args.minimum_music,
        "Actor": args.minimum_actors,
        "Creator": args.minimum_creators,
    }
    for category, minimum in category_minimums.items():
        if category_counts[category] < minimum:
            errors.append(f"Only {category_counts[category]:,} {category} records; expected at least {minimum:,}")

    discipline_counts = Counter(
        str(record.get("discipline"))
        for record in records
        if record.get("primaryCategory") == "Athlete"
    )
    discipline_minimums = {
        "Baseball": args.minimum_baseball,
        "Tennis": args.minimum_tennis,
        "Golf": args.minimum_golf,
        "Motorsport": args.minimum_motorsport,
        "Combat Sports": args.minimum_combat,
        "Cricket": args.minimum_cricket,
        "Soccer": args.minimum_soccer,
    }
    for discipline, minimum in discipline_minimums.items():
        if discipline_counts[discipline] < minimum:
            errors.append(
                f"Only {discipline_counts[discipline]:,} {discipline} athletes; expected at least {minimum:,}"
            )

    with csv_path.open(encoding="utf-8", newline="") as handle:
        csv_count = sum(1 for _ in csv.DictReader(handle))
    if csv_count != len(records):
        errors.append(f"CSV count {csv_count:,} does not match JSON count {len(records):,}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("currentCatalogRecords") != len(records):
        errors.append("Manifest currentCatalogRecords does not match catalog")

    expansion_manifest = json.loads(expansion_manifest_path.read_text(encoding="utf-8"))
    if expansion_manifest.get("generatedTotalAdditions") != 9_400:
        errors.append("Expansion manifest does not report exactly 9,400 additions")

    print(f"Current records: {len(records):,}")
    print(f"Automated roster verified: {len(automated):,}")
    print(f"Wikipedia/Wikidata expansion: {len(expansion):,}")
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
