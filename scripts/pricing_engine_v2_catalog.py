#!/usr/bin/env python3
"""Apply TalentX pricing engine v2 to exactly one category-owned catalog."""
from __future__ import annotations

import argparse
from pathlib import Path

from pricing_engine_v2 import process


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    count = process(args.catalog)
    if not count:
        raise SystemExit(f"No records were repriced in {args.catalog}")
    print(f"Applied pricing engine v2 to {count:,} category-owned records in {args.catalog}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
