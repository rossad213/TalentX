#!/usr/bin/env python3
"""One-time WNBA market reset after repairing league event-state isolation.

Historical priceEvents/priceHistory remain intact for audit. The current WNBA
market epoch is reset to clean v2 fair value because prior WNBA game markers
were coupled to NFL model-version changes, allowing already-priced games to be
applied again. Existing game IDs are seeded into a dedicated WNBA manifest so
pre-repair games are never replayed after the reset.
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

MIGRATION_VERSION = "1.0-wnba-event-ledger-isolation"
MIGRATION_EVENT_ID = "model:wnba-event-ledger-isolation-v1"
STATE_VERSION = "1.0-wnba-event-pricing"

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
)


def is_wnba(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("leagueOrMedium") or "").strip().upper() == "WNBA"
    )


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def migration_point(history: list[dict[str, Any]], price: float, stamp: str) -> list[dict[str, Any]]:
    if any(str(item.get("eventId") or "") == MIGRATION_EVENT_ID for item in history if isinstance(item, dict)):
        return history
    return [
        *history,
        {
            "time": stamp,
            "eventId": MIGRATION_EVENT_ID,
            "eventType": "model_migration",
            "phase": "close",
            "name": "WNBA event-ledger isolation repair",
            "price": round(price, 2),
        },
    ]


def migrate_record(record: dict[str, Any], stamp: str) -> tuple[dict[str, Any], bool]:
    if not is_wnba(record):
        return dict(record), False
    if str(record.get("wnbaMarketMigrationVersion") or "") == MIGRATION_VERSION:
        return dict(record), False

    prior_market = finite(record.get("marketPrice"))
    prior_game_move = finite(record.get("lastGameMovePct"))

    clean = dict(record)
    clean["lastGameMovePct"] = 0.0
    clean["dailyChange"] = 0.0
    clean["hourlyChangePct"] = 0.0
    repriced = apply_v2(clean)
    target = finite(repriced.get("fairValue"))
    if target is None or target <= 0:
        return dict(record), False
    target = round(target, 2)

    result = dict(record)
    for field in _V2_FIELDS:
        if field in repriced:
            result[field] = repriced[field]

    history = [dict(item) for item in result.get("priceHistory", []) if isinstance(item, dict)]
    result["priceHistory"] = migration_point(history, target, stamp)
    result["marketPrice"] = target
    result["previousMarketPrice"] = target
    result["modelTargetPrice"] = target
    result["dailyChange"] = 0.0
    result["hourlyChangePct"] = 0.0
    result["trend"] = [target] * 18
    result["lastGameMovePct"] = 0.0
    result["lastPriceRefreshAt"] = stamp
    result["wnbaMarketMigrationVersion"] = MIGRATION_VERSION
    result["wnbaMarketMigratedAt"] = stamp
    result["wnbaMarketMigrationPriorMarketPrice"] = round(prior_market, 2) if prior_market is not None else None
    result["wnbaMarketMigrationPriorLastGameMovePct"] = round(prior_game_move, 3) if prior_game_move is not None else None
    result["wnbaMarketMigrationTargetPrice"] = target
    result["wnbaMarketMigrationReason"] = (
        "Reset WNBA current market state after decoupling game dedupe from NFL model-version changes"
    )
    return result, True


def _game_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    return [
        dict(item)
        for item in events
        if isinstance(item, dict)
        and str(item.get("eventType") or "").lower() == "game"
        and str(item.get("eventKey") or item.get("eventId") or "").strip()
    ]


def seed_state_manifest(records: list[dict[str, Any]], path: Path, stamp: str) -> tuple[int, int]:
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}

    processed_events: dict[str, dict[str, Any]] = {}
    for item in existing.get("processedEvents", []) if isinstance(existing.get("processedEvents"), list) else []:
        if isinstance(item, str):
            processed_events[item] = {"key": item}
        elif isinstance(item, dict) and str(item.get("key") or ""):
            processed_events[str(item.get("key"))] = dict(item)

    processed_players = {
        str(item.get("key") if isinstance(item, dict) else item)
        for item in (existing.get("processedPlayerEvents", []) if isinstance(existing.get("processedPlayerEvents"), list) else [])
        if str(item.get("key") if isinstance(item, dict) else item)
    }

    for record in records:
        if not is_wnba(record):
            continue
        namespace = str(record.get("sourceNamespace") or "").strip()
        athlete_id = str(record.get("sourceRecordId") or "").strip()
        if not namespace or not athlete_id:
            continue
        for event in _game_events(record):
            key = str(event.get("eventKey") or event.get("eventId") or "").strip()
            started_at = str(event.get("startedAt") or "").strip()
            processed_events[key] = {
                "key": key,
                "provider": event.get("provider") or "ESPN",
                "eventId": event.get("eventId") or key,
                "league": event.get("league") or "wnba",
                "name": event.get("name") or "WNBA game",
                "startedAt": started_at or None,
                "processedAt": stamp,
                "playersWithStats": None,
                "matchedCatalogPlayers": None,
            }
            processed_players.add(f"{key}|{namespace}:{athlete_id}")

    payload = {
        **existing,
        "version": STATE_VERSION,
        "generatedAt": stamp,
        "league": "WNBA",
        "processedEvents": sorted(
            processed_events.values(),
            key=lambda item: str(item.get("startedAt") or item.get("processedAt") or ""),
        )[-2000:],
        "processedPlayerEvents": sorted(processed_players)[-50000:],
        "method": "Dedicated WNBA game-event dedupe state; independent of NFL model versions.",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(payload["processedEvents"]), len(payload["processedPlayerEvents"])


def migrate_catalog(path: Path, *, migrated_at: str | None = None) -> tuple[int, list[dict[str, Any]], str]:
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
    path.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if path.name == "current_catalog.json":
        fields = sorted({
            key for record in output for key, value in record.items()
            if not isinstance(value, (dict, list))
        })
        with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output)
    return changed, output, stamp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    parser.add_argument("--state-manifest", type=Path, default=Path("data/wnba_event_refresh_manifest.json"))
    args = parser.parse_args()
    changed, records, stamp = migrate_catalog(args.catalog)
    events, players = seed_state_manifest(records, args.state_manifest, stamp)
    print(
        f"Migrated {changed:,} WNBA listing(s) to the repaired market epoch; "
        f"seeded {events:,} event(s) and {players:,} player-event marker(s); non-WNBA records were unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
