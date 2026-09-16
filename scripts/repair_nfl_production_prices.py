#!/usr/bin/env python3
"""Rebase NFL prices onto the stable production-led TalentX valuation scale.

Raw football statistics are first normalized inside comparable position groups
(QB, RB, receivers, defense, offensive line, special teams). Those normalized
0-100 production percentiles then share one universal NFL dollar scale. This
avoids comparing passing-yard raw totals directly with rushing, receiving, or
defensive raw totals while still allowing every NFL player to be valued on the
same TalentX market scale.

The first few games of a new season are shrunk toward durable career evidence so
one hot or cold week cannot silently replace an established fundamental. Verified
game events remain separate and can still move the market price immediately.

No-debut drafted players can retain a time-decaying Rookie IPO anchor even when a
roster feed labels them as second-year players.
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from category_market_store import load_records, write_records
from enrich_current_catalog import percentile, role_group
from nfl_production_pricing import MODEL_VERSION, production_fair_value

REPAIR_VERSION = "3.0-nfl-position-normalized-sample-stable-rookie-ipo-rebase"
SIGNAL_KEYS = (
    "recentProduction",
    "careerProduction",
    "efficiency",
    "usage",
    "careerUsage",
    "awardPoints",
)
POSITION_NORMALIZED_KEYS = (
    "recentProduction",
    "careerProduction",
    "efficiency",
    "usage",
    "careerUsage",
)
EARLY_SEASON_FULL_WEIGHT_GAMES = 6.0


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


def _nfl_pools(records: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, float]]], list[dict[str, float]]]:
    pools: dict[str, list[dict[str, float]]] = defaultdict(list)
    universal: list[dict[str, float]] = []
    for record in records:
        if not _is_nfl(record):
            continue
        signals = _raw_signals(record)
        if signals is None:
            continue
        pools[role_group(record)].append(signals)
        universal.append(signals)
    return dict(pools), universal


def _recent_sample_games(record: dict[str, Any]) -> int | None:
    """Estimate how much current-season evidence is represented by raw signals.

    Future collectors may write an exact recent-game count. Existing records can
    be estimated conservatively from the enrichment ``usage`` signal, which is
    starts*3 + games. Starters therefore contribute about four usage points per
    game. For non-starters we use a more permissive divisor of two so rotational
    players are not treated as if a full season were only a few appearances.
    """
    summary = record.get("pricingEvidenceSummary") if isinstance(record.get("pricingEvidenceSummary"), dict) else {}
    for key in ("recentSeasonGames", "recentGames", "seasonGames"):
        direct = _number(summary.get(key))
        if direct is not None and direct >= 0:
            return min(18, int(round(direct)))

    signals = _raw_signals(record)
    if signals is None:
        return None
    usage = max(0.0, float(signals.get("usage", 0.0)))
    if usage <= 0:
        return None
    divisor = 4.0 if bool(record.get("starter")) else 2.0
    return min(18, max(1, int(math.ceil(usage / divisor))))


def _stabilize_early_season_percentiles(
    record: dict[str, Any], pcts: dict[str, float]
) -> tuple[dict[str, float], int | None, float]:
    """Shrink tiny recent samples toward durable career/neutral evidence."""
    games = _recent_sample_games(record)
    if games is None or games >= EARLY_SEASON_FULL_WEIGHT_GAMES:
        return dict(pcts), games, 1.0

    weight = max(0.0, min(1.0, games / EARLY_SEASON_FULL_WEIGHT_GAMES))
    output = dict(pcts)
    recent = float(output.get("recentProduction", 0.5))
    career = float(output.get("careerProduction", 0.5))
    efficiency = float(output.get("efficiency", 0.5))
    output["recentProduction"] = career + (recent - career) * weight
    output["efficiency"] = 0.5 + (efficiency - 0.5) * weight
    return output, games, weight


def _refresh_nfl_percentiles(
    record: dict[str, Any],
    pools: dict[str, list[dict[str, float]]],
    universal_pool: list[dict[str, float]],
) -> bool:
    signals = _raw_signals(record)
    if signals is None:
        return False
    group = role_group(record)
    position_pool = pools.get(group, [])
    if not position_pool:
        return False

    raw_pcts: dict[str, float] = {}
    for key in POSITION_NORMALIZED_KEYS:
        raw_pcts[key] = percentile(
            float(signals.get(key, 0.0)),
            [float(peer.get(key, 0.0)) for peer in position_pool],
        )
    # Award points are already one comparable concept across positions, so keep
    # that percentile league-wide rather than manufacturing a position premium.
    award_pool = universal_pool or position_pool
    raw_pcts["awardPoints"] = percentile(
        float(signals.get("awardPoints", 0.0)),
        [float(peer.get("awardPoints", 0.0)) for peer in award_pool],
    )

    pcts, sample_games, sample_weight = _stabilize_early_season_percentiles(record, raw_pcts)
    summary = dict(record.get("pricingEvidenceSummary") or {})
    summary["cohort"] = f"NFL · {group} normalized production"
    summary["normalization"] = "position-group raw statistics -> universal 0-100 NFL value scale"
    summary["unstabilizedPercentiles"] = {key: round(value, 4) for key, value in raw_pcts.items()}
    summary["percentiles"] = {key: round(value, 4) for key, value in pcts.items()}
    summary["recentSampleGamesEstimate"] = sample_games
    summary["recentSampleWeight"] = round(sample_weight, 4)
    record["pricingEvidenceSummary"] = summary

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
    for key in ("lastGameSurpriseMovePct", "lastGameMovePct"):
        value = _number(record.get(key))
        if value is not None and -8.0 <= value <= 8.0:
            return value
    return 0.0


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    records = load_records(path)
    pools, universal_pool = _nfl_pools(records)
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    repriced = 0
    synchronized = 0

    for record in records:
        if not _is_nfl(record):
            continue

        has_ranked_evidence = _refresh_nfl_percentiles(record, pools, universal_pool)
        valuation_score, fair, explanation = production_fair_value(record)
        if valuation_score is None or fair is None or explanation is None:
            continue

        production_score = _number(explanation.get("productionScore"))
        record["nflProductionScore"] = round(production_score, 2) if production_score is not None else None
        record["nflValuationScore"] = round(valuation_score, 2)
        record["nflProductionPricing"] = explanation
        record["nflProductionPriceModelVersion"] = MODEL_VERSION
        record["fairValue"] = round(fair, 2)
        record["fundamentalValue"] = round(fair, 2)
        record["modelTargetPrice"] = round(fair, 2)
        synchronized += 1

        rookie_influence = _number(explanation.get("rookieInfluence")) or 0.0
        can_reprice = has_ranked_evidence or rookie_influence > 0.0
        if not can_reprice:
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
        cohort = str((record.get("pricingEvidenceSummary") or {}).get("cohort") or "NFL normalized")
        record["nflProductionRebase"] = {
            "oldPrice": round(current, 2),
            "productionLedFairValue": round(fair, 2),
            "valuationScore": round(valuation_score, 2),
            "productionScore": round(production_score, 2) if production_score is not None else None,
            "rookieInfluence": round(rookie_influence, 4),
            "latestGameOverlayPct": round(overlay, 3),
            "rebasedPrice": target,
            "productionCohort": cohort,
            "recentSampleGamesEstimate": (record.get("pricingEvidenceSummary") or {}).get("recentSampleGamesEstimate"),
            "recentSampleWeight": (record.get("pricingEvidenceSummary") or {}).get("recentSampleWeight"),
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
        f"Synchronized {synchronized:,} position-normalized NFL valuation(s); "
        f"rebased {repriced:,} price(s) with early-season stability and no-debut Rookie IPO protection."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
