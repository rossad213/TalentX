#!/usr/bin/env python3
"""Validate and normalize source-backed TalentX historical price events.

The event/date may be historical fact; the backfilled TalentX price is still a
simulation. This script enforces that distinction. It never invents an event,
date, source, or result. Historical events that cannot meet the source/date
contract are removed from the chart history rather than guessed.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MAX_EVENTS = 2500
MAX_HISTORY = 5000
OUTCOME_TYPES_WITHOUT_INTRINSIC_DATE = {
    "music-chart-outcome",
    "actor-box-office-outcome",
}


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


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


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_time(event: dict[str, Any]) -> str:
    return str(event.get("startedAt") or event.get("time") or event.get("date") or "").strip()


def source_url_for(record: dict[str, Any], event: dict[str, Any]) -> str:
    existing = str(event.get("sourceUrl") or "").strip()
    if existing.startswith("https://"):
        return existing

    provider = str(event.get("provider") or "").strip().lower()
    event_id = str(event.get("eventId") or "").strip()
    if not event_id:
        return ""

    if provider == "nhl":
        return f"https://api-web.nhle.com/v1/gamecenter/{event_id}/boxscore"

    if provider == "espn":
        discipline = str(record.get("discipline") or "")
        sport = {
            "Basketball": "basketball",
            "American Football": "football",
            "Baseball": "baseball",
            "Soccer": "soccer",
            "Tennis": "tennis",
            "Golf": "golf",
        }.get(discipline, str(event.get("sport") or "").strip().lower())
        league = str(event.get("league") or record.get("sourceLeagueSlug") or "").strip().lower()
        if sport and league:
            return f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary?event={event_id}"

    return ""


def creator_pageview_date_matches(event: dict[str, Any], started: datetime) -> bool:
    url = str(event.get("sourceUrl") or "")
    if "wikimedia.org/api/rest_v1/metrics/pageviews/per-article/" not in url:
        return False
    match = re.search(r"/daily/(\d{8})/(\d{8})(?:$|[?#])", url)
    if not match:
        return False
    try:
        first = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
        last = datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    day = started.replace(hour=0, minute=0, second=0, microsecond=0)
    return first <= day <= last


def is_source_backed_historical(record: dict[str, Any], event: dict[str, Any], now: datetime) -> tuple[bool, str]:
    if event.get("historicalBackfill") is not True:
        return True, "live-or-existing"
    if event.get("verified") is False or event.get("synthetic") is True or event.get("reconstructed") is True:
        return False, "not-verified"

    started = parse_time(event_time(event))
    if started is None:
        return False, "invalid-date"
    if started > now + timedelta(days=1):
        return False, "future-date"

    etype = str(event.get("eventType") or "").strip().lower()
    if etype in OUTCOME_TYPES_WITHOUT_INTRINSIC_DATE and event.get("outcomeDateVerified") is not True:
        return False, "inferred-outcome-date"

    url = source_url_for(record, event)
    if not url:
        return False, "missing-source-url"

    provider = str(event.get("provider") or "").strip()
    if not provider:
        return False, "missing-provider"

    if etype == "creator-attention-outcome" and not creator_pageview_date_matches({**event, "sourceUrl": url}, started):
        return False, "creator-date-not-in-source-range"

    if etype in {"music-release", "actor-release"} and event.get("dateVerified") is not True:
        return False, "release-date-not-exactly-verified"

    if abs(number(event.get("movePct"), 0.0)) < 0.001:
        return False, "no-price-move"

    return True, "source-backed-exact-date"


def reconstruct_chain(current_price: float, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    after = max(0.01, current_price)
    rebuilt: list[dict[str, Any]] = []
    for event in reversed(events):
        result = dict(event)
        move = number(result.get("movePct"), 0.0)
        if abs(move) < 0.001:
            rebuilt.append(result)
            continue
        denominator = 1.0 + move / 100.0
        if denominator <= 0:
            continue
        before = max(0.01, round(after / denominator, 2))
        after_rounded = max(0.01, round(after, 2))
        result["priceBefore"] = before
        result["priceAfter"] = after_rounded
        result["movePct"] = round((after_rounded / before - 1.0) * 100.0, 3)
        if result.get("historicalBackfill") is True:
            result["priceBasis"] = "talentx-simulated-event-backfill"
            result["eventEvidenceStatus"] = "source-backed"
        rebuilt.append(result)
        after = before
    rebuilt.reverse()
    return rebuilt[-MAX_EVENTS:]


def history_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for event in events:
        if event.get("verified") is False:
            continue
        started = parse_time(event_time(event))
        key = event_key(event)
        before = number(event.get("priceBefore"), 0.0)
        after = number(event.get("priceAfter"), 0.0)
        if started is None or not key or before <= 0 or after <= 0:
            continue
        source = str(event.get("sourceUrl") or "")
        label = str(event.get("name") or "Source-backed market event")
        common = {
            "eventId": key,
            "label": label,
            "source": source,
            "provider": event.get("provider"),
            "historyType": "source-backed-event-simulated-price" if event.get("historicalBackfill") is True else "talentx-recorded-event",
            "eventType": event.get("eventType"),
            "movePct": event.get("movePct"),
            "priceBasis": event.get("priceBasis") or "talentx-recorded",
        }
        points.append({**common, "time": iso(started - timedelta(seconds=1)), "price": round(before, 2), "phase": "open"})
        points.append({**common, "time": iso(started), "price": round(after, 2), "phase": "close"})
    points.sort(key=lambda item: str(item.get("time") or ""))
    return points[-MAX_HISTORY:]


def category_match(record: dict[str, Any], category: str) -> bool:
    if category == "all":
        return True
    return str(record.get("primaryCategory") or "").lower() == category.lower()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--category", default="all")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--rewrite", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    now = datetime.now(timezone.utc)

    reasons: Counter[str] = Counter()
    coverage: dict[str, dict[str, int]] = {}
    updated: list[dict[str, Any]] = []
    total_historical = 0
    retained_historical = 0

    for record in records:
        result = dict(record)
        if not category_match(result, args.category):
            updated.append(result)
            continue
        events = [dict(item) for item in result.get("priceEvents", []) if isinstance(item, dict)] if isinstance(result.get("priceEvents"), list) else []
        kept: list[dict[str, Any]] = []
        for event in events:
            if event.get("historicalBackfill") is True:
                total_historical += 1
                ok, reason = is_source_backed_historical(result, event, now)
                if not ok:
                    reasons[reason] += 1
                    continue
                event["sourceUrl"] = source_url_for(result, event)
                event["eventEvidenceStatus"] = "source-backed"
                event["dateEvidenceStatus"] = "exact-source-date"
                event["priceBasis"] = "talentx-simulated-event-backfill"
                retained_historical += 1
            kept.append(event)

        kept.sort(key=lambda item: event_time(item))
        if args.rewrite:
            rebuilt = reconstruct_chain(max(0.01, number(result.get("marketPrice"), 0.01)), kept)
            result["priceEvents"] = rebuilt
            result["priceHistory"] = history_from_events(rebuilt)
            if any(event.get("historicalBackfill") is True for event in rebuilt):
                result["priceHistoryStatus"] = "source-backed-exact-date-backfill"
                result["priceHistoryDisclosure"] = "Historical event facts and dates are source-backed; historical TalentX prices are simulated model responses reconstructed from the current price."

        cat = str(result.get("primaryCategory") or "Unknown")
        bucket = coverage.setdefault(cat, {"profiles": 0, "with1": 0, "with3": 0, "with5": 0, "with10": 0, "events": 0})
        bucket["profiles"] += 1
        count = sum(1 for event in kept if event.get("historicalBackfill") is True and event.get("verified") is not False)
        bucket["events"] += count
        for threshold, key in ((1, "with1"), (3, "with3"), (5, "with5"), (10, "with10")):
            if count >= threshold:
                bucket[key] += 1
        updated.append(result)

    if args.rewrite:
        args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    report = {
        "generatedAt": iso(now),
        "catalog": str(args.catalog),
        "categoryFilter": args.category,
        "historicalEventsSeen": total_historical,
        "historicalEventsRetained": retained_historical,
        "historicalEventsRemoved": total_historical - retained_historical,
        "removedReasons": dict(sorted(reasons.items())),
        "coverage": coverage,
        "policy": {
            "events": "source-backed real-world events with exact dates only",
            "historicalPrice": "simulated TalentX model response, not a historical security price",
            "inferredOutcomeDatesAllowed": False,
        },
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
