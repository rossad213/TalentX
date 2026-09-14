#!/usr/bin/env python3
"""NFL expectation/calibration layer for the TalentX hourly Sports refresh.

This is intentionally a narrow extension of the existing Sports engine. It keeps
verified event discovery, durable price history, result-proportional movement and
uncapped pricing intact while fixing one NFL-specific problem: season/career
production must be converted to a true one-game expectation before a completed
box score is graded.

Early in an NFL season, career per-game production is used as the stable baseline
when available so Week 1/2/3 results are not compared mostly with themselves.
As the current season becomes established, the baseline blends toward current-
season per-game production. The layer also gives verified NFL regular-season and
postseason surprises a slightly stronger sensitivity so clearly exceptional games
can create meaningful low-single-digit market moves without a hard ceiling.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import hourly_price_refresh as refresh
from category_market_store import load_records, write_records
from results_event_pricing import MODEL_VERSION as RESULTS_MODEL_VERSION
from results_event_pricing import result_move_from_delta, result_sensitivity

NFL_EXPECTATION_MODEL_VERSION = "1.0-nfl-true-per-game-expectation"
NFL_RESULT_SCALE = 1.45
NFL_MIGRATION_LOOKBACK_DAYS = 14

_original_expected_game_signal = refresh.expected_game_signal
_original_reliable_results_move = None

_GAME_COUNT_KEYS = {"gamesplayed", "games", "appearances", "gp"}
_RATE_HINTS = (
    "avg",
    "average",
    "pct",
    "percentage",
    "rate",
    "rating",
    "qbr",
    "perattempt",
    "percarry",
    "perreception",
    "pergame",
    "yardsper",
    "longest",
    "long",
)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _game_count(stats: dict[str, Any], fallback: Any = None) -> float | None:
    games = refresh.stat_value(stats, "gamesPlayed", "games", "appearances", "gp")
    if games is None:
        games = _finite(fallback)
    return games if games is not None and games > 0 else None


def _is_rate_stat(key: str) -> bool:
    normalized = refresh.norm_key(key)
    return any(hint in normalized for hint in _RATE_HINTS)


def nfl_per_game_stats(stats: dict[str, Any], games: float) -> dict[str, float]:
    """Convert an NFL cumulative stat map into one-game values.

    Rate/percentage/rating fields stay unchanged. Counting statistics are divided
    by games. Keys are normalized to the same representation used by the existing
    Sports signal engine. An unrelated AVG field can therefore never cause all
    season totals to be mistaken for one-game values.
    """
    output: dict[str, float] = {}
    if games <= 0:
        return output
    for key, raw in stats.items():
        value = refresh.numeric_box_value(raw)
        if value is None:
            continue
        normalized = refresh.norm_key(key)
        if not normalized or normalized in _GAME_COUNT_KEYS:
            continue
        output[normalized] = value if _is_rate_stat(key) else value / games
    return output


def nfl_per_game_production(record: dict[str, Any], stats: dict[str, Any], games: float | None) -> float | None:
    if not isinstance(stats, dict) or not stats or games is None or games <= 0:
        return None
    per_game = nfl_per_game_stats(stats, games)
    if not per_game:
        return None
    signals = refresh.signal_bundle(record, per_game, {}, 0)
    score = _finite(signals.get("recentProduction"))
    return score if score is not None and score > 0 else None


def nfl_aware_expected_game_signal(record: dict[str, Any], item: dict[str, Any]) -> float:
    """Return a true one-game NFL production expectation.

    Weeks 1-3 prefer career per-game production when available. Weeks 4-7 blend
    current season and career 50/50; from Week 8 onward current-season form gets
    70% weight. Rookies/players without usable career evidence fall back to their
    available current-season per-game evidence and then to the legacy behavior.
    """
    if str(record.get("leagueOrMedium") or "") != "NFL":
        return _original_expected_game_signal(record, item)

    recent = item.get("recent") if isinstance(item.get("recent"), dict) else {}
    career = item.get("career") if isinstance(item.get("career"), dict) else {}
    recent_games = _game_count(recent)
    career_games = _game_count(career, record.get("professionalGames"))
    recent_score = nfl_per_game_production(record, recent, recent_games)
    career_score = nfl_per_game_production(record, career, career_games)

    if career_score is not None:
        if recent_score is not None and recent_games is not None and recent_games >= 8:
            return recent_score * 0.70 + career_score * 0.30
        if recent_score is not None and recent_games is not None and recent_games >= 4:
            return recent_score * 0.50 + career_score * 0.50
        return career_score
    if recent_score is not None:
        return recent_score

    # Last-resort compatibility path for unusual ESPN payloads with no usable
    # game counts. The base function remains authoritative outside the NFL.
    return _original_expected_game_signal(record, item)


def _nfl_game_evidence(record: dict[str, Any], item: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Calculate the same performance evidence as the base engine using the corrected NFL denominator."""
    stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
    normalized_stats: dict[str, float] = {}
    for key, value in stats.items():
        parsed = refresh.numeric_box_value(value)
        if parsed is not None:
            normalized_stats[refresh.norm_key(key)] = parsed

    actual_signals = refresh.signal_bundle(record, normalized_stats, {}, 0)
    actual_production = float(actual_signals.get("recentProduction") or 0)
    expected_production = nfl_aware_expected_game_signal(record, item)
    expected_signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
    actual_efficiency = float(actual_signals.get("efficiency") or 0)
    expected_efficiency = float(expected_signals.get("efficiency") or 0)

    if expected_production <= 0:
        return {
            "comparable": False,
            "reason": "No one-game NFL baseline was available for this box score",
            "actualPerformanceScore": round(actual_production, 3),
            "expectedPerformanceScore": 0.0,
        }

    production_delta = (actual_production / expected_production - 1.0) * 100.0
    efficiency_delta: float | None = None
    if abs(actual_efficiency) > 0.01 and abs(expected_efficiency) > 0.01:
        efficiency_delta = (actual_efficiency / expected_efficiency - 1.0) * 100.0
    performance_delta = (
        production_delta
        if efficiency_delta is None
        else production_delta * 0.80 + efficiency_delta * 0.20
    )
    return {
        "comparable": True,
        "actualPerformanceScore": round(actual_production, 3),
        "expectedPerformanceScore": round(expected_production, 3),
        "performanceDeltaPct": round(performance_delta, 2),
        "productionDeltaPct": round(production_delta, 2),
        "efficiencyDeltaPct": round(efficiency_delta, 2) if efficiency_delta is not None else None,
    }


