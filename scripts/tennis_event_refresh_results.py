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


def verified_tennis_record_strength(record):
    """Prefer the verified current roster identity over legacy/prototype duplicates.

    Live match evidence must land on the same canonical record that survives the
    catalog finalizer. Price is deliberately only a late tie-breaker: a stale
    prototype duplicate must never win identity matching merely because its old
    market price is higher.
    """
    source_namespace = str(record.get("sourceNamespace") or "").strip().lower()
    source_type = str(record.get("sourceType") or "").strip().lower()
    verification = str(record.get("verificationStatus") or "").strip().lower()
    record_id = str(record.get("id") or "")

    official_roster = int(
        source_namespace == "curated-individual-sport-roster"
        or source_type == "official-ranking-roster"
    )
    non_prototype = int("prototype" not in verification and "seed" not in verification)
    canonical_id = int(record_id.startswith("athlete-tennis-"))
    current_segment = int(str(record.get("marketSegment") or "").strip().lower() == "current")
    confidence = float(record.get("pricingConfidence") or record.get("dataConfidence") or 0)
    market_price = float(record.get("marketPrice") or 0)
    return (
        official_roster,
        non_prototype,
        canonical_id,
        current_segment,
        confidence,
        market_price,
        record_id,
    )


base.tennis_match_move = tennis_match_move_results
base.record_strength = verified_tennis_record_strength
base.RESULTS_EVENT_PRICING_MODEL = RESULTS_MODEL_VERSION

if __name__ == "__main__":
    raise SystemExit(base.main())
