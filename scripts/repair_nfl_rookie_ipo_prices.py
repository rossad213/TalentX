#!/usr/bin/env python3
"""Apply the NFL stock-market valuation architecture.

Rookies and second-year players use the Rookie IPO transition. Year-3+ / established
players use the league-wide Production + Potential fundamental model. Fundamental
value may refresh as evidence changes, but marketPrice is rebased only once per
migration version. After migration, game, role, injury, award, and trading/event
logic moves the quoted market price from its prior level.

NBA pricing is not imported, modified, or rewritten here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import nfl_production_potential_value as veteran_value
import nfl_rookie_transition as rookie
import repair_nfl_production_prices as base

REPAIR_VERSION = "1.3-nfl-ipo-then-production-potential"


def _already_migrated(record: dict, field: str) -> bool:
    return str(record.get(field) or "") == REPAIR_VERSION


def _migrate_market_once(
    record: dict,
    fair: float,
    *,
    field: str,
    stamp: str,
    reason: str,
    audit_field: str,
    extra: dict | None = None,
) -> bool:
    """Correct legacy NFL market state once, then preserve stock-like continuity."""
    if _already_migrated(record, field):
        return False
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
    record[field] = REPAIR_VERSION
    record[f"{field.removesuffix('Version')}dAt"] = stamp
    record[audit_field] = {
        "reason": reason,
        "oldPrice": round(current, 2),
        "fundamentalValue": round(fair, 2),
        "latestGameOverlayPct": round(overlay, 3),
        "rebasedPrice": target,
        "historyScaleRatio": round(ratio, 6),
        "futureBehavior": "marketPrice is not reset by fundamental refreshes; events/trading move the quote",
        **(extra or {}),
    }
    return True


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    records = base.load_records(path)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    synchronized = 0
    repriced = 0

    # Regime 1: Rookie + second-year players behave like IPO stocks. Draft capital,
    # opportunity, age/development and early NFL evidence establish the fundamental;
    # professional evidence then hands the valuation toward the veteran model.
    for record in records:
        if not base._is_nfl(record) or not veteran_value.is_ipo_player(record):
            continue
        fair, explanation = rookie.fair_value(record)
        if fair is None or explanation is None:
            continue

        synchronized += 1
        signature = rookie.evidence_signature(record, explanation)
        explanation = dict(explanation)
        explanation["valuationRegime"] = "Rookie / Year-2 IPO"
        explanation["marketBehavior"] = (
            "IPO fundamental updates with evidence; quoted market price keeps continuity after migration"
        )
        record["nflProductionPricing"] = explanation
        record["fairValue"] = fair
        record["fundamentalValue"] = fair
        record["modelTargetPrice"] = fair
        record["nflRookieIpoModelVersion"] = rookie.MODEL_VERSION
        record["nflRookieIpoEvidenceSignature"] = signature
        record["nflRookieIpoEvaluatedAt"] = stamp

        factors = explanation.get("rookieIpoFactors") or {}
        if _migrate_market_once(
            record,
            fair,
            field="nflRookieIpoRebaseVersion",
            stamp=stamp,
            reason="one-time-rookie-year2-ipo-market-migration",
            audit_field="nflRookieIpoRebase",
            extra={
                "rookieIpoAnchor": explanation.get("rookieIpoAnchor"),
                "rookieInfluence": explanation.get("rookieInfluence"),
                "draftPick": record.get("draftPick"),
                "age": record.get("age"),
                "experienceYears": record.get("experienceYears"),
                "careerGameEvidence": factors.get("careerGameEvidence"),
            },
        ):
            repriced += 1

    # Regime 2: Year-3+ / established NFL players use the same top-level market
    # architecture requested for NBA-style valuation: Production + Potential.
    # NFL-specific subfactors feed those two buckets; there are no player overrides.
    for record in records:
        if not base._is_nfl(record) or veteran_value.is_ipo_player(record):
            continue
        fair, explanation = veteran_value.fair_value(record)
        if fair is None or explanation is None:
            continue

        synchronized += 1
        explanation = dict(explanation)
        explanation["valuationRegime"] = "Veteran Production + Potential"
        explanation["marketBehavior"] = (
            "fundamental anchor refreshes from Production + Potential; quoted market price moves through events/trading"
        )
        record["nflProductionPotentialPricing"] = explanation
        record["nflProductionScore"] = explanation.get("productionScore")
        record["nflPotentialScore"] = explanation.get("potentialScore")
        record["nflValuationScore"] = explanation.get("valuationScore")
        record["fairValue"] = fair
        record["fundamentalValue"] = fair
        record["modelTargetPrice"] = fair
        record["nflProductionPotentialModelVersion"] = veteran_value.MODEL_VERSION
        record["nflProductionPotentialEvaluatedAt"] = stamp

        if _migrate_market_once(
            record,
            fair,
            field="nflProductionPotentialRebaseVersion",
            stamp=stamp,
            reason="one-time-veteran-production-potential-market-migration",
            audit_field="nflProductionPotentialRebase",
            extra={
                "productionScore": explanation.get("productionScore"),
                "potentialScore": explanation.get("potentialScore"),
                "valuationScore": explanation.get("valuationScore"),
                "topLevelWeights": explanation.get("topLevelWeights"),
                "age": record.get("age"),
                "experienceYears": record.get("experienceYears"),
            },
        ):
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
        f"NFL market audit synchronized {synchronized:,} IPO/Production+Potential listings "
        f"and performed {repriced:,} one-time market migrations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
