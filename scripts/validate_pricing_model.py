#!/usr/bin/env python3
"""Regression checks for TalentX pricing-model integrity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pricing_model import ACTIVE_WEIGHTS, LEGACY_WEIGHTS, active_score, fundamental_from_score, legacy_score

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TOLERANCE = 0.11


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be an array")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimum-current", type=int, default=0)
    args = parser.parse_args()
    current = load(DATA / "current_catalog.json") or load(DATA / "current_seed.json")
    historical = load(DATA / "legacy_catalog_v2.json")
    errors: list[str] = []

    if ACTIVE_WEIGHTS.get("achievements") != 0.25 or ACTIVE_WEIGHTS.get("potential") != 0.20:
        errors.append("Active weights must use 25% achievements and 20% potential")
    if ACTIVE_WEIGHTS.get("achievements", 0) <= ACTIVE_WEIGHTS.get("potential", 0):
        errors.append("Achievements must outweigh potential in the active-career model")

    if len(current) < args.minimum_current:
        errors.append(f"Current catalog has {len(current):,}; expected at least {args.minimum_current:,}")

    for record in current + historical:
        segment = record.get("marketSegment")
        if segment in {"Legacy", "Under Review"} or str(record.get("modelType", "")).startswith("Legacy"):
            metrics = record.get("legacyMetrics") or {}
            expected_score = legacy_score(metrics)
            expected_fundamental = fundamental_from_score(expected_score, legacy=True, under_review=segment == "Under Review")
        else:
            metrics = record.get("activeMetrics") or {}
            expected_score = active_score(metrics)
            expected_fundamental = fundamental_from_score(expected_score)
            if str(record.get("pricingDataStatus", "")).startswith("Provisional"):
                expected_fundamental = min(expected_fundamental, 62.0)
        if abs(float(record.get("careerScore", -999)) - expected_score) > TOLERANCE:
            errors.append(f"Score mismatch: {record.get('name')} expected {expected_score}, found {record.get('careerScore')}")
        if abs(float(record.get("fundamentalValue", -999)) - expected_fundamental) > TOLERANCE:
            errors.append(f"Fundamental mismatch: {record.get('name')} expected {expected_fundamental}, found {record.get('fundamentalValue')}")
        if str(record.get("pricingDataStatus", "")).startswith("Provisional") and float(record.get("fundamentalValue", 999)) > 62.01:
            errors.append(f"Unsupported star valuation: {record.get('name')} exceeds provisional cap")
        if record.get("pricingModelVersion") != "3.2-achievements-weighted":
            errors.append(f"Wrong model version: {record.get('name')}")
        if len(errors) >= 40:
            break

    by_name = {str(r.get("name")): r for r in current}
    dak = by_name.get("Dak Prescott")
    incoom = by_name.get("Thomas Incoom")
    if dak and incoom and float(dak.get("marketPrice", 0)) <= float(incoom.get("marketPrice", 0)):
        errors.append(
            f"Regression failed: Dak Prescott ({dak.get('marketPrice')}) must price above Thomas Incoom ({incoom.get('marketPrice')})"
        )

    print(f"Checked {len(current) + len(historical):,} records against model weights.")
    print(f"Active weights: {ACTIVE_WEIGHTS}")
    print(f"Legacy weights: {LEGACY_WEIGHTS}")
    if dak and incoom:
        print(f"Dak Prescott: ${dak.get('marketPrice')} | Thomas Incoom: ${incoom.get('marketPrice')}")

    if errors:
        print("\nPRICING VALIDATION ERRORS")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Pricing validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
