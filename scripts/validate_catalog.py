#!/usr/bin/env python3
"""Validate TalentX v2 catalog files."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
files = [ROOT/"data/current_seed.json", ROOT/"data/legacy_catalog_v2.json"]
records = []
for path in files:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, list), f"{path} must contain a list"
    records.extend(payload)

required = {
    "id","name","ticker","primaryCategory","discipline","careerStatus",
    "marketSegment","verificationStatus","careerStage","careerScore","marketPrice"
}
for index, record in enumerate(records):
    missing = required - record.keys()
    assert not missing, f"Record {index} missing {sorted(missing)}"
    assert record["marketPrice"] > 0, f"{record['name']} has nonpositive price"
    assert record["marketSegment"] in {"Current","Legacy","Under Review"}
    if record["marketSegment"] == "Current":
        assert record["verificationStatus"], f"{record['name']} lacks verification disclosure"
    if record.get("rookiePricing"):
        rookie = record["rookiePricing"]
        for key in ("overallPick","draftCapitalScore","positionValueScore","ipoPrice"):
            assert key in rookie, f"{record['name']} rookiePricing missing {key}"
        assert rookie["overallPick"] > 0 and rookie["ipoPrice"] > 0

ids = [r["id"] for r in records]
assert len(ids) == len(set(ids)), "Duplicate profile IDs"

names = [r["name"].casefold() for r in records]
assert len(names) == len(set(names)), "Duplicate profile names"

print(f"Validated {len(records):,} unique profiles")
print("Segments:", dict(Counter(r["marketSegment"] for r in records)))
print("Categories:", dict(Counter(r["primaryCategory"] for r in records)))
