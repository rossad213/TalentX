#!/usr/bin/env python3
"""Recover canonical live ESPN Soccer identities from the latest full baseline.

Sports market state can outlive a weekly full-catalog baseline. Older market
overlays may therefore carry legacy `cur-*` Soccer identities after the
publisher has already seen a cleaner `live-espn-*` record. This reconciler
restores the verified baseline identity while preserving the richest durable
market/event history from the older alias.

It is Soccer-only and does not alter other sports.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

MARKET_HISTORY_FIELDS = (
    "priceEvents",
    "priceHistory",
    "lastPriceEventAt",
    "lastPriceEvent",
    "lastPriceEventId",
    "lastEventMovePct",
    "lastEventType",
    "lastEventSource",
    "lastGameMovePct",
    "lastGamePerformanceDeltaPct",
    "lastGameStats",
    "priceHistoryStatus",
)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _is_soccer(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() in {"soccer", "football"}
    )


def _is_live_espn(record: dict[str, Any]) -> bool:
    return (
        _is_soccer(record)
        and str(record.get("sourceNamespace") or "").strip().lower() == "espn"
        and bool(str(record.get("sourceRecordId") or "").strip())
        and str(record.get("marketSegment") or "Current") == "Current"
    )


def _history_richness(record: dict[str, Any]) -> tuple[int, int, int]:
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    history = record.get("priceHistory") if isinstance(record.get("priceHistory"), list) else []
    return len(events), len(history), int(bool(record.get("lastPriceEventId")))


def reconcile_records(
    current: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    baseline_live = [dict(item) for item in baseline if _is_live_espn(item)]
    if not baseline_live:
        return [dict(item) for item in current], []

    current_records = [dict(item) for item in current]
    suppressed: set[str] = set()
    additions: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []

    by_id = {str(item.get("id") or ""): item for item in current_records if item.get("id")}
    by_source: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in current_records:
        if not _is_soccer(item):
            continue
        source_id = str(item.get("sourceRecordId") or "").strip()
        if source_id:
            by_source.setdefault(source_id, []).append(item)
        name_key = _norm(item.get("name"))
        if name_key:
            by_name.setdefault(name_key, []).append(item)

    for base in baseline_live:
        base_id = str(base.get("id") or "")
        source_id = str(base.get("sourceRecordId") or "").strip()
        name_key = _norm(base.get("name"))

        exact = by_id.get(base_id)
        if exact is not None:
            # The canonical live ID already exists. Leave its current evidence
            # untouched; only remove provable legacy aliases of the same person.
            candidates = [
                item for item in by_name.get(name_key, [])
                if item is not exact and (
                    str(item.get("sourceRecordId") or "").strip() == source_id
                    or str(item.get("sourceName") or "") == "TalentX current-first seed"
                    or str(item.get("id") or "").startswith("cur-")
                )
            ]
            for item in candidates:
                if item.get("id"):
                    suppressed.add(str(item["id"]))
                    repairs.append({
                        "name": str(base.get("name") or ""),
                        "canonicalId": base_id,
                        "suppressedId": str(item.get("id") or ""),
                        "reason": "canonical live ESPN identity already present",
                    })
            continue

        candidates = list(by_source.get(source_id, [])) if source_id else []
        if not candidates:
            candidates = [
                item for item in by_name.get(name_key, [])
                if (
                    str(item.get("sourceName") or "") == "TalentX current-first seed"
                    or str(item.get("id") or "").startswith("cur-")
                )
            ]
        if not candidates:
            additions.append(base)
            repairs.append({
                "name": str(base.get("name") or ""),
                "canonicalId": base_id,
                "suppressedId": "",
                "reason": "restored missing verified ESPN baseline identity",
            })
            continue

        richest = max(candidates, key=_history_richness)
        canonical = dict(base)
        for field in MARKET_HISTORY_FIELDS:
            if field in richest:
                canonical[field] = richest[field]
        aliases = sorted({
            str(item.get("id") or "") for item in candidates
            if str(item.get("id") or "")
        })
        canonical["soccerCanonicalAliasIds"] = aliases
        canonical["soccerIdentityReconciled"] = True
        additions.append(canonical)

        for item in candidates:
            record_id = str(item.get("id") or "")
            if record_id:
                suppressed.add(record_id)
        repairs.append({
            "name": str(base.get("name") or ""),
            "canonicalId": base_id,
            "suppressedId": ",".join(aliases),
            "reason": "recovered verified baseline identity and retained richest event ledger",
        })

    output = [
        item for item in current_records
        if str(item.get("id") or "") not in suppressed
    ]
    present = {str(item.get("id") or "") for item in output}
    for item in additions:
        if str(item.get("id") or "") not in present:
            output.append(item)
            present.add(str(item.get("id") or ""))
    return output, repairs


def _load(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
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
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()

    current = _load(args.catalog)
    baseline = _load(args.baseline) if args.baseline.exists() else []
    reconciled, repairs = reconcile_records(current, baseline)
    args.catalog.write_text(json.dumps(reconciled, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.catalog.name == "current_catalog.json":
        _write_csv(args.catalog.with_suffix(".csv"), reconciled)

    print(f"Soccer identity reconciliation: {len(current):,} -> {len(reconciled):,}; {len(repairs):,} repair(s).")
    for repair in repairs[:20]:
        print(
            f"  {repair['name']}: {repair['reason']} "
            f"(canonical={repair['canonicalId']}, suppressed={repair['suppressedId'] or 'none'})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
