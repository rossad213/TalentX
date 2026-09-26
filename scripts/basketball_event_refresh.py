#!/usr/bin/env python3
"""Run the shared results-based game pricing engine for NBA/WNBA in isolation.

This wrapper installs the same uncapped, result-proportional event curve used by
Sports pricing without the NFL-only evidence hooks, migrations, ticker repair,
or rookie-history setup. The workflow supplies --league-only NBA or WNBA plus a
league-specific persistent state manifest.
"""
from __future__ import annotations

import sys

import hourly_price_refresh as refresh
import hourly_price_refresh_reliable  # noqa: F401  # installs shared reliability/event hooks


def requested_league(argv: list[str]) -> str:
    for index, value in enumerate(argv):
        if value == "--league-only" and index + 1 < len(argv):
            return str(argv[index + 1]).strip().upper()
        if value.startswith("--league-only="):
            return value.split("=", 1)[1].strip().upper()
    return ""


def main() -> int:
    league = requested_league(sys.argv[1:])
    if league not in {"NBA", "WNBA"}:
        raise SystemExit("basketball_event_refresh.py requires --league-only NBA or --league-only WNBA")
    return refresh.main()


if __name__ == "__main__":
    raise SystemExit(main())
