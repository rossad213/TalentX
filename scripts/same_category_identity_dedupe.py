#!/usr/bin/env python3
"""Conservative same-category identity deduplication for TalentX catalogs."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


CURATED_NAMESPACE_HINTS = (
    "curated",
    "editorial",
)


def normalize_identity_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _is_curated(record: dict[str, Any]) -> bool:
    namespace = str(record.get("sourceNamespace") or "").lower()
    source_name = str(record.get("sourceName") or "")
    return (
        any(hint in namespace for hint in CURATED_NAMESPACE_HINTS)
        or source_name == "TalentX current-first seed"
    )


def _is_verified_live_soccer(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() in {"soccer", "football"}
        and str(record.get("sourceNamespace") or "").strip().lower() == "espn"
        and bool(str(record.get("sourceRecordId") or "").strip())
    )


def _score(record: dict[str, Any]) -> tuple[Any, ...]:
    curated = int(_is_curated(record))
    data_confidence = float(record.get("dataConfidence") or 0)
    pricing_confidence = float(record.get("pricingConfidence") or 0)
    evidence_count = len(record.get("pricingEvidence") or []) if isinstance(record.get("pricingEvidence"), list) else 0

    if str(record.get("primaryCategory") or "") == "Athlete" and str(record.get("discipline") or "").strip().lower() in {"soccer", "football"}:
        verified_live = int(_is_verified_live_soccer(record))
        canonical_live_id = int(str(record.get("id") or "").startswith("live-espn-soccer-"))
        events = len(record.get("priceEvents") or []) if isinstance(record.get("priceEvents"), list) else 0
        history = len(record.get("priceHistory") or []) if isinstance(record.get("priceHistory"), list) else 0
        return verified_live, canonical_live_id, events + history, data_confidence, pricing_confidence, evidence_count

    return curated, data_confidence, pricing_confidence, evidence_count


def _safe_duplicate_group(group: list[dict[str, Any]]) -> bool:
    """Return True only when there is evidence that same-name records are one identity.

    Exact names alone are not enough because two real people can share a name. We
    collapse a group only when a curated/editorial record overlaps source-discovered
    records, or when multiple records explicitly share the same sourceRecordId.
    """
    curated_count = sum(1 for record in group if _is_curated(record))
    if curated_count == 1 and any(not _is_curated(record) for record in group):
        return True

    source_ids = [str(record.get("sourceRecordId") or "").strip() for record in group]
    nonempty = [value for value in source_ids if value]
    return bool(nonempty) and len(nonempty) != len(set(nonempty))


def dedupe_same_category_identities(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Collapse safely provable duplicate identities inside one category."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        category = str(record.get("primaryCategory") or "")
        name_key = normalize_identity_name(record.get("name"))
        if category and name_key:
            groups[(category, name_key)].append(record)

    suppressed: set[str] = set()
    repairs: list[dict[str, str]] = []

    for (category, name_key), group in groups.items():
        if len(group) < 2 or not _safe_duplicate_group(group):
            continue

        winner = max(group, key=_score)
        winner_id = str(winner.get("id") or "")
        winner_source_id = str(winner.get("sourceRecordId") or "").strip()
        curated_overlap = (
            sum(1 for item in group if _is_curated(item)) == 1
            and any(not _is_curated(item) for item in group)
        )

        for record in group:
            if record is winner:
                continue

            # Curated/current-first seed vs discovered source is already a safe
            # identity proof regardless of which side wins. Otherwise require an
            # explicit shared provider identity before suppressing a same-name row.
            if not curated_overlap:
                record_source_id = str(record.get("sourceRecordId") or "").strip()
                if not winner_source_id or record_source_id != winner_source_id:
                    continue

            record_id = str(record.get("id") or "")
            if record_id:
                suppressed.add(record_id)
            repairs.append({
                "identityKey": name_key,
                "name": str(winner.get("name") or record.get("name") or ""),
                "category": category,
                "primaryId": winner_id,
                "suppressedId": record_id,
                "suppressedSourceNamespace": str(record.get("sourceNamespace") or ""),
            })

    return [record for record in records if str(record.get("id") or "") not in suppressed], repairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--repairs", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    deduped, repairs = dedupe_same_category_identities(records)
    args.catalog.write_text(json.dumps(deduped, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    if args.repairs:
        args.repairs.parent.mkdir(parents=True, exist_ok=True)
        args.repairs.write_text(json.dumps(repairs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Same-category identity dedupe: {len(records):,} -> {len(deduped):,}; suppressed {len(repairs):,} duplicate listings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
