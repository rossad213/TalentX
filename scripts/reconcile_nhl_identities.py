#!/usr/bin/env python3
"""Collapse duplicate NHL provider identities into one canonical TalentX asset.

NHL player IDs are provider-stable identity evidence. If multiple NHL rows share
one non-empty sourceRecordId, they represent the same player even when one name
contains accents and another does not. Prefer the current live-nhl-* identity,
merge durable event/history ledgers from aliases, and keep current market and
fundamental fields from the canonical row.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

LAST_EVENT_FIELDS = (
    "lastPriceEventAt",
    "lastPriceEvent",
    "lastPriceEventId",
    "lastEventMovePct",
    "lastEventType",
    "lastEventSource",
    "lastGameMovePct",
    "lastGamePerformanceDeltaPct",
    "lastGameStats",
)


def is_nhl(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("leagueOrMedium") or "").strip().upper() == "NHL"
        and str(record.get("sourceNamespace") or "").strip().lower() == "nhl"
        and bool(str(record.get("sourceRecordId") or "").strip())
    )


def canonical_score(record: dict[str, Any]) -> tuple[Any, ...]:
    record_id = str(record.get("id") or "")
    live = int(record_id.startswith("live-nhl-"))
    verified_at = str(record.get("lastVerifiedAt") or "")
    data_confidence = float(record.get("dataConfidence") or 0.0)
    pricing_confidence = float(record.get("pricingConfidence") or 0.0)
    return live, verified_at, data_confidence, pricing_confidence


def event_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("eventKey") or item.get("eventId") or ""),
        str(item.get("startedAt") or item.get("time") or ""),
        str(item.get("eventType") or ""),
    )


def history_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("time") or ""),
        str(item.get("eventId") or item.get("eventKey") or ""),
        str(item.get("phase") or ""),
    )


def merge_list_field(
    group: list[dict[str, Any]],
    canonical: dict[str, Any],
    field: str,
    key_fn,
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    # Aliases first; canonical last so any exact collision keeps the canonical
    # event/price representation while still retaining alias-only history.
    ordered = [item for item in group if item is not canonical] + [canonical]
    for record in ordered:
        values = record.get(field) if isinstance(record.get(field), list) else []
        for value in values:
            if not isinstance(value, dict):
                continue
            key = key_fn(value)
            if not any(key):
                continue
            merged[key] = dict(value)
    return sorted(
        merged.values(),
        key=lambda item: str(item.get("startedAt") or item.get("time") or ""),
    )


def reconcile_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if is_nhl(record):
            groups[str(record.get("sourceRecordId") or "").strip()].append(record)

    replacements: dict[str, dict[str, Any]] = {}
    suppressed: set[str] = set()
    repairs: list[dict[str, Any]] = []

    for source_id, group in groups.items():
        if len(group) < 2:
            continue
        canonical = max(group, key=canonical_score)
        canonical_id = str(canonical.get("id") or "")
        if not canonical_id:
            continue

        result = dict(canonical)
        result["priceEvents"] = merge_list_field(group, canonical, "priceEvents", event_key)
        result["priceHistory"] = merge_list_field(group, canonical, "priceHistory", history_key)

        # Last-event scalar fields should follow the chronologically latest row,
        # but never replace the canonical row's current market/fundamental state.
        latest_event_record = max(group, key=lambda item: str(item.get("lastPriceEventAt") or ""))
        for field in LAST_EVENT_FIELDS:
            if field in latest_event_record:
                result[field] = latest_event_record[field]

        aliases = sorted(
            str(item.get("id") or "")
            for item in group
            if str(item.get("id") or "") and str(item.get("id") or "") != canonical_id
        )
        result["nhlCanonicalAliasIds"] = aliases
        result["nhlIdentityReconciled"] = True
        result["nhlCanonicalSourceRecordId"] = source_id
        replacements[canonical_id] = result

        for item in group:
            record_id = str(item.get("id") or "")
            if record_id and record_id != canonical_id:
                suppressed.add(record_id)
                repairs.append({
                    "name": str(canonical.get("name") or item.get("name") or ""),
                    "sourceRecordId": source_id,
                    "canonicalId": canonical_id,
                    "suppressedId": record_id,
                    "reason": "shared NHL provider player identity",
                })

    output: list[dict[str, Any]] = []
    for record in records:
        record_id = str(record.get("id") or "")
        if record_id in suppressed:
            continue
        if record_id in replacements:
            output.append(replacements[record_id])
        else:
            output.append(dict(record))
    return output, repairs


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = sorted({
        key for record in records for key, value in record.items()
        if not isinstance(value, (dict, list))
    })
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    reconciled, repairs = reconcile_records(records)
    args.catalog.write_text(json.dumps(reconciled, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.catalog.name == "current_catalog.json":
        write_csv(args.catalog.with_suffix(".csv"), reconciled)

    print(
        f"NHL identity reconciliation: {len(records):,} -> {len(reconciled):,}; "
        f"suppressed {len(repairs):,} duplicate listing(s)."
    )
    for repair in repairs[:20]:
        print(
            f"  {repair['name']}: {repair['suppressedId']} -> {repair['canonicalId']} "
            f"(NHL player {repair['sourceRecordId']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
