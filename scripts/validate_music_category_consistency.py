#!/usr/bin/env python3
"""Fail a TalentX build when known screen-first profiles leak into Music."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
SOURCE_NAMESPACES = {"wikidata-non-athlete", "wikidata-music-expanded"}
SCREEN_FIRST_REGRESSIONS = {"zacefron", "tomhanks", "quentintarantino"}


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def validate(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    actor_names = {
        normalize(record.get("name"))
        for record in records
        if record.get("primaryCategory") == "Actor"
    }
    actor_source_ids = {
        str(record.get("sourceRecordId"))
        for record in records
        if record.get("primaryCategory") == "Actor" and record.get("sourceRecordId")
    }

    for record in records:
        if record.get("primaryCategory") != "Music":
            continue
        name = str(record.get("name") or "")
        key = normalize(name)
        source_namespace = str(record.get("sourceNamespace") or "")
        source_id = str(record.get("sourceRecordId") or "")

        if key in SCREEN_FIRST_REGRESSIONS:
            errors.append(f"Known screen-first profile remains in Music: {name}")

        if source_namespace in SOURCE_NAMESPACES:
            if source_id and source_id in actor_source_ids:
                errors.append(f"Same source identity exists in both Actor and Music: {name} ({source_id})")
            elif key in actor_names:
                errors.append(f"Source-discovered Music profile conflicts with Actor primary category: {name}")

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
        for error in errors[:50]:
            print(f"- {error}")
        return 1
    print("Music category consistency passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
