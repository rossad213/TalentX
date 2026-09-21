#!/usr/bin/env python3
"""Apply the multifactor NFL Rookie IPO transition to the Sports catalog."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import nfl_rookie_transition as rookie
import repair_nfl_production_prices as base

REPAIR_VERSION = "1.1-nfl-rookie-year2-model-migration"


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    records = base.load_records(path)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    synchronized = 0
    repriced = 0

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

        # Rebase only when the pricing model version changes. New games/stat
        # signatures update fundamentals, while the event engine moves marketPrice.
        if prior_version == rookie.MODEL_VERSION:
            continue

        current = base._number(record.get("marketPrice"))
        if current is None or current <= 0:
            current = fair
        overlay = base._recent_overlay_pct(record)
        target = max(0.01, round(fair * (1.0 + overlay / 100.0), 2))
        ratio = target / current if current > 0 else 1.0
        record["previousMarketPrice"] = round(current, 2)
        record["marketPrice"] = target
        record["dailyChange"] = round(target - current, 2)
        record["hourlyChangePct"] = round((target / current - 1.0) * 100.0, 3) if current > 0 else 0.0
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
            "historyScaleRatio": 1.0,
            "marketMoveRatio": round(ratio, 6),
            "futureBehavior": "fundamental updates do not reset marketPrice; verified events/trading move the quote",
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
    print(f"Rookie IPO audit synchronized {synchronized:,} recent drafted NFL players and rebased {repriced:,} changed listings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
