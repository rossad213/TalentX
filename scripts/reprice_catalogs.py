#!/usr/bin/env python3
"""Apply the same audited pricing model to every TalentX catalog file."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pricing_model import CATEGORY_WEIGHTS, MODEL_VERSION, apply_pricing_to_records, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OVERRIDES = DATA / "pricing_overrides.json"
CURRENT_SEED = DATA / "current_seed.json"


def read_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a JSON array")
    return payload


def write_array(path: Path, records: list[dict[str, Any]]) -> None:
    compact = path.name == "current_catalog.json"
    text = json.dumps(records, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(records, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


def write_current_csv(records: list[dict[str, Any]]) -> None:
    path = DATA / "current_catalog.csv"
    fields = [
        "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
        "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
        "careerStage", "lastVerifiedAt", "verificationStatus", "sourceName",
        "sourceUrl", "sourceRecordId", "dataConfidence", "pricingConfidence",
        "pricingDataStatus", "pricingModelVersion", "marketPrice", "fundamentalValue", "careerScore",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    overrides = load_overrides(OVERRIDES)
    benchmark_records = read_array(CURRENT_SEED)
    calibration_reference = read_array(DATA / "current_catalog.json") or benchmark_records
    totals: dict[str, int] = {}
    for filename in ("current_seed.json", "current_catalog.json", "legacy_catalog_v2.json"):
        path = DATA / filename
        if not path.exists():
            continue
        records = read_array(path)
        reference = calibration_reference if filename != "legacy_catalog_v2.json" else records
        repriced = apply_pricing_to_records(
            records,
            overrides,
            benchmark_records=benchmark_records,
            calibration_reference=reference,
        )
        write_array(path, repriced)
        totals[filename] = len(repriced)
        if filename == "current_catalog.json":
            write_current_csv(repriced)

    manifest_path = DATA / "catalog_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update({
        "pricingModelVersion": MODEL_VERSION,
        "pricingRule": "Category-specific fundamentals are calibrated 70/30 with profession peers; evidence quality limits unsupported valuations.",
        "categoryWeights": CATEGORY_WEIGHTS,
        "crossCategoryCalibration": {"absolute": 0.70, "professionPeer": 0.30},
        "pricingCatalogsProcessed": totals,
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for filename, count in totals.items():
        print(f"Repriced {count:,} records in {filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
