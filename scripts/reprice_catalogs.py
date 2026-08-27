#!/usr/bin/env python3
"""Apply the same audited pricing model to every TalentX catalog file.

Established-athlete fundamentals use the last successful verified catalog as a
continuity anchor.  Short fresh samples may move current performance, but they
must not erase years of career evidence in a single baseline refresh.  Event
pricing remains separate and can still react immediately to dated events.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pricing_model import CATEGORY_WEIGHTS, MODEL_VERSION, apply_pricing_to_records, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OVERRIDES = DATA / "pricing_overrides.json"
CURRENT_SEED = DATA / "current_seed.json"
PRIOR_CURRENT = DATA / "prior_current_catalog.json"


def read_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must be a JSON array")
    return payload


def write_array(path: Path, records: list[dict[str, Any]]) -> None:
    compact = path.name == "current_catalog.json"
    text = json.dumps(records, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(records, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


def write_current_csv(records: list[dict[str, Any]]) -> None:
    path = DATA / "current_catalog.csv"
    fields = [
        "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
        "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
        "careerStage", "lastVerifiedAt", "verificationStatus", "sourceName",
        "sourceUrl", "sourceRecordId", "dataConfidence", "pricingConfidence",
        "pricingDataStatus", "pricingModelVersion", "marketPrice", "fundamentalValue", "careerScore",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _identity_key(record: dict[str, Any]) -> tuple[str, ...]:
    namespace = str(record.get("sourceNamespace") or "").strip().lower()
    source_id = str(record.get("sourceRecordId") or "").strip()
    if namespace and source_id:
        return ("source", namespace, source_id)
    return (
        "profile",
        str(record.get("name") or "").strip().casefold(),
        str(record.get("discipline") or "").strip().casefold(),
        str(record.get("leagueOrMedium") or "").strip().casefold(),
    )


def stabilize_athlete_fundamentals(
    records: list[dict[str, Any]], prior_records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Blend established-athlete fundamentals with the last good baseline.

    The baseline price itself is never copied.  Only durable input metrics are
    stabilized before the pricing model runs.  That keeps normal repricing and
    dated event impacts intact while preventing tiny/new-season stat samples or
    partial source responses from replacing a player's established career signal.
    """
    if not prior_records:
        return records, 0

    prior_by_key = {
        _identity_key(record): record
        for record in prior_records
        if record.get("primaryCategory") == "Athlete"
        and isinstance(record.get("activeMetrics"), dict)
    }
    stabilized = 0
    output: list[dict[str, Any]] = []

    for source_record in records:
        record = dict(source_record)
        if record.get("primaryCategory") != "Athlete" or record.get("marketSegment") != "Current":
            output.append(record)
            continue

        current_metrics = record.get("activeMetrics")
        prior = prior_by_key.get(_identity_key(record))
        prior_metrics = prior.get("activeMetrics") if isinstance(prior, dict) else None
        if not isinstance(current_metrics, dict) or not isinstance(prior_metrics, dict):
            output.append(record)
            continue
        if prior.get("leagueOrMedium") != record.get("leagueOrMedium"):
            output.append(record)
            continue

        # Rookie IPOs and true first-year players should be allowed to establish
        # a new market baseline rather than inherit an older career anchor.
        status = str(record.get("pricingDataStatus") or "")
        experience = _number(record.get("experienceYears"))
        professional_games = _number(record.get("professionalGames"))
        if "Rookie IPO" in status or ((experience is not None and experience <= 1) and (professional_games or 0) < 8):
            output.append(record)
            continue

        # A fallback-retained row has no new roster verification, so its prior
        # verified fundamentals are the correct baseline.  Fresh established
        # rows receive a 45% continuity anchor; the majority remains fresh data.
        prior_weight = 1.0 if record.get("fallbackRetained") else 0.45
        current_weight = 1.0 - prior_weight
        blended = dict(current_metrics)

        for field in ("performance", "achievements", "consistency", "audience"):
            current_value = _number(current_metrics.get(field))
            prior_value = _number(prior_metrics.get(field))
            if current_value is None or prior_value is None:
                continue
            value = current_value * current_weight + prior_value * prior_weight
            # Achievements are cumulative career evidence.  A routine refresh
            # may add achievements, but a partial source response must not erase
            # them.  Large downward corrections should be handled explicitly.
            if field == "achievements" and not record.get("fallbackRetained"):
                value = max(value, prior_value * 0.95)
            blended[field] = round(max(0.0, min(100.0, value)), 1)

        if record.get("fallbackRetained"):
            # Potential and availability also stay on the prior evidence for a
            # retained row; no live source was available to justify changing them.
            for field in ("potential", "availability"):
                if _number(prior_metrics.get(field)) is not None:
                    blended[field] = prior_metrics[field]

        record["activeMetrics"] = blended
        record["fundamentalContinuity"] = {
            "source": "last_successful_verified_baseline",
            "priorWeight": prior_weight,
            "freshWeight": current_weight,
            "rule": "durable career fundamentals stabilized; dated event pricing remains independent",
        }
        stabilized += 1
        output.append(record)

    return output, stabilized


def main() -> int:
    overrides = load_overrides(OVERRIDES)
    benchmark_records = read_array(CURRENT_SEED)
    prior_current = read_array(PRIOR_CURRENT)

    raw_current = read_array(DATA / "current_catalog.json")
    calibration_reference, calibration_stabilized = stabilize_athlete_fundamentals(raw_current, prior_current)
    calibration_reference = calibration_reference or benchmark_records

    totals: dict[str, int] = {}
    stabilized_counts: dict[str, int] = {}
    for filename in ("current_seed.json", "current_catalog.json", "legacy_catalog_v2.json"):
        path = DATA / filename
        if not path.exists():
            continue
        records = read_array(path)
        if filename == "current_catalog.json":
            records, stabilized_counts[filename] = stabilize_athlete_fundamentals(records, prior_current)
        reference = calibration_reference if filename != "legacy_catalog_v2.json" else records
        repriced = apply_pricing_to_records(
            records,
            overrides,
            benchmark_records=benchmark_records,
            calibration_reference=reference,
        )
        write_array(path, repriced)
        totals[filename] = len(repriced)
        if filename == "current_catalog.json":
            write_current_csv(repriced)

    manifest_path = DATA / "catalog_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update({
        "pricingModelVersion": MODEL_VERSION,
        "pricingRule": "Category-specific fundamentals are calibrated 70/30 with profession peers; evidence quality limits unsupported valuations.",
        "categoryWeights": CATEGORY_WEIGHTS,
        "crossCategoryCalibration": {"absolute": 0.70, "professionPeer": 0.30},
        "pricingCatalogsProcessed": totals,
        "fundamentalContinuity": {
            "enabled": bool(prior_current),
            "priorBaselineFile": "data/prior_current_catalog.json" if prior_current else None,
            "establishedAthletePriorWeight": 0.45,
            "fallbackRetainedPriorWeight": 1.0,
            "currentCatalogRecordsStabilized": stabilized_counts.get("current_catalog.json", 0),
            "calibrationRecordsStabilized": calibration_stabilized,
        },
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for filename, count in totals.items():
        suffix = f"; stabilized {stabilized_counts[filename]:,} athlete fundamentals" if filename in stabilized_counts else ""
        print(f"Repriced {count:,} records in {filename}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
