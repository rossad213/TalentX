#!/usr/bin/env python3
"""Top up a partial current catalog from the last successful verified athlete baseline.

This is an outage-recovery step, not a source of new identities. Records are only
eligible when they were already present as active/current athletes in a prior
successful baseline and were source-backed by ESPN or the official NHL feed.
Their original lastVerifiedAt is preserved so retained records are never presented
as freshly verified during the current run.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def key(record: dict[str, Any]) -> tuple[str, str]:
    return norm(record.get("name")), norm(record.get("discipline"))


def load_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [r for r in data if isinstance(r, dict)]


def is_verified_fallback_candidate(record: dict[str, Any]) -> bool:
    return (
        record.get("primaryCategory") == "Athlete"
        and record.get("careerStatus") == "Active"
        and record.get("marketSegment") == "Current"
        and record.get("sourceNamespace") in {"espn", "nhl"}
        and bool(record.get("lastVerifiedAt"))
        and bool(record.get("name"))
        and bool(record.get("discipline"))
    )


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
        "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
        "careerStage", "lastVerifiedAt", "verificationStatus", "sourceName",
        "sourceUrl", "sourceRecordId", "dataConfidence", "pricingConfidence",
        "pricingDataStatus", "pricingModelVersion", "marketPrice", "careerScore",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def update_manifest(path: Path, *, total: int, retained: int, live_count: int) -> None:
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            pass
    payload["currentSeedRecords"] = total
    payload["currentCatalogRecords"] = total
    payload["automatedRosterVerifiedRecords"] = live_count
    payload["fallbackRetainedAthleteRecords"] = retained
    payload["fallbackPolicy"] = (
        "When current roster sources are unavailable, previously source-verified active athletes may be retained "
        "from the last successful baseline without changing their original lastVerifiedAt timestamp."
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_source_manifest(path: Path, *, retained: int, fallback_path: Path) -> None:
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            pass
    payload["fallbackRetainedAthleteRecords"] = retained
    payload["fallbackCatalog"] = str(fallback_path)
    payload["fallbackFreshnessRule"] = "Preserve original lastVerifiedAt; never stamp fallback rows as freshly verified."
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--fallback", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--target-additional-athletes", type=int, default=10_000)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()

    current = load_records(args.catalog)
    seed = load_records(args.seed)
    if not args.fallback.exists():
        current_keys = {key(r) for r in current}
        seed_keys = {key(r) for r in seed}
        additional = sum(
            1 for r in current
            if r.get("primaryCategory") == "Athlete" and key(r) not in seed_keys
        )
        if additional >= args.target_additional_athletes:
            print("Live roster coverage already meets the athlete target; no fallback needed.")
            return 0
        raise SystemExit(
            f"Live roster coverage has only {additional:,} additional athletes and no prior verified fallback catalog is available."
        )

    fallback = load_records(args.fallback)
    seed_keys = {key(r) for r in seed}
    existing_keys = {key(r) for r in current}
    used_ids = {str(r.get("id")) for r in current if r.get("id")}
    used_tickers = {str(r.get("ticker")) for r in current if r.get("ticker")}

    additional = sum(
        1 for r in current
        if r.get("primaryCategory") == "Athlete" and key(r) not in seed_keys
    )
    needed = max(0, args.target_additional_athletes - additional)
    if needed == 0:
        print(f"Live roster coverage already supplies {additional:,} additional athletes; no fallback needed.")
        return 0

    candidates = [r for r in fallback if is_verified_fallback_candidate(r)]
    candidates.sort(
        key=lambda r: (
            str(r.get("lastVerifiedAt") or ""),
            str(r.get("leagueOrMedium") or ""),
            str(r.get("name") or ""),
        ),
        reverse=True,
    )

    retained = 0
    retained_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for prior in candidates:
        if retained >= needed:
            break
        k = key(prior)
        if k in existing_keys or k in seed_keys:
            continue
        prior_id = str(prior.get("id") or "")
        prior_ticker = str(prior.get("ticker") or "")
        if not prior_id or prior_id in used_ids or not prior_ticker or prior_ticker in used_tickers:
            continue

        record = dict(prior)
        record["verificationStatus"] = "Retained from prior verified roster snapshot — current live source unavailable during this build"
        record["fallbackRetained"] = True
        record["fallbackRetainedAt"] = retained_at
        record["fallbackReason"] = "Current roster source shortfall/outage"
        record["fallbackSource"] = "last_successful_baseline"
        record["dataConfidence"] = round(min(float(record.get("dataConfidence", 0.82) or 0.82), 0.82), 2)
        status = str(record.get("pricingDataStatus") or "").strip()
        if "fallback" not in status.lower():
            record["pricingDataStatus"] = (status + " | Prior verified roster fallback").strip(" |")
        # Critically: lastVerifiedAt is intentionally NOT changed.
        current.append(record)
        existing_keys.add(k)
        used_ids.add(prior_id)
        used_tickers.add(prior_ticker)
        retained += 1

    final_additional = additional + retained
    if final_additional < args.target_additional_athletes:
        raise SystemExit(
            f"Fallback exhausted: live={additional:,}, retained={retained:,}, "
            f"final={final_additional:,}, target={args.target_additional_athletes:,}."
        )

    args.catalog.write_text(json.dumps(current, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.csv:
        write_csv(args.csv, current)
    live_count = sum(
        1 for r in current
        if r.get("sourceNamespace") in {"espn", "nhl"} and not r.get("fallbackRetained")
    )
    if args.manifest:
        update_manifest(args.manifest, total=len(current), retained=retained, live_count=live_count)
    if args.source_manifest:
        update_source_manifest(args.source_manifest, retained=retained, fallback_path=args.fallback)

    print(
        f"Recovered athlete target using prior verified baseline: live={additional:,}, "
        f"retained={retained:,}, final additional athletes={final_additional:,}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
