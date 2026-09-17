#!/usr/bin/env python3
"""Apply the NFL stock-market valuation architecture without resetting live quotes.

Rookies and second-year players use the calibrated Rookie IPO transition. Year-3+
players use Production + Potential. Fundamental/model targets can refresh, but the
quoted market price retains continuity. This revision also reverses the prior v1.3
full-price migration and replaces it with one bounded model correction, after which
games, role changes, injuries, awards, and trading/event logic move marketPrice.

NBA pricing is not imported, modified, or rewritten here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import nfl_production_potential_value as veteran_value
import nfl_rookie_transition as rookie
import repair_nfl_production_prices as base

BAD_MIGRATION_VERSION = "1.3-nfl-ipo-then-production-potential"
REPAIR_VERSION = "1.4-nfl-stock-market-continuity"
MAX_MODEL_CORRECTION_PCT = 0.20


def _restore_bad_migration(record: dict, *, field: str, audit_field: str, stamp: str) -> bool:
    """Undo the v1.3 full fair-value reset using its own recorded old price/ratio."""
    if str(record.get(field) or "") != BAD_MIGRATION_VERSION:
        return False
    info = record.get(audit_field) if isinstance(record.get(audit_field), dict) else {}
    old_price = base._number(info.get("oldPrice"))
    ratio = base._number(info.get("historyScaleRatio"))
    if old_price is None or old_price <= 0:
        return False
    bad_price = base._number(record.get("marketPrice"))
    if ratio is not None and ratio > 0:
        base._scale_price_state(record, 1.0 / ratio)
    record["marketPrice"] = round(old_price, 2)
    record[field] = f"restored-by-{REPAIR_VERSION}"
    record[audit_field] = {
        **dict(info),
        "restoredBy": REPAIR_VERSION,
        "restoredAt": stamp,
        "badMigrationPrice": round(bad_price, 2) if bad_price is not None else None,
        "restoredPrice": round(old_price, 2),
        "reason": "restore-pre-v1.3-market-quote",
    }
    return True


def _apply_bounded_model_correction(record: dict, fair: float, *, regime: str, stamp: str) -> bool:
    """Move toward a corrected model target once without snapping marketPrice to fair value."""
    if str(record.get("nflMarketArchitectureVersion") or "") == REPAIR_VERSION:
        return False
    current = base._number(record.get("marketPrice"))
    if current is None or current <= 0:
        record["marketPrice"] = round(fair, 2)
        record["previousMarketPrice"] = round(fair, 2)
        record["nflMarketArchitectureVersion"] = REPAIR_VERSION
        record["nflMarketArchitectureAdjustedAt"] = stamp
        record["nflMarketArchitectureAdjustment"] = {
            "reason": "initialize-missing-market-quote",
            "regime": regime,
            "modelTargetPrice": round(fair, 2),
            "afterPrice": round(fair, 2),
        }
        return True

    desired_pct = fair / current - 1.0
    move_pct = max(-MAX_MODEL_CORRECTION_PCT, min(MAX_MODEL_CORRECTION_PCT, desired_pct))
    target = round(max(0.01, current * (1.0 + move_pct)), 2)

    # Do not rewrite historical prices for a model correction. Preserve the prior
    # quote and let append_price_history record this one explicit architecture move.
    record["previousMarketPrice"] = round(current, 2)
    record["marketPrice"] = target
    record["dailyChange"] = round(target - current, 2)
    record["hourlyChangePct"] = round(move_pct * 100.0, 3)
    record["nflMarketArchitectureVersion"] = REPAIR_VERSION
    record["nflMarketArchitectureAdjustedAt"] = stamp
    record["nflMarketArchitectureAdjustment"] = {
        "reason": "bounded-model-architecture-correction",
        "regime": regime,
        "beforePrice": round(current, 2),
        "modelTargetPrice": round(fair, 2),
        "maxCorrectionPct": round(MAX_MODEL_CORRECTION_PCT * 100.0, 1),
        "appliedMovePct": round(move_pct * 100.0, 3),
        "afterPrice": target,
        "futureBehavior": "no automatic fair-value reset; verified market events/trading move the quote",
    }
    return abs(target - current) >= 0.01


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> tuple[int, int, int]:
    if not path.exists():
        return 0, 0, 0
    records = base.load_records(path)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    synchronized = 0
    corrected = 0
    restored = 0

    # First undo the bad full-price v1.3 migration wherever it was applied.
    for record in records:
        if not base._is_nfl(record):
            continue
        if _restore_bad_migration(
            record,
            field="nflRookieIpoRebaseVersion",
            audit_field="nflRookieIpoRebase",
            stamp=stamp,
        ):
            restored += 1
        if _restore_bad_migration(
            record,
            field="nflProductionPotentialRebaseVersion",
            audit_field="nflProductionPotentialRebase",
            stamp=stamp,
        ):
            restored += 1

    # Regime 1: Rookie + Year-2 players behave like IPO stocks.
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
        explanation["marketBehavior"] = "fundamental/model target can update; live quote keeps market continuity"
        record["nflProductionPricing"] = explanation
        record["fairValue"] = fair
        record["fundamentalValue"] = fair
        record["modelTargetPrice"] = fair
        record["nflRookieIpoModelVersion"] = rookie.MODEL_VERSION
        record["nflRookieIpoEvidenceSignature"] = signature
        record["nflRookieIpoEvaluatedAt"] = stamp
        if _apply_bounded_model_correction(record, fair, regime="Rookie / Year-2 IPO", stamp=stamp):
            corrected += 1

    # Regime 2: Year-3+ / established players use Production + Potential.
    for record in records:
        if not base._is_nfl(record) or veteran_value.is_ipo_player(record):
            continue
        fair, explanation = veteran_value.fair_value(record)
        if fair is None or explanation is None:
            continue
        synchronized += 1
        explanation = dict(explanation)
        explanation["valuationRegime"] = "Veteran Production + Potential"
        explanation["marketBehavior"] = "fundamental/model target can update; live quote keeps market continuity"
        record["nflProductionPotentialPricing"] = explanation
        record["nflProductionScore"] = explanation.get("productionScore")
        record["nflPotentialScore"] = explanation.get("potentialScore")
        record["nflValuationScore"] = explanation.get("valuationScore")
        record["fairValue"] = fair
        record["fundamentalValue"] = fair
        record["modelTargetPrice"] = fair
        record["nflProductionPotentialModelVersion"] = veteran_value.MODEL_VERSION
        record["nflProductionPotentialEvaluatedAt"] = stamp
        if _apply_bounded_model_correction(record, fair, regime="Veteran Production + Potential", stamp=stamp):
            corrected += 1

    if synchronized or restored:
        base.write_records(path, records)
    return corrected, synchronized, restored


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    corrected, synchronized, restored = repair_catalog(args.catalog)
    print(
        f"NFL market audit synchronized {synchronized:,} listings, restored {restored:,} bad v1.3 "
        f"migration state(s), and applied {corrected:,} bounded architecture correction(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
