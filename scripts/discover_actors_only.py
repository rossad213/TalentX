#!/usr/bin/env python3
"""Discover source-backed Actor records without touching Music."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from expand_non_athlete_sources import (
    RECENT_ACTIVITY_YEARS,
    discover_category,
    make_record,
    make_session,
    normalize,
    update_taxonomy,
)
from pricing_model import apply_pricing_to_records, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_TAXONOMY = DATA / "taxonomy.json"
DEFAULT_MANIFEST = DATA / "actor_discovery_manifest.json"
DEFAULT_OVERRIDES = DATA / "pricing_overrides.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--actor-additions", type=int, default=1000)
    parser.add_argument("--per-occupation-limit", type=int, default=900)
    parser.add_argument("--minimum-sitelinks", type=int, default=10)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--sleep", type=float, default=.2)
    parser.add_argument("--allow-shortfall", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")
    records = [record for record in payload if isinstance(record, dict)]
    requested = max(0, int(args.actor_additions))
    if requested == 0:
        return 0

    existing_names = {normalize(str(record.get("name") or "")) for record in records}
    existing_source_ids = {str(record.get("sourceRecordId") or "") for record in records if record.get("sourceRecordId")}
    used_ids = {str(record.get("id")) for record in records if record.get("id")}
    used_tickers = {str(record.get("ticker")) for record in records if record.get("ticker")}

    current_year = datetime.now(timezone.utc).year
    recent_cutoff = current_year - RECENT_ACTIVITY_YEARS
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    session = make_session()
    candidates, source_errors = discover_category(
        session,
        "Actor",
        max(1, args.per_occupation_limit),
        args.request_timeout,
        args.sleep,
        max(1, args.minimum_sitelinks),
        recent_cutoff,
    )

    selected = []
    for candidate in candidates:
        key = normalize(str(candidate.get("name") or ""))
        qid = str(candidate.get("qid") or "")
        if not key or key in existing_names or qid in existing_source_ids:
            continue
        selected.append(candidate)
        existing_names.add(key)
        existing_source_ids.add(qid)
        if len(selected) >= requested:
            break

    if len(selected) < requested and not args.allow_shortfall:
        raise RuntimeError(f"Only found {len(selected)} eligible Actor records; requested {requested}.")

    counts = Counter(str(record.get("primaryCategory") or "") for record in records)
    pool_size = counts["Actor"] + len(selected)
    additions = [
        make_record(
            candidate,
            "Actor",
            counts["Actor"] + offset,
            pool_size,
            used_ids,
            used_tickers,
            verified_at,
        )
        for offset, candidate in enumerate(selected, start=1)
    ]
    combined = records + additions
    combined = apply_pricing_to_records(
        combined,
        load_overrides(DEFAULT_OVERRIDES),
        benchmark_records=combined,
        calibration_reference=combined,
    )
    args.seed.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    update_taxonomy(args.taxonomy, combined)
    manifest = {
        "version": "1.0-actor-only",
        "generatedAt": verified_at,
        "requestedActorAdditions": requested,
        "actualActorAdditions": len(additions),
        "musicRecordsAdded": 0,
        "sourceErrors": source_errors,
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Actor-only discovery added {len(additions):,} Actor records and 0 Music records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
