#!/usr/bin/env python3
"""Shared breakout calibration for verified non-athlete TalentX outcomes.

This policy intentionally leaves weak/proxy evidence conservative while allowing
verified direct outcomes to scale materially when the result is exceptional.
There is no hard movement ceiling.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

MODEL_VERSION = "2.0-market-wide-breakout-calibration"


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def music_chart_target(rank: int) -> tuple[str, float]:
    """Price chart outcomes with much stronger separation at the elite end."""
    rank = max(1, int(rank))
    if rank == 1:
        return "top-1", 3.50
    if rank <= 5:
        return "top-5", 2.20
    if rank <= 10:
        return "top-10", 1.35
    if rank <= 40:
        return "top-40", 0.55
    if rank <= 100:
        return "top-100", 0.18
    return "charted", 0.05


def music_movement_target(current_rank: int, last_week_rank: int | None, status: str = "ranked") -> float:
    """Uncapped Billboard movement using the calibrated chart tiers."""
    _, current_tier = music_chart_target(current_rank)
    if last_week_rank is None:
        if status not in {"new", "re-entry"}:
            return 0.0
        novelty = 0.08 * math.log2(1 + max(1, 101 - min(current_rank, 100)))
        return round(current_tier + novelty, 3)

    if current_rank == last_week_rank:
        return 0.0

    _, prior_tier = music_chart_target(last_week_rank)
    delta = last_week_rank - current_rank
    direction = 1.0 if delta > 0 else -1.0
    magnitude = abs(current_tier - prior_tier) + 0.10 * math.log2(1 + abs(delta))
    for threshold in (40, 10, 5, 1):
        crossed_up = current_rank <= threshold < last_week_rank
        crossed_down = last_week_rank <= threshold < current_rank
        if crossed_up or crossed_down:
            magnitude += 0.08 if threshold == 40 else 0.12 if threshold == 10 else 0.15 if threshold == 5 else 0.20
    return round(direction * magnitude, 3)


def actor_box_office_target(ratio: float, age_days: float) -> tuple[str, float] | None:
    """Gross/cost outcome curve with stronger breakout and failure separation."""
    ratio = max(0.0, number(ratio))
    if ratio >= 1.0:
        label = "breakout" if ratio >= 3.0 else "strong" if ratio >= 2.0 else "cost-recovered"
        return label, 0.35 + 0.95 * math.log2(max(1.0, ratio))
    if age_days < 14:
        return None
    shortfall = 1.0 - ratio
    age_weight = 0.75 + 0.25 * math.log1p(max(0.0, age_days - 14.0) / 7.0)
    move = -(0.40 + 1.25 * shortfall) * age_weight
    label = "severe-underperform" if ratio < 0.50 and age_days >= 21 else "underperform"
    return label, move


def actor_direct_box_office_target(row: dict[str, Any], rows: list[dict[str, Any]]) -> float:
    """Direct theatrical result versus the current box-office field."""
    ranked = [item for item in rows if int(item.get("rank") or 999) <= 20 and number(item.get("gross"), 0) > 0]
    reference = median([number(item.get("gross"), 0) for item in ranked]) if ranked else number(row.get("gross"), 1)
    rank = max(1, int(number(row.get("rank"), 20)))
    rank_component = 1.10 * math.log2(21.0 / min(21.0, rank + 1.0)) / math.log2(21.0 / 2.0)
    gross_ratio = max(0.01, number(row.get("gross"), 0.01) / max(1.0, reference))
    gross_component = 0.70 * math.log2(gross_ratio)
    change = row.get("weeklyChangePct")
    hold_component = 0.0 if change is None else 0.010 * (number(change) + 45.0)
    return rank_component + gross_component + hold_component


def actor_netflix_target(
    row: dict[str, Any],
    current_rows: list[dict[str, Any]],
    previous_row: dict[str, Any] | None,
) -> float:
    """Direct streaming result versus current field, momentum and persistence."""
    category_rows = [item for item in current_rows if str(item.get("category")) == str(row.get("category"))]
    view_values = [number(item.get("views"), 0) for item in category_rows if number(item.get("views"), 0) > 0]
    reference = median(view_values) if view_values else number(row.get("views"), 0)
    rank = max(1, int(number(row.get("rank"), 10)))
    rank_component = 1.00 * math.log2(11.0 / min(11.0, rank + 1.0)) / math.log2(11.0 / 2.0)
    views = number(row.get("views"), 0)
    scale_component = 0.0
    if views > 0 and reference > 0:
        scale_component = 0.75 * math.log2(max(0.01, views / reference))
    momentum_component = 0.0
    if previous_row and views > 0 and number(previous_row.get("views"), 0) > 0:
        momentum_component = 0.65 * math.log2(max(0.01, views / number(previous_row.get("views"), 1)))
    persistence = 0.08 * math.log1p(max(0, int(number(row.get("weeksInTop10"), 1)) - 1))
    return rank_component + scale_component + momentum_component + persistence


def creator_performance_target(ratio: float) -> tuple[str, float] | None:
    """Direct YouTube growth surprise; ordinary wins stay small, virality scales."""
    ratio = number(ratio)
    if ratio >= 1.60:
        bucket = "breakout" if ratio >= 5.0 else "hot" if ratio >= 2.5 else "warm"
        return bucket, 1.10 * math.log2(ratio)
    if 0 < ratio <= 0.50:
        bucket = "cold" if ratio <= 0.25 else "cool"
        return bucket, -0.75 * math.log2(1.0 / ratio)
    return None


def amplify_legacy_direct_actor_target(target: float) -> float:
    """Approximate v2 strength for direct Actor events lacking full field context."""
    value = number(target)
    if value == 0:
        return 0.0
    magnitude = abs(value)
    factor = 1.12 + 0.24 * math.log1p(magnitude * 4.0)
    return math.copysign(magnitude * factor, value)
