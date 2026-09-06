#!/usr/bin/env python3
"""Run Golf event refresh with results-proportional, uncapped movement."""
from __future__ import annotations

import math

import golf_event_refresh as base
from results_event_pricing import MODEL_VERSION as RESULTS_MODEL_VERSION


def golf_tournament_move_results(
    *,
    finish: int,
    field_size: int,
    score_to_par: float | None,
    status: str,
    major: bool,
    player_record=None,
    max_move_pct: float = 2.5,
) -> float:
    """Price a verified Golf result without a fixed percentage ceiling.

    The move is driven by finish versus the player's ranking/roster expectation,
    absolute placement, score to par, completion status and major importance. The
    legacy ``max_move_pct`` argument remains only for CLI compatibility.
    """
    del max_move_pct
    field = max(2, int(field_size or 0))
    position = max(1, min(int(finish or field), field))
    own_rank = base.player_rank(player_record)
    expected = min(field, own_rank) if own_rank else max(1, round(field * 0.50))

    # Naturally bounded by field position itself; no arbitrary market-move cap.
    expectation = ((expected - position) / field) * 0.95
    if position == 1:
        placement = 1.05
    elif position <= 3:
        placement = 0.62
    elif position <= 10:
        placement = 0.32
    elif position <= 25:
        placement = 0.14
    elif position <= 50:
        placement = 0.02
    else:
        placement = -0.08

    status_key = str(status or "").upper()
    if status_key in {"DQ", "WD", "W/D", "DNS"}:
        placement -= 0.42
    elif status_key in {"CUT", "MC", "MDF", "CUT_OR_INCOMPLETE"}:
        placement -= 0.28

    score_component = 0.0
    if score_to_par is not None:
        try:
            score = float(score_to_par)
        except (TypeError, ValueError):
            score = float("nan")
        if math.isfinite(score):
            # Exceptional scoring can continue to add/subtract signal instead of
            # flattening at the old ±0.15 component clamp.
            score_component = -score * 0.012

    move = expectation + placement + score_component
    if major:
        move *= 1.55

    if abs(move) < 0.03:
        move = 0.03 if position <= expected else -0.03
    return round(move, 3)


base.golf_tournament_move = golf_tournament_move_results
base.RESULTS_EVENT_PRICING_MODEL = RESULTS_MODEL_VERSION

if __name__ == "__main__":
    raise SystemExit(base.main())
