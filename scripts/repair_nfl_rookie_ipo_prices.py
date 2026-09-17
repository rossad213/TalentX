#!/usr/bin/env python3
"""Apply systemic NFL Rookie IPO and young-starter valuation repairs.

Fundamental value may refresh whenever evidence changes, but market price is only
rebased once per migration version. After that one-time correction, games, role
changes, injuries, awards, and trading/event logic move marketPrice from its prior
market level. This keeps rookies and second-year players IPO-like without repeatedly
resetting their quoted market price back to a model target.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import nfl_rookie_transition as rookie
import nfl_young_starter_value as starter_value
import repair_nfl_production_prices as base

REPAIR_VERSION = "1.2-nfl-ipo-one-time-market-migration"


def _already_migrated(record: dict, field: str) -> bool:
    return str(record.get(field) or "") == REPAIR_VERSION


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    records = base.load_records(path)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    synchronized = 0
    repriced = 0

    # Rookie / Year-2 IPO bridge: continuously update the fundamental, but only
    # migrate the quoted market price once for this model version. New evidence
    # after migration must be expressed through market events, not another reset.
    for record in records:
        if not base._is_nfl(record):
            continue
        fair, explanation = rookie.fair_value(record)
        if fair is None or explanation is None:
            continue

        synchronized += 1
        signature = rookie.evidence_signature(record, explanation)

        explanation = dict(explanation)
        explanation["marketBehavior"] = (
            "IPO-style: draft/opportunity establish the early-career fundamental; "
            "after one migration, marketPrice moves from events/trading rather than model resets"
        )
        record["nflProductionPricing"] = explanation
        record["fairValue"] = fair
        record["fundamentalValue"] = fair
        record["modelTargetPrice"] = fair
        record["nflRookieIpoModelVersion"] = rookie.MODEL_VERSION
        record["nflRookieIpoEvidenceSignature"] = signature
        record["nflRookieIpoEvaluatedAt"] = stamp

        # CRITICAL market rule: evidence/signature changes do not authorize
        # another market-price rebase. Only a new migration version does.
        if _already_migrated(record, "nflRookieIpoRebaseVersion"):
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
            "reason": "one-time-rookie-year2-ipo-market-migration",
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
            "futureBehavior": "no automatic rebase for evidence changes; market events move price",
        }
        repriced += 1

    # League-wide young-starter support follows the same stock-market rule. It may
    # improve the fundamental estimate, but it cannot repeatedly drag marketPrice
    # back to fair value whenever production or starter evidence changes.
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
        explanation["marketBehavior"] = (
            "fundamental support only after migration; quoted market price moves through market events"
        )
        signature = starter_value.evidence_signature(record, explanation)

        record["nflYoungStarterPricing"] = explanation
        record["fairValue"] = fair
        record["fundamentalValue"] = fair
        record["modelTargetPrice"] = fair
        record["nflYoungStarterModelVersion"] = starter_value.MODEL_VERSION
        record["nflYoungStarterEvidenceSignature"] = signature
        record["nflYoungStarterEvaluatedAt"] = stamp

        if _already_migrated(record, "nflYoungStarterRebaseVersion"):
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
            "reason": "one-time-league-wide-young-starter-market-migration",
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
            "futureBehavior": "no automatic rebase for evidence changes; market events move price",
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
        f"NFL IPO/fundamental audit synchronized {synchronized:,} listings and performed "
        f"{repriced:,} one-time market migrations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
