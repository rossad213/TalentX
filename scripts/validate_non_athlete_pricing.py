#!/usr/bin/env python3
"""Validate obvious reviewed-vs-generic non-athlete pricing relationships."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DEFAULT_RULES = ROOT / "data" / "pricing_sanity_pairs.json"


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def validate(records: list[dict[str, Any]], rules: dict[str, Any]) -> tuple[list[str], list[str]]:
    by_key = {
        (str(record.get("primaryCategory") or ""), normalize(record.get("name"))): record
        for record in records
        if record.get("name")
    }
    failures: list[str] = []
    notices: list[str] = []
    for rule in rules.get("pairs", []):
        if not isinstance(rule, dict):
            continue
        category = str(rule.get("category") or "")
        higher_name = str(rule.get("higher") or "")
        lower_name = str(rule.get("lower") or "")
        higher = by_key.get((category, normalize(higher_name)))
        lower = by_key.get((category, normalize(lower_name)))
        if not higher:
            failures.append(f"Missing required benchmark record: {category} / {higher_name}")
            continue
        if not lower:
            notices.append(f"Skipped {higher_name} > {lower_name}: lower comparison record not present this run")
            continue
        higher_price = float(higher.get("marketPrice") or 0)
        lower_price = float(lower.get("marketPrice") or 0)
        minimum_gap_pct = max(0.0, float(rule.get("minimumGapPct") or 0))
        required = lower_price * (1 + minimum_gap_pct / 100.0)
        if higher_price <= required:
            failures.append(
                f"Pricing sanity failure: {higher_name} ${higher_price:.2f} must exceed "
                f"{lower_name} ${lower_price:.2f} by more than {minimum_gap_pct:.1f}%"
            )
    return failures, notices


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    args = parser.parse_args()
    records = json.loads(args.catalog.read_text(encoding="utf-8"))
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not isinstance(rules, dict):
        raise ValueError("Catalog must be an array and rules must be an object")
    failures, notices = validate([row for row in records if isinstance(row, dict)], rules)
    for notice in notices:
        print("NOTICE:", notice)
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Non-athlete pricing sanity checks passed ({len(rules.get('pairs', []))} configured pairs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
