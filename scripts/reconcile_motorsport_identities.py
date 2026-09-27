#!/usr/bin/env python3
"""Collapse duplicate Motorsport people into one canonical TalentX identity.

The Motorsport seed historically mixed an official Formula 1 roster, prototype
anchors, and broad Wikidata discovery. This reconciler keeps one person-level
asset, strongly preferring the official F1 roster when present, while preserving
verified event/history evidence from aliases. A small explicit alias map covers
well-known short/full-name variants that normalization alone cannot resolve.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

F1_ALIASES = {
    "andreakimiantonelli": "kimiantonelli",
    "kimiantonelli": "kimiantonelli",
    "alexalbon": "alexanderalbon",
    "alexanderalbon": "alexanderalbon",
    "checoperez": "sergioperez",
    "sergioperez": "sergioperez",
}

LAST_EVENT_FIELDS = (
    "lastPriceEventAt", "lastPriceEvent", "lastPriceEventId", "lastEventMovePct",
    "lastEventType", "lastEventSource", "lastGameMovePct",
    "lastGamePerformanceDeltaPct", "lastGameStats",
)


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def identity_key(record: dict[str, Any]) -> str:
    key = normalize_name(record.get("name"))
    return F1_ALIASES.get(key, key)


def is_motorsport(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() == "motorsport"
    )


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def canonical_score(record: dict[str, Any]) -> tuple[Any, ...]:
    namespace = str(record.get("sourceNamespace") or "").strip().lower()
    source_type = str(record.get("sourceType") or "").strip().lower()
    league = str(record.get("leagueOrMedium") or "").strip().lower()
    record_id = str(record.get("id") or "")
    official_f1 = int(
        league == "formula 1"
        and (namespace == "curated-individual-sport-roster" or source_type == "official-ranking-roster")
    )
    verified = int(bool(record.get("professionEvidenceVerified")))
    canonical_id = int(record_id.startswith("athlete-motorsport-"))
    events = len(record.get("priceEvents") or []) if isinstance(record.get("priceEvents"), list) else 0
    history = len(record.get("priceHistory") or []) if isinstance(record.get("priceHistory"), list) else 0
    confidence = _num(record.get("pricingConfidence", record.get("dataConfidence", 0)))
    last_verified = str(record.get("lastVerifiedAt") or "")
    return official_f1, verified, canonical_id, events, history, confidence, last_verified


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


def merge_list_field(group: list[dict[str, Any]], canonical: dict[str, Any], field: str, key_fn) -> list[dict[str, Any]]:
    merged: dict[Any, dict[str, Any]] = {}
    for record in [item for item in group if item is not canonical] + [canonical]:
        values = record.get(field) if isinstance(record.get(field), list) else []
        for value in values:
            if not isinstance(value, dict):
                continue
            key = key_fn(value)
            if not any(str(part) for part in key):
                continue
            merged[key] = dict(value)
    return sorted(merged.values(), key=lambda item: str(item.get("startedAt") or item.get("time") or ""))


def reconcile_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if is_motorsport(record):
            key = identity_key(record)
            if key:
                groups[key].append(record)

    replacements: dict[str, dict[str, Any]] = {}
    suppressed: set[str] = set()
    repairs: list[dict[str, Any]] = []

    for key, group in groups.items():
        if len(group) < 2:
            continue
        canonical = max(group, key=canonical_score)
        canonical_id = str(canonical.get("id") or "")
        if not canonical_id:
            continue
        result = dict(canonical)
        result["priceEvents"] = merge_list_field(group, canonical, "priceEvents", event_key)
        result["priceHistory"] = merge_list_field(group, canonical, "priceHistory", history_key)

        latest_event_record = max(group, key=lambda item: str(item.get("lastPriceEventAt") or ""))
        for field in LAST_EVENT_FIELDS:
            if field in latest_event_record:
                result[field] = latest_event_record[field]

        official_rows = [
            item for item in group
            if str(item.get("leagueOrMedium") or "").strip().lower() == "formula 1"
            and str(item.get("sourceNamespace") or "").strip().lower() == "curated-individual-sport-roster"
        ]
        if official_rows:
            official = max(official_rows, key=canonical_score)
            for field in (
                "name", "leagueOrMedium", "teamOrPlatform", "role", "country", "careerStatus",
                "sourceName", "sourceUrl", "sourceNamespace", "sourceType", "sourceAsOf",
                "sourceRank", "statusSource", "rosterSourceName", "rosterSourceUrl",
                "rosterSourceAsOf", "rosterSourceRank", "rosterVersion", "rosterGroup", "rosterPriority",
            ):
                if official.get(field) is not None:
                    result[field] = official.get(field)

        aliases = sorted(
            str(item.get("id") or "") for item in group
            if str(item.get("id") or "") and str(item.get("id") or "") != canonical_id
        )
        result["motorsportIdentityReconciled"] = True
        result["motorsportCanonicalIdentityKey"] = key
        result["motorsportCanonicalAliasIds"] = aliases
        replacements[canonical_id] = result

        for item in group:
            record_id = str(item.get("id") or "")
            if record_id and record_id != canonical_id:
                suppressed.add(record_id)
                repairs.append({
                    "name": str(result.get("name") or item.get("name") or ""),
                    "canonicalId": canonical_id,
                    "suppressedId": record_id,
                    "reason": "same Motorsport person identity",
                })

    output: list[dict[str, Any]] = []
    for record in records:
        record_id = str(record.get("id") or "")
        if record_id in suppressed:
            continue
        output.append(replacements.get(record_id, dict(record)))
    return output, repairs


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = sorted({key for record in records for key, value in record.items() if not isinstance(value, (dict, list))})
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
    print(f"Motorsport identity reconciliation: {len(records):,} -> {len(reconciled):,}; suppressed {len(repairs):,} duplicate listing(s).")
    for repair in repairs[:40]:
        print(f"  {repair['name']}: {repair['suppressedId']} -> {repair['canonicalId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
