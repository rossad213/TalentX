#!/usr/bin/env python3
"""Ensure source-backed Actor career anchors exist in the Actor-owned market."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from discover_actors_only import fetch_actor_career_anchors
from expand_non_athlete_sources import make_record, make_session, normalize
from pricing_model import apply_pricing_to_records, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_CATALOG = DATA / "current_catalog.json"
DEFAULT_ANCHORS = DATA / "actor_career_anchors.json"
DEFAULT_OVERRIDES = DATA / "pricing_overrides.json"


def apply_anchor_metadata(record: dict, candidate: dict, recent_cutoff: int) -> dict:
    result = dict(record)
    result["actorCareerAnchor"] = True
    result["careerAnchorSource"] = f"https://www.wikidata.org/wiki/{candidate.get('qid')}"
    result["verificationStatus"] = "Source-backed Actor career anchor; profession evidence verified separately"
    result["statusSource"] = "Wikidata Actor occupation and career-anchor coverage list"
    result.pop("benchmarkRank", None)
    result.pop("benchmarkPoolSize", None)
    work_end = candidate.get("workEndYear")
    if isinstance(work_end, int) and work_end < recent_cutoff:
        result["careerStatus"] = "Retired / legacy"
        result["marketSegment"] = "Legacy"
        result["careerStage"] = "Legacy career"
        result["searchText"] = str(result.get("searchText") or "").replace("current active", "legacy retired")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    records = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{args.catalog} must contain a JSON array")

    session = make_session()
    try:
        candidates = fetch_actor_career_anchors(session, args.anchors, args.request_timeout)
    except Exception as exc:
        if args.allow_source_errors:
            print(f"Actor career-anchor source unavailable: {exc}")
            return 0
        raise

    current_year = datetime.now(timezone.utc).year
    recent_cutoff = current_year - 3
    existing_names = {normalize(str(r.get("name") or "")) for r in records}
    existing_source_ids = {
        str(r.get("sourceRecordId") or r.get("wikidataSourceRecordId") or "")
        for r in records
        if r.get("sourceRecordId") or r.get("wikidataSourceRecordId")
    }
    used_ids = {str(r.get("id") or "") for r in records if r.get("id")}
    used_tickers = {str(r.get("ticker") or "") for r in records if r.get("ticker")}
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    additions = []
    updated_existing = 0
    by_name = {normalize(str(r.get("name") or "")): i for i, r in enumerate(records)}
    by_qid = {
        str(r.get("sourceRecordId") or r.get("wikidataSourceRecordId") or ""): i
        for i, r in enumerate(records)
        if r.get("sourceRecordId") or r.get("wikidataSourceRecordId")
    }
    for candidate in candidates:
        name_key = normalize(str(candidate.get("name") or ""))
        qid = str(candidate.get("qid") or "")
        existing_index = by_qid.get(qid)
        if existing_index is None:
            existing_index = by_name.get(name_key)
        if existing_index is not None:
            records[existing_index] = apply_anchor_metadata(records[existing_index], candidate, recent_cutoff)
            updated_existing += 1
            continue
        if not name_key:
            continue
        record = make_record(
            candidate,
            "Actor",
            len(records) + len(additions) + 1,
            len(records) + len(candidates),
            used_ids,
            used_tickers,
            verified_at,
        )
        additions.append(apply_anchor_metadata(record, candidate, recent_cutoff))
        existing_names.add(name_key)
        existing_source_ids.add(qid)

    if additions:
        priced_additions = apply_pricing_to_records(
            additions,
            load_overrides(DEFAULT_OVERRIDES),
            benchmark_records=records + additions,
            calibration_reference=records + additions,
        )
        records.extend(priced_additions)
    if additions or updated_existing:
        args.catalog.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    found = [str(c.get("name") or "") for c in candidates]
    print(
        f"Actor career anchors fetched: {len(candidates)}; added: {len(additions)}; "
        f"existing marked: {updated_existing}; names: {', '.join(found)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
