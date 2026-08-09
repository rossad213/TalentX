#!/usr/bin/env python3
"""Run the proven generic Sports history backfill for non-Soccer athletes only.

Soccer has its own team-schedule adapter because TalentX carries dozens of ESPN
Soccer league slugs. Feeding all of those leagues through the generic date-by-date
scoreboard scan is slow and can starve Soccer coverage. This wrapper isolates the
existing MLB/NBA/NFL/NHL path, then merges those processed records back into the
full Sports category catalog without touching Soccer records.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def load(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--max-athletes", type=int, default=5000)
    parser.add_argument("--max-game-move-pct", type=float, default=2.5)
    args = parser.parse_args()

    records = load(args.catalog)
    non_soccer = [
        dict(record)
        for record in records
        if str(record.get("discipline") or "").strip().lower() != "soccer"
    ]
    print(f"Generic non-Soccer Sports history records: {len(non_soccer):,} of {len(records):,}")
    if not non_soccer:
        return 0

    script = Path(__file__).resolve().with_name("backfill_price_history.py")
    with tempfile.TemporaryDirectory(prefix="talentx-non-soccer-") as temp_dir:
        working = Path(temp_dir) / "sports-non-soccer.json"
        working.write_text(json.dumps(non_soccer, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        command = [
            sys.executable,
            str(script),
            "--catalog", str(working),
            "--days", str(args.days),
            "--workers", str(args.workers),
            "--request-timeout", str(args.request_timeout),
            "--max-athletes", str(args.max_athletes),
            "--max-game-move-pct", str(args.max_game_move_pct),
        ]
        subprocess.run(command, check=True)
        processed = load(working)

    processed_by_id = {
        str(record.get("id") or ""): record
        for record in processed
        if str(record.get("id") or "")
    }
    output: list[dict[str, Any]] = []
    replaced = 0
    for record in records:
        record_id = str(record.get("id") or "")
        replacement = processed_by_id.get(record_id)
        if replacement is not None and str(record.get("discipline") or "").strip().lower() != "soccer":
            output.append(dict(replacement))
            replaced += 1
        else:
            output.append(dict(record))

    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Merged generic history back into {replaced:,} non-Soccer Sports records; Soccer left for its dedicated adapter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
