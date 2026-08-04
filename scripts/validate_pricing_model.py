#!/usr/bin/env python3
"""Regression and distribution checks for TalentX pricing integrity."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pricing_model import (
    ACTIVE_WEIGHTS,
    ATHLETE_ACTIVE_WEIGHTS,
    LEGACY_WEIGHTS,
    MODEL_VERSION,
    ROOKIE_WEIGHTS,
    active_pricing_components,
    fundamental_from_score,
    legacy_score,
)

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
    parser.add_argument("--minimum-enriched", type=int, default=0)
    parser.add_argument("--max-identical-score-rate", type=float, default=0.08)
    args = parser.parse_args()

    current = load(DATA / "current_catalog.json") or load(DATA / "current_seed.json")
    historical = load(DATA / "legacy_catalog_v2.json")
    errors: list[str] = []

    expected_athlete_weights = {
        "performance": 0.35,
        "achievements": 0.25,
        "potential": 0.15,
        "audience": 0.15,
        "availability": 0.10,
    }
    if ATHLETE_ACTIVE_WEIGHTS != expected_athlete_weights:
        errors.append(f"Athlete weights must equal {expected_athlete_weights}")
    if abs(sum(ATHLETE_ACTIVE_WEIGHTS.values()) - 1.0) > 1e-9:
        errors.append("Athlete pricing weights must total 100%")
    if ACTIVE_WEIGHTS.get("achievements") != 0.25 or ACTIVE_WEIGHTS.get("potential") != 0.20:
        errors.append("Non-athlete active weights must remain unchanged")
    if abs(sum(ACTIVE_WEIGHTS.values()) - 1.0) > 1e-9:
        errors.append("Active pricing weights must total 100%")
    if abs(sum(ROOKIE_WEIGHTS.values()) - 1.0) > 1e-9:
        errors.append("Rookie IPO weights must total 100%")
    if ROOKIE_WEIGHTS.get("draftCapital") != 0.35:
        errors.append("Draft capital must remain the strongest Rookie IPO input at 35%")

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
            components = active_pricing_components(record, metrics)
            expected_score = float(components["score"])
            expected_fundamental = float(components["fundamental"])
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
        if (
            str(record.get("pricingDataStatus", "")).startswith("Provisional")
            and not isinstance(record.get("rookiePricing"), dict)
            and actual_fundamental > 62.01
        ):
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

    for record in enriched:
        if record.get("primaryCategory") != "Athlete":
            continue
        metrics = record.get("activeMetrics")
        summary = record.get("pricingEvidenceSummary")
        percentiles = summary.get("percentiles") if isinstance(summary, dict) else None
        if not isinstance(metrics, dict) or not isinstance(percentiles, dict):
            continue
        try:
            production = float(percentiles["recentProduction"])
            efficiency = float(percentiles["efficiency"])
            actual_performance = float(metrics["performance"])
            actual_availability = float(metrics["availability"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"Missing revised athlete inputs: {record.get('name')}")
            if len(errors) >= 50:
                break
            continue
        expected_performance = round(
            max(20.0, min(98.0, 24 + 72 * (production * 0.70 + efficiency * 0.30))),
            1,
        )
        expected_availability = 75.0 if record.get("careerStatus") == "Active" else 55.0
        if abs(actual_performance - expected_performance) > TOLERANCE:
            errors.append(
                f"Production-first performance mismatch: {record.get('name')} "
                f"expected {expected_performance}, found {actual_performance}"
            )
        if abs(actual_availability - expected_availability) > TOLERANCE:
            errors.append(
                f"Neutral availability mismatch: {record.get('name')} "
                f"expected {expected_availability}, found {actual_availability}"
            )
        if len(errors) >= 50:
            break

    rookies = [record for record in current if isinstance(record.get("rookiePricing"), dict)]
    for record in rookies:
        rookie = record["rookiePricing"]
        pick = float(rookie.get("overallPick") or 0)
        influence = float(rookie.get("draftInfluencePct") or 0)
        games = float(rookie.get("professionalGames") or 0)
        if pick <= 0:
            errors.append(f"Rookie model lacks draft pick: {record.get('name')}")
        if not 0 < influence <= 100:
            errors.append(f"Invalid rookie draft influence: {record.get('name')} ({influence})")
        if games == 0 and influence < 99:
            errors.append(f"Pre-debut rookie must retain full IPO influence: {record.get('name')}")
        if len(errors) >= 50:
            break

    by_name = {str(record.get("name")): record for record in current}
    comparisons = [
        ("Dak Prescott", "Thomas Incoom"),
        ("Justin Herbert", "Kyle Allen"),
        ("Trevor Lawrence", "Trey Lance"),
        ("Josh Jacobs", "Ty Johnson"),
    ]
    aj = by_name.get("AJ Dybantsa")
    if aj:
        aj_price = float(aj.get("marketPrice", 0))
        comparison_output = [f"AJ Dybantsa rookie IPO: ${aj_price:.2f}"]
        if aj.get("draftPick") != 1:
            errors.append(f"AJ Dybantsa must be recognized as draft pick 1, found {aj.get('draftPick')}")
        if not isinstance(aj.get("rookiePricing"), dict):
            errors.append("AJ Dybantsa must use the Rookie transition model")
        if aj_price < 55 or aj_price > 90:
            errors.append(f"AJ Dybantsa rookie IPO price is outside the expected prototype range: ${aj_price:.2f}")
    else:
        comparison_output = []
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
    print(f"Evidence enriched: {len(enriched):,}; provisional: {len(provisional):,}; rookie transitions: {len(rookies):,}")
    print(f"Active weights: {ACTIVE_WEIGHTS}")
    print(f"Athlete active weights: {ATHLETE_ACTIVE_WEIGHTS}")
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
