#!/usr/bin/env python3
"""NFL opportunity protection for TalentX game-event pricing.

This layer is intentionally narrow. It does not reprice established NFL players
whose source-backed per-game expectations are already credible. It only protects
against the pathological case where a reserve player's tiny historical workload
becomes the denominator for a much larger one-game opportunity, making ordinary
injury-replacement production look like a historic breakout.

The layer also preserves the most recent *played* prior season when a player has
a zero-game season between meaningful seasons. That lets a returning player keep
a sensible pre-absence performance prior instead of being treated like a player
with no NFL history.

The migration scans the whole recent event chain, not just the newest game. If a
bad reserve-denominator move happened in an earlier game, later legitimate moves
are replayed from the corrected price so the old inflation cannot remain embedded
in today's market price.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import hourly_price_refresh as refresh
import hourly_price_refresh_nfl as nfl

OPPORTUNITY_MODEL_VERSION = "1.1-nfl-role-adjusted-opportunity-floor-history-replay"

# Conservative replacement/full-game production scores on TalentX's existing
# NFL signal scale. These are denominator floors only; they are not price caps.
# Established players with a higher personal expectation are completely
# unaffected. When actual production is below the role floor, the effective
# expectation is limited to actual production so expanded opportunity by itself
# cannot manufacture either a giant gain or an artificial punishment.
ROLE_REPLACEMENT_BASELINES = {
    "QB": 5.0,
    "RB": 2.0,
    "REC": 2.5,
    "DEF": 8.0,
    "ST": 2.5,
    "OL": 2.0,
}

_original_expected_baseline_stats = nfl.nfl_expected_baseline_stats
_original_game_evidence = nfl._nfl_game_evidence
_installed = False


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return default
    return parsed


def role_group(record: dict[str, Any]) -> str:
    role = str(record.get("role") or "").lower().strip()
    if role == "qb" or "quarterback" in role:
        return "QB"
    if role in {"rb", "fb"} or "running back" in role or "fullback" in role:
        return "RB"
    if role in {"wr", "te"} or "wide receiver" in role or "receiver" in role or "tight end" in role:
        return "REC"
    if any(token in role for token in ("kicker", "punter", "long snapper")):
        return "ST"
    if any(token in role for token in ("offensive line", "offensive tackle", "offensive guard")) or role in {"ot", "og", "c"}:
        return "OL"
    return "DEF"


def replacement_baseline_score(record: dict[str, Any]) -> float:
    return ROLE_REPLACEMENT_BASELINES.get(role_group(record), ROLE_REPLACEMENT_BASELINES["DEF"])


def _played_prior_history(item: dict[str, Any]) -> dict[str, Any]:
    """Drop zero-game prior seasons while retaining the current season.

    A 17-game injury absence should not erase the last source-backed season that
    actually describes the player's established role. This is deliberately based
    on observed games rather than trying to infer the reason for every absence.
    """
    history = item.get("nflSeasonStats") if isinstance(item.get("nflSeasonStats"), dict) else None
    if not history:
        return item

    normalized: dict[int, dict[str, Any]] = {}
    for raw_year, stats in history.items():
        if not isinstance(stats, dict):
            continue
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            continue
        normalized[year] = stats
    if len(normalized) < 2:
        return item

    current_year = max(normalized)
    filtered: dict[int, dict[str, Any]] = {}
    for year, stats in normalized.items():
        games = nfl._game_count(stats)
        if year == current_year or (games is not None and games > 0):
            filtered[year] = stats
    if filtered.keys() == normalized.keys():
        return item

    result = dict(item)
    result["nflSeasonStats"] = filtered
    result["opportunityPriorSkippedZeroGameSeasons"] = sorted(set(normalized) - set(filtered))
    return result


def expected_baseline_stats(record: dict[str, Any], item: dict[str, Any]) -> dict[str, float]:
    """Use the normal NFL expectation model after skipping zero-game gap seasons."""
    return _original_expected_baseline_stats(record, _played_prior_history(item))


def protect_game_evidence(
    record: dict[str, Any],
    item: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Protect tiny reserve baselines without changing normal established players."""
    evidence = dict(_original_game_evidence(record, _played_prior_history(item), event))
    if not evidence.get("comparable"):
        return evidence

    actual = _finite(evidence.get("actualPerformanceScore"), 0.0) or 0.0
    personal_expected = _finite(evidence.get("expectedPerformanceScore"), 0.0) or 0.0
    role_floor = replacement_baseline_score(record)

    # Surgical guard: if the existing personal expectation is already credible
    # for the role, leave every field and every price move untouched.
    if actual <= 0 or personal_expected <= 0 or personal_expected >= role_floor:
        evidence["opportunityFloorApplied"] = False
        evidence["opportunityModelVersion"] = OPPORTUNITY_MODEL_VERSION
        return evidence

    effective_expected = max(personal_expected, min(role_floor, actual))
    if effective_expected <= 0:
        return evidence

    production_delta = (actual / effective_expected - 1.0) * 100.0

    # A tiny historical workload also makes its efficiency comparison unstable.
    # When the floor activates, price the game from production relative to the
    # role-adjusted denominator and keep the old efficiency figure informational.
    evidence.update({
        "personalExpectedPerformanceScore": round(personal_expected, 3),
        "expectedPerformanceScore": round(effective_expected, 3),
        "replacementBaselineScore": round(role_floor, 3),
        "productionDeltaPct": round(production_delta, 2),
        "performanceDeltaPct": round(production_delta, 2),
        "opportunityFloorApplied": True,
        "opportunityModelVersion": OPPORTUNITY_MODEL_VERSION,
        "expectationSource": (
            f"{evidence.get('expectationSource') or 'NFL source-backed per-game history'} "
            "+ TalentX role-adjusted opportunity floor"
        ),
    })
    return evidence


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


