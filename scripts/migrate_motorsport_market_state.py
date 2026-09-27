#!/usr/bin/env python3
"""One-time Motorsport rebase onto verified race evidence and protected fair values.

Formula 1 backfill events are normalized so their chronological movement path
ends exactly at the corrected current v2 fair value. This creates an honest
source-backed 2026 chart without double-counting the season that is already
reflected in fundamentals. Unverified Motorsport discoveries are simply rebased
to the evidence-gated fair value and remain Under Review.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pricing_engine_v2 import apply_v2

MIGRATION_VERSION = "1.0-motorsport-verified-race-ledger"
MIGRATION_EVENT_ID = "model:motorsport-verified-race-ledger-v1"
PROVIDER = "Jolpica F1"

_V2_FIELDS = (
    "talentScore", "marketScore", "confidenceScore", "situationScore",
    "expectedValueScore", "fairValue", "fundamentalValue", "pricingModelVersion",
    "pricingEngine", "pricingV2",
)


def is_motorsport(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() == "motorsport"
    )


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_time(value: Any) -> datetime | None:
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


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def f1_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    events = [
        dict(item) for item in record.get("priceEvents", [])
        if isinstance(item, dict)
        and str(item.get("provider") or "") == PROVIDER
        and str(item.get("eventType") or "").lower() == "game"
        and finite(item.get("movePct")) is not None
        and parse_time(item.get("startedAt")) is not None
    ]
    events.sort(key=lambda item: str(item.get("startedAt") or ""))
    return events


def normalized_event_path(events: list[dict[str, Any]], target: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not events:
        return [], []
    factor = 1.0
    for event in events:
        move = finite(event.get("movePct")) or 0.0
        factor *= max(0.01, 1.0 + move / 100.0)
    price = max(0.01, target / max(0.01, factor))
    rebuilt: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for index, raw in enumerate(events):
        event = dict(raw)
        move = finite(event.get("movePct")) or 0.0
        before = price
        after = max(0.01, before * (1.0 + move / 100.0))
        if index == len(events) - 1:
            after = target
        before_rounded = round(before, 2)
        after_rounded = round(after, 2)
        realized = round((after_rounded / before_rounded - 1.0) * 100.0, 3) if before_rounded > 0 else round(move, 3)
        event["priceBefore"] = before_rounded
        event["priceAfter"] = after_rounded
        event["movePct"] = realized
        event["historicalBackfill"] = True
        event["backfillModel"] = MIGRATION_VERSION
        rebuilt.append(event)
        when = parse_time(event.get("startedAt"))
        if when is not None:
            event_id = str(event.get("eventKey") or event.get("eventId") or "")
            label = str(event.get("name") or "Verified Formula 1 race")
            history.extend([
                {
                    "time": iso(when - timedelta(seconds=1)),
                    "price": before_rounded,
                    "eventId": event_id,
                    "label": label,
                    "phase": "open",
                    "historyType": "verified-event-replay",
                    "eventType": "game",
                    "source": event.get("sourceUrl"),
                    "provider": PROVIDER,
                    "movePct": realized,
                    "performanceDeltaPct": event.get("performanceDeltaPct"),
                },
                {
                    "time": iso(when),
                    "price": after_rounded,
                    "eventId": event_id,
                    "label": label,
                    "phase": "close",
                    "historyType": "verified-event-replay",
                    "eventType": "game",
                    "source": event.get("sourceUrl"),
                    "provider": PROVIDER,
                    "movePct": realized,
                    "performanceDeltaPct": event.get("performanceDeltaPct"),
                },
            ])
        price = after
    return rebuilt, history


def clean_prior_history(record: dict[str, Any], f1_event_ids: set[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in record.get("priceHistory", []) if isinstance(record.get("priceHistory"), list) else []:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("eventId") or item.get("eventKey") or "")
        event_type = str(item.get("eventType") or "").lower()
        if event_id == "current-market-price" or event_type == "market-observation" or event_id in f1_event_ids:
            continue
        key = (str(item.get("time") or ""), event_id, str(item.get("phase") or ""))
        if key in seen:
            continue
        seen.add(key)
        output.append(dict(item))
    return output


def migrate_record(record: dict[str, Any], stamp: str) -> tuple[dict[str, Any], bool]:
    if not is_motorsport(record):
        return dict(record), False
    if str(record.get("motorsportMarketMigrationVersion") or "") == MIGRATION_VERSION:
        return dict(record), False

    current_f1_events = f1_events(record)
    is_formula1 = str(record.get("leagueOrMedium") or "").strip().lower() == "formula 1"
    # Never mark a Formula 1 listing migrated until verified race evidence is
    # actually present. Otherwise a temporary provider outage could set the
    # migration epoch early and make a later historical backfill look "live".
    if is_formula1 and (not bool(record.get("professionEvidenceVerified")) or not current_f1_events):
        return dict(record), False

    prior_market = finite(record.get("marketPrice"))
    repriced = apply_v2(dict(record))
    target = finite(repriced.get("fairValue"))
    if target is None or target <= 0:
        return dict(record), False
    target = round(target, 2)

    result = dict(record)
    for field in _V2_FIELDS:
        if field in repriced:
            result[field] = repriced[field]

    all_events = [dict(item) for item in result.get("priceEvents", []) if isinstance(item, dict)]
    current_f1 = current_f1_events
    rebuilt_f1, race_history = normalized_event_path(current_f1, target)
    f1_ids = {str(item.get("eventKey") or item.get("eventId") or "") for item in current_f1}
    other_events = [item for item in all_events if str(item.get("eventKey") or item.get("eventId") or "") not in f1_ids]
    result["priceEvents"] = sorted([*other_events, *rebuilt_f1], key=lambda item: str(item.get("startedAt") or ""))[-2500:]

    history = clean_prior_history(result, f1_ids)
    history.extend(race_history)
    history.append({
        "time": stamp,
        "eventId": MIGRATION_EVENT_ID,
        "label": "Motorsport verified-race market rebase",
        "phase": "close",
        "price": target,
        "historyType": "verified",
        "eventType": "model_migration",
        "priceBasis": "one-time Motorsport evidence correction; F1 race path normalized to corrected current fair value",
    })
    history.sort(key=lambda item: str(item.get("time") or ""))
    result["priceHistory"] = history[-2500:]
    result["marketPrice"] = target
    result["modelTargetPrice"] = target
    result["lastPriceRefreshAt"] = stamp
    result["motorsportMarketMigrationVersion"] = MIGRATION_VERSION
    result["motorsportMarketMigratedAt"] = stamp
    result["motorsportMarketMigrationPriorMarketPrice"] = round(prior_market, 2) if prior_market is not None else None
    result["motorsportMarketMigrationTargetPrice"] = target
    result["motorsportMarketMigrationReason"] = "Rebase Motorsport after verified F1 race ingestion, identity reconciliation, and low-evidence price protection"

    if rebuilt_f1:
        latest = rebuilt_f1[-1]
        result["previousMarketPrice"] = latest.get("priceBefore")
        result["dailyChange"] = finite(latest.get("movePct")) or 0.0
        result["hourlyChangePct"] = finite(latest.get("movePct")) or 0.0
        result["lastGameMovePct"] = finite(latest.get("movePct")) or 0.0
        result["lastGamePerformanceDeltaPct"] = finite(latest.get("performanceDeltaPct")) or 0.0
        result["lastGameStats"] = dict(latest.get("stats") or {})
        result["lastPriceEventAt"] = latest.get("startedAt")
        result["lastPriceEvent"] = latest.get("name")
        result["lastPriceEventId"] = latest.get("eventKey")
        result["priceExplanation"] = latest.get("reason")
        closes = [item.get("priceAfter") for item in rebuilt_f1[-18:] if finite(item.get("priceAfter")) is not None]
        result["trend"] = [round(float(value), 2) for value in closes] or [target]
        result["priceHistoryStatus"] = "source-backed-formula1-race-replay"
    else:
        result["previousMarketPrice"] = target
        result["dailyChange"] = 0.0
        result["hourlyChangePct"] = 0.0
        result["lastGameMovePct"] = 0.0
        result["trend"] = [target]
        result["priceHistoryStatus"] = "verified" if history else "unavailable"
    return result, True


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
        fields = sorted({key for record in output for key, value in record.items() if not isinstance(value, (dict, list))})
        with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output)
    return changed, output, stamp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    changed, _, _ = migrate_catalog(args.catalog)
    print(f"Migrated {changed:,} Motorsport listing(s) onto verified-race/evidence-gated market state; non-Motorsport records were unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
