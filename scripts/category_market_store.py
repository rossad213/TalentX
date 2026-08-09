#!/usr/bin/env python3
"""Create and compose category-owned TalentX market state.

TalentX publishes one catalog, but each market category can own its operational
state independently. Category workflows use ``extract`` to create a small
working catalog and the publisher uses ``merge`` to compose those states back
onto the latest full-catalog baseline.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

try:
    from merge_hourly_market_state import MARKET_STATE_FIELDS
except ModuleNotFoundError:  # Imported as scripts.category_market_store in tests.
    from scripts.merge_hourly_market_state import MARKET_STATE_FIELDS

CATEGORY_ALIASES = {
    "sports": "Athlete",
    "sport": "Athlete",
    "athlete": "Athlete",
    "athletes": "Athlete",
    "music": "Music",
    "actor": "Actor",
    "actors": "Actor",
    "creator": "Creator",
    "creators": "Creator",
}

CSV_FIELDS = [
    "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
    "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
    "careerStage", "lastVerifiedAt", "verificationStatus", "sourceName",
    "sourceUrl", "sourceRecordId", "dataConfidence", "pricingConfidence",
    "pricingDataStatus", "pricingModelVersion", "marketPrice", "careerScore",
    "fundamentalValue", "draftYear", "draftRound", "draftPick",
    "professionalGames", "hourlyChangePct", "lastPriceEventAt",
]

# Fields that are useful chart/market metadata but predate the original hourly
# overlay helper. They should survive a full-catalog rebuild along with the
# authoritative event market fields.
EXTRA_MARKET_FIELDS = (
    "priceHistoryStatus",
    "hourlyEvidenceCheckedAt",
    "hourlyEvidenceWarning",
)


def primary_category(value: str) -> str:
    key = str(value or "").strip().lower()
    if key in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[key]
    if value in {"Athlete", "Music", "Actor", "Creator"}:
        return value
    raise ValueError(f"Unsupported TalentX category: {value}")


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def extract_category(records: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    expected = primary_category(category)
    return [dict(record) for record in records if str(record.get("primaryCategory") or "") == expected]


def validate_overlay(overlay: list[dict[str, Any]], category: str) -> str:
    expected = primary_category(category)
    wrong = sorted({str(item.get("primaryCategory") or "") for item in overlay if str(item.get("primaryCategory") or "") != expected})
    if wrong:
        raise ValueError(f"Category overlay for {expected} contains other categories: {', '.join(wrong)}")
    ids = [str(item.get("id") or "") for item in overlay]
    if any(not item for item in ids):
        raise ValueError(f"Category overlay for {expected} contains a record without an id")
    if len(ids) != len(set(ids)):
        raise ValueError(f"Category overlay for {expected} contains duplicate ids")
    return expected


def merge_category(
    base: list[dict[str, Any]],
    overlay: list[dict[str, Any]],
    category: str,
    mode: str,
) -> tuple[list[dict[str, Any]], int]:
    expected = validate_overlay(overlay, category)
    overlay_by_id = {str(item.get("id")): dict(item) for item in overlay}
    merged: list[dict[str, Any]] = []
    touched = 0
    seen: set[str] = set()
    base_ids = {str(item.get("id") or "") for item in base}

    for record in base:
        result = dict(record)
        record_id = str(result.get("id") or "")
        prior = overlay_by_id.get(record_id)
        if str(result.get("primaryCategory") or "") == expected and prior is not None:
            if mode == "replace":
                result = dict(prior)
            elif mode == "market":
                for field in (*MARKET_STATE_FIELDS, *EXTRA_MARKET_FIELDS):
                    if field in prior:
                        result[field] = prior[field]
                # A new baseline can refresh fundamentals without creating a
                # fake market move merely because the catalog was rebuilt.
                result["dailyChange"] = 0.0
                result["hourlyChangePct"] = 0.0
            else:
                raise ValueError(f"Unsupported merge mode: {mode}")
            touched += 1
            seen.add(record_id)
        merged.append(result)

    # Category workflows begin from the full baseline, so additions are rare,
    # but retaining an independently discovered category record is safer than
    # silently dropping it during publication.
    for record_id, record in overlay_by_id.items():
        if record_id not in seen and record_id not in base_ids:
            merged.append(dict(record))
            touched += 1

    return merged, touched


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract")
    extract.add_argument("--catalog", type=Path, required=True)
    extract.add_argument("--category", required=True)
    extract.add_argument("--output", type=Path, required=True)

    merge = subparsers.add_parser("merge")
    merge.add_argument("--base", type=Path, required=True)
    merge.add_argument("--overlay", type=Path, required=True)
    merge.add_argument("--category", required=True)
    merge.add_argument("--mode", choices=("replace", "market"), required=True)

    csv_parser = subparsers.add_parser("csv")
    csv_parser.add_argument("--catalog", type=Path, required=True)
    csv_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "extract":
        records = load_records(args.catalog)
        selected = extract_category(records, args.category)
        if not selected:
            raise SystemExit(f"No records found for category {args.category}")
        write_records(args.output, selected)
        print(f"Extracted {len(selected):,} {primary_category(args.category)} records to {args.output}.")
        return 0

    if args.command == "merge":
        base = load_records(args.base)
        overlay = load_records(args.overlay)
        merged, touched = merge_category(base, overlay, args.category, args.mode)
        write_records(args.base, merged)
        print(f"Merged {touched:,} {primary_category(args.category)} records in {args.mode} mode.")
        return 0

    records = load_records(args.catalog)
    write_csv(records, args.output)
    print(f"Wrote {len(records):,} catalog rows to {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
