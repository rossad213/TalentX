#!/usr/bin/env python3
"""Backfill verified Music/Actor event history without repricing the live market.

Historical releases/projects are discovered with the same source contracts used
by live TalentX non-athlete pricing. New historical events are merged with
existing durable ``priceEvents`` and the whole chain is reconstructed backward
from today's unchanged market price.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from non_athlete_event_refresh import (
    actor_release_event,
    claim_strings,
    event_move_pct,
    existing_mbid,
    fetch_entities,
    fetch_works,
    iso,
    make_session,
    musicbrainz_release_match,
    qid_for,
    release_event,
    utc_now,
)

MAX_PRICE_EVENTS = 2500


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_time(event: dict[str, Any]) -> str:
    return str(event.get("startedAt") or event.get("time") or event.get("date") or "").strip()


def existing_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("priceEvents")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def merge_events(existing: list[dict[str, Any]], generated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in existing:
        key = event_key(event)
        if key:
            by_key[key] = dict(event)
        else:
            anonymous.append(dict(event))
    for event in generated:
        key = event_key(event)
        if not key:
            continue
        if key in by_key:
            merged = dict(event)
            merged.update(by_key[key])
            by_key[key] = merged
        else:
            by_key[key] = dict(event)
    combined = [*anonymous, *by_key.values()]
    combined = [event for event in combined if event_time(event)]
    combined.sort(key=event_time)
    return combined[-MAX_PRICE_EVENTS:]


def reconstruct_chain(record: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    after = max(0.01, number(record.get("marketPrice"), 0.01))
    rebuilt: list[dict[str, Any]] = []
    for event in reversed(events):
        result = dict(event)
        move = number(result.get("movePct"), 0.0)
        if abs(move) < 0.001:
            rebuilt.append(result)
            continue
        denominator = 1.0 + move / 100.0
        before = after / denominator if abs(denominator) > 0.0001 else after
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


def history_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for event in events:
        started = event_time(event)
        key = event_key(event)
        before = number(event.get("priceBefore"), 0.0)
        after = number(event.get("priceAfter"), 0.0)
        if not started or not key or before <= 0 or after <= 0:
            continue
        try:
            parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        points.extend([
            {
                "time": iso(parsed - timedelta(seconds=1)),
                "price": round(before, 2),
                "eventId": key,
                "label": str(event.get("name") or "Verified career event"),
                "phase": "open",
                "source": "verified-non-athlete-history-backfill",
                "historyType": "verified",
                "movePct": event.get("movePct"),
            },
            {
                "time": started,
                "price": round(after, 2),
                "eventId": key,
                "label": str(event.get("name") or "Verified career event"),
                "phase": "close",
                "source": "verified-non-athlete-history-backfill",
                "historyType": "verified",
                "movePct": event.get("movePct"),
            },
        ])
    points.sort(key=lambda value: str(value.get("time") or ""))
    return points[-5000:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--category", choices=["Music", "Actor"], required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--wikidata-batch-size", type=int, default=60)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--max-music-confirmations", type=int, default=500)
    parser.add_argument("--max-events-per-record", type=int, default=12)
    args = parser.parse_args()

    records = load_catalog(args.catalog)
    now = utc_now()
    start = now - timedelta(days=max(1, args.days))
    qid_to_indexes: dict[str, list[int]] = defaultdict(list)
    qids: list[str] = []
    for index, record in enumerate(records):
        if str(record.get("primaryCategory") or "") != args.category:
            continue
        qid = qid_for(record)
        if not qid:
            continue
        qid_to_indexes[qid].append(index)
        qids.append(qid)
    qids = sorted(set(qids))
    print(f"{args.category} identities eligible for historical scan: {len(qids):,}")
    if not qids:
        return 0

    session = make_session()
    entities, entity_errors = fetch_entities(session, qids, args.request_timeout)
    works, work_errors = fetch_works(
        session,
        args.category,
        qids,
        start,
        now,
        max(1, args.wikidata_batch_size),
        args.request_timeout,
    )
    if entity_errors or work_errors:
        print(f"Source warnings: {len(entity_errors) + len(work_errors):,}")

    generated_by_index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    confirmations = 0
    if args.category == "Music":
        mbids: dict[str, str] = {}
        for qid in qids:
            record = records[qid_to_indexes[qid][0]]
            mbid = existing_mbid(record)
            if not mbid:
                values = claim_strings(entities.get(qid, {}), "P434")
                if values:
                    mbid = str(values[0]).lower()
            if mbid:
                mbids[qid] = mbid

        for qid in qids:
            mbid = mbids.get(qid)
            if not mbid:
                continue
            candidates = works.get(qid, [])[-max(1, args.max_events_per_record):]
            for candidate in candidates:
                if confirmations >= max(0, args.max_music_confirmations):
                    break
                confirmations += 1
                try:
                    match = musicbrainz_release_match(session, mbid, candidate, args.request_timeout)
                except Exception as exc:  # noqa: BLE001
                    print(f"MusicBrainz warning for {qid}: {type(exc).__name__}: {exc}")
                    continue
                if not match:
                    continue
                for index in qid_to_indexes[qid]:
                    event = release_event(records[index], candidate, match)
                    move = event_move_pct(records[index], event)
                    if abs(move) < 0.001:
                        continue
                    generated_by_index[index].append({
                        **event,
                        "movePct": move,
                        "verified": True,
                        "historicalBackfill": True,
                        "backfillModel": "verified-music-release-history-v1",
                    })
            if confirmations >= max(0, args.max_music_confirmations):
                break
    else:
        for qid in qids:
            candidates = works.get(qid, [])[-max(1, args.max_events_per_record):]
            for candidate in candidates:
                for index in qid_to_indexes[qid]:
                    event = actor_release_event(records[index], candidate, upcoming=False)
                    move = event_move_pct(records[index], event)
                    if abs(move) < 0.001:
                        continue
                    generated_by_index[index].append({
                        **event,
                        "movePct": move,
                        "verified": True,
                        "historicalBackfill": True,
                        "backfillModel": "verified-actor-release-history-v1",
                    })

    updated = list(records)
    touched = 0
    generated_count = 0
    for index, record in enumerate(records):
        generated = generated_by_index.get(index, [])
        current_events = existing_events(record)
        if not generated and not current_events:
            continue
        result = dict(record)
        combined = merge_events(current_events, generated)
        rebuilt = reconstruct_chain(result, combined)
        result["priceEvents"] = rebuilt
        result["priceHistory"] = history_from_events(rebuilt)
        result["priceHistoryStatus"] = "verified-event-backfill"
        result["priceHistoryBackfilledAt"] = iso(now)
        result["priceHistoryBackfillDays"] = args.days
        result["priceHistoryBackfillModel"] = (
            "verified-music-release-history-v1" if args.category == "Music"
            else "verified-actor-release-history-v1"
        )
        updated[index] = result
        touched += 1
        generated_count += len(generated)

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Backfilled {touched:,} {args.category} records with {generated_count:,} verified historical events.")
    if args.category == "Music":
        print(f"MusicBrainz release confirmations attempted: {confirmations:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
