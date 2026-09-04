#!/usr/bin/env python3
"""Top up TalentX's Creator category from source-backed Wikidata occupations.

The curated Creator roster remains the editorial anchor. This discovery layer adds
living people whose Wikidata occupation explicitly identifies them as a digital
creator, YouTuber, streamer, influencer, podcaster, vlogger, or related creator.
It is intentionally isolated from Athlete, Music, and Actor expansion so Creator
growth cannot shrink or rewrite the other TalentX categories.

Wikidata is not a live employment roster. Generated records therefore use an
"active-status proxy" and clearly state that the evidence is occupation/current-
activity proxy data rather than a real-time platform roster.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_non_athlete_catalog import initials, normalize, slugify, unique_ticker
from expand_non_athlete_sources import (
    RECENT_ACTIVITY_YEARS,
    fetch_occupation_candidates,
    make_session,
)
from pricing_model import apply_pricing_to_records, clamp, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_TAXONOMY = DATA / "taxonomy.json"
DEFAULT_MANIFEST = DATA / "creator_discovery_manifest.json"
DEFAULT_OVERRIDES = DATA / "pricing_overrides.json"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# Explicit creator occupations only. These QIDs are intentionally narrower than
# generic entertainer/media occupations so TalentX does not relabel actors,
# musicians, or athletes as Creators just because they also use social media.
CREATOR_OCCUPATIONS: dict[str, tuple[str, str, str]] = {
    "Q109459317": ("Content creator", "Digital Content", "Digital platforms"),
    "Q111263847": ("Digital creator", "Digital Content", "Digital platforms"),
    "Q17125263": ("YouTuber", "YouTube", "YouTube"),
    "Q4110598": ("Vlogger", "Vlogging", "Video platforms"),
    "Q50279140": ("Twitch streamer", "Twitch", "Twitch"),
    "Q57414145": ("Online streamer", "Livestreaming", "Streaming platforms"),
    "Q2906862": ("Social media influencer", "Social Media", "Social platforms"),
    "Q15077007": ("Podcaster", "Podcasting", "Podcast platforms"),
    "Q55155641": ("VTuber", "VTubing", "Video / streaming platforms"),
    "Q8246794": ("Blogger", "Blogging", "Web / social platforms"),
}

DISCIPLINE_PRIORITY = {
    "YouTube": 10,
    "Twitch": 9,
    "VTubing": 8,
    "Livestreaming": 7,
    "Digital Content": 6,
    "Social Media": 5,
    "Podcasting": 4,
    "Vlogging": 3,
    "Blogging": 2,
}


def creator_candidate_is_eligible(candidate: dict[str, Any], minimum_sitelinks: int, recent_cutoff: int) -> bool:
    """Use a creator-specific current/notability proxy.

    Creator occupations are themselves strong public-role evidence, so unlike the
    actor/music discovery layer we do not require 40+ sitelinks when a work-start
    date is absent. That keeps the pool broad enough to represent internet-native
    creators whose Wikidata biographies are smaller than traditional celebrities.
    """
    name = str(candidate.get("name") or "").strip()
    qid = str(candidate.get("qid") or "")
    sitelinks = int(candidate.get("sitelinks") or 0)
    if not name or not re.fullmatch(r"Q\d+", qid) or sitelinks < max(1, minimum_sitelinks):
        return False
    current_year = datetime.now(timezone.utc).year
    birth_year = candidate.get("birthYear")
    if isinstance(birth_year, int):
        age = current_year - birth_year
        if age < 12 or age > 100:
            return False
    work_end = candidate.get("workEndYear")
    if isinstance(work_end, int) and work_end < recent_cutoff:
        return False
    return True


def merge_creator_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate creator identities while keeping their strongest platform role."""
    by_qid: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        qid = str(candidate.get("qid") or "")
        if not qid:
            continue
        existing = by_qid.get(qid)
        if existing is None:
            by_qid[qid] = dict(candidate)
            continue
        if int(candidate.get("sitelinks") or 0) > int(existing.get("sitelinks") or 0):
            existing["sitelinks"] = candidate.get("sitelinks")
        if DISCIPLINE_PRIORITY.get(str(candidate.get("discipline") or ""), 0) > DISCIPLINE_PRIORITY.get(str(existing.get("discipline") or ""), 0):
            existing["discipline"] = candidate.get("discipline")
            existing["role"] = candidate.get("role")
            existing["platform"] = candidate.get("platform")
        for key in ("birthYear", "workStartYear", "workEndYear"):
            if existing.get(key) is None and candidate.get(key) is not None:
                existing[key] = candidate.get(key)
        if existing.get("country") == "Not listed" and candidate.get("country") not in {None, "", "Not listed"}:
            existing["country"] = candidate.get("country")
    return sorted(
        by_qid.values(),
        key=lambda row: (-int(row.get("sitelinks") or 0), str(row.get("name") or "")),
    )


