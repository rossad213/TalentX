#!/usr/bin/env python3
"""Shared results-proportional event movement helpers for TalentX.

The model deliberately has no hard percentage ceiling. Ordinary results should
produce ordinary price moves, while increasingly exceptional results are allowed
to produce increasingly large moves. A logarithmic surprise curve prevents
routine positive/negative variance from looking dramatic without flattening truly
extreme verified outcomes at an arbitrary cap.
"""
from __future__ import annotations

import math
from typing import Any

MODEL_VERSION = "2.0-results-proportional-uncapped"


def finite_number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def result_move_from_delta(
    delta_pct: Any,
    *,
    scale: float = 0.80,
    reference_pct: float = 20.0,
    exponent: float = 1.50,
    dead_zone_pct: float = 2.0,
) -> float:
    """Convert performance-vs-expectation into an uncapped percentage move.

    The curve is intentionally sublinear around normal variance and remains
    mathematically unbounded. Typical examples with the default settings:
      +10% vs expectation -> about +0.16%
      +25%                -> about +0.54%
      +50%                -> about +1.08%
      +100%               -> about +1.89%
      +200%               -> about +2.95%
      +500%               -> about +4.70%

    Negative deltas use the same shape. The small dead zone prevents noise around
    expectation from creating meaningless micro-moves.
    """
    delta = finite_number(delta_pct)
    magnitude = max(0.0, abs(delta) - max(0.0, finite_number(dead_zone_pct)))
    if magnitude <= 0:
        return 0.0
    reference = max(0.001, finite_number(reference_pct, 20.0))
    power = max(0.25, finite_number(exponent, 1.50))
    sensitivity = max(0.0, finite_number(scale, 0.80))
    move = sensitivity * math.log1p(magnitude / reference) ** power
    return math.copysign(move, delta)


def result_sensitivity(record: dict[str, Any]) -> tuple[str, float]:
    """Return a career-stage sensitivity multiplier without imposing a cap."""
    games = max(0.0, finite_number(record.get("professionalGames")))
    stage = str(record.get("careerStage") or "").lower()
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    consistency = finite_number(metrics.get("consistency"), 70.0)

    if "rookie" in stage or games < 20:
        return "High", 1.15
    if "emerging" in stage or games < 80:
        return "Medium-high", 1.05
    if consistency >= 85 and games >= 200:
        return "Low", 0.78
    if consistency >= 75 and games >= 100:
        return "Medium-low", 0.88
    return "Medium", 1.00


def soft_anchor_move(gap_pct: Any, *, scale: float = 0.16, reference_pct: float = 10.0) -> float:
    """Small uncapped fair-value context adjustment.

    This is not an event ceiling. It is a diminishing context term for systems
    that choose to use a fair-value anchor. Large gaps remain able to contribute
    more, but verified event results remain the dominant movement signal.
    """
    gap = finite_number(gap_pct)
    if abs(gap) < 0.25:
        return 0.0
    reference = max(0.001, finite_number(reference_pct, 10.0))
    magnitude = max(0.0, finite_number(scale, 0.16)) * math.log1p(abs(gap) / reference)
    return math.copysign(magnitude, gap)


def valid_move(value: Any) -> float | None:
    """Return a finite price move when it can produce a positive price.

    This is a mathematical validity check, not a volatility cap. Moves above
    -100% have no upper ceiling; a move of -100% or below cannot produce a
    positive TalentX price and is therefore rejected as invalid input.
    """
    move = finite_number(value, float("nan"))
    if not math.isfinite(move) or move <= -100.0:
        return None
    return move