def _event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def _predates_nfl_market_epoch(record: dict[str, Any], event: dict[str, Any]) -> bool:
    """Do not let legacy NFL events rewrite prices after the v2 market reset."""
    migrated_at = _parse_time(record.get("nflMarketMigratedAt"))
    event_time = _parse_time(event.get("startedAt"))
    return migrated_at is not None and event_time is not None and event_time <= migrated_at


def _stored_event_move(event: dict[str, Any]) -> float | None:
    move = _finite(event.get("movePct"))
    if move is not None and move > -100.0:
        return move
    before = _finite(event.get("priceBefore"))
    after = _finite(event.get("priceAfter"))
    if before is None or before <= 0 or after is None or after <= 0:
        return None
    return (after / before - 1.0) * 100.0


def _event_needs_opportunity_repair(record: dict[str, Any], event: dict[str, Any], *, now: datetime) -> bool:
    if str(record.get("leagueOrMedium") or "") != "NFL":
        return False
    if str(event.get("eventType") or "").lower() != "game":
        return False
    if _predates_nfl_market_epoch(record, event):
        return False
    league = str(event.get("league") or record.get("leagueOrMedium") or "").lower()
    if league not in {"nfl", ""}:
        return False
    if not isinstance(event.get("stats"), dict) or not event.get("stats"):
        return False
    event_time = _parse_time(event.get("startedAt"))
    if event_time is None or event_time < now - timedelta(days=nfl.NFL_MIGRATION_LOOKBACK_DAYS):
        return False

    expected = _finite(event.get("expectedPerformanceScore"))
    if expected is not None and expected > 0:
        return expected < replacement_baseline_score(record)

    # Compacted legacy events may lack the detailed denominator. Use the event's
    # own recorded delta first; only fall back to the record-level latest delta.
    delta = abs(_finite(event.get("performanceDeltaPct"), 0.0) or 0.0)
    if delta <= 0 and _event_key(event) == str(record.get("lastPriceEventId") or ""):
        delta = abs(_finite(record.get("lastGamePerformanceDeltaPct"), 0.0) or 0.0)
    games = max(0.0, _finite(record.get("professionalGames"), 0.0) or 0.0)
    return delta >= 400.0 and games < 80.0


def _needs_opportunity_repair(record: dict[str, Any], *, now: datetime) -> bool:
    """Compatibility helper: is the latest priced NFL game itself repairable?"""
    if str(record.get("leagueOrMedium") or "") != "NFL":
        return False
    selected = nfl._latest_nfl_game_event(record)
    if selected is None:
        return False
    _, event = selected
    event_key = _event_key(event)
    if not event_key or str(record.get("lastPriceEventId") or "") != event_key:
        return False
    return _event_needs_opportunity_repair(record, event, now=now)


def _event_aliases(event: dict[str, Any]) -> set[str]:
    return {
        str(value).strip()
        for value in (event.get("eventKey"), event.get("eventId"))
        if str(value or "").strip()
    }