def creator_metrics(candidate: dict[str, Any]) -> dict[str, float]:
    current_year = datetime.now(timezone.utc).year
    sitelinks = max(0, int(candidate.get("sitelinks") or 0))
    birth_year = candidate.get("birthYear")
    start_year = candidate.get("workStartYear")
    age = current_year - birth_year if isinstance(birth_year, int) else None
    years_active = max(0, current_year - start_year) if isinstance(start_year, int) else None

    audience = clamp(38 + math.log1p(sitelinks) * 10.2, 38, 97)
    consistency = clamp(50 + min(36, (years_active or 4) * 2.0), 48, 94)
    achievements = clamp(43 + math.log1p(sitelinks) * 7.7 + min(10, (years_active or 0) * .3), 43, 95)
    potential = clamp(94 - max(0, (age or 27) - 20) * 2.15, 35, 96)
    performance = clamp(audience * .35 + achievements * .30 + consistency * .35, 45, 96)
    return {
        "audience": round(audience, 2),
        "performance": round(performance, 2),
        "potential": round(potential, 2),
        "consistency": round(consistency, 2),
        "achievements": round(achievements, 2),
        "availability": 80.0,
    }


def creator_confidence(candidate: dict[str, Any]) -> float:
    sitelinks = max(0, int(candidate.get("sitelinks") or 0))
    completeness = sum(candidate.get(key) is not None for key in ("birthYear", "workStartYear"))
    has_country = candidate.get("country") not in {None, "", "Not listed"}
    value = .68 + min(.12, math.log1p(sitelinks) / 55) + completeness * .035 + int(has_country) * .025
    return round(float(clamp(value, .68, .90)), 2)


def make_creator_record(
    candidate: dict[str, Any],
    rank: int,
    pool_size: int,
    used_ids: set[str],
    used_tickers: set[str],
    verified_at: str,
) -> dict[str, Any]:
    name = str(candidate["name"]).strip()
    qid = str(candidate["qid"])
    base_id = f"cur-{slugify(name)}"
    profile_id = base_id if base_id not in used_ids else f"{base_id}-creator-{qid.lower()}"
    used_ids.add(profile_id)
    ticker = unique_ticker(name, f"wikidata:Creator:{qid}", used_tickers)
    start_year = candidate.get("workStartYear")
    birth_year = candidate.get("birthYear")
    current_year = datetime.now(timezone.utc).year
    age = current_year - birth_year if isinstance(birth_year, int) else None
    years_active = max(0, current_year - start_year) if isinstance(start_year, int) else None
    discipline = str(candidate.get("discipline") or "Digital Content")
    role = str(candidate.get("role") or "Content creator")
    platform = str(candidate.get("platform") or "Digital platforms")
    source_url = f"https://www.wikidata.org/wiki/{qid}"
    confidence = creator_confidence(candidate)
    proxy_note = "Living-person creator-occupation proxy; not a live platform roster"

    record: dict[str, Any] = {
        "id": profile_id,
        "name": name,
        "ticker": ticker,
        "primaryCategory": "Creator",
        "discipline": discipline,
        "leagueOrMedium": "Digital Media",
        "teamOrPlatform": platform,
        "role": role,
        "country": str(candidate.get("country") or "Not listed"),
        "careerStatus": "Active-status proxy",
        "marketSegment": "Current",
        "careerStage": "Active career",
        "verificationStatus": proxy_note,
        "lastVerifiedAt": verified_at,
        "statusSource": "Wikidata creator occupation, living-person, and work-period statements",
        "sourceName": "Wikidata",
        "sourceUrl": source_url,
        "sourceRecordId": qid,
        "sourceNamespace": "wikidata-creator",
        "dataConfidence": confidence,
        "pricingConfidence": confidence,
        "pricingDataStatus": "Public creator identity/activity evidence; platform performance evidence partial",
        "pricingEvidence": [source_url],
        "activeMetrics": creator_metrics(candidate),
        "legacyMetrics": {},
        "modelType": "Active career model",
        "avatar": initials(name),
        "description": f"{role} associated with {discipline}. {proxy_note}.",
        "searchText": " ".join([
            name, "Creator", discipline, "Digital Media", platform, role,
            str(candidate.get("country") or ""), "Current active",
        ]).lower(),
        "benchmarkRank": rank,
        "benchmarkPoolSize": pool_size,
        "wikidataSitelinks": int(candidate.get("sitelinks") or 0),
        "wikidataWorkStartYear": start_year,
        "wikidataWorkEndYear": candidate.get("workEndYear"),
        "creatorDiscoveryFrameworkVersion": "1.0",
        "discoveryEvidence": proxy_note,
    }
    if age is not None:
        record["age"] = age
    if years_active is not None:
        record["yearsActive"] = years_active
        record["debutYear"] = start_year
    return record


