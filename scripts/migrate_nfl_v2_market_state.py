#!/usr/bin/env python3
"""One-time NFL market-state migration onto the shared v2/NBA-style price base.

This migration is intentionally NFL-only. It does not alter NBA, WNBA, NHL, MLB,
Soccer, Tennis, Golf, Music, Actor, or Creator records.

Why this exists:
The Sports workflow recalculates v2 fair value and then restores durable event
market state. That is normally correct, but it also preserved legacy NFL market
prices created before the NFL adopted the NBA-style architecture. This migration
moves each NFL listing onto its clean v2 fair value exactly once, keeps the old
price/game history for audit, and lets future verified NFL games compound from
that new market base.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pricing_engine_v2 import apply_v2
from nfl_metric_calibration import calibrate_record as calibrate_nfl_record
from hourly_price_refresh_nfl import (
    NFL_EXPECTATION_MODEL_VERSION,
    NFL_FUNDAMENTAL_EVIDENCE_VERSION,
    nfl_absolute_performance_authority,
)

MIGRATION_VERSION = "1.6-nfl-significance-aware-preserved-game-reset"
MIGRATION_EVENT_ID = "model:nfl-v2-market-state-reset-v1-6"

_V2_FIELDS = (
    "talentScore",
    "marketScore",
    "confidenceScore",
    "situationScore",
    "expectedValueScore",
    "fairValue",
    "fundamentalValue",
    "pricingModelVersion",
    "pricingEngine",
    "pricingV2",
    "rookiePricing",
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


def _latest_preservable_game(record: dict[str, Any], stamp: str) -> tuple[dict[str, Any], float] | None:
    key = str(record.get("lastPriceEventId") or "").strip()
    move = _finite(record.get("lastGameMovePct"))
    if not key or move is None or move <= -100.0:
        return None
    candidates = [
        item for item in (record.get("priceEvents") or [])
        if isinstance(item, dict)
        and str(item.get("eventKey") or item.get("eventId") or "") == key
        and str(item.get("eventType") or "").lower() == "game"
        and item.get("verified") is not False
    ]
    if not candidates:
        return None
    event = max(candidates, key=lambda item: str(item.get("startedAt") or ""))
    event_time = _parse_time(event.get("startedAt") or record.get("lastPriceEventAt"))
    migrated_at = _parse_time(record.get("nflMarketMigratedAt"))
    migration_time = _parse_time(stamp)
    if event_time is None or migration_time is None:
        return None
    if event_time > migration_time or (migration_time - event_time).days > 7:
        return None
    prior_version = str(record.get("nflMarketMigrationVersion") or "")
    prior_preserved_key = str(record.get("nflMarketMigrationPreservedEventId") or "")
    explicitly_preserved_by_v1_5 = (
        prior_version == "1.5-nfl-career-tier-and-exactly-once-reset"
        and prior_preserved_key
        and prior_preserved_key == key
    )
    if migrated_at is not None and event_time <= migrated_at and not explicitly_preserved_by_v1_5:
        return None
    return event, move


def _recalibrate_preserved_game_move(
    record: dict[str, Any],
    event: dict[str, Any],
    fallback_move: float,
) -> tuple[dict[str, Any], float]:
    """Apply the current absolute-performance authority to one preserved game.

    The career-tier migration may preserve a recent verified game that was
    originally priced under the older pure-relative-surprise NFL model. Rebase
    that event onto the current significance-aware logic when the durable event
    already contains enough evidence to do so. If the required fields are absent,
    preserve the audited move rather than inventing evidence.
    """
    result = dict(event)
    if str(result.get("nflExpectationModelVersion") or "") == NFL_EXPECTATION_MODEL_VERSION:
        return result, fallback_move

    actual = _finite(result.get("actualPerformanceScore"))
    expected = _finite(result.get("expectedPerformanceScore"))
    old_performance_move = _finite(result.get("performanceMovePct"))
    outcome_move = _finite(result.get("outcomeMovePct"))
    if actual is None or expected is None or old_performance_move is None:
        return result, fallback_move

    authority, target = nfl_absolute_performance_authority(record, actual, expected)
    new_performance_move = old_performance_move * authority
    new_move = new_performance_move + (outcome_move or 0.0)

    result["rawPerformanceMovePct"] = round(old_performance_move, 3)
    result["absolutePerformanceAuthority"] = authority
    result["absolutePerformanceTargetScore"] = target
    result["performanceMovePct"] = round(new_performance_move, 3)
    result["movePct"] = round(new_move, 3)
    result["nflExpectationModelVersion"] = NFL_EXPECTATION_MODEL_VERSION
    result["preservedGameMoveRecalibrated"] = MIGRATION_VERSION
    return result, round(new_move, 3)


def _is_nfl(record: dict[str, Any]) -> bool:
    return str(record.get("leagueOrMedium") or "").strip().upper() == "NFL"


def _migration_point(history: list[dict[str, Any]], price: float, stamp: str) -> list[dict[str, Any]]:
    if any(str(item.get("eventId") or "") == MIGRATION_EVENT_ID for item in history if isinstance(item, dict)):
        return history
    return [
        *history,
        {
            "time": stamp,
            "eventId": MIGRATION_EVENT_ID,
            "eventType": "model_migration",
            "phase": "close",
            "name": "NFL v2 market-state migration",
            "price": round(price, 2),
        },
    ]


def migrate_record(record: dict[str, Any], stamp: str) -> tuple[dict[str, Any], bool]:
    """Reset one NFL listing to a clean v2 price exactly once."""
    if not _is_nfl(record):
        return dict(record), False
    if str(record.get("nflMarketMigrationVersion") or "") == MIGRATION_VERSION:
        return dict(record), False

    # Live ESPN players migrate only after this model version has built their
    # same-run position/multi-season evidence window. Players missed by the
    # current event lookback remain eligible for migration on a later refresh
    # instead of being permanently reset from stale UNIVERSAL evidence.
    if (
        str(record.get("sourceNamespace") or "").lower() == "espn"
        and str(record.get("careerStatus") or "Active").lower() == "active"
        and str(record.get("nflFundamentalEvidenceVersion") or "") != NFL_FUNDAMENTAL_EVIDENCE_VERSION
    ):
        games = _finite(record.get("professionalGames")) or 0.0
        has_rookie_anchor = (
            record.get("draftPick") is not None
            or record.get("draftYear") is not None
            or isinstance(record.get("rookiePricing"), dict)
        )
        # Zero-game drafted players cannot ever acquire an "established" window
        # before debut. Let their current rookie/IPO evidence establish the new
        # market epoch instead of preserving a stale legacy price indefinitely.
        if games > 0 or not has_rookie_anchor:
            return dict(record), False

    prior_market = _finite(record.get("marketPrice"))
    prior_game_move = _finite(record.get("lastGameMovePct"))

    # Recalibrate NFL-only semantic inputs before establishing the new market
    # epoch. This corrects legacy achievement/performance/audience distortion
    # without touching any other league.
    calibrated = calibrate_nfl_record(record)

    # Build the new fundamental without allowing a stale pre-migration game move
    # to contaminate the reset price. Future verified games will populate this
    # field normally and compound from the migrated market price.
    clean = dict(calibrated)
    clean["lastGameMovePct"] = 0.0
    clean["dailyChange"] = 0.0
    clean["hourlyChangePct"] = 0.0
    repriced = apply_v2(clean)
    fundamental_target = _finite(repriced.get("fairValue"))
    if fundamental_target is None or fundamental_target <= 0:
        return dict(record), False
    fundamental_target = round(fundamental_target, 2)

    preserved_game = _latest_preservable_game(record, stamp)
    if preserved_game is not None:
        preserved_event, preserved_move = preserved_game
        preserved_event, preserved_move = _recalibrate_preserved_game_move(
            record,
            preserved_event,
            preserved_move,
        )
        preserved_game = (preserved_event, preserved_move)
    else:
        preserved_move = 0.0
    target = round(
        fundamental_target * (1.0 + preserved_move / 100.0),
        2,
    ) if preserved_game is not None else fundamental_target

    result = dict(calibrated)
    for field in _V2_FIELDS:
        if field in repriced:
            result[field] = repriced[field]

    # Rebase the one preserved latest game onto the new market epoch and remove
    # observation-only points created after that game. Those observations are
    # where the old retry bug surfaced as second/third applications; the verified
    # game itself remains in the durable ledger.
    history = [
        dict(item)
        for item in result.get("priceHistory", [])
        if isinstance(item, dict)
    ]
    if preserved_game is not None:
        preserved_event, _preserved_move = preserved_game
        preserved_key = str(preserved_event.get("eventKey") or preserved_event.get("eventId") or "")
        preserved_time = _parse_time(preserved_event.get("startedAt") or result.get("lastPriceEventAt"))
        rebased_events: list[dict[str, Any]] = []
        for raw_event in result.get("priceEvents", []) if isinstance(result.get("priceEvents"), list) else []:
            if not isinstance(raw_event, dict):
                continue
            event = dict(raw_event)
            event_key = str(event.get("eventKey") or event.get("eventId") or "")
            if event_key == preserved_key and str(event.get("eventType") or "").lower() == "game":
                # Carry the recalibrated significance metadata onto the durable
                # event before rebasing its prices to the new career-tier epoch.
                event.update({
                    key: value
                    for key, value in preserved_event.items()
                    if key not in {"priceBefore", "priceAfter"}
                })
                event["preMigrationPriceBefore"] = raw_event.get("priceBefore")
                event["preMigrationPriceAfter"] = raw_event.get("priceAfter")
                event["priceBefore"] = fundamental_target
                event["priceAfter"] = target
                event["movePct"] = round((target / fundamental_target - 1.0) * 100.0, 3)
                event["marketEpochRebased"] = MIGRATION_VERSION
            rebased_events.append(event)
        result["priceEvents"] = rebased_events

        cleaned_history: list[dict[str, Any]] = []
        for raw_point in history:
            point = dict(raw_point)
            point_time = _parse_time(point.get("time") or point.get("date"))
            point_event = str(point.get("eventId") or point.get("eventKey") or "")
            if (
                preserved_time is not None
                and point_time is not None
                and point_time > preserved_time
                and point_event == "current-market-price"
            ):
                continue
            if point_event == preserved_key:
                phase = str(point.get("phase") or "").lower()
                if phase == "open":
                    point["price"] = fundamental_target
                elif phase == "close":
                    point["price"] = target
                point["marketEpochRebased"] = MIGRATION_VERSION
            cleaned_history.append(point)
        history = cleaned_history

    result["priceHistory"] = _migration_point(history, target, stamp)
    result["marketPrice"] = target
    result["previousMarketPrice"] = fundamental_target if preserved_game is not None else target
    result["modelTargetPrice"] = fundamental_target
    result["dailyChange"] = round(preserved_move, 3) if preserved_game is not None else 0.0
    result["hourlyChangePct"] = round(preserved_move, 3) if preserved_game is not None else 0.0
    result["trend"] = (
        [fundamental_target] * 17 + [target]
        if preserved_game is not None
        else [target] * 18
    )
    result["lastGameMovePct"] = round(preserved_move, 3) if preserved_game is not None else 0.0
    result["lastPriceRefreshAt"] = stamp

    result["nflMarketMigrationVersion"] = MIGRATION_VERSION
    result["nflMarketMigratedAt"] = stamp
    result["nflMarketMigrationPriorMarketPrice"] = round(prior_market, 2) if prior_market is not None else None
    result["nflMarketMigrationPriorLastGameMovePct"] = (
        round(prior_game_move, 3) if prior_game_move is not None else None
    )
    result["nflMarketMigrationTargetPrice"] = target
    result["nflMarketMigrationFundamentalPrice"] = fundamental_target
    if preserved_game is not None:
        event, move = preserved_game
        result["nflMarketMigrationPreservedEventId"] = str(event.get("eventKey") or event.get("eventId") or "")
        result["nflMarketMigrationPreservedEventMovePct"] = round(move, 3)
    else:
        result.pop("nflMarketMigrationPreservedEventId", None)
        result.pop("nflMarketMigrationPreservedEventMovePct", None)
    result["nflMarketMigrationReason"] = (
        "Reset NFL to career-tier v2 fair value, preserve one recent verified game exactly once, and recalibrate legacy preserved moves to the current absolute-performance-authority model when evidence permits"
    )
    return result, True


def migrate_catalog(path: Path, *, migrated_at: str | None = None) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")

    stamp = migrated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output: list[dict[str, Any]] = []
    changed = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        updated, did_change = migrate_record(item, stamp)
        output.append(updated)
        changed += int(did_change)

    path.write_text(
        json.dumps(output, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    if path.name == "current_catalog.json":
        csv_path = path.with_suffix(".csv")
        fields = sorted({
            key
            for record in output
            for key, value in record.items()
            if not isinstance(value, (dict, list))
        })
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    changed = migrate_catalog(args.catalog)
    print(
        f"Migrated {changed:,} NFL listing(s) to clean v2 market state; "
        "all non-NFL records were left unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
