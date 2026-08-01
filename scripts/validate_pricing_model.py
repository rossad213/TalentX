#!/usr/bin/env python3
"""Regression and distribution checks for TalentX pricing integrity."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pricing_model import ACTIVE_WEIGHTS, LEGACY_WEIGHTS, active_score, fundamental_from_score, legacy_score

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TOLERANCE = 0.11
MODEL_VERSION = "3.2-achievements-weighted"


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
    parser.add_argument("--minimum-enriched", type=int, default=0)
    parser.add_argument("--max-identical-score-rate", type=float, default=0.08)
    args = parser.parse_args()

    current = load(DATA / "current_catalog.json") or load(DATA / "current_seed.json")
    historical = load(DATA / "legacy_catalog_v2.json")
    errors: list[str] = []

    if ACTIVE_WEIGHTS.get("achievements") != 0.25 or ACTIVE_WEIGHTS.get("potential") != 0.20:
        errors.append("Active weights must use 25% achievements and 20% potential")
    if ACTIVE_WEIGHTS.get("achievements", 0) <= ACTIVE_WEIGHTS.get("potential", 0):
        errors.append("Achievements must outweigh potential in the active-career model")
    if abs(sum(ACTIVE_WEIGHTS.values()) - 1.0) > 1e-9:
        errors.append("Active pricing weights must total 100%")

    if len(current) < args.minimum_current:
        errors.append(f"Current catalog has {len(current):,}; expected at least {args.minimum_current:,}")

    enriched = [
        record for record in current
        if str(record.get("pricingDataStatus", "")).startswith("Evidence enriched")
    ]
    provisional = [
        record for record in current
        if str(record.get("pricingDataStatus", "")).startswith("Provisional")
    ]
    if len(enriched) < args.minimum_enriched:
        errors.append(f"Only {len(enriched):,} evidence-enriched records; expected at least {args.minimum_enriched:,}")

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
        try:
            actual_score = float(record.get("careerScore", -999))
            actual_fundamental = float(record.get("fundamentalValue", -999))
        except (TypeError, ValueError):
            errors.append(f"Non-numeric price fields: {record.get('name')}")
            continue
        if abs(actual_score - expected_score) > TOLERANCE:
            errors.append(f"Score mismatch: {record.get('name')} expected {expected_score}, found {actual_score}")
        if abs(actual_fundamental - expected_fundamental) > TOLERANCE:
            errors.append(f"Fundamental mismatch: {record.get('name')} expected {expected_fundamental}, found {actual_fundamental}")
        if str(record.get("pricingDataStatus", "")).startswith("Provisional") and actual_fundamental > 62.01:
            errors.append(f"Unsupported star valuation: {record.get('name')} exceeds provisional cap")
        if record.get("pricingModelVersion") != MODEL_VERSION:
            errors.append(f"Wrong model version: {record.get('name')}")
        if len(errors) >= 50:
            break

    if current:
        rounded_scores = [round(float(record.get("careerScore", 0)), 1) for record in current]
        common_score, common_count = Counter(rounded_scores).most_common(1)[0]
        identical_rate = common_count / len(rounded_scores)
        if identical_rate > args.max_identical_score_rate:
            errors.append(
                f"Pricing is over-clustered: {common_count:,} records ({identical_rate:.1%}) share score {common_score}"
            )
        if len(set(rounded_scores)) < 80 and len(current) >= 1000:
            errors.append(f"Only {len(set(rounded_scores))} distinct career scores across {len(current):,} current records")

    # Evidence-enriched records must include a traceable explanation.
    for record in enriched:
        summary = record.get("pricingEvidenceSummary")
        evidence = record.get("pricingEvidence")
        if not isinstance(summary, dict) or not isinstance(evidence, list) or not evidence:
            errors.append(f"Evidence-enriched record lacks audit details: {record.get('name')}")
            if len(errors) >= 50:
                break

    by_name = {str(record.get("name")): record for record in current}
    comparisons = [
        ("Dak Prescott", "Thomas Incoom"),
        ("Justin Herbert", "Kyle Allen"),
        ("Trevor Lawrence", "Trey Lance"),
        ("Josh Jacobs", "Ty Johnson"),
    ]
    comparison_output: list[str] = []
    for higher_name, lower_name in comparisons:
        higher = by_name.get(higher_name)
        lower = by_name.get(lower_name)
        if not higher or not lower:
            continue
        higher_price = float(higher.get("marketPrice", 0))
        lower_price = float(lower.get("marketPrice", 0))
        comparison_output.append(f"{higher_name}: ${higher_price:.2f} | {lower_name}: ${lower_price:.2f}")
        if higher_price <= lower_price:
            errors.append(
                f"Regression failed: {higher_name} (${higher_price:.2f}) must price above {lower_name} (${lower_price:.2f})"
            )

    print(f"Checked {len(current) + len(historical):,} records against model weights.")
    print(f"Evidence enriched: {len(enriched):,}; provisional: {len(provisional):,}")
    print(f"Active weights: {ACTIVE_WEIGHTS}")
    print(f"Legacy weights: {LEGACY_WEIGHTS}")
    for line in comparison_output:
        print(line)

    if errors:
        print("\nPRICING VALIDATION ERRORS")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Pricing validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
