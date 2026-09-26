#!/usr/bin/env python3
"""Collapse duplicate ATP/WTA TalentX listings into one canonical player identity.

Tennis currently has two curated ingestion paths for some stars: a current
official ranking roster and an older current-first benchmark row. When both rows
share the same normalized player name and the same ATP/WTA tour, they represent
one player. This reconciler keeps the stronger current metric profile, merges all
verified match/history evidence, and carries the official ranking onto the
canonical row before the Tennis market epoch is recalculated.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def is_tennis(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() == "tennis"
        and str(record.get("leagueOrMedium") or "").strip().upper() in {"ATP", "WTA"}
    )


def rank_value(record: dict[str, Any]) -> int | None:
    for key in ("sourceRank", "rosterSourceRank"):
        try:
            value = int(float(record.get(key)))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _canonical_score(record: dict[str, Any]) -> tuple[Any, ...]:
    """Prefer the stronger valuation evidence, then the richer verified ledger."""
    talent = _num(record.get("talentScore"))
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    metric_strength = sum(_num(metrics.get(key)) for key in (
        "performance", "achievements", "consistency", "potential", "availability", "audience"
    ))
    events = len(record.get("priceEvents") or []) if isinstance(record.get("priceEvents"), list) else 0
    history = len(record.get("priceHistory") or []) if isinstance(record.get("priceHistory"), list) else 0
    confidence = _num(record.get("pricingConfidence", record.get("dataConfidence", 0)))
    official = int(str(record.get("sourceNamespace") or "") == "curated-individual-sport-roster")
    return talent, metric_strength, events + history, confidence, official


def _event_key(event: dict[str, Any]) -> tuple[str, str]:
    event_id = str(event.get("eventId") or event.get("competitionId") or "").strip()
    if event_id:
        return ("provider-event", event_id)
    return (
        str(event.get("eventKey") or "").strip(),
        str(event.get("startedAt") or event.get("time") or "").strip(),
    )


def _history_key(point: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(point.get("time") or point.get("date") or ""),
        str(point.get("eventId") or point.get("eventKey") or ""),
        str(point.get("phase") or ""),
        str(point.get("source") or ""),
    )


def _merge_dict_lists(
    group: list[dict[str, Any]],
    field: str,
    key_fn,
    canonical: dict[str, Any],
) -> list[dict[str, Any]]:
    merged: dict[Any, dict[str, Any]] = {}
    # Canonical row is processed last so its recorded live version wins a collision.
    ordered = [item for item in group if item is not canonical] + [canonical]
    for record in ordered:
        values = record.get(field) if isinstance(record.get(field), list) else []
        for value in values:
            if not isinstance(value, dict):
                continue
            key = key_fn(value)
            if not any(str(part) for part in key):
                continue
            merged[key] = dict(value)
    return sorted(
        merged.values(),
        key=lambda item: str(item.get("startedAt") or item.get("time") or item.get("date") or ""),
    )


def reconcile_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not is_tennis(record):
            continue
        name_key = normalize_name(record.get("name"))
        tour = str(record.get("leagueOrMedium") or "").strip().upper()
        if name_key:
            groups[(tour, name_key)].append(record)

    replacements: dict[str, dict[str, Any]] = {}
    suppressed: set[str] = set()
    repairs: list[dict[str, Any]] = []

    for (tour, _), group in groups.items():
        if len(group) < 2:
            continue
        ranks = [rank_value(record) for record in group]
        known_ranks = [rank for rank in ranks if rank is not None]
        # Do not collapse same-name rows unless current ranking metadata proves
        # these are two curated paths for the same tour identity.
        if not known_ranks or len(set(known_ranks)) != 1:
            continue

        canonical = max(group, key=_canonical_score)
        canonical_id = str(canonical.get("id") or "")
        if not canonical_id:
            continue
        official_rank = known_ranks[0]

        result = dict(canonical)
        result["priceEvents"] = _merge_dict_lists(group, "priceEvents", _event_key, canonical)
        result["priceHistory"] = _merge_dict_lists(group, "priceHistory", _history_key, canonical)
        result["sourceRank"] = official_rank
        result["rosterSourceRank"] = official_rank

        official_rows = [
            record for record in group
            if str(record.get("sourceNamespace") or "") == "curated-individual-sport-roster"
        ]
        if official_rows:
            official = max(official_rows, key=lambda item: len(item.get("priceEvents") or []))
            for source_key in (
                "sourceName", "sourceUrl", "statusSource", "sourceAsOf",
                "rosterSourceName", "rosterSourceUrl", "rosterSourceAsOf",
                "rosterVersion", "rosterGroup", "rosterPriority",
            ):
                if official.get(source_key) is not None:
                    result[source_key] = official.get(source_key)

        aliases = sorted(
            str(item.get("id") or "")
            for item in group
            if str(item.get("id") or "") and str(item.get("id") or "") != canonical_id
        )
        result["tennisIdentityReconciled"] = True
        result["tennisCanonicalAliasIds"] = aliases
        result["tennisCanonicalSourceRank"] = official_rank
        result["tennisCanonicalTour"] = tour
        replacements[canonical_id] = result

        for item in group:
            record_id = str(item.get("id") or "")
            if record_id and record_id != canonical_id:
                suppressed.add(record_id)
                repairs.append({
                    "name": str(result.get("name") or ""),
                    "tour": tour,
                    "rank": official_rank,
                    "canonicalId": canonical_id,
                    "suppressedId": record_id,
                    "reason": "same normalized Tennis name, same tour, same official ATP/WTA rank",
                })

    output: list[dict[str, Any]] = []
    for record in records:
        record_id = str(record.get("id") or "")
        if record_id in suppressed:
            continue
        output.append(replacements.get(record_id, dict(record)))
    return output, repairs


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = sorted({
        key for record in records for key, value in record.items()
        if not isinstance(value, (dict, list))
    })
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    reconciled, repairs = reconcile_records(records)
    args.catalog.write_text(json.dumps(reconciled, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.catalog.name == "current_catalog.json":
        write_csv(args.catalog.with_suffix(".csv"), reconciled)

    print(
        f"Tennis identity reconciliation: {len(records):,} -> {len(reconciled):,}; "
        f"suppressed {len(repairs):,} duplicate listing(s)."
    )
    for repair in repairs[:40]:
        print(
            f"  {repair['tour']} · {repair['name']} (rank {repair['rank']}): "
            f"{repair['suppressedId']} -> {repair['canonicalId']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
