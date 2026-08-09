#!/usr/bin/env python3
"""Add verified historical outcome events without changing today's market price.

Music uses Wikidata chart placements tied to already verified release events.
Actors use Wikidata box office versus production cost when both amounts share a
currency. The generated outcome moves use the same bounded outcome policy as
live TalentX pricing, then the historical event chain is reconstructed backward
from the unchanged current market price.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backfill_non_athlete_event_history import (
    event_key,
    event_time,
    existing_events,
    history_from_events,
    merge_events,
    reconstruct_chain,
)
from non_athlete_event_refresh import clamp, iso, number, qid_for, utc_now
from non_athlete_outcome_refresh import (
    MAX_OUTCOME_MOVE_PCT,
    actor_box_office_target,
    best_box_office_ratio,
    chart_target,
    music_chart_positions,
    record_multiplier,
    wikidata_entities,
)


def load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


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


def release_events(record: dict[str, Any], category: str, cutoff: datetime, now: datetime) -> list[dict[str, Any]]:
    wanted = "music-release" if category == "Music" else "actor-release"
    output: list[dict[str, Any]] = []
    for event in existing_events(record):
        if str(event.get("eventType") or "") != wanted:
            continue
        when = parse_time(event.get("startedAt"))
        work_qid = str(event.get("workQid") or event.get("eventId") or "")
        if when is None or when < cutoff or when > now or not re.fullmatch(r"Q\d+", work_qid):
            continue
        output.append({**event, "_when": when, "_workQid": work_qid})
    return output


def outcome_move(record: dict[str, Any], target: float) -> float:
    return round(clamp(target * record_multiplier(record), -MAX_OUTCOME_MOVE_PCT, MAX_OUTCOME_MOVE_PCT), 3)


def music_outcome(record: dict[str, Any], release: dict[str, Any], entity: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    positions = music_chart_positions(entity)
    if not positions:
        return None
    best_rank, chart_qid = positions[0]
    tier, target = chart_target(best_rank)
    move = outcome_move(record, target)
    if abs(move) < 0.01:
        return None
    when = min(now, release["_when"] + timedelta(days=7))
    work_qid = release["_workQid"]
    return {
        "eventKey": f"wikidata:historical-music-chart:{qid_for(record)}:{work_qid}:{tier}",
        "eventId": f"historical-music-chart:{work_qid}:{tier}",
        "eventType": "music-chart-outcome",
        "provider": "Wikidata",
        "sourceUrl": f"https://www.wikidata.org/wiki/{work_qid}",
        "name": f"Verified chart outcome: #{best_rank}",
        "startedAt": iso(when),
        "workQid": work_qid,
        "chartQid": chart_qid,
        "chartRank": best_rank,
        "outcomeTier": tier,
        "movePct": move,
        "verified": True,
        "historicalBackfill": True,
        "backfillModel": "verified-music-chart-history-v1",
    }


def actor_outcome(record: dict[str, Any], release: dict[str, Any], entity: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    ratio_info = best_box_office_ratio(entity)
    if not ratio_info:
        return None
    ratio, gross, cost, unit = ratio_info
    age_days = max(0.0, (now - release["_when"]).total_seconds() / 86400.0)
    target_info = actor_box_office_target(ratio, age_days)
    if not target_info:
        return None
    tier, target = target_info
    move = outcome_move(record, target)
    if abs(move) < 0.01:
        return None
    when = min(now, release["_when"] + timedelta(days=21))
    work_qid = release["_workQid"]
    return {
        "eventKey": f"wikidata:historical-box-office:{qid_for(record)}:{work_qid}:{tier}",
        "eventId": f"historical-box-office:{work_qid}:{tier}",
        "eventType": "actor-box-office-outcome",
        "provider": "Wikidata",
        "sourceUrl": f"https://www.wikidata.org/wiki/{work_qid}",
        "name": f"Verified box-office outcome: {ratio:.2f}× production cost",
        "startedAt": iso(when),
        "workQid": work_qid,
        "boxOfficeToCostRatio": round(ratio, 4),
        "boxOffice": gross,
        "productionCost": cost,
        "currencyUnit": unit,
        "outcomeTier": tier,
        "movePct": move,
        "verified": True,
        "historicalBackfill": True,
        "backfillModel": "verified-actor-box-office-history-v1",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--category", choices=["Music", "Actor"], required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    args = parser.parse_args()

    records = load_catalog(args.catalog)
    now = utc_now()
    cutoff = now - timedelta(days=max(1, args.days))
    releases_by_index: dict[int, list[dict[str, Any]]] = {}
    work_qids: set[str] = set()
    for index, record in enumerate(records):
        if str(record.get("primaryCategory") or "") != args.category:
            continue
        releases = release_events(record, args.category, cutoff, now)
        if not releases:
            continue
        releases_by_index[index] = releases
        work_qids.update(str(item["_workQid"]) for item in releases)

    if not work_qids:
        print(f"No verified {args.category} release events available for historical outcome backfill.")
        return 0

    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": "TalentX-Historical-Outcomes/1.0 (+https://github.com/rossad213/TalentX)"})
    entities, errors = wikidata_entities(session, work_qids, args.request_timeout)
    if errors:
        print(f"Historical outcome source warnings: {len(errors):,}")

    updated = list(records)
    generated_count = 0
    touched = 0
    for index, releases in releases_by_index.items():
        record = records[index]
        generated: list[dict[str, Any]] = []
        for release in releases:
            entity = entities.get(str(release["_workQid"]), {})
            if not entity:
                continue
            event = (
                music_outcome(record, release, entity, now)
                if args.category == "Music"
                else actor_outcome(record, release, entity, now)
            )
            if event is not None:
                generated.append(event)
        if not generated:
            continue
        result = dict(record)
        combined = merge_events(existing_events(result), generated)
        rebuilt = reconstruct_chain(result, combined)
        result["priceEvents"] = rebuilt
        result["priceHistory"] = history_from_events(rebuilt)
        result["priceHistoryStatus"] = "verified-event-and-outcome-backfill"
        result["priceHistoryBackfilledAt"] = iso(now)
        result["priceHistoryBackfillDays"] = args.days
        updated[index] = result
        generated_count += len([event for event in generated if event_key(event)])
        touched += 1

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Added {generated_count:,} verified historical {args.category} outcome events across {touched:,} profiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
