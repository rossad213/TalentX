#!/usr/bin/env python3
"""Create the latest-game explanation without changing the recorded market price."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import event_pricing_policy as policy


def signed_direction(value: float) -> str:
    return "increased" if value > 0 else "decreased" if value < 0 else "held steady"


def explain_only(record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    result = dict(record)
    calculated = policy.explainable_event_move(result)
    if calculated is None:
        return result, False

    suggested_move, explanation = calculated
    recorded_move = policy.number(result.get("lastGameMovePct"))
    final_move = round(recorded_move if recorded_move is not None else suggested_move, 2)

    components = explanation.get("components") if isinstance(explanation.get("components"), dict) else {}
    component_total = sum(float(value or 0) for value in components.values())
    if abs(component_total) > 1e-9:
        scale = final_move / component_total
        explanation["components"] = {key: round(float(value or 0) * scale, 3) for key, value in components.items()}

    explanation["policySuggestedMovePct"] = round(float(suggested_move), 2)
    explanation["finalMovePct"] = final_move
    explanation["direction"] = signed_direction(final_move)
    explanation["recordedMarketPrice"] = result.get("marketPrice")
    explanation["pricingMode"] = "Explanation only — game-by-game market price preserved"

    changed = result.get("priceExplanation") != explanation
    result["priceExplanation"] = explanation
    result["volatilityTier"] = explanation.get("volatilityTier")
    return result, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    output: list[dict[str, Any]] = []
    changed = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        record, did_change = explain_only(item)
        output.append(record)
        changed += int(did_change)
    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Updated {changed:,} event explanations without repricing recorded game moves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
