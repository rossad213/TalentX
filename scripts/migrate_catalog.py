#!/usr/bin/env python3
"""Migrate the original TalentX 5,000-person JSON into lifecycle-aware v2 records.

Usage:
    python3 scripts/migrate_catalog.py talent_catalog_5000.json output.json

This script deliberately does not label historical source records as verified Current.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

def years(value):
    return [int(x) for x in re.findall(r"\b(?:19|20)\d{2}\b", str(value or ""))]

def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: migrate_catalog.py INPUT.json OUTPUT.json", file=sys.stderr)
        return 2
    source = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = []
    for old in source:
        category = {"Singer":"Music"}.get(old["category"], old["category"])
        verified = old.get("verified", {})
        segment = "Under Review"
        status = "Status under review"
        if category == "Athlete":
            latest = max(years(verified.get("Tournament years") or old.get("sub")), default=0)
            birth = years(verified.get("Birth date"))
            birth_year = birth[0] if birth else 0
            if latest < 2022 or (birth_year and birth_year < 1988):
                segment, status = "Legacy", "Retired — Legacy"
        elif old.get("source","").startswith("Pantheon"):
            birth_year = verified.get("Birth year")
            if isinstance(birth_year, (int,float)) and birth_year < 1970:
                segment, status = "Legacy", "Legacy"
        output.append({
            "id": f"hist-{old['id']}",
            "name": old["name"],
            "ticker": old["ticker"],
            "primaryCategory": category,
            "careerStatus": status,
            "careerStage": "Legacy" if segment == "Legacy" else "Stage under review",
            "marketSegment": segment,
            "verificationStatus": "Historical source snapshot — current status not verified",
            "lastVerifiedAt": None,
            "sourceName": old.get("source"),
            "sourceUrl": old.get("sourceUrl",""),
            "original": old
        })
    Path(sys.argv[2]).write_text(json.dumps(output, ensure_ascii=False, separators=(",",":")), encoding="utf-8")
    print(f"Wrote {len(output):,} records to {sys.argv[2]}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
