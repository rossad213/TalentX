#!/usr/bin/env python3
"""Create, compose, and finalize category-owned TalentX market state.

TalentX publishes one catalog, but each market category can own its operational
state independently. Category workflows use ``extract`` to create a small
working catalog and the publisher uses ``merge`` to compose those states back
onto the latest full-catalog baseline. ``finalize`` performs the global checks
that only make sense after all categories have been recombined.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
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

TICKER_CATEGORY_CODES = {
    "Athlete": "S",
    "Music": "M",
    "Actor": "A",
    "Creator": "C",
}


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


def _ticker_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _identity_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _verified_current_athlete(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("marketSegment") or "") == "Current"
        and str(record.get("sourceNamespace") or "").lower() in {"espn", "nhl"}
    )


def _athlete_identity_score(record: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(record.get("dataConfidence") or 0),
        float(record.get("pricingConfidence") or 0),
        float(record.get("careerScore") or 0),
    )


def resolve_cross_category_identities(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge conservative cross-category duplicates into a verified athlete.

    Exact-name matches alone are not enough to prove identity. Automatic merging
    is therefore limited to groups containing a source-verified current athlete
    (ESPN/NHL) plus one or more non-athlete records with the same normalized name.
    The athlete remains the primary TalentX listing; the removed records become
    secondary career activity on that listing so their context remains searchable.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = _identity_name(record.get("name"))
        if key:
            groups.setdefault(key, []).append(record)

    suppressed_ids: set[str] = set()
    repairs: list[dict[str, Any]] = []

    for key, group in groups.items():
        categories = {str(record.get("primaryCategory") or "") for record in group}
        if len(categories) < 2:
            continue
        verified_athletes = [record for record in group if _verified_current_athlete(record)]
        if not verified_athletes:
            continue

        winner = max(verified_athletes, key=_athlete_identity_score)
        secondary = [
            record for record in group
            if record is not winner and str(record.get("primaryCategory") or "") != "Athlete"
        ]
        if not secondary:
            continue

        activities = list(winner.get("secondaryCareerActivities") or [])
        existing_activity_ids = {str(item.get("sourceRecordId") or item.get("id") or "") for item in activities if isinstance(item, dict)}
        secondary_categories = set(str(item) for item in (winner.get("secondaryCategories") or []) if item)
        search_terms = [str(winner.get("searchText") or "")]

        for record in secondary:
            record_id = str(record.get("id") or "")
            activity_key = str(record.get("sourceRecordId") or record_id)
            activity = {
                "id": record_id,
                "sourceRecordId": str(record.get("sourceRecordId") or ""),
                "category": str(record.get("primaryCategory") or ""),
                "discipline": str(record.get("discipline") or ""),
                "leagueOrMedium": str(record.get("leagueOrMedium") or ""),
                "teamOrPlatform": str(record.get("teamOrPlatform") or ""),
                "role": str(record.get("role") or ""),
                "sourceName": str(record.get("sourceName") or ""),
                "sourceUrl": str(record.get("sourceUrl") or ""),
            }
            if activity_key not in existing_activity_ids:
                activities.append(activity)
                existing_activity_ids.add(activity_key)
            secondary_categories.add(activity["category"])
            search_terms.extend([
                str(record.get("name") or ""), activity["category"], activity["discipline"],
                activity["leagueOrMedium"], activity["teamOrPlatform"], activity["role"],
            ])
            if record_id:
                suppressed_ids.add(record_id)
            repairs.append({
                "identityKey": key,
                "name": str(winner.get("name") or ""),
                "primaryId": str(winner.get("id") or ""),
                "primaryCategory": "Athlete",
                "suppressedId": record_id,
                "suppressedCategory": activity["category"],
            })

        winner["secondaryCareerActivities"] = activities
        winner["secondaryCategories"] = sorted(category for category in secondary_categories if category)
        winner["identityResolutionStatus"] = "Verified athlete is primary; secondary cross-category listings merged"
        winner["searchText"] = " ".join(term for term in search_terms if term).lower()

    resolved = [record for record in records if str(record.get("id") or "") not in suppressed_ids]
    return resolved, repairs


def _replacement_ticker(record: dict[str, Any], original: str, used: set[str]) -> str:
    stem = "".join(character for character in original.upper() if character.isalnum()) or "TX"
    category_code = TICKER_CATEGORY_CODES.get(str(record.get("primaryCategory") or ""), "X")
    record_id = str(record.get("id") or record.get("name") or original)
    digest = hashlib.sha1(record_id.encode("utf-8")).hexdigest().upper()
    # Keep replacement tickers compact while making them stable across every
    # publisher run. Extend the hash only if an extremely rare collision occurs.
    for hash_length in range(2, len(digest) + 1):
        candidate = f"{stem[:5]}{category_code}{digest[:hash_length]}"
        if candidate not in used:
            return candidate
    raise RuntimeError(f"Could not create a unique ticker for {record_id}")


def dedupe_tickers(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Resolve only global ticker collisions after category composition.

    The first occurrence keeps its existing ticker. Later records with the same
    ticker receive a deterministic ID-derived ticker, so rerunning publication
    does not churn symbols from one deploy to the next.
    """
    used: set[str] = set()
    repairs: list[dict[str, str]] = []
    for record in records:
        original = _ticker_key(record.get("ticker"))
        if not original:
            continue
        if original not in used:
            used.add(original)
            continue
        replacement = _replacement_ticker(record, original, used)
        record["ticker"] = replacement
        used.add(replacement)
        repairs.append({
            "id": str(record.get("id") or ""),
            "name": str(record.get("name") or ""),
            "category": str(record.get("primaryCategory") or ""),
            "from": original,
            "to": replacement,
        })
    return repairs