def update_creator_taxonomy(path: Path, records: list[dict[str, Any]]) -> None:
    taxonomy = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"categories": {}}
    categories = taxonomy.setdefault("categories", {})
    block = categories.setdefault("Creator", {"label": "Creator", "disciplines": [], "filters": []})
    disciplines = {str(value) for value in block.get("disciplines", []) if value}
    disciplines.update(
        str(record.get("discipline"))
        for record in records
        if record.get("primaryCategory") == "Creator" and record.get("discipline")
    )
    block["disciplines"] = sorted(disciplines)
    path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_creators(
    per_occupation_limit: int,
    timeout: float,
    sleep_seconds: float,
    minimum_sitelinks: int,
    recent_cutoff: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    session = make_session()
    raw: list[dict[str, Any]] = []
    errors: list[str] = []
    for qid, (role, discipline, platform) in CREATOR_OCCUPATIONS.items():
        try:
            rows = fetch_occupation_candidates(
                session,
                qid,
                role,
                discipline,
                max(1, per_occupation_limit),
                timeout,
                recent_cutoff,
            )
            for row in rows:
                row["platform"] = platform
            raw.extend(rows)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Creator:{qid}:{type(exc).__name__}:{exc}")
        if sleep_seconds:
            time.sleep(sleep_seconds)
    merged = merge_creator_candidates(raw)
    eligible = [row for row in merged if creator_candidate_is_eligible(row, minimum_sitelinks, recent_cutoff)]
    return eligible, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target-total", type=int, default=1000)
    parser.add_argument("--minimum-total", type=int, default=900)
    parser.add_argument("--per-occupation-limit", type=int, default=1200)
    parser.add_argument("--minimum-sitelinks", type=int, default=3)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--sleep", type=float, default=.2)
    parser.add_argument("--allow-shortfall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(seed, list):
        raise ValueError("current_seed.json must contain a JSON array")
    records = [record for record in seed if isinstance(record, dict)]
    existing_creator_count = sum(1 for record in records if record.get("primaryCategory") == "Creator")
    target_total = max(existing_creator_count, int(args.target_total))
    additions_needed = max(0, target_total - existing_creator_count)

    existing_names = {normalize(str(record.get("name") or "")) for record in records}
    existing_source_ids = {str(record.get("sourceRecordId") or "") for record in records if record.get("sourceRecordId")}
    used_ids = {str(record.get("id")) for record in records if record.get("id")}
    used_tickers = {str(record.get("ticker")) for record in records if record.get("ticker")}

    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    recent_cutoff = datetime.now(timezone.utc).year - RECENT_ACTIVITY_YEARS
    candidates, source_errors = discover_creators(
        args.per_occupation_limit,
        args.request_timeout,
        args.sleep,
        args.minimum_sitelinks,
        recent_cutoff,
    )

    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        name_key = normalize(str(candidate.get("name") or ""))
        qid = str(candidate.get("qid") or "")
        if not name_key or name_key in existing_names or qid in existing_source_ids:
            continue
        selected.append(candidate)
        existing_names.add(name_key)
        existing_source_ids.add(qid)
        if len(selected) >= additions_needed:
            break

    achieved_total = existing_creator_count + len(selected)
    if achieved_total < int(args.minimum_total) and not args.allow_shortfall:
        detail = "; ".join(source_errors[-5:]) if source_errors else "no source error reported"
        raise RuntimeError(
            f"Creator discovery reached only {achieved_total} total records; minimum is {args.minimum_total}. "
            f"Recent source detail: {detail}"
        )

    additions = [
        make_creator_record(
            candidate,
            existing_creator_count + offset,
            max(achieved_total, target_total),
            used_ids,
            used_tickers,
            verified_at,
        )
        for offset, candidate in enumerate(selected, start=1)
    ]
    combined = records + additions

    ids = [str(record.get("id") or "") for record in combined]
    tickers = [str(record.get("ticker") or "") for record in combined]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate profile IDs after Creator discovery")
    if len(tickers) != len(set(tickers)):
        raise ValueError("Duplicate tickers after Creator discovery")

    combined = apply_pricing_to_records(
        combined,
        load_overrides(DEFAULT_OVERRIDES),
        benchmark_records=combined,
        calibration_reference=combined,
    )
    counts = Counter(str(record.get("primaryCategory") or "") for record in combined)
    manifest = {
        "version": "1.0",
        "generatedAt": verified_at,
        "source": SPARQL_ENDPOINT,
        "activityProxyCutoffYear": recent_cutoff,
        "minimumSitelinks": args.minimum_sitelinks,
        "requestedCreatorTotal": int(args.target_total),
        "minimumCreatorTotal": int(args.minimum_total),
        "creatorCountBefore": existing_creator_count,
        "creatorAdditions": len(additions),
        "creatorCountAfter": counts.get("Creator", 0),
        "categoryCountsAfterDiscovery": dict(sorted(counts.items())),
        "occupationSources": [
            {"qid": qid, "role": role, "discipline": discipline, "platform": platform}
            for qid, (role, discipline, platform) in CREATOR_OCCUPATIONS.items()
        ],
        "sourceErrors": source_errors,
        "statusLimitation": (
            "Wikidata is not a live platform roster. Creator eligibility uses living-person, "
            "explicit creator occupation, sitelink, and work-end proxies and should be refreshed regularly."
        ),
    }

    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return 0

    args.seed.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    update_creator_taxonomy(args.taxonomy, combined)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Creator discovery: {existing_creator_count:,} existing + {len(additions):,} added "
        f"= {counts.get('Creator', 0):,} total Creator records."
    )
    if source_errors:
        print(f"Completed with {len(source_errors)} source request error(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
