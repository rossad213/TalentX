#!/usr/bin/env python3
"""Final no-exceptions contract for the TalentX Music category.

Curated Music records are allowed as editorial seed records. Every other Music
record must have been explicitly verified by the strict Music pipeline and carry
a MusicBrainz artist ID plus a specific verified music occupation.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
KNOWN_SCREEN_FIRST = {"zacefron", "tomhanks", "quentintarantino"}
GENERIC_ROLES = {"", "music", "musician", "artist", "performer"}


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def is_curated(record: dict[str, Any]) -> bool:
    return bool(record.get("nonAthleteRosterVersion")) or str(record.get("statusSource") or "") == "TalentX curated non-athlete roster"


def validate(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for record in records:
        if record.get("primaryCategory") != "Music":
            continue
        name = str(record.get("name") or "")
        key = normalize(name)
        if key in KNOWN_SCREEN_FIRST:
            errors.append(f"screen-first profile in Music: {name}")
            continue
        if is_curated(record):
            continue
        if record.get("musicCategoryVerified") is not True:
            errors.append(f"unverified discovered Music profile: {name}")
        mbids = record.get("musicBrainzArtistIds")
        if not isinstance(mbids, list) or not any(str(item).strip() for item in mbids):
            errors.append(f"missing MusicBrainz artist ID: {name}")
        occupations = record.get("verifiedMusicOccupations")
        if not isinstance(occupations, list) or not any(str(item).strip() for item in occupations):
            errors.append(f"missing specific verified music occupation: {name}")
        if normalize(record.get("role")) in {normalize(role) for role in GENERIC_ROLES}:
            errors.append(f"generic Music role remains after verification: {name} ({record.get('role')})")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.catalog.name} must contain a JSON array")
    errors = validate([record for record in payload if isinstance(record, dict)])
    if errors:
        print("STRICT MUSIC CONTRACT FAILED")
        for error in errors[:100]:
            print(f"- {error}")
        print(f"Total Music contract errors: {len(errors)}")
        return 1
    print("Strict Music contract passed: every non-curated Music listing is source-verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
