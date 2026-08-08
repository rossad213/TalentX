#!/usr/bin/env python3
"""Keep transient outcome-source failures retryable.

The outcome scanner records an attentionState entry when it attempts a Wikimedia
pageview comparison. If the source request failed or returned too little data,
there is no measured ratio. Remove those incomplete markers so a later six-hour
scan can retry instead of treating the outcome as permanently checked.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if not args.manifest.exists():
        return 0
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{args.manifest} must contain an object")
    state = payload.get("attentionState") if isinstance(payload.get("attentionState"), dict) else {}
    repaired = {
        key: value
        for key, value in state.items()
        if isinstance(value, dict) and isinstance(value.get("ratio"), (int, float))
    }
    removed = len(state) - len(repaired)
    payload["attentionState"] = repaired
    payload["retryableAttentionChecksRemoved"] = removed
    args.manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Removed {removed:,} incomplete attention-check markers so they can retry later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
