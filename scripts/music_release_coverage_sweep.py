#!/usr/bin/env python3
"""Stateful verified Music release coverage for TalentX.

The primary non-athlete event refresh intentionally uses bounded source calls.
This sweep prevents those bounds from repeatedly checking the same newest
release candidates forever. It remembers candidate attempts, prioritizes work
that has never been checked, retries unmatched candidates after a cooling-off
period, and applies only releases confirmed by both Wikidata and MusicBrainz.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from non_athlete_event_refresh import (
    apply_events,
    claim_strings,
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

MAX_ATTEMPTS_SAVED = 30000


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


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def candidate_key(qid: str, candidate: dict[str, Any]) -> str:
    date = candidate.get("date")
    date_key = date.date().isoformat() if isinstance(date, datetime) else str(date or "")[:10]
    return f"{qid}:{candidate.get('workQid') or ''}:{date_key}"


def already_has_work(record: dict[str, Any], candidate: dict[str, Any]) -> bool:
    work_qid = str(candidate.get("workQid") or "")
    date = candidate.get("date")
    date_key = date.date().isoformat() if isinstance(date, datetime) else str(date or "")[:10]
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or str(event.get("eventType") or "") != "music-release":
            continue
        if work_qid and str(event.get("workQid") or "") == work_qid:
            return True
        started = str(event.get("startedAt") or "")[:10]
        if date_key and started == date_key and str(event.get("artist") or "") == str(record.get("name") or ""):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--wikidata-batch-size", type=int, default=80)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--max-checks", type=int, default=700)
    parser.add_argument("--retry-unmatched-days", type=int, default=7)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    records = load_json(args.catalog, [])
    if not isinstance(records, list) or not records:
        raise SystemExit(f"{args.catalog} must contain a non-empty array")
    records = [dict(item) for item in records if isinstance(item, dict)]
    manifest = load_json(args.manifest, {})
    manifest = manifest if isinstance(manifest, dict) else {}
    attempts = manifest.get("attempts") if isinstance(manifest.get("attempts"), dict) else {}
    attempts = {str(key): dict(value) for key, value in attempts.items() if isinstance(value, dict)}

    now = utc_now()
    start = now - timedelta(days=max(1, args.lookback_days))
    qid_to_indexes: dict[str, list[int]] = {}
    qids: list[str] = []
    for index, record in enumerate(records):
        if str(record.get("primaryCategory") or "") != "Music":
            continue
        qid = qid_for(record)
        if not qid:
            continue
        qid_to_indexes.setdefault(qid, []).append(index)
        qids.append(qid)
    qids = sorted(set(qids))

    session = make_session()
    entities, errors = fetch_entities(session, qids, args.request_timeout)
    works, work_errors = fetch_works(
        session,
        "Music",
        qids,
        start,
        now,
        max(1, args.wikidata_batch_size),
        args.request_timeout,
    )
    errors.extend(work_errors)

    mbids: dict[str, str] = {}
    for qid in qids:
        index = qid_to_indexes[qid][0]
        record = records[index]
        mbid = existing_mbid(record)
        if not mbid:
            values = claim_strings(entities.get(qid, {}), "P434")
            value = str(values[0]).lower() if values else ""
            if re.fullmatch(r"[0-9a-fA-F-]{36}", value):
                mbid = value
        if mbid:
            mbids[qid] = mbid

    candidates: list[tuple[bool, datetime, str, dict[str, Any]]] = []
    retry_after = timedelta(days=max(1, args.retry_unmatched_days))
    for qid, items in works.items():
        if qid not in mbids:
            continue
        representative = records[qid_to_indexes[qid][0]]
        for candidate in items:
            if already_has_work(representative, candidate):
                continue
            key = candidate_key(qid, candidate)
            prior = attempts.get(key, {})
            if prior.get("matched") is True:
                continue
            checked = parse_time(prior.get("checkedAt"))
            if checked is not None and now - checked < retry_after:
                continue
            never_checked = checked is None
            when = candidate.get("date") if isinstance(candidate.get("date"), datetime) else start
            candidates.append((never_checked, when, qid, candidate))

    # Never-checked candidates first. Within each group, newest releases first so
    # current market activity is caught quickly without starving older backlog.
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

    checks = 0
    matches = 0
    changed_records = 0
    events_applied = 0
    last_request = 0.0
    for _never_checked, _when, qid, candidate in candidates:
        if checks >= max(0, args.max_checks):
            break
        elapsed = time.time() - last_request
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        key = candidate_key(qid, candidate)
        try:
            match = musicbrainz_release_match(session, mbids[qid], candidate, args.request_timeout)
            last_request = time.time()
        except Exception as exc:  # noqa: BLE001
            last_request = time.time()
            attempts[key] = {
                "checkedAt": iso(now),
                "matched": False,
                "sourceError": f"{type(exc).__name__}: {exc}",
            }
            errors.append(f"MusicBrainz coverage {key}: {type(exc).__name__}: {exc}")
            checks += 1
            continue

        checks += 1
        attempts[key] = {"checkedAt": iso(now), "matched": bool(match)}
        if not match:
            continue
        matches += 1
        for index in qid_to_indexes.get(qid, []):
            event = release_event(records[index], candidate, match)
            updated, added = apply_events(records[index], [event])
            if added:
                records[index] = updated
                changed_records += 1
                events_applied += added
                attempts[key]["eventKey"] = event.get("eventKey")

    if errors and not args.allow_source_errors and checks == 0:
        raise RuntimeError("Music coverage sources failed before usable checks: " + "; ".join(errors[-8:]))

    # Keep newest attempt metadata and discard stale overflow deterministically.
    ordered_attempts = sorted(
        attempts.items(),
        key=lambda item: str(item[1].get("checkedAt") or ""),
    )[-MAX_ATTEMPTS_SAVED:]
    manifest_out = {
        "version": "1.0-stateful-music-release-coverage",
        "generatedAt": iso(now),
        "lookbackDays": args.lookback_days,
        "eligibleMusicProfiles": len(qids),
        "profilesWithMusicBrainzIdentity": len(mbids),
        "uncheckedOrRetryableCandidates": len(candidates),
        "checksAttempted": checks,
        "matchesConfirmed": matches,
        "recordsChanged": changed_records,
        "eventsApplied": events_applied,
        "retryUnmatchedDays": args.retry_unmatched_days,
        "attempts": dict(ordered_attempts),
        "sourceErrorCount": len(errors),
        "sourceErrors": errors[-100:],
    }
    args.catalog.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    args.manifest.write_text(json.dumps(manifest_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Music release coverage: {checks:,} candidates checked, {matches:,} confirmed, "
        f"{events_applied:,} new verified price events across {changed_records:,} records; "
        f"{len(candidates):,} candidates were eligible this run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
