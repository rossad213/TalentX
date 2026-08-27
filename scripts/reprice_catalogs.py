#!/usr/bin/env python3
"""Apply the same audited pricing model to every TalentX catalog file.

Established-athlete fundamentals use the last successful verified catalog as the
baseline continuity source. Routine roster/stat refreshes must not replace durable
career evidence with a partial-season or source-glitch snapshot. Dated event
pricing remains separate and can still react immediately on the correct timeline.
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
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _is_rookie(record: dict[str, Any]) -> bool:
    status = str(record.get("pricingDataStatus") or "")
    experience = _number(record.get("experienceYears"))
    professional_games = _number(record.get("professionalGames"))
    return "Rookie IPO" in status or ((experience is not None and experience <= 1) and (professional_games or 0) < 8)


def stabilize_athlete_fundamentals(
    records: list[dict[str, Any]], prior_records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Preserve durable established-athlete evidence from the last good build.

    The pricing model derives athlete metrics twice: first from ``activeMetrics``
    and then, when available, from ``pricingEvidenceSummary.percentiles``. Earlier
    continuity attempts blended only ``activeMetrics``; the fresh percentile
    summary then overwrote that blend during pricing. This function preserves
    both durable inputs for established athletes so a routine baseline rebuild
    cannot silently rebase years of career evidence.

    Rookies remain on the normal IPO/transition model. Live identity, team,
    roster, verification and availability metadata are kept from the fresh row.
    Dated game/career events remain independent of this baseline continuity layer.
    """
    if not prior_records:
        return records, 0

    prior_by_key = {
        _identity_key(record): record
        for record in prior_records
        if record.get("primaryCategory") == "Athlete"
        and isinstance(record.get("activeMetrics"), dict)
    }
    output: list[dict[str, Any]] = []
    stabilized = 0

    for source_record in records:
        record = dict(source_record)
        if record.get("primaryCategory") != "Athlete" or record.get("marketSegment") != "Current" or _is_rookie(record):
            output.append(record)
            continue

        prior = prior_by_key.get(_identity_key(record))
        if not isinstance(prior, dict) or prior.get("leagueOrMedium") != record.get("leagueOrMedium"):
            output.append(record)
            continue

        prior_metrics = prior.get("activeMetrics")
        if not isinstance(prior_metrics, dict):
            output.append(record)
            continue

        # Preserve the complete previously verified durable metric set. Current
        # games and career events are intentionally priced by the event layer,
        # not by silently rebasing the full fundamental model on every roster run.
        record["activeMetrics"] = dict(prior_metrics)

        # Critical: pricing_model.normalize_evidence_metrics() reads this field
        # and can otherwise overwrite activeMetrics with new percentile values.
        # Keep the last successful verified career percentile snapshot for the
        # established baseline. New verified career evidence can be promoted by
        # the dedicated event/career refresh rather than a roster rebuild.
        prior_summary = prior.get("pricingEvidenceSummary")
        if isinstance(prior_summary, dict):
            record["pricingEvidenceSummary"] = dict(prior_summary)

        prior_evidence = prior.get("pricingEvidence")
        if isinstance(prior_evidence, list) and prior_evidence:
            record["pricingEvidence"] = list(prior_evidence)

        prior_confidence = _number(prior.get("pricingConfidence"))
        if prior_confidence is not None:
            record["pricingConfidence"] = max(
                prior_confidence,
                _number(record.get("pricingConfidence")) or 0.0,
            )

        record["fundamentalContinuity"] = {
            "source": "last_successful_verified_baseline",
            "mode": "preserve_durable_metrics_and_percentiles",
            "priorFundamentalValue": prior.get("fundamentalValue"),
            "priorCareerScore": prior.get("careerScore"),
            "rule": "routine baseline rebuild preserves established career evidence; dated event pricing remains independent",
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
        "fundamentalContinuity": {
            "enabled": bool(prior_current),
            "priorBaselineFile": "data/prior_current_catalog.json" if prior_current else None,
            "mode": "preserve_durable_metrics_and_percentiles",
            "currentCatalogRecordsStabilized": stabilized_counts.get("current_catalog.json", 0),
            "calibrationRecordsStabilized": calibration_stabilized,
        },
        "pricingCatalogsProcessed": totals,
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for filename, count in totals.items():
        suffix = f"; preserved {stabilized_counts[filename]:,} established athlete evidence baselines" if filename in stabilized_counts else ""
        print(f"Repriced {count:,} records in {filename}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
