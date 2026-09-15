#!/usr/bin/env python3
"""Rebase NFL prices onto one production-only valuation scale.

The migration is intentionally NFL-only. Every NFL player is ranked in the same
production pool after football statistics have been translated into the common
TalentX production signals. Position, starter/reserve status, fame, age,
achievements, potential, and availability do not affect the price level.

Verified chart/event percentage moves are preserved while stale absolute prices
are rebased onto the production-only fair value.
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from category_market_store import load_records, write_records
from enrich_current_catalog import percentile
from nfl_production_pricing import MODEL_VERSION, production_fair_value

REPAIR_VERSION = "1.2-nfl-production-only-universal-rebase"
SIGNAL_KEYS = (
    "recentProduction",
    "careerProduction",
    "efficiency",
    "usage",
    "careerUsage",
    "awardPoints",
)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp(value: Any, low: float, high: float) -> float:
    parsed = _number(value)
    if parsed is None:
        parsed = low
    return max(low, min(high, parsed))


def _is_nfl(record: dict[str, Any]) -> bool:
    return str(record.get("leagueOrMedium") or "").strip().upper() == "NFL"


def _raw_signals(record: dict[str, Any]) -> dict[str, float] | None:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    raw = summary.get("rawSignals") if isinstance(summary.get("rawSignals"), dict) else {}
    output = {key: float(_number(raw.get(key)) or 0.0) for key in SIGNAL_KEYS}
    return output if any(output.values()) else None


def _nfl_pool(records: list[dict[str, Any]]) -> list[dict[str, float]]:
    pool: list[dict[str, float]] = []
    for record in records:
        if not _is_nfl(record):
            continue
        signals = _raw_signals(record)
        if signals is not None:
            pool.append(signals)
    return pool


def _refresh_nfl_percentiles(record: dict[str, Any], pool: list[dict[str, float]]) -> bool:
    signals = _raw_signals(record)
    if signals is None or not pool:
        return False
    pcts = {
        key: percentile(float(signals.get(key, 0.0)), [float(peer.get(key, 0.0)) for peer in pool])
        for key in SIGNAL_KEYS
    }
    summary = dict(record.get("pricingEvidenceSummary") or {})
    summary["cohort"] = "NFL · universal production"
    summary["percentiles"] = {key: round(value, 4) for key, value in pcts.items()}
    record["pricingEvidenceSummary"] = summary

    # Keep generic active metrics synchronized for UI/backward compatibility,
    # but NFL fair value reads only production-derived performance/consistency
    # when a full ranked production record is not available.
    metrics = dict(record.get("activeMetrics") or {})
    recent_pct = pcts["recentProduction"]
    career_pct = pcts["careerProduction"]
    efficiency_pct = pcts["efficiency"]
    metrics["performance"] = round(_clamp(24 + 72 * (recent_pct * 0.70 + efficiency_pct * 0.30), 20, 98), 1)
    metrics["consistency"] = round(_clamp(24 + 72 * (career_pct * 0.65 + recent_pct * 0.35), 24, 97), 1)
    record["activeMetrics"] = metrics
    return True


def _scale_price_state(record: dict[str, Any], ratio: float) -> None:
    if not math.isfinite(ratio) or ratio <= 0:
        return
    previous = _number(record.get("previousMarketPrice"))
    if previous is not None and previous > 0:
        record["previousMarketPrice"] = round(previous * ratio, 2)

    trend = []
    for value in record.get("trend", []) if isinstance(record.get("trend"), list) else []:
        parsed = _number(value)
        if parsed is not None and parsed > 0:
            trend.append(round(parsed * ratio, 2))
    if trend:
        record["trend"] = trend

    events = []
    for value in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(value, dict):
            events.append(value)
            continue
        event = dict(value)
        for key in ("priceBefore", "priceAfter"):
            parsed = _number(event.get(key))
            if parsed is not None and parsed > 0:
                event[key] = round(parsed * ratio, 2)
        events.append(event)
    if events:
        record["priceEvents"] = events

    history = []
    for value in record.get("priceHistory", []) if isinstance(record.get("priceHistory"), list) else []:
        if not isinstance(value, dict):
            history.append(value)
            continue
        point = dict(value)
        parsed = _number(point.get("price"))
        if parsed is not None and parsed > 0:
            point["price"] = round(parsed * ratio, 2)
        history.append(point)
    if history:
        record["priceHistory"] = history


def _recent_overlay_pct(record: dict[str, Any]) -> float:
    # Preserve the verified latest production-vs-expectation move. Old absolute
    # drift is intentionally discarded by this migration.
    for key in ("lastGameSurpriseMovePct", "lastGameMovePct"):
        value = _number(record.get(key))
        if value is not None and -8.0 <= value <= 8.0:
            return value
    return 0.0


def _pure_rookie_ipo(record: dict[str, Any]) -> bool:
    """Keep a true pre-production draft IPO until NFL production exists."""
    status = str(record.get("pricingDataStatus") or "")
    pricing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else {}
    influence = _number(pricing.get("draftInfluencePct")) or 0.0
    return status.startswith("Rookie IPO") and influence >= 75.0 and _raw_signals(record) is None


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    records = load_records(path)
    pool = _nfl_pool(records)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    repriced = 0
    synchronized = 0

    for record in records:
        if not _is_nfl(record):
            continue
        has_ranked_evidence = _refresh_nfl_percentiles(record, pool)
        score, fair, explanation = production_fair_value(record)
        if score is None or fair is None or explanation is None:
            continue

        record["nflProductionScore"] = score
        record["nflProductionPricing"] = explanation
        record["nflProductionPriceModelVersion"] = MODEL_VERSION
        record["fairValue"] = round(fair, 2)
        record["fundamentalValue"] = round(fair, 2)
        record["modelTargetPrice"] = round(fair, 2)
        synchronized += 1

        if not has_ranked_evidence or _pure_rookie_ipo(record):
            continue
        if str(record.get("nflProductionRebaseVersion") or "") == REPAIR_VERSION:
            continue

        current = _number(record.get("marketPrice"))
        if current is None or current <= 0:
            current = fair
        overlay = _recent_overlay_pct(record)
        target = max(0.01, round(fair * (1.0 + overlay / 100.0), 2))
        ratio = target / current if current > 0 else 1.0
        _scale_price_state(record, ratio)
        record["marketPrice"] = target
        if _number(record.get("previousMarketPrice")) is None:
            record["previousMarketPrice"] = target
        record["nflProductionRebaseVersion"] = REPAIR_VERSION
        record["nflProductionRebasedAt"] = stamp
        record["nflProductionRebase"] = {
            "oldPrice": round(current, 2),
            "productionFairValue": round(fair, 2),
            "latestGameOverlayPct": round(overlay, 3),
            "rebasedPrice": target,
            "productionCohort": "NFL universal",
            "historyScaleRatio": round(ratio, 6),
        }
        repriced += 1

    if synchronized:
        write_records(path, records)
    return repriced, synchronized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    repriced, synchronized = repair_catalog(args.catalog)
    print(
        f"Synchronized {synchronized:,} NFL production valuation(s); "
        f"rebased {repriced:,} stale market price(s) on the universal production scale."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