def refresh_manifest(
    records: list[dict[str, Any]],
    path: Path,
    ticker_repairs: int = 0,
    identity_repairs: int = 0,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                manifest = dict(payload)
        except (json.JSONDecodeError, OSError):
            manifest = {}

    categories = Counter(str(record.get("primaryCategory") or "") for record in records)
    disciplines = Counter(
        str(record.get("discipline") or "")
        for record in records
        if record.get("primaryCategory") == "Athlete"
    )
    automated = [record for record in records if record.get("sourceNamespace") in {"espn", "nhl"}]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    manifest.update({
        "buildDate": now.date().isoformat(),
        "totalRecords": len(records),
        "categories": dict(categories),
        "currentDisciplines": dict(disciplines),
        "currentCatalogRecords": len(records),
        "automatedRosterVerifiedRecords": len(automated),
        "currentCatalogFile": "data/current_catalog.json",
        "currentCatalogCsv": "data/current_catalog.csv",
        "marketDataMode": "Category-owned event-driven simulated market",
        "statusDataMode": "Unified from latest healthy category market states",
        "unifiedMarketFinalizedAt": now.isoformat().replace("+00:00", "Z"),
        "tickerCollisionRepairs": int(ticker_repairs),
        "crossCategoryIdentityRepairs": int(identity_repairs),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def finalize_catalog(catalog: Path, csv_path: Path, manifest_path: Path) -> tuple[int, int]:
    records = load_records(catalog)
    records, identity_repairs = resolve_cross_category_identities(records)
    repairs = dedupe_tickers(records)
    write_records(catalog, records)
    write_csv(records, csv_path)
    refresh_manifest(
        records,
        manifest_path,
        ticker_repairs=len(repairs),
        identity_repairs=len(identity_repairs),
    )
    return len(records), len(repairs)


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

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--catalog", type=Path, required=True)
    finalize.add_argument("--csv", type=Path, required=True)
    finalize.add_argument("--manifest", type=Path, required=True)

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

    if args.command == "finalize":
        count, repaired = finalize_catalog(args.catalog, args.csv, args.manifest)
        print(f"Finalized {count:,} unified records; repaired {repaired:,} ticker collision(s).")
        return 0

    records = load_records(args.catalog)
    write_csv(records, args.output)
    print(f"Wrote {len(records):,} catalog rows to {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
