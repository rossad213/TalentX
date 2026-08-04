#!/usr/bin/env python3
"""Regression and distribution checks for TalentX pricing integrity."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from pricing_model import (
    CATEGORY_WEIGHTS,
    LEGACY_WEIGHTS,
    MARKET_ADJUSTMENT_CAP_PCT,
    MODEL_VERSION,
    ROOKIE_WEIGHTS,
    apply_pricing_to_records,
    fundamental_from_score,
    legacy_score,
    load_overrides,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OVERRIDES = DATA / "pricing_overrides.json"
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
    parser.add_argument("--minimum-non-athlete-per-category", type=int, default=100)
    args = parser.parse_args()

    current = load(DATA / "current_catalog.json") or load(DATA / "current_seed.json")
    historical = load(DATA / "legacy_catalog_v2.json")
    errors: list[str] = []

    required_categories = {"Athlete", "Music", "Actor", "Creator"}
    if set(CATEGORY_WEIGHTS) != required_categories:
        errors.append(f"Category weights must cover exactly {sorted(required_categories)}")
    for category, weights in CATEGORY_WEIGHTS.items():
        if abs(sum(weights.values()) - 1.0) > 1e-9:
            errors.append(f"{category} pricing weights must total 100%")
    athlete_weights = CATEGORY_WEIGHTS.get("Athlete", {})
    if athlete_weights.get("performance") != 0.35:
        errors.append("Athlete performance must remain the strongest input at 35%")
    if athlete_weights.get("achievements") != 0.25:
        errors.append("Athlete achievements must remain 25%")
    if athlete_weights.get("consistency") != 0.15 or athlete_weights.get("potential") != 0.15:
        errors.append("Athlete consistency and potential must each be 15%")
    if abs(sum(ROOKIE_WEIGHTS.values()) - 1.0) > 1e-9:
        errors.append("Rookie IPO weights must total 100%")
    if ROOKIE_WEIGHTS.get("draftCapital") != 0.35:
        errors.append("Draft capital must remain the strongest Rookie IPO input at 35%")

    if len(current) < args.minimum_current:
        errors.append(f"Current catalog has {len(current):,}; expected at least {args.minimum_current:,}")

    category_counts = Counter(str(record.get("primaryCategory") or "Unknown") for record in current)
    for category in ("Music", "Actor", "Creator"):
        count = category_counts.get(category, 0)
        if count < args.minimum_non_athlete_per_category:
            errors.append(
                f"Only {count} {category} records; expected at least "
                f"{args.minimum_non_athlete_per_category}"
            )
        ranked = [
            int(record.get("benchmarkRank"))
            for record in current
            if record.get("primaryCategory") == category and record.get("benchmarkRank") is not None
        ]
        if ranked and len(ranked) != len(set(ranked)):
            errors.append(f"Duplicate curated benchmark ranks found in {category}")
        ranked_records = sorted(
            [
                record for record in current
                if record.get("primaryCategory") == category
                and record.get("benchmarkRank") is not None
            ],
            key=lambda record: int(record.get("benchmarkRank")),
        )
        for higher, lower in zip(ranked_records, ranked_records[1:]):
            if float(higher.get("fundamentalValue") or 0) + TOLERANCE < float(lower.get("fundamentalValue") or 0):
                errors.append(
                    f"{category} benchmark order reversed: {higher.get('name')} "
                    f"must not price below {lower.get('name')} on fundamentals"
                )
                break

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

    benchmark_records = load(DATA / "current_seed.json")
    overrides = load_overrides(OVERRIDES)
    expected_current = apply_pricing_to_records(
        current,
        overrides,
        benchmark_records=benchmark_records,
        calibration_reference=current,
    )
    expected_historical = apply_pricing_to_records(
        historical,
        overrides,
        benchmark_records=benchmark_records,
        calibration_reference=historical,
    )
    expected_by_id = {
        str(record.get("id") or f"{record.get('name')}::{index}"): record
        for index, record in enumerate(expected_current + expected_historical)
    }

    for index, record in enumerate(current + historical):
        key = str(record.get("id") or f"{record.get('name')}::{index}")
        expected = expected_by_id.get(key)
        if expected is None:
            errors.append(f"Unable to reproduce price: {record.get('name')}")
            continue
        expected_score = float(expected.get("careerScore", -999))
        expected_fundamental = float(expected.get("fundamentalValue", -999))
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
        status = str(record.get("pricingDataStatus", ""))
        if status.startswith("Provisional") and not isinstance(record.get("rookiePricing"), dict):
            if actual_fundamental > 62.01:
                errors.append(f"Unsupported star valuation: {record.get('name')} exceeds provisional fundamental cap")
            if float(record.get("marketPrice") or 0) > 65.01:
                errors.append(f"Unsupported market valuation: {record.get('name')} exceeds provisional market cap")
        if (status.startswith("Curated prototype") or status.startswith("Curated benchmark")) and actual_score > 95.01:
            errors.append(f"Curated prototype exceeds evidence score cap: {record.get('name')}")
        if float(record.get("pricingConfidence") or record.get("dataConfidence") or 0) < 0.60 and actual_fundamental > 100:
            errors.append(f"Low-confidence record entered top pricing tier: {record.get('name')}")
        market_adjustment = abs(float(record.get("marketAdjustmentPct") or 0))
        if market_adjustment > MARKET_ADJUSTMENT_CAP_PCT + 0.01:
            errors.append(f"Market adjustment exceeds cap: {record.get('name')} ({market_adjustment:.2f}%)")
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

    supported = [
        record for record in current
        if not str(record.get("pricingDataStatus", "")).startswith("Provisional")
    ]
    category_prices: dict[str, list[float]] = {}
    for record in supported:
        category_prices.setdefault(str(record.get("primaryCategory") or "Unknown"), []).append(
            float(record.get("fundamentalValue") or 0)
        )
    for category, prices in sorted(category_prices.items()):
        if prices and max(prices) > 180.01:
            errors.append(f"{category} exceeds the universal fundamental ceiling")
        if prices and min(prices) < 2:
            errors.append(f"{category} fell below the universal fundamental floor")

    by_name = {str(record.get("name")): record for record in current}
    comparisons = [
        ("Dak Prescott", "Thomas Incoom"),
        ("Justin Herbert", "Kyle Allen"),
        ("Trevor Lawrence", "Trey Lance"),
        ("Josh Jacobs", "Ty Johnson"),
        ("Anthony Edwards", "Amen Thompson"),
        ("Anthony Edwards", "Tyrese Maxey"),
        ("Taylor Swift", "Gracie Abrams"),
        ("Beyoncé", "Gracie Abrams"),
        ("Rihanna", "Alex Warren"),
        ("Ed Sheeran", "sombr"),
        ("David Guetta", "NewJeans"),
        ("MrBeast", "Marques Brownlee"),
        ("Joe Rogan", "Lex Fridman"),
        ("Ibai Llanos", "Lele Pons"),
        ("Zendaya", "Pedro Pascal"),
        ("Shah Rukh Khan", "Jacob Elordi"),
        ("Zoe Saldaña", "Hunter Schafer"),
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
    print(f"Category weights: {CATEGORY_WEIGHTS}")
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
