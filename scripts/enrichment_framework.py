#!/usr/bin/env python3
"""Shared modular enrichment framework for TalentX catalogs.

Each enricher converts category-specific evidence into a normalized TalentX
record. The pricing engine consumes the normalized fields and does not need to
know which source adapter produced them.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

NORMALIZED_METRICS = ("performance", "achievements", "consistency", "potential", "availability", "audience")


class Enricher(Protocol):
    name: str
    def supports(self, record: dict[str, Any]) -> bool: ...
    def enrich(self, record: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class Registry:
    enrichers: list[Enricher]

    def register(self, enricher: Enricher) -> None:
        self.enrichers.append(enricher)

    def resolve(self, record: dict[str, Any]) -> Enricher | None:
        for enricher in self.enrichers:
            if enricher.supports(record):
                return enricher
        return None


def clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return round(max(low, min(high, number)), 2)


def normalized_metrics(record: dict[str, Any]) -> dict[str, float]:
    existing = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    fallback = clamp(record.get("careerScore", 50))
    return {key: clamp(existing.get(key, fallback)) for key in NORMALIZED_METRICS}


def infer_generic_stage(record: dict[str, Any]) -> str:
    stage = str(record.get("careerStage") or "").strip()
    if stage and stage.lower() not in {"stage under review", "under review", "unknown", "not listed"}:
        return stage
    years = max(0.0, float(record.get("yearsActive") or record.get("experienceYears") or 0))
    age = float(record.get("age") or 0)
    if years >= 15 or age >= 35:
        return "Veteran"
    if years >= 8:
        return "Established"
    if years >= 4:
        return "Prime"
    if years >= 2:
        return "Emerging"
    if years > 0:
        return "Early Career"
    return "Stage under review"


def finalize(record: dict[str, Any], enricher_name: str) -> dict[str, Any]:
    result = dict(record)
    result["activeMetrics"] = normalized_metrics(result)
    result["careerStage"] = infer_generic_stage(result)
    result["enrichmentFrameworkVersion"] = "1.0"
    result["enrichmentAdapter"] = enricher_name
    result["enrichmentStatus"] = "Enriched" if result["careerStage"] != "Stage under review" else "Partial"
    result["normalizedEvidence"] = {
        "talentEvidence": result["activeMetrics"],
        "marketEvidence": {
            "audience": result["activeMetrics"]["audience"],
            "momentumPct": result.get("momentumPct", 0),
            "demandPremiumPct": result.get("demandPremiumPct", 0),
        },
        "confidenceEvidence": {
            "yearsActive": result.get("yearsActive", result.get("experienceYears")),
            "professionalGames": result.get("professionalGames"),
            "pricingConfidence": result.get("pricingConfidence", result.get("dataConfidence")),
        },
        "situationEvidence": result.get("situationEvidence", {}),
    }
    return result


def load_registry() -> Registry:
    registry = Registry([])
    modules = (
        "enrichers.soccer",
        "enrichers.athlete",
        "enrichers.music",
        "enrichers.actor",
        "enrichers.creator",
        "enrichers.generic",
    )
    for module_name in modules:
        module = importlib.import_module(module_name)
        registry.register(module.ENRICHER)
    return registry


def process(records: list[dict[str, Any]], registry: Registry) -> tuple[list[dict[str, Any]], dict[str, int]]:
    output: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for record in records:
        enricher = registry.resolve(record)
        if enricher is None:
            output.append(finalize(record, "none"))
            counts["none"] = counts.get("none", 0) + 1
            continue
        enriched = finalize(enricher.enrich(record), enricher.name)
        output.append(enriched)
        counts[enricher.name] = counts.get(enricher.name, 0) + 1
    return output, counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("Catalog must contain a JSON array")
    updated, counts = process([x for x in payload if isinstance(x, dict)], load_registry())
    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.catalog.name == "current_catalog.json":
        csv_path = args.catalog.with_suffix(".csv")
        fields = sorted({key for row in updated for key, value in row.items() if not isinstance(value, (dict, list))})
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader(); writer.writerows(updated)
    print("Applied modular enrichment:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
