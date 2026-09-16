#!/usr/bin/env python3
"""Safely apply NFL live-sample and missing-draft repairs.

This wrapper deliberately limits market-price rebases to records whose pricing
inputs actually changed: either a previously missing recent-game sample can now
be inferred from a verified regular-season event, or factual draft metadata was
recovered for a no-debut player. Unaffected NFL listings keep their accumulated
market state.
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import repair_nfl_production_prices as base
from nfl_production_pricing import MODEL_VERSION, production_fair_value

SAFE_REPAIR_VERSION = "4.1-nfl-selective-live-sample-and-draft-rebase"
EARLY_SEASON_FULL_WEIGHT_GAMES = base.EARLY_SEASON_FULL_WEIGHT_GAMES


def _regular_season_event_games(record: dict[str, Any], *, now: datetime | None = None) -> int | None:
    """Count verified current-season regular-season game events only.

    Preseason events are ignored and, by themselves, do not imply a zero-game
    current-season sample. That distinction protects established players whose
    provider stat map is incomplete while still letting real September games
    supply a missing sample count.
    """
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    current = now or datetime.now(timezone.utc)
    season_year = current.year if current.month >= 7 else current.year - 1
    regular_start = datetime(season_year, 9, 1, tzinfo=timezone.utc)
    regular_end = datetime(season_year + 1, 2, 16, tzinfo=timezone.utc)
    keys: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or str(event.get("eventType") or "").lower() != "game":
            continue
        started = base._parse_event_time(event.get("startedAt") or event.get("eventDate") or event.get("date"))
        if started is None or not (regular_start <= started < regular_end):
            continue
        key = str(event.get("eventKey") or event.get("eventId") or started.isoformat())
        keys.add(key)
    return min(18, len(keys)) if keys else None


def _recent_sample_games(record: dict[str, Any]) -> int | None:
    """Prefer provider evidence; use verified game history only as a fallback."""
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    for key in ("recentSeasonGames", "recentGames", "seasonGames"):
        direct = base._number(summary.get(key))
        if direct is not None and direct >= 0:
            return min(18, int(round(direct)))

    signals = base._raw_signals(record)
    if signals is not None:
        usage = max(0.0, float(signals.get("usage", 0.0)))
        if usage > 0:
            divisor = 4.0 if bool(record.get("starter")) else 2.0
            return min(18, max(1, int(math.ceil(usage / divisor))))

    return _regular_season_event_games(record)


def _sample_changed(previous: Any, current: Any) -> bool:
    new_value = base._number(current)
    if new_value is None:
        return False
    old_value = base._number(previous)
    if old_value is None:
        return True
    return int(round(old_value)) != int(round(new_value))


def repair_catalog(
    path: Path,
    *,
    repaired_at: str | None = None,
    draft_metadata_path: Path | None = None,
) -> tuple[int, int, int, int]:
    if not path.exists():
        return 0, 0, 0, 0

    # Make the base percentile refresher use the safe inference order above.
    base._regular_season_event_games = _regular_season_event_games
    base._recent_sample_games = _recent_sample_games

    records = base.load_records(path)
    pools, universal_pool = base._nfl_pools(records)
    draft_metadata = base._load_nfl_draft_metadata(draft_metadata_path or base.NFL_DRAFT_METADATA)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    repriced = 0
    synchronized = 0
    sample_repairs = 0
    draft_repairs = 0

    for record in records:
        if not base._is_nfl(record):
            continue

        prior_summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
        prior_sample = prior_summary.get("recentSampleGamesEstimate")
        draft_recovered = base._recover_draft_metadata(record, draft_metadata)
        has_ranked_evidence = base._refresh_nfl_percentiles(record, pools, universal_pool)
        new_summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
        current_sample = new_summary.get("recentSampleGamesEstimate")
        sample_repaired = _sample_changed(prior_sample, current_sample)

        if sample_repaired:
            sample_repairs += 1
        if draft_recovered:
            draft_repairs += 1

        valuation_score, fair, explanation = production_fair_value(record)
        if valuation_score is None or fair is None or explanation is None:
            continue

        production_score = base._number(explanation.get("productionScore"))
        record["nflProductionScore"] = round(production_score, 2) if production_score is not None else None
        record["nflValuationScore"] = round(valuation_score, 2)
        record["nflProductionPricing"] = explanation
        record["nflProductionPriceModelVersion"] = MODEL_VERSION
        record["fairValue"] = round(fair, 2)
        record["fundamentalValue"] = round(fair, 2)
        record["modelTargetPrice"] = round(fair, 2)
        synchronized += 1

        rookie_influence = base._number(explanation.get("rookieInfluence")) or 0.0
        evidence_changed = sample_repaired or draft_recovered
        can_reprice = evidence_changed and (has_ranked_evidence or rookie_influence > 0.0)
        if not can_reprice:
            continue
        if str(record.get("nflProductionRebaseVersion") or "") == SAFE_REPAIR_VERSION:
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
        record["nflProductionRebaseVersion"] = SAFE_REPAIR_VERSION
        record["nflProductionRebasedAt"] = stamp
        cohort = str((record.get("pricingEvidenceSummary") or {}).get("cohort") or "NFL normalized")
        reason = "missing-sample-recovered" if sample_repaired else "missing-draft-metadata-recovered"
        if sample_repaired and draft_recovered:
            reason = "missing-sample-and-draft-metadata-recovered"
        record["nflProductionRebase"] = {
            "reason": reason,
            "oldPrice": round(current, 2),
            "productionLedFairValue": round(fair, 2),
            "valuationScore": round(valuation_score, 2),
            "productionScore": round(production_score, 2) if production_score is not None else None,
            "rookieInfluence": round(rookie_influence, 4),
            "latestGameOverlayPct": round(overlay, 3),
            "rebasedPrice": target,
            "productionCohort": cohort,
            "previousRecentSampleGamesEstimate": prior_sample,
            "recentSampleGamesEstimate": current_sample,
            "recentSampleWeight": (record.get("pricingEvidenceSummary") or {}).get("recentSampleWeight"),
            "historyScaleRatio": round(ratio, 6),
        }
        repriced += 1

    if synchronized:
        base.write_records(path, records)
    return repriced, synchronized, sample_repairs, draft_repairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    repriced, synchronized, sample_repairs, draft_repairs = repair_catalog(args.catalog)
    print(
        f"Synchronized {synchronized:,} NFL valuation(s); selectively rebased {repriced:,} price(s) "
        f"from {sample_repairs:,} repaired live-sample input(s) and {draft_repairs:,} recovered draft record(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
