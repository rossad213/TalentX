#!/usr/bin/env python3
"""Run strict Music discovery with CI-safe source limits.

This wrapper does not relax TalentX Music identity rules. It only bounds how
many sequential Wikidata pages the full-catalog baseline requests and shortens
individual source timeouts. The underlying discovery workflow already permits a
quality-first shortfall, so source slowness should reduce additions rather than
hold the entire baseline build for an hour.
"""
from __future__ import annotations

import sys

import discover_strict_music as strict_music

CI_OVERRIDES = {
    "--per-occupation-limit": "400",
    "--request-timeout": "15",
    "--sleep": "0.05",
}


def upsert_arg(name: str, value: str) -> None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        sys.argv.extend([name, value])
        return
    if index + 1 < len(sys.argv):
        sys.argv[index + 1] = value
    else:
        sys.argv.append(value)


def main() -> int:
    for name, value in CI_OVERRIDES.items():
        upsert_arg(name, value)
    print(
        "Strict Music CI limits: 400 candidates/occupation, 15s request timeout; "
        "verification rules unchanged."
    )
    return strict_music.main()


if __name__ == "__main__":
    raise SystemExit(main())
