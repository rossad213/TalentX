#!/usr/bin/env python3
"""Expand the Creator-owned TalentX market state without rebuilding all categories.

This script is designed for the Creator market workflow. It starts from the latest
verified full-catalog baseline plus the last-known-good Creator overlay, discovers
source-backed creator identities, prices only the new Creator records, and writes
an updated Creator-only overlay. Athlete, Music, and Actor records are never
rewritten by this path.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_non_athlete_catalog import normalize
from discover_creators_only import discover_creators, make_creator_record
from expand_non_athlete_sources import RECENT_ACTIVITY_YEARS
from pricing_model import apply_pricing_to_records, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OVERRIDES = DATA / "pricing_overrides.json"


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def unique_by_id(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        record_id = str(record.get("id") or "").strip()
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        output.append(dict(record))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--creator-catalog", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DATA / "creator_discovery_manifest.json")
    parser.add_argument("--target-total", type=int, default=1000)
    parser.add_argument("--minimum-total", type=int, default=900)
    parser.add_argument("--per-occupation-limit", type=int, default=1200)
    parser.add_argument("--minimum-sitelinks", type=int, default=3)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--sleep", type=float, default=.2)
    parser.add_argument("--allow-shortfall", action="store_true")
    args = parser.parse_args()

    baseline = load_records(args.baseline)
    creator_state = unique_by_id(
        [record for record in load_records(args.creator_catalog) if record.get("primaryCategory") == "Creator"]
    )
    starting_count = len(creator_state)
    target_total = max(starting_count, int(args.target_total))
    additions_needed = max(0, target_total - starting_count)
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    recent_cutoff = datetime.now(timezone.utc).year - RECENT_ACTIVITY_YEARS

    source_errors: list[str] = []
    selected: list[dict[str, Any]] = []
    candidate_count = 0

    if additions_needed:
        identity_pool = unique_by_id(baseline + creator_state)
        existing_names = {normalize(str(record.get("name") or "")) for record in identity_pool}
        existing_source_ids = {
            str(record.get("sourceRecordId") or "")
            for record in identity_pool
            if record.get("sourceRecordId")
        }
        used_ids = {str(record.get("id") or "") for record in identity_pool if record.get("id")}
        used_tickers = {str(record.get("ticker") or "") for record in identity_pool if record.get("ticker")}

        candidates, source_errors = discover_creators(
            args.per_occupation_limit,
            args.request_timeout,
            args.sleep,
            args.minimum_sitelinks,
            recent_cutoff,
        )
        candidate_count = len(candidates)
        for candidate in candidates:
            name_key = normalize(str(candidate.get("name") or ""))
            qid = str(candidate.get("qid") or "")
            if not name_key or name_key in existing_names or qid in existing_source_ids:
                continue
            selected.append(candidate)
            existing_names.add(name_key)
            existing_source_ids.add(qid)
            if len(selected) >= additions_needed:
                break

        projected_total = starting_count + len(selected)
        if projected_total < int(args.minimum_total) and not args.allow_shortfall:
            detail = "; ".join(source_errors[-5:]) if source_errors else "no source error reported"
            raise RuntimeError(
                f"Creator-only discovery reached only {projected_total} records; "
                f"minimum is {args.minimum_total}. Recent source detail: {detail}"
            )

        raw_additions = [
            make_creator_record(
                candidate,
                starting_count + offset,
                max(projected_total, target_total),
                used_ids,
                used_tickers,
                verified_at,
            )
            for offset, candidate in enumerate(selected, start=1)
        ]

        # Preserve all existing market/event state. Only newly discovered Creator
        # records receive initial model pricing here.
        priced_additions = apply_pricing_to_records(
            raw_additions,
            load_overrides(DEFAULT_OVERRIDES),
            benchmark_records=creator_state + raw_additions,
            calibration_reference=baseline + raw_additions,
        )
        for record in priced_additions:
            record["dailyChange"] = 0.0
            record["hourlyChangePct"] = 0.0
        creator_state.extend(priced_additions)

    creator_state = unique_by_id(creator_state)
    final_count = len(creator_state)
    ids = [str(record.get("id") or "") for record in creator_state]
    tickers = [str(record.get("ticker") or "") for record in creator_state]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate Creator ids after Creator-only expansion")
    if len(tickers) != len(set(tickers)):
        raise ValueError("Duplicate Creator tickers after Creator-only expansion")
    if final_count < int(args.minimum_total) and not args.allow_shortfall:
        raise RuntimeError(f"Creator catalog has {final_count} records; minimum is {args.minimum_total}")

    write_records(args.creator_catalog, creator_state)
    manifest = {
        "version": "creator-market-expansion-1.0",
        "generatedAt": verified_at,
        "creatorCountBefore": starting_count,
        "creatorAdditions": max(0, final_count - starting_count),
        "creatorCountAfter": final_count,
        "requestedCreatorTotal": int(args.target_total),
        "minimumCreatorTotal": int(args.minimum_total),
        "eligibleSourceCandidates": candidate_count,
        "minimumSitelinks": int(args.minimum_sitelinks),
        "activityProxyCutoffYear": recent_cutoff,
        "sourceErrors": source_errors,
        "scope": "Creator-only overlay; Athlete, Music, and Actor records are untouched",
        "statusLimitation": (
            "Wikidata is not a live platform roster. New Creator records use living-person, "
            "explicit creator-occupation, sitelink, and work-period proxies."
        ),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Creator market expansion: {starting_count:,} existing + "
        f"{max(0, final_count - starting_count):,} added = {final_count:,} total Creators."
    )
    if source_errors:
        print(f"Completed with {len(source_errors)} source request error(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
