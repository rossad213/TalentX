#!/usr/bin/env python3
"""Sync direct Billboard outcomes into the existing Music chart-state manifest.

The Wikidata outcome refresher stores an absolute chart target per verified release.
When Billboard sees that outcome first, this script copies the Billboard rank into
that same state whenever the Billboard title can be tied to an existing verified
Music release/work QID. If Wikidata catches up later, its delta is then zero rather
than pricing the same result a second time.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from non_athlete_event_refresh import iso, utc_now
from non_athlete_outcome_refresh import chart_target

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DEFAULT_MANIFEST = ROOT / "data" / "music_outcome_manifest.json"


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def normalized_title(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    text = re.sub(r"\s+[—–-]\s+(?:single|album|ep|broadcast|other)\s*$", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def release_work_qid(record: dict[str, Any], release_title: str) -> str:
    target = normalized_title(release_title)
    if not target:
        return ""
    candidates: list[tuple[int, str]] = []
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or str(event.get("eventType") or "") != "music-release":
            continue
        work_qid = str(event.get("workQid") or "")
        if not re.fullmatch(r"Q\d+", work_qid):
            continue
        names = [event.get("releaseTitle"), event.get("title"), event.get("name")]
        for value in names:
            candidate = normalized_title(value)
            if not candidate:
                continue
            if candidate == target:
                return work_qid
            if target in candidate or candidate in target:
                candidates.append((abs(len(candidate) - len(target)), work_qid))
    return min(candidates)[1] if candidates else ""


def latest_billboard_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict):
            continue
        if str(event.get("eventType") or "") != "music-chart-outcome":
            continue
        if str(event.get("chartProvider") or "") != "Billboard":
            continue
        if not event.get("releaseTitle") or not event.get("chartRank"):
            continue
        output.append(event)
    return output


def sync_manifest(records: list[dict[str, Any]], manifest: dict[str, Any], now: datetime | None = None) -> tuple[dict[str, Any], int]:
    stamp = now or utc_now()
    output = dict(manifest)
    chart_state = output.get("musicChartState") if isinstance(output.get("musicChartState"), dict) else {}
    next_state = dict(chart_state)
    synced = 0

    for index, record in enumerate(records):
        if str(record.get("primaryCategory") or "") != "Music":
            continue
        for event in latest_billboard_events(record):
            title = str(event.get("releaseTitle") or "")
            work_qid = release_work_qid(record, title)
            if not work_qid:
                continue
            try:
                rank = int(event.get("chartRank") or 0)
            except (TypeError, ValueError):
                continue
            if rank <= 0:
                continue
            tier, target = chart_target(rank)
            state_key = f"{index}:{work_qid}"
            current = next_state.get(state_key) if isinstance(next_state.get(state_key), dict) else {}
            if (
                int(current.get("bestRank") or 0) == rank
                and abs(float(current.get("targetMovePct") or 0) - float(target)) < 1e-9
            ):
                continue
            next_state[state_key] = {
                **current,
                "bestRank": rank,
                "chartQid": str(event.get("chartSlug") or "billboard"),
                "tier": tier,
                "targetMovePct": target,
                "updatedAt": iso(stamp),
                "provider": "Billboard",
                "releaseTitle": title,
                "billboardChartDate": event.get("chartDate"),
                "billboardEventKey": event.get("eventKey"),
            }
            synced += 1

    output["musicChartState"] = next_state
    if synced:
        output["billboardStateSyncedAt"] = iso(stamp)
        output["billboardStateSyncCount"] = synced
    return output, synced


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    records = load_json(args.catalog, [])
    manifest = load_json(args.manifest, {})
    if not isinstance(records, list):
        print("Billboard manifest sync: catalog must be an array")
        return 1
    if not isinstance(manifest, dict):
        manifest = {}

    updated, synced = sync_manifest([dict(item) for item in records if isinstance(item, dict)], manifest)
    if synced:
        args.manifest.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Billboard manifest sync: {synced} chart states synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
