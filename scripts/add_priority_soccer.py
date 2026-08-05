#!/usr/bin/env python3
"""Ensure a curated set of notable soccer players is present in the current catalog.

Identity and club/status fields are taken from the same point-in-time ESPN roster
feeds used by the main catalog builder. Names that cannot be verified in a live
roster response are skipped rather than inserted with guessed current details.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_current_catalog import (
    ESPN_LEAGUES,
    build_market_fields,
    collect_espn_league,
    normalize,
)
from pricing_model import apply_pricing_to_records, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CATALOG = DATA / "current_catalog.json"
PRIORITY = DATA / "priority_soccer_names.json"
OVERRIDES = DATA / "pricing_overrides.json"


def read_array(path: Path) -> list[Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must contain a JSON array")
    return payload


def main() -> int:
    records = [item for item in read_array(CATALOG) if isinstance(item, dict)]
    requested = [str(name).strip() for name in read_array(PRIORITY) if str(name).strip()]
    wanted = {normalize(name): name for name in requested}
    existing = {
        normalize(str(record.get("name") or ""))
        for record in records
        if normalize(str(record.get("discipline") or "")) == normalize("Soccer")
    }
    remaining = set(wanted) - existing
    if not remaining:
        print("All priority soccer players are already present.")
        return 0

    found: dict[str, dict[str, Any]] = {}
    source_errors: list[str] = []
    soccer_leagues = [cfg for cfg in ESPN_LEAGUES if cfg.get("discipline") == "Soccer"]
    for cfg in soccer_leagues:
        result = collect_espn_league(cfg, workers=12)
        if result.error:
            source_errors.append(f"{cfg['label']}: {result.error}")
        for raw in result.records:
            key = normalize(str(raw.get("name") or ""))
            if key in remaining and key not in found:
                found[key] = raw
        remaining -= set(found)
        if not remaining:
            break

    used_tickers = {str(record.get("ticker") or "") for record in records if record.get("ticker")}
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    additions = [build_market_fields(raw, verified_at, used_tickers) for raw in found.values()]

    by_key = {
        (normalize(str(record.get("name") or "")), normalize(str(record.get("discipline") or ""))): record
        for record in records
    }
    for record in additions:
        by_key[(normalize(record["name"]), normalize(record["discipline"]))] = record

    merged = list(by_key.values())
    merged = apply_pricing_to_records(merged, load_overrides(OVERRIDES))
    CATALOG.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"Added or refreshed {len(additions)} priority soccer players.")
    if remaining:
        print("Not found in current roster feeds: " + ", ".join(wanted[key] for key in sorted(remaining)))
    if source_errors:
        print(f"Roster source warnings: {len(source_errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
