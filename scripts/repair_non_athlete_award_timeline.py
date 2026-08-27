#!/usr/bin/env python3
"""Repair Music/Actor award and nomination pricing to their real event timelines.

Rules enforced here:
- ``startedAt`` is the real-world event date, never the ingestion/verification time.
- ``verifiedAt`` records when TalentX observed the evidence.
- Only statement-level award/nominations with an exact day-qualified Wikidata date
  can move price.
- Undated/low-precision claims remain career evidence but are not price events.
- Repeated wins of the same award are distinct statement/date events.
- Historical events are inserted on their historical dates and the event-price
  chain is rebuilt forward to today's price.

This script is intentionally run after ``non_athlete_event_refresh.py`` so it also
repairs any legacy discovery-time award event before the category state is saved.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backfill_non_athlete_event_history import history_from_events, reconstruct_chain
from non_athlete_event_refresh import (
    event_move_pct,
    explanation_for,
    fetch_entities,
    fetch_labels,
    iso,
    make_session,
    qid_for,
    utc_now,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DATE_QUALIFIERS = ("P585", "P580", "P577")  # point in time, start time, publication date
EVENT_PROPS = {"award": "P166", "nomination": "P1411"}
MODEL_VERSION = "2.0-statement-level-real-event-time"
MAX_PRICE_EVENTS = 2500
MAX_UNPRICED_EVIDENCE = 1500


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


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


def _item_qid(claim: dict[str, Any]) -> str:
    value = claim.get("mainsnak", {}).get("datavalue", {}).get("value") if isinstance(claim, dict) else None
    if not isinstance(value, dict):
        return ""
    qid = str(value.get("id") or "")
    return qid if re.fullmatch(r"Q\d+", qid) else ""


def _wikidata_exact_time(snak: dict[str, Any]) -> tuple[datetime | None, int]:
    value = snak.get("datavalue", {}).get("value") if isinstance(snak, dict) else None
    if not isinstance(value, dict):
        return None, 0
    precision = int(value.get("precision") or 0)
    raw = str(value.get("time") or "").strip()
    # Precision 11 is day-level in Wikidata. We refuse to invent a day for
    # month/year-only claims because that would recreate the timeline problem.
    if precision < 11 or not raw or raw.startswith("-"):
        return None, precision
    raw = raw.lstrip("+")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, precision
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), precision


def statement_event_date(claim: dict[str, Any]) -> tuple[datetime | None, str, int]:
    qualifiers = claim.get("qualifiers") if isinstance(claim.get("qualifiers"), dict) else {}
    best_precision = 0
    for prop in DATE_QUALIFIERS:
        snaks = qualifiers.get(prop) if isinstance(qualifiers.get(prop), list) else []
        for snak in snaks:
            when, precision = _wikidata_exact_time(snak)
            best_precision = max(best_precision, precision)
            if when is not None:
                return when, prop, precision
    return None, "", best_precision


def extract_statement_events(entity: dict[str, Any], qid: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    claims = entity.get("claims") if isinstance(entity.get("claims"), dict) else {}
    dated: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    label_qids: set[str] = set()
    for event_type, prop in EVENT_PROPS.items():
        statements = claims.get(prop) if isinstance(claims.get(prop), list) else []
        for index, claim in enumerate(statements):
            if not isinstance(claim, dict):
                continue
            claim_qid = _item_qid(claim)
            if not claim_qid:
                continue
            label_qids.add(claim_qid)
            statement_id = str(claim.get("id") or f"{qid}:{prop}:{index}")
            when, date_prop, precision = statement_event_date(claim)
            base = {
                "personQid": qid,
                "claimQid": claim_qid,
                "statementId": statement_id,
                "eventType": event_type,
                "dateQualifier": date_prop,
                "datePrecision": precision,
            }
            if when is None:
                unresolved.append(base)
            else:
                dated.append({**base, "when": when})
    return dated, unresolved, label_qids


def canonical_event(record: dict[str, Any], statement: dict[str, Any], label: str, verified_at: datetime) -> dict[str, Any]:
    event_type = str(statement["eventType"])
    when = statement["when"]
    date_key = when.date().isoformat()
    claim_qid = str(statement["claimQid"])
    statement_id = str(statement["statementId"])
    event = {
        "eventKey": f"wikidata:{event_type}:{statement['personQid']}:{claim_qid}:{date_key}:{statement_id}",
        "eventId": claim_qid,
        "eventType": event_type,
        "provider": "Wikidata",
        "sourceUrl": f"https://www.wikidata.org/wiki/{claim_qid}",
        "name": f"{'Award' if event_type == 'award' else 'Nomination'}: {label}",
        "startedAt": iso(when),
        "eventOccurredAt": iso(when),
        "verifiedAt": iso(verified_at),
        "claimQid": claim_qid,
        "statementId": statement_id,
        "artist": record.get("name"),
        "timelineDateSource": f"Wikidata statement qualifier {statement['dateQualifier']}",
        "timelineDatePrecision": "day",
        "timelineStatus": "verified-event-date",
        "verified": True,
    }
    event["movePct"] = event_move_pct(record, event)
    return event


def _award_factor(events: list[dict[str, Any]]) -> float:
    factor = 1.0
    for event in events:
        if str(event.get("eventType") or "") not in {"award", "nomination"}:
            continue
        move = number(event.get("movePct"), 0.0)
        factor *= max(0.0001, 1.0 + move / 100.0)
    return factor


def _event_time(event: dict[str, Any]) -> str:
    return str(event.get("startedAt") or event.get("time") or event.get("date") or "")


def _recent_change(events: list[dict[str, Any]], now: datetime, hours: float) -> float:
    cutoff = now - timedelta(hours=hours)
    factor = 1.0
    for event in events:
        when = parse_time(event.get("startedAt"))
        if when is None or when < cutoff or when > now + timedelta(minutes=5):
            continue
        factor *= max(0.0001, 1.0 + number(event.get("movePct"), 0.0) / 100.0)
    return round((factor - 1.0) * 100.0, 2)


def repair_record(
    record: dict[str, Any],
    entity: dict[str, Any],
    labels: dict[str, str],
    now: datetime,
) -> tuple[dict[str, Any], dict[str, int]]:
    result = dict(record)
    qid = qid_for(result)
    if not qid:
        return result, {"corrected": 0, "unpriced": 0, "canonical": 0}

    dated, unresolved, _label_qids = extract_statement_events(entity, qid)
    existing = [dict(item) for item in result.get("priceEvents", []) if isinstance(item, dict)]
    old_awards = [event for event in existing if str(event.get("eventType") or "") in {"award", "nomination"}]
    non_awards = [event for event in existing if str(event.get("eventType") or "") not in {"award", "nomination"}]

    canonical = [
        canonical_event(result, item, labels.get(str(item["claimQid"]), str(item["claimQid"])), now)
        for item in dated
    ]
    canonical = [event for event in canonical if abs(number(event.get("movePct"))) >= 0.001]

    # Remove the pricing contribution of whatever award events were previously
    # embedded in today's market price, then apply only the canonical dated set.
    current = max(0.01, number(result.get("marketPrice"), 0.01))
    old_factor = _award_factor(old_awards)
    new_factor = _award_factor(canonical)
    base_without_awards = current / old_factor if old_factor > 0 else current
    desired_current = max(0.01, round(base_without_awards * new_factor, 2))

    merged = [*non_awards, *canonical]
    merged = [event for event in merged if _event_time(event)]
    merged.sort(key=_event_time)
    merged = merged[-MAX_PRICE_EVENTS:]
    # reconstruct_chain anchors the full event history to today's desired price,
    # which places each move at its real historical timestamp without treating
    # discovery day as a new market event.
    anchor_record = dict(result)
    anchor_record["marketPrice"] = desired_current
    rebuilt = reconstruct_chain(anchor_record, merged)

    prior_unpriced = [dict(item) for item in result.get("unpricedCareerEvidence", []) if isinstance(item, dict)]
    evidence_by_key = {str(item.get("evidenceKey") or ""): item for item in prior_unpriced if item.get("evidenceKey")}
    for item in unresolved:
        claim_qid = str(item["claimQid"])
        key = f"wikidata:{item['eventType']}:{qid}:{claim_qid}:{item['statementId']}"
        evidence_by_key[key] = {
            "evidenceKey": key,
            "eventType": item["eventType"],
            "claimQid": claim_qid,
            "statementId": item["statementId"],
            "name": f"{'Award' if item['eventType'] == 'award' else 'Nomination'}: {labels.get(claim_qid, claim_qid)}",
            "provider": "Wikidata",
            "sourceUrl": f"https://www.wikidata.org/wiki/{claim_qid}",
            "verifiedAt": iso(now),
            "timelineStatus": "date-unresolved-no-price-impact",
            "datePrecision": item.get("datePrecision", 0),
            "reason": "No exact day-qualified event date is available; TalentX does not invent a pricing timestamp.",
        }

    result["priceEvents"] = rebuilt
    result["priceHistory"] = history_from_events(rebuilt)
    result["priceHistoryStatus"] = "verified-real-event-timeline"
    result["marketPrice"] = desired_current
    result["dailyChange"] = _recent_change(rebuilt, now, 24.0)
    result["hourlyChangePct"] = _recent_change(rebuilt, now, 1.0)
    daily_factor = 1.0 + result["dailyChange"] / 100.0
    result["previousMarketPrice"] = round(desired_current / daily_factor, 2) if abs(daily_factor) > 0.0001 else desired_current
    result["trend"] = [number(event.get("priceAfter"), desired_current) for event in rebuilt[-18:]] or [desired_current]
    result["unpricedCareerEvidence"] = list(evidence_by_key.values())[-MAX_UNPRICED_EVIDENCE:]
    result["awardTimelineModel"] = MODEL_VERSION
    result["awardTimelineCheckedAt"] = iso(now)

    if rebuilt:
        latest = rebuilt[-1]
        result["lastPriceEventAt"] = latest.get("startedAt")
        result["lastPriceEvent"] = latest.get("name")
        result["lastPriceEventId"] = latest.get("eventKey")
        result["lastEventMovePct"] = latest.get("movePct")
        result["lastEventType"] = latest.get("eventType")
        result["lastEventSource"] = latest.get("provider")
        result["priceExplanation"] = explanation_for(latest, number(latest.get("movePct")), desired_current)

    corrected = sum(
        1 for event in old_awards
        if not event.get("timelineDateSource") or event.get("startedAt") not in {item.get("startedAt") for item in canonical}
    )
    return result, {"corrected": corrected, "unpriced": len(unresolved), "canonical": len(canonical)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]

    qid_to_indexes: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        if str(record.get("primaryCategory") or "") not in {"Music", "Actor"}:
            continue
        qid = qid_for(record)
        if qid:
            qid_to_indexes.setdefault(qid, []).append(index)
    if not qid_to_indexes:
        print("No Music/Actor Wikidata identities to repair.")
        return 0

    session = make_session()
    entities, errors = fetch_entities(session, sorted(qid_to_indexes), args.request_timeout)
    label_qids: set[str] = set()
    parsed_by_qid: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for qid, entity in entities.items():
        dated, unresolved, labels_needed = extract_statement_events(entity, qid)
        parsed_by_qid[qid] = (dated, unresolved)
        label_qids.update(labels_needed)
    labels, label_errors = fetch_labels(session, label_qids, args.request_timeout)
    errors.extend(label_errors)

    if errors and not args.allow_source_errors and not entities:
        raise RuntimeError("Award timeline sources failed: " + "; ".join(errors[-8:]))

    now = utc_now()
    corrected = unpriced = canonical = touched = 0
    for qid, indexes in qid_to_indexes.items():
        entity = entities.get(qid)
        if not isinstance(entity, dict):
            continue
        for index in indexes:
            repaired, stats = repair_record(records[index], entity, labels, now)
            records[index] = repaired
            corrected += stats["corrected"]
            unpriced += stats["unpriced"]
            canonical += stats["canonical"]
            touched += 1

    args.catalog.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"Award timeline repair: checked {touched:,} profiles; "
        f"canonical dated events {canonical:,}; corrected legacy events {corrected:,}; "
        f"undated claims held as non-pricing evidence {unpriced:,}; source warnings {len(errors):,}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
