#!/usr/bin/env python3
"""Helpers for conservative same-category identity deduplication."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


CURATED_NAMESPACE_HINTS = (
    "curated",
    "editorial",
)


def normalize_identity_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _score(record: dict[str, Any]) -> tuple[int, float, float, int]:
    namespace = str(record.get("sourceNamespace") or "").lower()
    curated = int(any(hint in namespace for hint in CURATED_NAMESPACE_HINTS))
    data_confidence = float(record.get("dataConfidence") or 0)
    pricing_confidence = float(record.get("pricingConfidence") or 0)
    evidence_count = len(record.get("pricingEvidence") or []) if isinstance(record.get("pricingEvidence"), list) else 0
    return curated, data_confidence, pricing_confidence, evidence_count


def dedupe_same_category_identities(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Collapse exact normalized-name duplicates inside a single category.

    This is deliberately conservative: it only deduplicates records whose normalized
    names and primary categories are identical. Curated/editorial records win over
    source-discovered records; otherwise the strongest confidence/evidence record wins.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        category = str(record.get("primaryCategory") or "")
        name_key = normalize_identity_name(record.get("name"))
        if category and name_key:
            groups.setdefault((category, name_key), []).append(record)

    suppressed: set[str] = set()
    repairs: list[dict[str, str]] = []

    for (category, name_key), group in groups.items():
        if len(group) < 2:
            continue
        winner = max(group, key=_score)
        winner_id = str(winner.get("id") or "")
        for record in group:
            if record is winner:
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
