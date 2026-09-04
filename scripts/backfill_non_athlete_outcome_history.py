#!/usr/bin/env python3
"""Strictly verify historical Music/Actor event dates and remove inferred outcomes.

Historical charts may contain source-backed releases/projects, but they must not
place chart or box-office outcomes on guessed dates. Earlier code used release
+7 days and release +21 days for some outcome events; this verifier removes those
from historical chart history unless a future adapter supplies an independently
verified outcome date.

For release/project history, Wikidata must confirm:
  * the work is related to the TalentX person (performer or cast member), and
  * P577 contains a day-precision publication/release date matching startedAt.
Music releases also keep their MusicBrainz release-group source.

Today's TalentX market price is never changed by this script.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from backfill_non_athlete_event_history import history_from_events, reconstruct_chain
from non_athlete_event_refresh import iso, qid_for, utc_now

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
INFERRED_HISTORICAL_OUTCOMES = {"music-chart-outcome", "actor-box-office-outcome"}


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


def entity_claims(entity: dict[str, Any], prop: str) -> list[dict[str, Any]]:
    claims = entity.get("claims") if isinstance(entity.get("claims"), dict) else {}
    rows = claims.get(prop) if isinstance(claims.get(prop), list) else []
    return [row for row in rows if isinstance(row, dict)]


def claim_item_qids(entity: dict[str, Any], prop: str) -> set[str]:
    output: set[str] = set()
    for claim in entity_claims(entity, prop):
        snak = claim.get("mainsnak") if isinstance(claim.get("mainsnak"), dict) else {}
        value = ((snak.get("datavalue") or {}).get("value")) if isinstance(snak.get("datavalue"), dict) else None
        if isinstance(value, dict):
            qid = str(value.get("id") or "")
            if re.fullmatch(r"Q\d+", qid):
                output.add(qid)
    return output


def exact_release_dates(entity: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    for claim in entity_claims(entity, "P577"):
        snak = claim.get("mainsnak") if isinstance(claim.get("mainsnak"), dict) else {}
        data = snak.get("datavalue") if isinstance(snak.get("datavalue"), dict) else {}
        value = data.get("value") if isinstance(data.get("value"), dict) else {}
        precision = int(value.get("precision") or 0) if str(value.get("precision") or "").isdigit() else 0
        time_value = str(value.get("time") or "")
        if precision < 11 or not time_value:
            continue
        match = re.match(r"^[+-]?(\d{4})-(\d{2})-(\d{2})T", time_value)
        if match:
            output.add(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
    return output


def fetch_entities(session: requests.Session, qids: set[str], timeout: float) -> tuple[dict[str, dict[str, Any]], list[str]]:
    output: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    ordered = sorted(qids)
    for offset in range(0, len(ordered), 45):
        batch = ordered[offset:offset + 45]
        try:
            response = session.get(
                WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "claims",
                    "format": "json",
                    "formatversion": 2,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            entities = response.json().get("entities", {})
            if isinstance(entities, dict):
                for qid, entity in entities.items():
                    if isinstance(entity, dict) and entity.get("missing") is None:
                        output[str(qid)] = entity
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Wikidata entity batch {offset // 45 + 1}: {type(exc).__name__}: {exc}")
    return output, warnings


def release_verified(record: dict[str, Any], event: dict[str, Any], entity: dict[str, Any], category: str) -> tuple[bool, str]:
    started = parse_time(event.get("startedAt"))
    if started is None:
        return False, "invalid-startedAt"
    date_key = started.date().isoformat()
    if date_key not in exact_release_dates(entity):
        return False, "no-matching-day-precision-P577"

    person_qid = qid_for(record)
    if not person_qid:
        return False, "record-has-no-wikidata-identity"
    relation = "P175" if category == "Music" else "P161"
    if person_qid not in claim_item_qids(entity, relation):
        return False, f"person-not-confirmed-in-{relation}"

    if category == "Music":
        event_key = str(event.get("eventKey") or "")
        source_url = str(event.get("sourceUrl") or "")
        if not event_key.startswith("musicbrainz:") or not source_url.startswith("https://musicbrainz.org/release-group/"):
            return False, "musicbrainz-source-missing"

    return True, "exact-source-date-and-person-relation"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--category", choices=["Music", "Actor"], required=True)
    parser.add_argument("--days", type=int, default=1825, help="Retained for workflow compatibility")
    parser.add_argument("--request-timeout", type=float, default=15.0)
    args = parser.parse_args()

    records = load_catalog(args.catalog)
    work_qids: set[str] = set()
    for record in records:
        if str(record.get("primaryCategory") or "") != args.category:
            continue
        for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
            if not isinstance(event, dict) or event.get("historicalBackfill") is not True:
                continue
            if str(event.get("eventType") or "") not in {"music-release", "actor-release"}:
                continue
            qid = str(event.get("workQid") or "")
            if re.fullmatch(r"Q\d+", qid):
                work_qids.add(qid)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "TalentX-History-Date-Verifier/2.0 (+https://github.com/rossad213/TalentX)",
        "Accept": "application/json",
    })
    entities, warnings = fetch_entities(session, work_qids, args.request_timeout)
    for warning in warnings[:20]:
        print(f"WARNING {warning}")

    removed_inferred = 0
    removed_release = 0
    retained_release = 0
    touched = 0
    now = utc_now()
    output: list[dict[str, Any]] = []

    wanted_release = "music-release" if args.category == "Music" else "actor-release"
    for record in records:
        result = dict(record)
        if str(result.get("primaryCategory") or "") != args.category:
            output.append(result)
            continue

        prior = [dict(event) for event in result.get("priceEvents", []) if isinstance(event, dict)] if isinstance(result.get("priceEvents"), list) else []
        kept: list[dict[str, Any]] = []
        changed = False
        for event in prior:
            if event.get("historicalBackfill") is not True:
                kept.append(event)
                continue
            event_type = str(event.get("eventType") or "")
            if event_type in INFERRED_HISTORICAL_OUTCOMES:
                # No true historical outcome date is present in the source data.
                # Keep the factual outcome available to other evidence layers, but
                # do not put an assumed date on the price chart.
                removed_inferred += 1
                changed = True
                continue
            if event_type != wanted_release:
                kept.append(event)
                continue

            work_qid = str(event.get("workQid") or "")
            entity = entities.get(work_qid, {})
            ok, reason = release_verified(result, event, entity, args.category) if entity else (False, "work-entity-unavailable")
            if not ok:
                removed_release += 1
                changed = True
                print(f"DROP {args.category} {result.get('name')} / {event.get('name')}: {reason}")
                continue

            verified = dict(event)
            verified["dateVerified"] = True
            verified["datePrecision"] = "day"
            verified["dateEvidenceProvider"] = "Wikidata P577"
            verified["eventEvidenceStatus"] = "source-backed"
            verified["priceBasis"] = "talentx-simulated-event-backfill"
            kept.append(verified)
            retained_release += 1

        if changed or any(event.get("historicalBackfill") is True for event in kept):
            kept.sort(key=lambda event: str(event.get("startedAt") or ""))
            rebuilt = reconstruct_chain(result, kept)
            result["priceEvents"] = rebuilt
            result["priceHistory"] = history_from_events(rebuilt)
            result["priceHistoryStatus"] = "source-backed-exact-date-backfill"
            result["priceHistoryBackfilledAt"] = iso(now)
            result["priceHistoryBackfillModel"] = "strict-exact-date-history-v2"
            result["priceHistoryDisclosure"] = "Historical events/dates are source-backed. Backfilled TalentX prices are simulated model responses, not historical securities prices."
            touched += 1
        output.append(result)

    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"Strict {args.category} history verification: retained {retained_release:,} exact-date releases; "
        f"removed {removed_release:,} unverified/approximate releases and {removed_inferred:,} inferred-date outcomes across {touched:,} profiles."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
