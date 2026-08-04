#!/usr/bin/env python3
"""Apply the revised production-first TalentX athlete pricing model.

Athlete career score:
35% performance + 25% achievements + 15% potential
+ 15% audience + 10% availability

Performance:
70% recent-production percentile + 30% efficiency percentile

The old custom usage percentile remains in the audit evidence, but no longer
changes performance or availability. Availability is temporarily neutral until
normalized games-played / games-available evidence is stored.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pricing_model import clamp, controlled_market_fields, fundamental_from_score

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ACTIVE_WEIGHTS = {
    "performance": 0.35,
    "achievements": 0.25,
    "potential": 0.15,
    "audience": 0.15,
    "availability": 0.10,
}
MODEL_VERSION = "3.5-production-first"


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def revised_performance(record: dict[str, Any], existing: float) -> float:
    summary = record.get("pricingEvidenceSummary")
    if not isinstance(summary, dict):
        return existing
    percentiles = summary.get("percentiles")
    if not isinstance(percentiles, dict):
        return existing

    production = number(percentiles.get("recentProduction"))
    efficiency = number(percentiles.get("efficiency"))
    if production is None or efficiency is None:
        return existing

    weighted_percentile = production * 0.70 + efficiency * 0.30
    return round(clamp(24 + 72 * weighted_percentile, 20, 98), 1)


def update_record(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("primaryCategory") != "Athlete":
        return record
    metrics = record.get("activeMetrics")
    if not isinstance(metrics, dict):
        return record
    if record.get("marketSegment") in {"Legacy", "Under Review"}:
        return record
    if str(record.get("modelType", "")).startswith("Legacy"):
        return record

    result = dict(record)
    revised = dict(metrics)
    revised["performance"] = revised_performance(
        result, clamp(revised.get("performance", 50), 0, 100)
    )
    revised["availability"] = (
        75.0 if result.get("careerStatus") == "Active" else 55.0
    )
    revised = {
        key: round(clamp(value, 0, 100), 1)
        for key, value in revised.items()
    }

    score = round(
        sum(clamp(revised.get(key), 0, 100) * weight
            for key, weight in ACTIVE_WEIGHTS.items()),
        1,
    )
    fundamental = fundamental_from_score(score)

    result["activeMetrics"] = revised
    result["careerScore"] = score
    result["fundamentalValue"] = fundamental
    result["pricingModelVersion"] = MODEL_VERSION
    result["pricingAudit"] = {
        **(result.get("pricingAudit")
           if isinstance(result.get("pricingAudit"), dict) else {}),
        "weights": ACTIVE_WEIGHTS,
        "score": score,
        "performanceFormula": (
            "24 + 72 × (70% recent-production percentile "
            "+ 30% efficiency percentile)"
        ),
        "usageIncludedInPerformance": False,
        "availabilityRule": (
            "Neutral 75 active / 55 inactive pending normalized "
            "games-available evidence"
        ),
        "fundamentalFormula": "2 + 180 × (score ÷ 100)²",
    }

    summary = result.get("pricingEvidenceSummary")
    if isinstance(summary, dict):
        summary = dict(summary)
        summary["valuationInputs"] = {
            "performance": (
                "70% recent-production percentile + "
                "30% efficiency percentile"
            ),
            "usageIncludedInPerformance": False,
            "availability": (
                "Neutral 75 active / 55 inactive pending normalized "
                "games-available evidence"
            ),
        }
        result["pricingEvidenceSummary"] = summary

    # Rebuild deterministic market fields from the revised fundamentals.
    result.update(controlled_market_fields(result, score, revised, fundamental))
    return result


def rewrite_csv(records: list[dict[str, Any]]) -> None:
    path = DATA / "current_catalog.csv"
    if not path.exists():
        return
    fields = [
        "id", "name", "ticker", "primaryCategory", "discipline",
        "leagueOrMedium", "teamOrPlatform", "role", "country",
        "careerStatus", "marketSegment", "careerStage", "lastVerifiedAt",
        "verificationStatus", "sourceName", "sourceUrl", "sourceRecordId",
        "dataConfidence", "pricingConfidence", "pricingDataStatus",
        "pricingModelVersion", "marketPrice", "fundamentalValue",
        "careerScore",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    catalog = DATA / "current_catalog.json"
    if not catalog.exists():
        raise SystemExit("data/current_catalog.json does not exist")

    records = json.loads(catalog.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise SystemExit("current_catalog.json must be a JSON array")

    revised = [update_record(record) for record in records]
    catalog.write_text(
        json.dumps(revised, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    rewrite_csv(revised)

    manifest_path = DATA / "catalog_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {}
    )
    manifest.update({
        "pricingModelVersion": MODEL_VERSION,
        "athletePricingRule": (
            "35% performance, 25% achievements, 15% potential, "
            "15% audience, 10% availability."
        ),
        "athletePerformanceRule": (
            "70% recent production and 30% efficiency; "
            "custom usage is audit-only."
        ),
        "athleteAvailabilityRule": (
            "Neutral 75 active / 55 inactive until normalized "
            "games-available evidence is stored."
        ),
    })
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Applied revised athlete pricing to {len(revised):,} records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
