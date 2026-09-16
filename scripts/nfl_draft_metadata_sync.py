#!/usr/bin/env python3
"""Recover recent NFL draft metadata from nflverse's player ID database.

TalentX's ESPN roster feed often omits historical draft fields. That prevents the
NFL Rookie IPO model from reconstructing a draft anchor for recent draftees when
an upstream pricing pass has removed ``rookiePricing``.

This script joins the nflverse players dataset primarily by ESPN athlete ID,
falling back to a unique normalized name only when an ID match is unavailable.
It writes factual draft year/round/overall pick metadata only; it never assigns a
manual price. Records changed by the sync are marked so the selective NFL repair
can re-evaluate them once.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
SOURCE_URL = "https://nflreadr.nflverse.com/reference/load_players.html"
DEFAULT_DRAFT_LOOKBACK_YEARS = 3


def _norm_name(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _positive_int(value: Any) -> int | None:
    try:
        if value in (None, "", "NA", "NaN", "nan"):
            return None
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _row_value(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", "NA", "NaN", "nan"):
            return value
    return None


def parse_players_csv(
    content: str,
    *,
    current_year: int | None = None,
    lookback_years: int = DEFAULT_DRAFT_LOOKBACK_YEARS,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return recent drafted players indexed by ESPN id and unique normalized name."""
    year = int(current_year or datetime.now(timezone.utc).year)
    min_year = year - max(0, int(lookback_years))
    by_espn: dict[str, dict[str, Any]] = {}
    name_candidates: dict[str, list[dict[str, Any]]] = {}

    for row in csv.DictReader(io.StringIO(content)):
        draft_year = _positive_int(_row_value(row, "draft_year", "draftYear"))
        draft_round = _positive_int(_row_value(row, "draft_round", "draftRound"))
        draft_pick = _positive_int(_row_value(row, "draft_pick", "draft_number", "draftPick"))
        if draft_year is None or draft_year < min_year or draft_year > year:
            continue
        if draft_round is None or draft_pick is None:
            continue

        name = _row_value(row, "display_name", "pfr_player_name", "football_name", "full_name", "name")
        espn_id = _row_value(row, "espn_id", "espnId")
        entry = {
            "name": str(name or "").strip(),
            "league": "NFL",
            "draftYear": draft_year,
            "draftRound": draft_round,
            "draftPick": draft_pick,
            "sourceUrl": SOURCE_URL,
            "sourceDataset": "nflverse players",
        }
        if espn_id:
            normalized_id = str(espn_id).strip()
            if normalized_id.endswith(".0"):
                normalized_id = normalized_id[:-2]
            if normalized_id:
                by_espn[normalized_id] = entry
        key = _norm_name(name)
        if key:
            name_candidates.setdefault(key, []).append(entry)

    by_name = {
        key: items[0]
        for key, items in name_candidates.items()
        if len({(item["draftYear"], item["draftPick"]) for item in items}) == 1
    }
    return by_espn, by_name


def fetch_recent_draft_metadata(
    *,
    timeout: float = 25.0,
    current_year: int | None = None,
    lookback_years: int = DEFAULT_DRAFT_LOOKBACK_YEARS,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    response = requests.get(
        PLAYERS_URL,
        timeout=timeout,
        headers={"User-Agent": "TalentX-NFL-Draft-Metadata/1.0"},
    )
    response.raise_for_status()
    return parse_players_csv(
        response.text,
        current_year=current_year,
        lookback_years=lookback_years,
    )


def sync_records(
    records: list[dict[str, Any]],
    *,
    by_espn: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    synced_at: str | None = None,
) -> tuple[int, int]:
    """Fill missing recent-draft metadata and return (records_changed, id_matches)."""
    stamp = synced_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    changed = 0
    id_matches = 0

    for record in records:
        if str(record.get("leagueOrMedium") or "").strip().upper() != "NFL":
            continue

        espn_id = str(record.get("sourceRecordId") or "").strip()
        metadata = by_espn.get(espn_id) if espn_id else None
        matched_by = "espn-id" if metadata else None
        if metadata is not None:
            id_matches += 1
        if metadata is None:
            metadata = by_name.get(_norm_name(record.get("name")))
            if metadata is not None:
                matched_by = "unique-name"
        if metadata is None:
            continue

        before = tuple(record.get(field) for field in ("draftYear", "draftRound", "draftPick"))
        for field in ("draftYear", "draftRound", "draftPick"):
            if _positive_int(record.get(field)) is None and metadata.get(field) is not None:
                record[field] = metadata[field]
        after = tuple(record.get(field) for field in ("draftYear", "draftRound", "draftPick"))
        if after == before:
            continue

        summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
        summary = dict(summary)
        summary["draftYear"] = record.get("draftYear")
        summary["draftRound"] = record.get("draftRound")
        summary["draftPick"] = record.get("draftPick")
        record["pricingEvidenceSummary"] = summary
        record["draftMetadataSource"] = metadata.get("sourceUrl") or SOURCE_URL
        record["draftMetadataDataset"] = metadata.get("sourceDataset") or "nflverse players"
        record["draftMetadataMatchedBy"] = matched_by
        record["draftMetadataSyncedAt"] = stamp
        record["nflDraftMetadataRepairPending"] = True
        changed += 1

    return changed, id_matches


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--lookback-years", type=int, default=DEFAULT_DRAFT_LOOKBACK_YEARS)
    args = parser.parse_args()

    records = load_records(args.catalog)
    by_espn, by_name = fetch_recent_draft_metadata(timeout=args.timeout, lookback_years=args.lookback_years)
    changed, id_matches = sync_records(records, by_espn=by_espn, by_name=by_name)
    if changed:
        write_records(args.catalog, records)
    print(
        f"Recovered recent NFL draft metadata for {changed:,} listing(s) "
        f"({id_matches:,} recent-draft ESPN-ID matches available)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
