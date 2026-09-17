#!/usr/bin/env python3
"""Sync factual NFL draft metadata into the Sports catalog.

The primary join is nflverse ESPN athlete ID -> TalentX ``sourceRecordId``. A
unique normalized-name match is used only when an ESPN ID is unavailable. The
small local override file remains a final fallback for source mismatches.

This script never writes prices. It only supplies draft year/round/pick evidence
needed by the Rookie IPO model.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from category_market_store import load_records, write_records

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DEFAULT_OVERRIDES = ROOT / "data" / "nfl_draft_metadata_overrides.json"
NFLVERSE_PLAYERS_CSV = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
SYNC_VERSION = "2026-09-16-nflverse-espn-id-draft-sync-v1"


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_int(value: Any) -> int | None:
    parsed = _number(value)
    if parsed is None or parsed <= 0:
        return None
    return int(round(parsed))


def _name_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _espn_key(value: Any) -> str:
    parsed = _positive_int(value)
    return str(parsed) if parsed is not None else ""


def _draft_entry(row: dict[str, Any]) -> dict[str, Any] | None:
    year = _positive_int(row.get("draft_year") or row.get("draftYear"))
    round_ = _positive_int(row.get("draft_round") or row.get("draftRound"))
    pick = _positive_int(row.get("draft_pick") or row.get("draftPick"))
    if year is None or round_ is None or pick is None:
        return None
    return {
        "draftYear": year,
        "draftRound": round_,
        "draftPick": pick,
        "espnId": _espn_key(row.get("espn_id") or row.get("espnId")),
        "name": str(row.get("display_name") or row.get("name") or "").strip(),
    }


def download_player_rows(url: str = NFLVERSE_PLAYERS_CSV, *, timeout: int = 30) -> list[dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "TalentX-NFL-draft-sync/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def load_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("records", []) if isinstance(payload, dict) else payload
    output: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        entry = _draft_entry({
            "draftYear": row.get("draftYear"),
            "draftRound": row.get("draftRound"),
            "draftPick": row.get("draftPick"),
            "name": row.get("name"),
        })
        if entry is not None:
            entry["sourceUrl"] = row.get("sourceUrl")
            output[_name_key(row.get("name"))] = entry
    return output


def build_indexes(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_espn: dict[str, dict[str, Any]] = {}
    name_candidates: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entry = _draft_entry(row)
        if entry is None:
            continue
        if entry["espnId"]:
            by_espn[entry["espnId"]] = entry
        key = _name_key(entry["name"])
        if key:
            name_candidates.setdefault(key, []).append(entry)
    by_unique_name = {key: values[0] for key, values in name_candidates.items() if len(values) == 1}
    return by_espn, by_unique_name


def sync_records(
    records: list[dict[str, Any]],
    player_rows: Iterable[dict[str, Any]],
    overrides: dict[str, dict[str, Any]] | None = None,
    *,
    synced_at: str | None = None,
) -> dict[str, int]:
    by_espn, by_name = build_indexes(player_rows)
    overrides = overrides or {}
    stamp = synced_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    counts = {"nfl": 0, "matchedByEspnId": 0, "matchedByName": 0, "matchedByOverride": 0, "changed": 0}

    for record in records:
        if str(record.get("leagueOrMedium") or "").strip().upper() != "NFL":
            continue
        counts["nfl"] += 1
        espn_id = _espn_key(record.get("sourceRecordId")) if str(record.get("sourceNamespace") or "").lower() == "espn" else ""
        entry = by_espn.get(espn_id) if espn_id else None
        match = "espn-id" if entry is not None else ""
        if entry is None:
            entry = by_name.get(_name_key(record.get("name")))
            match = "unique-name" if entry is not None else ""
        if entry is None:
            entry = overrides.get(_name_key(record.get("name")))
            match = "local-override" if entry is not None else ""
        if entry is None:
            continue

        if match == "espn-id":
            counts["matchedByEspnId"] += 1
        elif match == "unique-name":
            counts["matchedByName"] += 1
        else:
            counts["matchedByOverride"] += 1

        before = tuple(_positive_int(record.get(field)) for field in ("draftYear", "draftRound", "draftPick"))
        authoritative_id_match = match == "espn-id"
        for field in ("draftYear", "draftRound", "draftPick"):
            value = _positive_int(entry.get(field))
            if value is None:
                continue
            if authoritative_id_match or _positive_int(record.get(field)) is None:
                record[field] = value

        after = tuple(_positive_int(record.get(field)) for field in ("draftYear", "draftRound", "draftPick"))
        if any(value is None for value in after):
            continue
        if after != before:
            counts["changed"] += 1

        source_url = entry.get("sourceUrl") or NFLVERSE_PLAYERS_CSV
        record["draftMetadataSource"] = source_url
        record["draftMetadataMatch"] = match
        record["draftMetadataSyncVersion"] = SYNC_VERSION
        record["draftMetadataSyncedAt"] = stamp
        summary = dict(record.get("pricingEvidenceSummary") or {})
        summary["draftYear"], summary["draftRound"], summary["draftPick"] = after
        record["pricingEvidenceSummary"] = summary

    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--source-url", default=NFLVERSE_PLAYERS_CSV)
    args = parser.parse_args()

    records = load_records(args.catalog)
    overrides = load_overrides(args.overrides)
    try:
        player_rows = download_player_rows(args.source_url)
    except Exception as exc:
        # A transient external-source outage should not take the Sports market
        # offline. Local factual overrides still provide a narrow fallback.
        print(f"WARNING: nflverse player metadata unavailable: {exc}")
        player_rows = []
    counts = sync_records(records, player_rows, overrides)
    write_records(args.catalog, records)
    print(
        "NFL draft metadata sync: "
        f"{counts['changed']:,} changed; "
        f"{counts['matchedByEspnId']:,} ESPN-ID matches, "
        f"{counts['matchedByName']:,} unique-name matches, "
        f"{counts['matchedByOverride']:,} override matches across {counts['nfl']:,} NFL records."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
