#!/usr/bin/env python3
"""Keep transient outcome-source failures retryable and apply verified event fallbacks.

The outcome scanner records an attentionState entry when it attempts a Wikimedia
pageview comparison. If the source request failed or returned too little data,
there is no measured ratio. Remove those incomplete markers so a later six-hour
scan can retry instead of treating the outcome as permanently checked.

Some legitimate Music/Actor events can also arrive in authoritative sources
before they are represented in Wikidata/MusicBrainz. A small reviewed override
file lets TalentX bridge that source-lag gap by exact profile identity while still
using the normal non-athlete event-pricing and durable-history machinery.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from non_athlete_event_refresh import apply_events, qid_for

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DEFAULT_OVERRIDES = ROOT / "config" / "verified_non_athlete_event_overrides.json"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def repair_manifest(path: Path) -> int:
    if not path.exists():
        return 0
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain an object")
    state = payload.get("attentionState") if isinstance(payload.get("attentionState"), dict) else {}
    repaired = {
        key: value
        for key, value in state.items()
        if isinstance(value, dict) and isinstance(value.get("ratio"), (int, float))
    }
    removed = len(state) - len(repaired)
    payload["attentionState"] = repaired
    payload["retryableAttentionChecksRemoved"] = removed
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def event_from_override(item: dict[str, Any]) -> dict[str, Any] | None:
    event_key = str(item.get("eventKey") or "").strip()
    event_type = str(item.get("eventType") or "").strip()
    started_at = str(item.get("startedAt") or "").strip()
    name = str(item.get("name") or "").strip()
    if not event_key or not event_type or not started_at or not name:
        return None
    return {
        "eventKey": event_key,
        "eventId": str(item.get("eventId") or event_key),
        "eventType": event_type,
        "provider": str(item.get("provider") or "Verified source override"),
        "sourceUrl": str(item.get("sourceUrl") or ""),
        "secondarySourceUrl": str(item.get("secondarySourceUrl") or ""),
        "name": name,
        "startedAt": started_at,
        "releaseType": str(item.get("releaseType") or "Other"),
        "verificationStatus": str(item.get("verificationStatus") or "Reviewed verified fallback"),
    }


def matches_override(record: dict[str, Any], item: dict[str, Any]) -> bool:
    wanted_category = str(item.get("primaryCategory") or "").strip()
    record_category = str(record.get("primaryCategory") or "").strip()
    if wanted_category and record_category != wanted_category:
        return False

    wanted_id = str(item.get("profileId") or "").strip()
    record_id = str(record.get("id") or "").strip()
    if wanted_id:
        return bool(record_id and record_id == wanted_id)

    wanted_qid = str(item.get("wikidataQid") or "").strip()
    record_qid = qid_for(record)
    if wanted_qid and re.fullmatch(r"Q\d+", wanted_qid):
        # If the catalog record has structured identity, a mismatch is final.
        # Never fall through to same-name matching (e.g. the two Steve Lacys).
        if record_qid:
            return record_qid == wanted_qid
        # Name fallback is allowed only when the record genuinely has no QID.

    wanted_name = str(item.get("profileName") or "").strip().casefold()
    return bool(
        wanted_name
        and str(record.get("name") or "").strip().casefold() == wanted_name
    )


def apply_verified_overrides(catalog_path: Path, overrides_path: Path) -> tuple[int, int]:
    catalog = load_json(catalog_path, [])
    overrides = load_json(overrides_path, [])
    if not isinstance(catalog, list) or not isinstance(overrides, list):
        return 0, 0
    records = [dict(item) for item in catalog if isinstance(item, dict)]
    changed_records = 0
    applied_events = 0
    for override in overrides:
        if not isinstance(override, dict):
            continue
        event = event_from_override(override)
        if event is None:
            continue
        for index, record in enumerate(records):
            if not matches_override(record, override):
                continue
            updated, added = apply_events(record, [event])
            if added:
                records[index] = updated
                changed_records += 1
                applied_events += added
            break
    if applied_events:
        catalog_path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return changed_records, applied_events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    args = parser.parse_args()

    removed = repair_manifest(args.manifest)
    changed_records, applied_events = apply_verified_overrides(args.catalog, args.overrides)
    print(f"Removed {removed:,} incomplete attention-check markers so they can retry later.")
    print(f"Applied {applied_events:,} verified fallback events to {changed_records:,} Music/Actor records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
