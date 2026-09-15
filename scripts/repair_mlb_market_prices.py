#!/usr/bin/env python3
"""Restore MLB market prices from the preserved pre-corruption pricing baseline.

TalentX keeps the last v1 market price inside ``pricingV1`` when pricing engine v2
runs. Recent generic game-event history used an invalid baseball per-game
comparison for some hitters and pitchers, which allowed repeated game moves to
compound into implausible prices. This repair is deliberately MLB-only.

The repair:
* restores every MLB listing to its preserved ``pricingV1.marketPrice`` when
  available (with a conservative fair-value fallback);
* removes corrupted game events while preserving non-game market events;
* keeps plausible dated price observations and safe sparkline history instead of
  replacing every chart point with a flat line;
* retains a valid non-game latest percentage move when one exists;
* leaves every non-MLB record untouched.

Until a baseball-specific per-game expectation model is calibrated, MLB game
results are informational only and must not reprice the market.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPAIR_VERSION = "1.1-mlb-v1-market-recovery-history-preserved"
HISTORY_RATIO_LOW = 0.25
HISTORY_RATIO_HIGH = 4.0
REPAIR_EVENT_ID = "mlb-market-repair"


def finite_positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def is_mlb(record: dict[str, Any]) -> bool:
    return str(record.get("leagueOrMedium") or "").strip().upper() == "MLB"


def baseline_price(record: dict[str, Any]) -> float | None:
    pricing_v1 = record.get("pricingV1") if isinstance(record.get("pricingV1"), dict) else {}
    for value in (
        pricing_v1.get("marketPrice"),
        record.get("fairValue"),
        record.get("fundamentalValue"),
        record.get("modelTargetPrice"),
    ):
        parsed = finite_positive(value)
        if parsed is not None:
            return round(parsed, 2)
    return None


def event_key(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def game_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    event_type = str(event.get("eventType") or "").strip().lower()
    key = event_key(event).lower()
    provider = str(event.get("provider") or "").strip().lower()
    return event_type == "game" or key.startswith("espn:") or (provider == "espn" and event_type != "career")


def event_move_pct(event: dict[str, Any]) -> float | None:
    move = finite_number(event.get("movePct"))
    if move is not None and move > -100:
        return round(move, 3)
    before = finite_positive(event.get("priceBefore"))
    after = finite_positive(event.get("priceAfter"))
    if before is None or after is None:
        return None
    return round((after / before - 1.0) * 100.0, 3)


def safe_history(record: dict[str, Any], anchor: float, repaired_at: str) -> list[dict[str, Any]]:
    history = record.get("priceHistory") if isinstance(record.get("priceHistory"), list) else []
    kept: list[dict[str, Any]] = []
    low = anchor * HISTORY_RATIO_LOW
    high = anchor * HISTORY_RATIO_HIGH
    for point in history:
        if not isinstance(point, dict):
            continue
        # Keep the repair idempotent: replace the old repair observation instead
        # of appending another copy on every deploy.
        if str(point.get("eventId") or "") == REPAIR_EVENT_ID:
            continue
        price = finite_positive(point.get("price"))
        if price is None:
            continue
        # Preserve plausible source-backed history but drop runaway observations
        # created after the baseball game-price denominator became unstable.
        if low <= price <= high:
            kept.append(dict(point))
    kept.append({
        "time": repaired_at,
        "price": round(anchor, 2),
        "eventId": REPAIR_EVENT_ID,
        "label": "MLB market price restored from preserved TalentX baseline",
        "phase": "close",
        "historyType": "verified",
        "eventType": "market-repair",
    })
    return kept[-730:]


def safe_trend(record: dict[str, Any], history: list[dict[str, Any]], anchor: float) -> list[float]:
    low = anchor * HISTORY_RATIO_LOW
    high = anchor * HISTORY_RATIO_HIGH
    history_prices = [
        round(price, 2)
        for point in history
        if isinstance(point, dict)
        for price in [finite_positive(point.get("price"))]
        if price is not None and low <= price <= high
    ]
    # Prefer dated history because it is what the profile chart uses. Fall back
    # to the old sparkline only when dated observations are sparse.
    candidates = history_prices
    if len(candidates) < 2:
        candidates = [
            round(price, 2)
            for raw in (record.get("trend") if isinstance(record.get("trend"), list) else [])
            for price in [finite_positive(raw)]
            if price is not None and low <= price <= high
        ]
        candidates.append(round(anchor, 2))
    if not candidates:
        return [round(anchor, 2)]
    if abs(candidates[-1] - anchor) >= 0.005:
        candidates.append(round(anchor, 2))
    return candidates[-18:]


def latest_preserved_event(record: dict[str, Any], preserved_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    latest_id = str(record.get("lastPriceEventId") or "").strip()
    if not latest_id:
        return None
    for event in reversed(preserved_events):
        aliases = {event_key(event), str(event.get("eventId") or "").strip()}
        if latest_id in aliases:
            return event
    return None


def repair_record(record: dict[str, Any], *, repaired_at: str) -> tuple[dict[str, Any], bool]:
    if not is_mlb(record):
        return record, False
    anchor = baseline_price(record)
    if anchor is None:
        return record, False

    result = dict(record)
    old_price = finite_positive(result.get("marketPrice"))
    existing_events = result.get("priceEvents") if isinstance(result.get("priceEvents"), list) else []
    preserved_events = [dict(event) for event in existing_events if isinstance(event, dict) and not game_event(event)]
    active_preserved = latest_preserved_event(result, preserved_events)

    repaired_history = safe_history(result, anchor, repaired_at)
    repaired_trend = safe_trend(result, repaired_history, anchor)
    prior_prices = [
        finite_positive(point.get("price"))
        for point in repaired_history[:-1]
        if isinstance(point, dict)
    ]
    prior_prices = [price for price in prior_prices if price is not None]

    result["marketPrice"] = anchor
    result["priceEvents"] = preserved_events
    result["priceHistory"] = repaired_history
    result["trend"] = repaired_trend
    result["priceHistoryStatus"] = "verified"
    result["lastGameMovePct"] = 0.0

    if active_preserved is not None:
        move = event_move_pct(active_preserved)
        before = finite_positive(active_preserved.get("priceBefore"))
        result["previousMarketPrice"] = round(before, 2) if before is not None else (round(prior_prices[-1], 2) if prior_prices else anchor)
        result["dailyChange"] = move if move is not None else finite_number(result.get("dailyChange")) or 0.0
        result["hourlyChangePct"] = move if move is not None else finite_number(result.get("hourlyChangePct")) or result["dailyChange"]
        result["lastPriceEventId"] = event_key(active_preserved)
        result["lastPriceEventAt"] = active_preserved.get("startedAt") or active_preserved.get("time") or result.get("lastPriceEventAt")
        result["lastPriceEvent"] = active_preserved.get("label") or active_preserved.get("eventType") or result.get("lastPriceEvent")
    else:
        result["previousMarketPrice"] = round(prior_prices[-1], 2) if prior_prices else anchor
        result["dailyChange"] = 0.0
        result["hourlyChangePct"] = 0.0
        for key in ("lastPriceEventAt", "lastPriceEvent", "lastPriceEventId"):
            result.pop(key, None)

    result["mlbGamePricingStatus"] = "protected-pending-baseball-specific-calibration"
    result["mlbMarketRepairVersion"] = REPAIR_VERSION
    result["mlbMarketRepairedAt"] = repaired_at
    result["mlbMarketRepair"] = {
        "reason": "Recovered from invalid generic baseball per-game comparison",
        "oldPrice": round(old_price, 2) if old_price is not None else None,
        "restoredPrice": anchor,
        "source": "pricingV1.marketPrice" if finite_positive((result.get("pricingV1") or {}).get("marketPrice") if isinstance(result.get("pricingV1"), dict) else None) is not None else "fair-value fallback",
        "removedGameEvents": len(existing_events) - len(preserved_events),
        "safeHistoryPoints": len(repaired_history),
    }

    for key in ("lastGamePerformanceDeltaPct", "eventPricingModel", "priceExplanation"):
        result.pop(key, None)
    return result, True


def repair_catalog(path: Path, *, repaired_at: str | None = None) -> int:
    if not path.exists():
        return 0
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{path} must contain a JSON array")
    stamp = repaired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    repaired: list[dict[str, Any]] = []
    count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        updated, changed = repair_record(record, repaired_at=stamp)
        repaired.append(updated)
        count += int(changed)
    compact = path.name == "current_catalog.json"
    path.write_text(
        json.dumps(repaired, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(repaired, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    args = parser.parse_args()
    count = repair_catalog(args.catalog)
    if count:
        print(f"Restored {count:,} MLB market price(s) from the preserved pricing baseline.")
    else:
        print("No MLB prices required repair.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
