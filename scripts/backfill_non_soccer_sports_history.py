#!/usr/bin/env python3
"""Run the generic Sports history backfill without Soccer league fan-out.

Soccer has a dedicated team-schedule adapter. This wrapper keeps the proven
MLB/NBA/NFL/NHL history path intact while preventing dozens of Soccer leagues
from multiplying the generic per-date scoreboard scan.
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

    full = load(args.catalog)
    subset = [
        dict(record) for record in full
        if str(record.get("discipline") or "") != "Soccer"
    ]
    print(f"Generic non-Soccer Sports history candidates: {len(subset):,} of {len(full):,}")

    with tempfile.TemporaryDirectory(prefix="talentx-nonsoccer-") as temp_dir:
        subset_path = Path(temp_dir) / "sports.json"
        subset_path.write_text(json.dumps(subset, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        command = [
            sys.executable,
            str(Path(__file__).with_name("backfill_price_history.py")),
            "--catalog", str(subset_path),
            "--days", str(args.days),
            "--workers", str(args.workers),
            "--request-timeout", str(args.request_timeout),
            "--max-athletes", str(args.max_athletes),
            "--max-game-move-pct", str(args.max_game_move_pct),
        ]
        subprocess.run(command, check=True)
        enriched = load(subset_path)

    by_id = {str(record.get("id") or ""): record for record in enriched if record.get("id")}
    output = [by_id.get(str(record.get("id") or ""), record) for record in full]
    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("Merged non-Soccer verified game history back into the full Sports catalog.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
