#!/usr/bin/env python3
"""Apply systemic NFL Rookie IPO and young-starter valuation repairs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import nfl_rookie_transition as rookie
import nfl_young_starter_value as starter_value
import repair_nfl_production_prices as base

REPAIR_VERSION = "1.1-nfl-rookie-ipo-plus-young-starter-rebase"


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    records = base.load_records(path)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    synchronized = 0
    repriced = 0

    # First preserve the factual, decaying Rookie IPO bridge for recent draft classes.
    for record in records:
        if not base._is_nfl(record):
            continue
        fair, explanation = rookie.fair_value(record)
        if fair is None or explanation is None:
            continue

        synchronized += 1
        signature = rookie.evidence_signature(record, explanation)
        prior_version = str(record.get("nflRookieIpoModelVersion") or "")
        prior_signature = str(record.get("nflRookieIpoEvidenceSignature") or "")

        record["nflProductionPricing"] = explanation
        record["fairValue"] = fair
        record["fundamentalValue"] = fair
        record["modelTargetPrice"] = fair
        record["nflRookieIpoModelVersion"] = rookie.MODEL_VERSION
        record["nflRookieIpoEvidenceSignature"] = signature
        record["nflRookieIpoEvaluatedAt"] = stamp

        if prior_version == rookie.MODEL_VERSION and prior_signature == signature:
            continue

        current = base._number(record.get("marketPrice"))
        if current is None or current <= 0:
            current = fair
        overlay = base._recent_overlay_pct(record)
        target = max(0.01, round(fair * (1.0 + overlay / 100.0), 2))
        ratio = target / current if current > 0 else 1.0
        base._scale_price_state(record, ratio)
        record["marketPrice"] = target
        if base._number(record.get("previousMarketPrice")) is None:
            record["previousMarketPrice"] = target
        record["nflRookieIpoRebaseVersion"] = REPAIR_VERSION
        record["nflRookieIpoRebasedAt"] = stamp
        factors = explanation.get("rookieIpoFactors") or {}
        record["nflRookieIpoRebase"] = {
            "reason": "rookie-ipo-systemic-multifactor-handoff",
            "oldPrice": round(current, 2),
            "careerFairValue": explanation.get("careerFairValue"),
            "rookieIpoAnchor": explanation.get("rookieIpoAnchor"),
            "rookieInfluence": explanation.get("rookieInfluence"),
            "draftPick": record.get("draftPick"),
            "age": record.get("age"),
            "experienceYears": record.get("experienceYears"),
            "careerGameEvidence": factors.get("careerGameEvidence"),
            "meaningfulProduction": factors.get("meaningfulProduction"),
            "roleGamePace": factors.get("roleGamePace"),
            "missingRecentSampleGuard": explanation.get("rookieIpoMissingRecentSampleGuard"),
            "latestGameOverlayPct": round(overlay, 3),
            "rebasedPrice": target,
            "historyScaleRatio": round(ratio, 6),
        }
        repriced += 1

    # Then apply the same league-wide role/runway rule to every qualifying young
    # NFL starter. This is deliberately not player-specific and only raises a
    # fundamental when verified starter opportunity + production justify it.
    for record in records:
        if not base._is_nfl(record):
            continue
        candidate, explanation = starter_value.fair_value(record)
        if candidate is None or explanation is None or not explanation.get("upliftApplied"):
            continue

        existing_candidates = [
            base._number(record.get("fairValue")),
            base._number(record.get("fundamentalValue")),
            base._number(record.get("modelTargetPrice")),
        ]
        existing_fair = max((value for value in existing_candidates if value is not None), default=0.0)
        fair = round(max(existing_fair, candidate), 2)
        if fair <= existing_fair + 0.01:
            continue

        synchronized += 1
        explanation = dict(explanation)
        explanation["priorSystemFairValue"] = round(existing_fair, 2)
        explanation["fairValue"] = fair
        signature = starter_value.evidence_signature(record, explanation)
        prior_version = str(record.get("nflYoungStarterModelVersion") or "")
        prior_signature = str(record.get("nflYoungStarterEvidenceSignature") or "")

        record["nflYoungStarterPricing"] = explanation
        record["fairValue"] = fair
        record["fundamentalValue"] = fair
        record["modelTargetPrice"] = fair
        record["nflYoungStarterModelVersion"] = starter_value.MODEL_VERSION
        record["nflYoungStarterEvidenceSignature"] = signature
        record["nflYoungStarterEvaluatedAt"] = stamp

        if prior_version == starter_value.MODEL_VERSION and prior_signature == signature:
            continue

        current = base._number(record.get("marketPrice"))
        if current is None or current <= 0:
            current = fair
        overlay = base._recent_overlay_pct(record)
        target = max(0.01, round(fair * (1.0 + overlay / 100.0), 2))
        ratio = target / current if current > 0 else 1.0
        base._scale_price_state(record, ratio)
        record["marketPrice"] = target
        if base._number(record.get("previousMarketPrice")) is None:
            record["previousMarketPrice"] = target
        record["nflYoungStarterRebaseVersion"] = REPAIR_VERSION
        record["nflYoungStarterRebasedAt"] = stamp
        record["nflYoungStarterRebase"] = {
            "reason": "league-wide-young-starter-role-runway-correction",
            "oldPrice": round(current, 2),
            "priorSystemFairValue": round(existing_fair, 2),
            "productionScore": explanation.get("productionScore"),
            "valuationScore": explanation.get("valuationScore"),
            "verifiedStarter": explanation.get("verifiedStarter"),
            "quarterback": explanation.get("quarterback"),
            "age": record.get("age"),
            "experienceYears": record.get("experienceYears"),
            "draftPick": record.get("draftPick"),
            "latestGameOverlayPct": round(overlay, 3),
            "rebasedPrice": target,
            "historyScaleRatio": round(ratio, 6),
        }
        repriced += 1

    if synchronized:
        base.write_records(path, records)
    return repriced, synchronized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    repriced, synchronized = repair_catalog(args.catalog)
    print(
        f"NFL valuation audit synchronized {synchronized:,} Rookie IPO/young-starter listings "
        f"and rebased {repriced:,} changed listings."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
