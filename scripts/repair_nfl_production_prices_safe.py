#!/usr/bin/env python3
"""Safely apply NFL live-sample and missing-draft repairs.

Market prices are rebased only when pricing evidence actually changes. The
wrapper also repairs state from the earlier broad v4 rebase: unaffected listings
are restored to their pre-v4 market state, while evidence-backed v4 corrections
are preserved.
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import repair_nfl_production_prices as base
from nfl_production_pricing import MODEL_VERSION, production_fair_value

UNSAFE_REPAIR_VERSION = base.REPAIR_VERSION
SAFE_REPAIR_VERSION = "4.2-nfl-selective-self-healing-rebase"
EARLY_SEASON_FULL_WEIGHT_GAMES = base.EARLY_SEASON_FULL_WEIGHT_GAMES


def _regular_season_event_games(record: dict[str, Any], *, now: datetime | None = None) -> int | None:
    """Count verified current-season regular-season game events only."""
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
    """Prefer provider evidence; use verified regular games only as a fallback."""
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


def _uses_event_fallback(record: dict[str, Any]) -> bool:
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    for key in ("recentSeasonGames", "recentGames", "seasonGames"):
        direct = base._number(summary.get(key))
        if direct is not None and direct >= 0:
            return False
    signals = base._raw_signals(record)
    if signals is not None and max(0.0, float(signals.get("usage", 0.0))) > 0:
        return False
    return _regular_season_event_games(record) is not None


def _sample_changed(previous: Any, current: Any) -> bool:
    new_value = base._number(current)
    if new_value is None:
        return False
    old_value = base._number(previous)
    if old_value is None:
        return True
    return int(round(old_value)) != int(round(new_value))


def _has_local_draft_override(record: dict[str, Any], draft_metadata: dict[str, dict[str, Any]]) -> bool:
    if max(0.0, base._number(record.get("professionalGames")) or 0.0) > 0:
        return False
    return base._name_key(record.get("name")) in draft_metadata


def _undo_unsafe_v4_rebase(record: dict[str, Any], stamp: str) -> bool:
    """Restore pre-v4 market state using the broad rebase's own audit fields."""
    if str(record.get("nflProductionRebaseVersion") or "") != UNSAFE_REPAIR_VERSION:
        return False
    info = record.get("nflProductionRebase") if isinstance(record.get("nflProductionRebase"), dict) else {}
    old_price = base._number(info.get("oldPrice"))
    if old_price is None or old_price <= 0:
        return False

    bad_price = base._number(record.get("marketPrice"))
    ratio = base._number(info.get("historyScaleRatio"))
    if ratio is not None and ratio > 0:
        base._scale_price_state(record, 1.0 / ratio)
    record["marketPrice"] = round(old_price, 2)
    record["nflProductionRebaseVersion"] = SAFE_REPAIR_VERSION
    record["nflProductionRebasedAt"] = stamp
    record["nflProductionRebase"] = {
        "reason": "unaffected-broad-v4-rebase-restored",
        "unsafeV4Price": round(bad_price, 2) if bad_price is not None else None,
        "restoredPrice": round(old_price, 2),
        "restoredFromVersion": UNSAFE_REPAIR_VERSION,
    }
    return True


def _mark_preserved_unsafe_v4(record: dict[str, Any], stamp: str, reason: str) -> None:
    info = dict(record.get("nflProductionRebase") or {})
    info["reason"] = reason
    info["preservedFromVersion"] = UNSAFE_REPAIR_VERSION
    record["nflProductionRebase"] = info
    record["nflProductionRebaseVersion"] = SAFE_REPAIR_VERSION
    record["nflProductionRebasedAt"] = stamp


def repair_catalog(
    path: Path,
    *,
    repaired_at: str | None = None,
    draft_metadata_path: Path | None = None,
) -> tuple[int, int, int, int]:
    if not path.exists():
        return 0, 0, 0, 0

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

        was_unsafe_v4 = str(record.get("nflProductionRebaseVersion") or "") == UNSAFE_REPAIR_VERSION
        event_fallback_needed = _uses_event_fallback(record)
        local_draft_override = _has_local_draft_override(record, draft_metadata)
        preserve_unsafe = was_unsafe_v4 and (event_fallback_needed or local_draft_override)
        if was_unsafe_v4 and not preserve_unsafe:
            _undo_unsafe_v4_rebase(record, stamp)

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

        # If broad v4 already made exactly the correction now considered valid,
        # keep that price but convert its marker to the safe version.
        if preserve_unsafe and not sample_repaired and not draft_recovered:
            reason = "missing-sample-recovered" if event_fallback_needed else "missing-draft-metadata-recovered"
            if event_fallback_needed and local_draft_override:
                reason = "missing-sample-and-draft-metadata-recovered"
            _mark_preserved_unsafe_v4(record, stamp, reason)
            continue

        rookie_influence = base._number(explanation.get("rookieInfluence")) or 0.0
        evidence_changed = sample_repaired or draft_recovered
        can_reprice = evidence_changed and (has_ranked_evidence or rookie_influence > 0.0)
        if not can_reprice:
            continue
        if str(record.get("nflProductionRebaseVersion") or "") == SAFE_REPAIR_VERSION and not was_unsafe_v4:
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