def nfl_results_based_game_event_move(record, item, event, legacy_max_game_move_pct):
    """Keep shared Sports behavior, with corrected NFL expectations and sensitivity."""
    if str(record.get("leagueOrMedium") or "") != "NFL":
        if _original_reliable_results_move is None:
            return refresh.game_event_move(record, item, event, legacy_max_game_move_pct)
        return _original_reliable_results_move(record, item, event, legacy_max_game_move_pct)

    evidence = _nfl_game_evidence(record, item, event)
    started = _parse_time(event.get("startedAt"))
    if not evidence.get("comparable"):
        # When installed in production, preserve the existing conservative rookie
        # preseason fallback for players who truly have no statistical baseline.
        if started is not None and started.month in {7, 8} and _original_reliable_results_move is not None:
            return _original_reliable_results_move(record, item, event, legacy_max_game_move_pct)
        return 0.0, evidence

    delta = float(evidence.get("performanceDeltaPct") or 0.0)
    tier, sensitivity = result_sensitivity(record)
    scale = 0.80 if started is not None and started.month in {7, 8} else NFL_RESULT_SCALE
    performance_move = result_move_from_delta(delta, scale=scale) * sensitivity
    outcome_move = 0.06 if event.get("teamWon") is True else -0.05 if event.get("teamWon") is False else 0.0
    tuned_move = performance_move + outcome_move
    return round(tuned_move, 3), {
        **evidence,
        "performanceMovePct": round(performance_move, 3),
        "outcomeMovePct": outcome_move,
        "volatilityTier": tier,
        "resultSensitivity": round(sensitivity, 3),
        "pricingBasis": f"{RESULTS_MODEL_VERSION}-{NFL_EXPECTATION_MODEL_VERSION}",
        "nflExpectationModelVersion": NFL_EXPECTATION_MODEL_VERSION,
        "hardMoveCapPct": None,
    }


def _latest_nfl_game_event(record: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        if str(event.get("eventType") or "").lower() != "game":
            continue
        league = str(event.get("league") or record.get("leagueOrMedium") or "").lower()
        if league not in {"nfl", ""}:
            continue
        if not isinstance(event.get("stats"), dict) or not event.get("stats"):
            continue
        candidates.append((index, event))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: str(pair[1].get("startedAt") or ""))


