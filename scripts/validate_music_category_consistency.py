#!/usr/bin/env python3
"""Fail a TalentX build when non-musicians leak into the Music category."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
SCREEN_FIRST_REGRESSIONS = {"zacefron", "tomhanks", "quentintarantino"}
GENERIC_MUSIC_ROLES = {"", "music", "musician", "artist", "performer"}


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def is_curated_music(record: dict[str, Any]) -> bool:
    return bool(record.get("nonAthleteRosterVersion")) or str(record.get("statusSource") or "") == "TalentX curated non-athlete roster"


def validate(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    actor_names = {
        normalize(record.get("name"))
        for record in records
        if record.get("primaryCategory") == "Actor"
    }

    for record in records:
        if record.get("primaryCategory") != "Music":
            continue
        name = str(record.get("name") or "")
        key = normalize(name)

        if key in SCREEN_FIRST_REGRESSIONS:
            errors.append(f"Known screen-first profile remains in Music: {name}")

        if is_curated_music(record):
            continue

        if record.get("musicCategoryVerified") is not True:
            errors.append(f"Discovered Music profile lacks strict music verification: {name}")
            continue

        if not record.get("musicBrainzArtistIds"):
            errors.append(f"Verified Music profile lacks MusicBrainz artist identity: {name}")
        if not record.get("verifiedMusicOccupations"):
            errors.append(f"Verified Music profile lacks a specific music profession: {name}")
        if normalize(record.get("role")) in {normalize(role) for role in GENERIC_MUSIC_ROLES}:
            errors.append(f"Generic Music role survived strict verification: {name} ({record.get('role')})")
        if key in actor_names and "screen-first" in str(record.get("categoryResolution") or "").lower():
            errors.append(f"Screen-first Actor/Music collision remains: {name}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.catalog.name} must contain a JSON array")
    records = [record for record in payload if isinstance(record, dict)]
    errors = validate(records)
    if errors:
        print("MUSIC CATEGORY CONSISTENCY ERRORS")
        for error in errors[:100]:
            print(f"- {error}")
        return 1
    strict_count = sum(
        1 for record in records
        if record.get("primaryCategory") == "Music" and record.get("musicCategoryVerified") is True
    )
    curated_count = sum(
        1 for record in records
        if record.get("primaryCategory") == "Music" and is_curated_music(record)
    )
    print(f"Music category consistency passed: {curated_count} curated + {strict_count} strictly verified discovered Music profiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
