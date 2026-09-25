#!/usr/bin/env python3
"""Rebuild NHL valuation metrics from saved source-backed evidence.

The generic catalog enrichment historically classified NHL position code "G" as
a skater. That contaminated goalie percentiles and persisted the bad cohort
labels into Sports market state. This pass is NHL-only and rebuilds the active
metrics from each record's saved raw evidence against corrected SKATER/GOALIE
cohorts. No player-specific price or rank is hard-coded.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from enrich_current_catalog import (
    SIGNAL_KEYS,
    clamp,
    cohort_key,
    number,
    percentile,
    potential_prior,
)

VERSION = "1.0-correct-nhl-role-cohorts"


def is_nhl(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("leagueOrMedium") or "").strip().upper() == "NHL"
    )


def saved_signals(record: dict[str, Any]) -> dict[str, float] | None:
    summary = record.get("pricingEvidenceSummary")
    raw = summary.get("rawSignals") if isinstance(summary, dict) else None
    if not isinstance(raw, dict):
        return None
    signals = {key: float(raw.get(key) or 0.0) for key in SIGNAL_KEYS}
    return signals if any(abs(value) > 1e-12 for value in signals.values()) else None


def recalibrate_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    cohorts: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
    league_pool: list[dict[str, float]] = []
    for record in records:
        if not is_nhl(record):
            continue
        signals = saved_signals(record)
        if signals is None:
            continue
        cohorts[cohort_key(record)].append(signals)
        league_pool.append(signals)

    output: list[dict[str, Any]] = []
    changed = 0
    for original in records:
        record = dict(original)
        if not is_nhl(record):
            output.append(record)
            continue
        signals = saved_signals(record)
        if signals is None:
            output.append(record)
            continue

        peers = cohorts.get(cohort_key(record), [])
        if len(peers) < 8:
            peers = league_pool or [signals]
        pcts = {
            key: percentile(signals[key], [peer.get(key, 0.0) for peer in peers])
            for key in SIGNAL_KEYS
        }
        recent = pcts["recentProduction"]
        career = pcts["careerProduction"]
        efficiency = pcts["efficiency"]
        awards = pcts["awardPoints"]

        existing = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
        performance = clamp(24 + 72 * (recent * 0.70 + efficiency * 0.30), 20, 98)
        achievements = clamp(8 + 88 * (career * 0.70 + awards * 0.30), 8, 99)
        consistency = clamp(24 + 72 * (career * 0.65 + recent * 0.35), 24, 97)
        potential = potential_prior(record, recent)
        audience_base = number(existing.get("audience")) or 40.0
        summary = dict(record.get("pricingEvidenceSummary") or {})
        news_count = int(summary.get("newsCount") or 0)
        news_boost = min(12.0, math.log1p(news_count) * 4.0)
        audience = clamp(audience_base * 0.68 + recent * 18 + awards * 10 + news_boost, 20, 97)
        availability = 75.0 if record.get("careerStatus") == "Active" else 55.0

        record["activeMetrics"] = {
            "performance": round(performance, 1),
            "achievements": round(achievements, 1),
            "consistency": round(consistency, 1),
            "potential": round(potential, 1),
            "availability": round(availability, 1),
            "audience": round(audience, 1),
        }
        summary["cohort"] = f"{cohort_key(record)[0]} · {cohort_key(record)[1]}"
        summary["percentiles"] = {key: round(value, 4) for key, value in pcts.items()}
        record["pricingEvidenceSummary"] = summary
        record["nhlMetricCalibrationVersion"] = VERSION
        record["nhlRoleGroup"] = cohort_key(record)[1]
        output.append(record)
        changed += 1

    return output, changed


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = sorted({
        key for record in records for key, value in record.items()
        if not isinstance(value, (dict, list))
    })
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    updated, changed = recalibrate_records(records)
    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.catalog.name == "current_catalog.json":
        write_csv(args.catalog.with_suffix(".csv"), updated)
    groups = Counter(
        str(record.get("nhlRoleGroup") or "")
        for record in updated
        if is_nhl(record) and record.get("nhlRoleGroup")
    )
    print(f"Recalibrated {changed:,} NHL record(s) with corrected role cohorts: {dict(groups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