def migrate_latest_nfl_expectations(
    catalog_path: Path = Path("data/current_catalog.json"),
    *,
    timeout: float = 10.0,
    now: datetime | None = None,
) -> int:
    """Reprice the latest recent NFL game once under the corrected expectation.

    Only a record whose latest price event is that NFL game is migrated. This
    avoids rewriting a chain that has a newer signing/trade/result after it. The
    event key is preserved, chart open/close points are updated in place, and a
    model-version stamp makes the migration idempotent.
    """
    if not catalog_path.exists():
        return 0
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=NFL_MIGRATION_LOOKBACK_DAYS)
    records = load_records(catalog_path)
    changed = 0

    for record in records:
        if str(record.get("leagueOrMedium") or "") != "NFL":
            continue
        if str(record.get("nflExpectationModelVersion") or "") == NFL_EXPECTATION_MODEL_VERSION:
            continue
        selected = _latest_nfl_game_event(record)
        if selected is None:
            continue
        index, old_event = selected
        event_time = _parse_time(old_event.get("startedAt"))
        if event_time is None or event_time < cutoff:
            continue
        event_key = str(old_event.get("eventKey") or old_event.get("eventId") or "").strip()
        if not event_key or str(record.get("lastPriceEventId") or "") != event_key:
            continue
        before = _finite(old_event.get("priceBefore"))
        old_after = _finite(old_event.get("priceAfter"))
        if before is None or before <= 0 or old_after is None or old_after <= 0:
            continue

        evidence_item = refresh.fetch_hourly_evidence(record, timeout)
        if not isinstance(evidence_item, dict) or not evidence_item.get("ok"):
            continue
        new_move, evidence = nfl_results_based_game_event_move(record, evidence_item, old_event, None)
        if not evidence.get("comparable") or new_move <= -100.0:
            continue

        new_after = max(0.01, round(before * (1.0 + new_move / 100.0), 2))
        actual_move = round((new_after / before - 1.0) * 100.0, 3)
        updated_event = {
            **old_event,
            **evidence,
            "movePct": actual_move,
            "modelMovePct": round(new_move, 3),
            "priceBefore": round(before, 2),
            "priceAfter": new_after,
            "nflExpectationModelVersion": NFL_EXPECTATION_MODEL_VERSION,
        }
        events = [dict(value) if isinstance(value, dict) else value for value in record.get("priceEvents", [])]
        events[index] = updated_event
        record["priceEvents"] = events
        record["previousMarketPrice"] = round(before, 2)
        record["marketPrice"] = new_after
        record["lastGameMovePct"] = actual_move
        record["lastGamePerformanceDeltaPct"] = evidence.get("performanceDeltaPct")
        record["lastGameStats"] = updated_event.get("stats", {})
        record["dailyChange"] = actual_move
        record["hourlyChangePct"] = actual_move
        record["nflExpectationModelVersion"] = NFL_EXPECTATION_MODEL_VERSION

        trend = [float(value) for value in record.get("trend", []) if isinstance(value, (int, float))]
        if trend:
            trend[-1] = new_after
            record["trend"] = [round(value, 2) for value in trend]

        history = [dict(value) for value in record.get("priceHistory", []) if isinstance(value, dict)]
        for point in history:
            if str(point.get("eventId") or "") != event_key:
                continue
            phase = str(point.get("phase") or "").lower()
            if phase == "open":
                point["price"] = round(before, 2)
            elif phase == "close":
                point["price"] = new_after
        if history:
            record["priceHistory"] = history
        changed += 1

    if changed:
        write_records(catalog_path, records)
        print(f"Repriced {changed:,} recent NFL latest-game event(s) with true per-game expectations.")
    return changed


def install_nfl_layer():
    """Install the NFL-only hooks after loading the existing reliability wrapper."""
    global _original_reliable_results_move
    refresh.expected_game_signal = nfl_aware_expected_game_signal
    import hourly_price_refresh_reliable as reliable

    _original_reliable_results_move = reliable.results_based_game_event_move
    reliable.results_based_game_event_move = nfl_results_based_game_event_move
    refresh.game_event_move = nfl_results_based_game_event_move
    return reliable


if __name__ == "__main__":
    reliability = install_nfl_layer()
    reliability.normalize_sports_tickers()
    reliability.repair_sports_price_integrity()
    reliability.seed_rookie_ipo_history()
    migrate_latest_nfl_expectations()
    raise SystemExit(refresh.main())
