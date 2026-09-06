#!/usr/bin/env python3
"""Run Tennis event refresh with results-proportional, uncapped movement."""
from __future__ import annotations

import math

import tennis_event_refresh as base
from results_event_pricing import MODEL_VERSION as RESULTS_MODEL_VERSION


def tennis_match_move_results(
    *,
    winner: bool,
    round_name: str,
    major: bool,
    sets_for: int,
    sets_against: int,
    player_record=None,
    opponent_record=None,
    max_move_pct: float = 2.5,
) -> float:
    """Price a verified Tennis result without a fixed percentage ceiling.

    Match outcome is the base signal. Round importance, major status, straight-set
    dominance and ranking surprise determine how unusual the result is. The
    legacy ``max_move_pct`` argument is accepted for CLI compatibility only.
    """
    del max_move_pct
    importance = base.round_importance(round_name)
    if winner:
        move = 0.06 + importance * 1.65
    else:
        # Reaching a late round is already reflected in the preceding wins, so a
        # late-round loss is negative but not the mirror image of a title win.
        move = -(0.07 + importance * 0.22)

    if major:
        move *= 1.75 if winner else 1.30

    if sets_for or sets_against:
        if winner and sets_against == 0:
            move += 0.04
        elif not winner and sets_for == 0:
            move -= 0.03

    own_rank = base.player_rank(player_record)
    opponent_rank = base.player_rank(opponent_record)
    if own_rank and opponent_rank:
        if winner and own_rank > opponent_rank:
            # Ranking surprise grows continuously rather than flattening at a
            # fixed upset bonus.
            move += 0.22 * math.log1p((own_rank - opponent_rank) / 20.0)
        elif not winner and own_rank < opponent_rank:
            move -= 0.18 * math.log1p((opponent_rank - own_rank) / 20.0)
        elif winner and own_rank < opponent_rank:
            # Expected favorite wins should still move, just slightly less.
            move -= 0.02 * math.log1p((opponent_rank - own_rank) / 100.0)

    if abs(move) < 0.03:
        move = 0.03 if winner else -0.03
    return round(move, 3)


base.tennis_match_move = tennis_match_move_results
base.RESULTS_EVENT_PRICING_MODEL = RESULTS_MODEL_VERSION

if __name__ == "__main__":
    raise SystemExit(base.main())
