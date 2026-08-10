#!/usr/bin/env python3
"""Build verified one-year ATP/WTA Tennis price-event history.

The collector uses ESPN Tennis scoreboards in bounded date windows. Completed
singles matches are matched to TalentX Tennis records by normalized full name and
priced with the same result/round/major model used by the live Tennis refresh.
Only historical chart evidence is changed; today's market price and current move
remain authoritative in the Sports market artifact.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from merge_historical_event_overlay import history_from_events, reconstruct_chain  # noqa: E402
from tennis_event_refresh import (  # noqa: E402
    MAX_EVENTS_PER_RECORD,
    apply_live_matches,
    build_name_index,
    discover_matches,
    event_for_record,
    existing_event_keys,
    iso_utc,
    load_records,
    matched_competitors,
    norm,
    number,
    session,
    utc_now,
)


def verified_historical_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(event)
        for event in record.get("priceEvents", [])
        if isinstance(event, dict)
        and event.get("historicalBackfill") is True
        and event.get("verified") is not False
        and str(event.get("eventType") or "game") == "game"
        and abs(float(event.get("movePct") or 0)) >= 0.001
    ]


def collect_year_matches(days: int, timeout: float, chunk_days: int) -> tuple[list[dict[str, Any]], list[str]]:
    now = utc_now()
    start = now - timedelta(days=max(1, days))
    cursor = start
    found: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    http = session()
    while cursor <= now:
        chunk_end = min(now, cursor + timedelta(days=max(1, chunk_days) - 1))
        matches, chunk_warnings = discover_matches(cursor, chunk_end, timeout=timeout, http=http)
        for match in matches:
            key = str(match.get("matchKey") or "")
            if key:
                found[key] = match
        warnings.extend(chunk_warnings)
        cursor = chunk_end + timedelta(days=1)
    matches = sorted(found.values(), key=lambda item: str(item.get("startedAt") or ""))
    return matches, warnings


def apply_history(
    records: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    *,
    days: int,
    max_move_pct: float,
) -> tuple[list[dict[str, Any]], int, int]:
    updated = [dict(record) for record in records]
    rows = matched_competitors(updated, matches)
    grouped: dict[int, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]]] = {}
    for index, match, competitor, opponent_record in rows:
        grouped.setdefault(index, []).append((match, competitor, opponent_record))

    touched = 0
    added_total = 0
    for index, entries in grouped.items():
        record = updated[index]
        if str(record.get("discipline") or "") != "Tennis":
            continue
        existing = [dict(event) for event in record.get("priceEvents", []) if isinstance(event, dict)]
        known = existing_event_keys(record)
        generated: list[dict[str, Any]] = []
        for match, competitor, opponent_record in sorted(entries, key=lambda item: str(item[0].get("startedAt") or "")):
            event = event_for_record(
                record,
                match,
                competitor,
                opponent_record,
                historical_backfill=True,
                max_move_pct=max_move_pct,
            )
            key = str(event.get("eventKey") or "")
            if not key or key in known:
                continue
            generated.append(event)
            known.add(key)
        if not generated:
            continue
        combined = [*existing, *generated]
        combined = [event for event in combined if str(event.get("startedAt") or "")]
        combined.sort(key=lambda event: str(event.get("startedAt") or ""))
        combined = combined[-MAX_EVENTS_PER_RECORD:]
        current_price = max(0.01, float(record.get("marketPrice") or 0.01))
        rebuilt = reconstruct_chain(current_price, combined)
        result = dict(record)
        result["priceEvents"] = rebuilt
        result["priceHistory"] = history_from_events(rebuilt)
        result["priceHistoryStatus"] = "verified-event-backfill"
        result["priceHistoryBackfilledAt"] = iso_utc(utc_now())
        result["priceHistoryBackfillDays"] = max(int(number(result.get("priceHistoryBackfillDays"), 0)), days)
        result["priceHistoryBackfillModel"] = "tennis-espn-scoreboard-v1"
        result["tennisVerifiedMatchEvents"] = sum(
            1 for event in rebuilt
            if isinstance(event, dict)
            and str(event.get("sport") or "").lower() == "tennis"
            and event.get("verified") is not False
            and abs(float(event.get("movePct") or 0)) >= 0.001
        )
        updated[index] = result
        touched += 1
        added_total += len(generated)
    return updated, touched, added_total


def ranked_gate(records: list[dict[str, Any]], top_n: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked = [
        record for record in records
        if str(record.get("discipline") or "") == "Tennis"
        and (number(record.get("sourceRank"), 0) > 0 or number(record.get("rosterSourceRank"), 0) > 0)
    ]
    ranked.sort(key=lambda record: (
        int(number(record.get("sourceRank") or record.get("rosterSourceRank"), 999999)),
        -float(record.get("pricingConfidence") or 0),
        str(record.get("name") or ""),
    ))
    selected = ranked[: max(0, top_n)]
    covered = [record for record in selected if verified_historical_events(record)]
    return selected, covered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--chunk-days", type=int, default=28)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    parser.add_argument("--minimum-total-covered", type=int, default=50)
    parser.add_argument("--ranked-gate-size", type=int, default=20)
    parser.add_argument("--minimum-ranked-covered", type=int, default=12)
    args = parser.parse_args()

    records = load_records(args.catalog)
    tennis = [record for record in records if str(record.get("primaryCategory") or "") == "Athlete" and str(record.get("discipline") or "") == "Tennis"]
    if not tennis:
        raise SystemExit("No Tennis records found in Sports catalog")
    print(f"Tennis records available: {len(tennis):,}")

    matches, warnings = collect_year_matches(args.days, args.request_timeout, args.chunk_days)
    print(f"Verified completed ATP/WTA singles matches discovered across {args.days} days: {len(matches):,}")
    if warnings:
        print(f"Tennis scoreboard warnings: {len(warnings):,}")
        for warning in warnings[:12]:
            print(f"WARNING {warning}")
    if not matches:
        raise SystemExit("Tennis scoreboard history collector found zero completed singles matches")

    updated, touched, added = apply_history(records, matches, days=args.days, max_move_pct=args.max_game_move_pct)
    tennis_updated = [record for record in updated if str(record.get("primaryCategory") or "") == "Athlete" and str(record.get("discipline") or "") == "Tennis"]
    covered = [record for record in tennis_updated if verified_historical_events(record)]
    ranked, ranked_covered = ranked_gate(tennis_updated, args.ranked_gate_size)

    print(f"Tennis chart coverage: {len(covered):,}/{len(tennis_updated):,} records with verified match history.")
    print(f"Ranked gate coverage: {len(ranked_covered):,}/{len(ranked):,} selected top-ranked records.")
    print(f"This pass added {added:,} verified Tennis match events to {touched:,} records.")
    if ranked:
        print("Top-ranked examples: " + ", ".join(
            f"{record.get('name')}={'yes' if verified_historical_events(record) else 'NO'}" for record in ranked[:12]
        ))

    if len(covered) < min(args.minimum_total_covered, len(tennis_updated)):
        raise SystemExit(
            f"Tennis history quality gate failed: {len(covered)} covered; minimum is "
            f"{min(args.minimum_total_covered, len(tennis_updated))}."
        )
    required_ranked = min(args.minimum_ranked_covered, len(ranked))
    if len(ranked_covered) < required_ranked:
        missing = [str(record.get("name") or "") for record in ranked if not verified_historical_events(record)]
        raise SystemExit(
            f"Tennis ranked quality gate failed: {len(ranked_covered)}/{len(ranked)} covered; "
            f"minimum is {required_ranked}. Missing: {', '.join(missing[:12])}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(tennis_updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
