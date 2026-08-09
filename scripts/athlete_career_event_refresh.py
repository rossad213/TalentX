#!/usr/bin/env python3
"""Apply verified non-game athlete career events to TalentX market state.

Sports pricing is primarily game-driven, but major career events such as a
verified signing or team change can materially affect the simulated market too.
This adapter has two inputs:

1. Curated, source-backed events in ``data/verified_athlete_events.json``. These
   can carry a stronger event-specific move for major signings, retirements, etc.
2. Automatic roster deltas between the prior Sports state and the newest verified
   roster state. These receive only a small bounded move because the roster feed
   verifies the team change but does not by itself explain whether the move is
   strongly positive or negative.

Newly discovered events are inserted chronologically into durable ``priceEvents``.
The current price receives the missed event move exactly once, then the complete
verified event chain is reconstructed backward from that new current price. This
keeps historical charts continuous while preserving every already-recorded game
move.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_EVENT_MOVE_PCT = 2.5
MAX_PRICE_EVENTS = 2500


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def load_specs(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_time(event: dict[str, Any]) -> str:
    return str(event.get("startedAt") or event.get("time") or event.get("date") or "").strip()


def existing_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("priceEvents")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def matches(record: dict[str, Any], spec: dict[str, Any]) -> bool:
    match = spec.get("match") if isinstance(spec.get("match"), dict) else {}
    if match.get("id") and str(record.get("id") or "") != str(match.get("id")):
        return False
    if match.get("name") and norm(record.get("name")) != norm(match.get("name")):
        return False
    if match.get("discipline") and norm(record.get("discipline")) != norm(match.get("discipline")):
        return False
    if match.get("league") and norm(record.get("leagueOrMedium")) != norm(match.get("league")):
        return False
    return bool(match)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def curated_event(record: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    key = str(spec.get("eventKey") or "").strip()
    started = str(spec.get("startedAt") or "").strip()
    source = str(spec.get("sourceUrl") or "").strip()
    if not key or not started or not source:
        return None
    move = clamp(number(spec.get("targetMovePct"), 0.0), -MAX_EVENT_MOVE_PCT, MAX_EVENT_MOVE_PCT)
    if abs(move) < 0.01:
        return None
    return {
        "eventKey": key,
        "eventId": key,
        "eventType": str(spec.get("eventType") or "athlete-career-event"),
        "provider": str(spec.get("provider") or "Verified source"),
        "sourceUrl": source,
        "name": str(spec.get("name") or "Verified career event"),
        "startedAt": started,
        "datePrecision": spec.get("datePrecision") or "timestamp",
        "movePct": round(move, 3),
        "verified": True,
        "careerEvent": True,
        "careerEventModel": "verified-athlete-career-event-v1",
        "destinationTeam": spec.get("destinationTeam"),
        "originTeam": spec.get("originTeam"),
        "reason": spec.get("reason"),
        "athlete": record.get("name"),
    }


def identity_keys(record: dict[str, Any]) -> list[str]:
    output: list[str] = []
    if record.get("id"):
        output.append(f"id:{record['id']}")
    namespace = str(record.get("sourceNamespace") or "").strip()
    source_id = str(record.get("sourceRecordId") or "").strip()
    if namespace and source_id:
        output.append(f"source:{namespace}:{source_id}")
    if record.get("name") and record.get("discipline"):
        output.append(f"name:{norm(record['name'])}:{norm(record['discipline'])}")
    return output


def prior_lookup(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for record in records:
        for key in identity_keys(record):
            output.setdefault(key, record)
    return output


def find_prior(record: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for key in identity_keys(record):
        if key in lookup:
            return lookup[key]
    return None


def automatic_team_move(record: dict[str, Any]) -> float:
    """Small roster-delta move; major events should use a curated source event."""
    current = max(0.01, number(record.get("marketPrice"), 0.01))
    target = number(
        record.get("fundamentalValue", record.get("fairValue", record.get("modelTargetPrice"))),
        current,
    )
    gap_pct = (target / current - 1.0) * 100.0 if current > 0 else 0.0
    move = clamp(gap_pct * 0.12, -0.60, 0.60)
    if abs(move) < 0.10:
        # A verified active-roster team change is itself a small career-continuity
        # catalyst, but we deliberately keep it much smaller than curated major
        # signings because the roster feed alone does not measure team fit.
        move = 0.10
    return round(move, 3)


def automatic_team_change(
    record: dict[str, Any],
    prior: dict[str, Any] | None,
    *,
    covered_destination: str = "",
) -> dict[str, Any] | None:
    if prior is None or str(record.get("primaryCategory") or "") != "Athlete":
        return None
    old_team = str(prior.get("teamOrPlatform") or "").strip()
    new_team = str(record.get("teamOrPlatform") or "").strip()
    if not old_team or not new_team or norm(old_team) == norm(new_team):
        return None
    if old_team.lower() in {"team not listed", "not listed", "unknown"}:
        return None
    if new_team.lower() in {"team not listed", "not listed", "unknown"}:
        return None
    if covered_destination and norm(covered_destination) == norm(new_team):
        return None
    verified_at = str(record.get("lastVerifiedAt") or iso_now())
    key = f"roster-team-change:{record.get('id') or norm(record.get('name'))}:{norm(old_team)}:{norm(new_team)}"
    return {
        "eventKey": key,
        "eventId": key,
        "eventType": "athlete-team-change",
        "provider": str(record.get("statusSource") or record.get("sourceName") or "Verified roster source"),
        "sourceUrl": str(record.get("sourceUrl") or ""),
        "name": f"Team change: {old_team} → {new_team}",
        "startedAt": verified_at,
        "datePrecision": "verified-at",
        "movePct": automatic_team_move(record),
        "verified": True,
        "careerEvent": True,
        "careerEventModel": "verified-roster-team-change-v1",
        "originTeam": old_team,
        "destinationTeam": new_team,
        "reason": "Current roster verification changed from the prior verified team state.",
        "athlete": record.get("name"),
    }


def merge_events(existing: list[dict[str, Any]], generated: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_key: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in existing:
        key = event_key(event)
        if key:
            by_key[key] = dict(event)
        else:
            anonymous.append(dict(event))
    added: list[dict[str, Any]] = []
    for event in generated:
        key = event_key(event)
        if not key or key in by_key:
            continue
        by_key[key] = dict(event)
        added.append(dict(event))
    combined = [*anonymous, *by_key.values()]
    combined = [event for event in combined if event_time(event)]
    combined.sort(key=event_time)
    return combined[-MAX_PRICE_EVENTS:], added


def move_for(event: dict[str, Any]) -> float:
    move = number(event.get("movePct"), float("nan"))
    if math.isfinite(move):
        return clamp(move, -15.0, 15.0)
    before = number(event.get("priceBefore"), 0.0)
    after = number(event.get("priceAfter"), 0.0)
    if before > 0 and after > 0:
        return clamp((after / before - 1.0) * 100.0, -15.0, 15.0)
    return 0.0


def reconstruct_chain(current_price: float, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    after = max(0.01, current_price)
    rebuilt: list[dict[str, Any]] = []
    for event in reversed(events):
        result = dict(event)
        move = move_for(result)
        if abs(move) < 0.001:
            rebuilt.append(result)
            continue
        denominator = 1.0 + move / 100.0
        before = after / denominator if denominator > 0.0001 else after
        before_rounded = max(0.01, round(before, 2))
        after_rounded = max(0.01, round(after, 2))
        result["priceBefore"] = before_rounded
        result["priceAfter"] = after_rounded
        result["movePct"] = round((after_rounded / before_rounded - 1.0) * 100.0, 3)
        result["verified"] = result.get("verified") is not False
        rebuilt.append(result)
        after = before_rounded
    rebuilt.reverse()
    return rebuilt


def explanation(event: dict[str, Any], move: float, price: float, applied_at: str) -> dict[str, Any]:
    summary = [
        "A verified non-game career event was added to the athlete market.",
        str(event.get("reason") or "The event was source-backed and applied through the bounded athlete career-event policy."),
    ]
    return {
        "version": "athlete-career-events-v1",
        "eventId": event.get("eventKey"),
        "event": event.get("name"),
        "eventAt": event.get("startedAt"),
        "headline": str(event.get("name") or "Verified athlete career event"),
        "summary": summary,
        "direction": "increased" if move > 0 else "decreased" if move < 0 else "held steady",
        "finalMovePct": round(move, 2),
        "recordedMarketPrice": round(price, 2),
        "pricingMode": "Verified athlete career event; catch-up applied once",
        "source": event.get("provider"),
        "sourceUrl": event.get("sourceUrl"),
        "appliedAt": applied_at,
    }


def recently_refreshed(record: dict[str, Any], now: datetime) -> bool:
    refreshed = parse_time(record.get("lastPriceRefreshAt"))
    return bool(refreshed and abs((now - refreshed).total_seconds()) <= 3 * 3600)


def apply_new_events(record: dict[str, Any], generated: list[dict[str, Any]], applied_at: str) -> tuple[dict[str, Any], int]:
    combined, added = merge_events(existing_events(record), generated)
    if not added:
        return dict(record), 0

    old_price = max(0.01, number(record.get("marketPrice"), 0.01))
    factor = 1.0
    for event in added:
        factor *= 1.0 + move_for(event) / 100.0
    new_price = max(0.01, round(old_price * factor, 2))
    rebuilt = reconstruct_chain(new_price, combined)

    now = parse_time(applied_at) or datetime.now(timezone.utc)
    previous = max(0.01, number(record.get("previousMarketPrice"), old_price)) if recently_refreshed(record, now) else old_price
    change = round((new_price / previous - 1.0) * 100.0, 2) if previous > 0 else 0.0

    result = dict(record)
    result["priceEvents"] = rebuilt
    result["previousMarketPrice"] = round(previous, 2)
    result["marketPrice"] = round(new_price, 2)
    result["dailyChange"] = change
    result["hourlyChangePct"] = change
    trend = [number(value) for value in result.get("trend", []) if number(value) > 0]
    result["trend"] = [round(value, 2) for value in (trend[-17:] + [new_price])]
    result["lastPriceRefreshAt"] = applied_at

    newest_added = max(added, key=lambda event: event_time(event))
    latest_overall = max(rebuilt, key=lambda event: event_time(event)) if rebuilt else newest_added
    if event_key(latest_overall) == event_key(newest_added):
        result["lastPriceEventAt"] = newest_added.get("startedAt")
        result["lastPriceEvent"] = newest_added.get("name")
        result["lastPriceEventId"] = newest_added.get("eventKey")
        result["lastEventMovePct"] = newest_added.get("movePct")
        result["lastEventType"] = newest_added.get("eventType")
        result["lastEventSource"] = newest_added.get("provider")
    result["lastCareerEventAt"] = newest_added.get("startedAt")
    result["lastCareerEvent"] = newest_added.get("name")
    result["lastCareerEventId"] = newest_added.get("eventKey")
    result["careerEventCatchupAt"] = applied_at
    result["priceExplanation"] = explanation(newest_added, move_for(newest_added), new_price, applied_at)
    return result, len(added)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--events", type=Path, default=Path("data/verified_athlete_events.json"))
    args = parser.parse_args()

    records = load_records(args.catalog)
    prior_records = load_records(args.prior) if args.prior and args.prior.exists() else []
    prior = prior_lookup(prior_records)
    specs = load_specs(args.events)
    applied_at = iso_now()

    output: list[dict[str, Any]] = []
    touched = 0
    added_total = 0
    curated_added = 0
    automatic_added = 0

    for record in records:
        if str(record.get("primaryCategory") or "") != "Athlete":
            output.append(dict(record))
            continue

        matched_specs = [spec for spec in specs if matches(record, spec)]
        generated: list[dict[str, Any]] = []
        destinations: list[str] = []
        for spec in matched_specs:
            event = curated_event(record, spec)
            if event:
                generated.append(event)
                if event.get("destinationTeam"):
                    destinations.append(str(event["destinationTeam"]))

        prior_record = find_prior(record, prior)
        covered_destination = next(
            (destination for destination in destinations if norm(destination) == norm(record.get("teamOrPlatform"))),
            "",
        )
        automatic = automatic_team_change(record, prior_record, covered_destination=covered_destination)
        if automatic:
            generated.append(automatic)

        result, added = apply_new_events(record, generated, applied_at)
        output.append(result)
        if added:
            touched += 1
            added_total += added
            known_before = {event_key(event) for event in existing_events(record)}
            for event in generated:
                if event_key(event) in known_before:
                    continue
                if event.get("careerEventModel") == "verified-roster-team-change-v1":
                    automatic_added += 1
                else:
                    curated_added += 1

    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"Applied {added_total:,} new athlete career events to {touched:,} records "
        f"({curated_added:,} curated, {automatic_added:,} roster-delta)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