def migrate_latest_opportunity_events(
    catalog_path: Path = Path("data/current_catalog.json"),
    *,
    timeout: float = 10.0,
    now: datetime | None = None,
) -> int:
    """Repair recent NFL reserve-denominator spikes anywhere in the event chain.

    A prior version only repaired the latest priced game. Once a newer game was
    appended, an older bad jump remained embedded in all later prices. This
    migration recomputes only the suspect game move(s), then replays subsequent
    stored moves from the corrected base. Legitimate later performance, signing,
    and team-change percentages are preserved.
    """
    if not catalog_path.exists():
        return 0
    current = now or datetime.now(timezone.utc)
    records = nfl.load_records(catalog_path)
    repaired_events = 0
    repaired_records = 0

    for record in records:
        if str(record.get("leagueOrMedium") or "") != "NFL":
            continue
        events = [dict(value) if isinstance(value, dict) else value for value in record.get("priceEvents", [])]
        chronological = sorted(
            (
                (index, event)
                for index, event in enumerate(events)
                if isinstance(event, dict) and _parse_time(event.get("startedAt")) is not None
            ),
            key=lambda pair: _parse_time(pair[1].get("startedAt")) or datetime.min.replace(tzinfo=timezone.utc),
        )
        flagged = [
            (index, event)
            for index, event in chronological
            if _event_needs_opportunity_repair(record, event, now=current)
        ]
        if not flagged:
            continue

        evidence_item = refresh.fetch_hourly_evidence(record, timeout)
        if not isinstance(evidence_item, dict) or not evidence_item.get("ok"):
            continue

        replacement_moves: dict[int, tuple[float, dict[str, Any]]] = {}
        for index, event in flagged:
            new_move, evidence = nfl.nfl_results_based_game_event_move(record, evidence_item, event, None)
            if not evidence.get("comparable") or new_move <= -100.0:
                continue
            replacement_moves[index] = (float(new_move), dict(evidence))
        if not replacement_moves:
            continue

        first_index = min(
            replacement_moves,
            key=lambda idx: _parse_time(events[idx].get("startedAt")) or datetime.max.replace(tzinfo=timezone.utc),
        )
        first_event = events[first_index]
        rolling_price = _finite(first_event.get("priceBefore"))
        if rolling_price is None or rolling_price <= 0:
            continue
        first_time = _parse_time(first_event.get("startedAt"))
        if first_time is None:
            continue

        repaired_price_map: dict[str, tuple[float, float]] = {}
        latest_priced_event: dict[str, Any] | None = None
        latest_move: float | None = None
        changed_this_record = 0

        for index, event in chronological:
            event_time = _parse_time(event.get("startedAt"))
            if event_time is None or event_time < first_time:
                continue
            move = _stored_event_move(event)
            if index in replacement_moves:
                move, evidence = replacement_moves[index]
                event.update(evidence)
                event["modelMovePct"] = round(move, 3)
                event["opportunityModelVersion"] = OPPORTUNITY_MODEL_VERSION
                event["nflExpectationModelVersion"] = nfl.NFL_EXPECTATION_MODEL_VERSION
                changed_this_record += 1
            if move is None or move <= -100.0:
                continue

            before = round(float(rolling_price), 2)
            after = max(0.01, round(before * (1.0 + float(move) / 100.0), 2))
            actual_move = round((after / before - 1.0) * 100.0, 3)
            event["priceBefore"] = before
            event["priceAfter"] = after
            event["movePct"] = actual_move
            events[index] = event
            for alias in _event_aliases(event):
                repaired_price_map[alias] = (before, after)
            rolling_price = after
            latest_priced_event = event
            latest_move = actual_move

        if not changed_this_record or latest_priced_event is None:
            continue

        record["priceEvents"] = events
        latest_before = _finite(latest_priced_event.get("priceBefore"))
        latest_after = _finite(latest_priced_event.get("priceAfter"))
        if latest_before is not None and latest_after is not None:
            record["previousMarketPrice"] = round(latest_before, 2)
            record["marketPrice"] = round(latest_after, 2)
            record["dailyChange"] = round(latest_move or 0.0, 3)
            record["hourlyChangePct"] = round(latest_move or 0.0, 3)
            if str(latest_priced_event.get("eventType") or "").lower() == "game":
                record["lastGameMovePct"] = round(latest_move or 0.0, 3)
                if latest_priced_event.get("performanceDeltaPct") is not None:
                    record["lastGamePerformanceDeltaPct"] = latest_priced_event.get("performanceDeltaPct")
                if isinstance(latest_priced_event.get("stats"), dict):
                    record["lastGameStats"] = latest_priced_event.get("stats", {})

        record["nflExpectationModelVersion"] = nfl.NFL_EXPECTATION_MODEL_VERSION
        record["opportunityModelVersion"] = OPPORTUNITY_MODEL_VERSION
        record["opportunityHistoricalRepairAt"] = current.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        record["opportunityHistoricalRepairCount"] = changed_this_record

        trend = [float(value) for value in record.get("trend", []) if isinstance(value, (int, float))]
        if trend and latest_after is not None:
            trend[-1] = round(latest_after, 2)
            record["trend"] = [round(value, 2) for value in trend]

        history = [dict(value) for value in record.get("priceHistory", []) if isinstance(value, dict)]
        if history and repaired_price_map:
            for point in history:
                alias = str(point.get("eventId") or point.get("eventKey") or "").strip()
                prices = repaired_price_map.get(alias)
                if prices is None:
                    continue
                phase = str(point.get("phase") or "").lower()
                if phase == "open":
                    point["price"] = prices[0]
                elif phase == "close":
                    point["price"] = prices[1]
            record["priceHistory"] = history

        repaired_events += changed_this_record
        repaired_records += 1

    if repaired_records:
        nfl.write_records(catalog_path, records)
        print(
            f"Repaired {repaired_events:,} NFL opportunity-inflated event(s) across "
            f"{repaired_records:,} listing(s) and replayed later moves from corrected bases."
        )
    return repaired_events


def install_opportunity_protection() -> None:
    """Install the narrow NFL opportunity wrappers exactly once."""
    global _installed
    if _installed:
        return
    nfl.nfl_expected_baseline_stats = expected_baseline_stats
    nfl._nfl_game_evidence = protect_game_evidence
    nfl.migrate_latest_nfl_expectations = migrate_latest_opportunity_events
    _installed = True
