#!/usr/bin/env python3
"""Top up TalentX's Current Music catalog toward a large source-backed target.

This supplements the existing Music discovery pass with a broader set of musical
occupations and paginated Wikidata queries. Existing profiles are never replaced.
New Wikidata-only records remain conservatively priced until stronger streaming,
chart, touring, award, and release evidence is available.
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

import requests

from expand_non_athlete_sources import (
    RECENT_ACTIVITY_YEARS,
    make_record,
    make_session,
    normalize,
    parse_year,
    update_taxonomy,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_TAXONOMY = DATA / "taxonomy.json"
DEFAULT_MANIFEST = DATA / "music_expansion_manifest.json"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
SOURCE_NAMESPACE = "wikidata-music-expanded"

# Broad music-talent coverage. These are all musical occupations in Wikidata.
MUSIC_OCCUPATIONS: dict[str, tuple[str, str]] = {
    "Q639669": ("Musician", "Music"),
    "Q177220": ("Singer", "Vocal"),
    "Q2252262": ("Rapper", "Hip-Hop"),
    "Q488205": ("Singer-songwriter", "Singer-Songwriter"),
    "Q130857": ("Disc jockey", "Electronic / DJ"),
    "Q855091": ("Guitarist", "Guitar"),
    "Q486748": ("Pianist", "Piano / Keys"),
    "Q386854": ("Drummer", "Drums / Percussion"),
    "Q584301": ("Bassist", "Bass"),
    "Q1259917": ("Violinist", "Violin / Strings"),
    "Q12800682": ("Saxophonist", "Saxophone / Woodwind"),
    "Q753110": ("Songwriter", "Songwriting"),
    "Q36834": ("Composer", "Composition"),
    "Q183945": ("Record producer", "Production"),
}


def sparql_query(occupation_qid: str, recent_cutoff: int, limit: int, offset: int) -> str:
    return f"""
SELECT DISTINCT ?person ?personLabel ?sitelinks ?birth ?workStart ?workEnd ?countryLabel WHERE {{
  ?person wdt:P31 wd:Q5;
          wdt:P106 wd:{occupation_qid};
          wikibase:sitelinks ?sitelinks.
  FILTER NOT EXISTS {{ ?person wdt:P570 ?death. }}
  OPTIONAL {{ ?person wdt:P569 ?birth. }}
  OPTIONAL {{ ?person wdt:P2031 ?workStart. }}
  OPTIONAL {{ ?person wdt:P2032 ?workEnd. }}
  OPTIONAL {{ ?person wdt:P27 ?country. }}
  FILTER(!BOUND(?workEnd) || YEAR(?workEnd) >= {recent_cutoff})
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY DESC(?sitelinks)
LIMIT {int(limit)}
OFFSET {int(offset)}
""".strip()


def binding_value(binding: dict[str, Any], key: str) -> str:
    value = binding.get(key)
    return str(value.get("value") or "") if isinstance(value, dict) else ""


def fetch_page(
    session: requests.Session,
    occupation_qid: str,
    role: str,
    discipline: str,
    recent_cutoff: int,
    page_size: int,
    offset: int,
    timeout: float,
) -> list[dict[str, Any]]:
    response = session.post(
        SPARQL_ENDPOINT,
        data={
            "query": sparql_query(occupation_qid, recent_cutoff, page_size, offset),
            "format": "json",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])
    rows: list[dict[str, Any]] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        uri = binding_value(binding, "person")
        match = re.search(r"/(Q\d+)$", uri)
        name = binding_value(binding, "personLabel").strip()
        if not match or not name or name == match.group(1):
            continue
        try:
            sitelinks = int(float(binding_value(binding, "sitelinks") or 0))
        except ValueError:
            sitelinks = 0
        rows.append({
            "qid": match.group(1),
            "name": name,
            "sitelinks": sitelinks,
            "birthYear": parse_year(binding_value(binding, "birth")),
            "workStartYear": parse_year(binding_value(binding, "workStart")),
            "workEndYear": parse_year(binding_value(binding, "workEnd")),
            "country": binding_value(binding, "countryLabel").strip() or "Not listed",
            "role": role,
            "discipline": discipline,
        })
    return rows


def eligible(candidate: dict[str, Any], minimum_sitelinks: int, recent_cutoff: int) -> bool:
    name = str(candidate.get("name") or "").strip()
    qid = str(candidate.get("qid") or "")
    sitelinks = int(candidate.get("sitelinks") or 0)
    if not name or not re.fullmatch(r"Q\d+", qid) or sitelinks < minimum_sitelinks:
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
    # Weakly documented people without a career-start statement need more public
    # documentation before entering the Current market.
    if candidate.get("workStartYear") is None and sitelinks < max(15, minimum_sitelinks * 3):
        return False
    return True


def merge_candidate(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    if int(incoming.get("sitelinks") or 0) > int(existing.get("sitelinks") or 0):
        existing["sitelinks"] = incoming.get("sitelinks")
    for key in ("birthYear", "workStartYear", "workEndYear"):
        if existing.get(key) is None and incoming.get(key) is not None:
            existing[key] = incoming[key]
    if existing.get("country") == "Not listed" and incoming.get("country") != "Not listed":
        existing["country"] = incoming.get("country")
    # Prefer more specific performance-oriented occupations over generic musician.
    priority = {
        "Singer": 9, "Rapper": 9, "Singer-songwriter": 9, "Guitarist": 8,
        "Pianist": 8, "Drummer": 8, "Bassist": 8, "Violinist": 8,
        "Saxophonist": 8, "Disc jockey": 7, "Songwriter": 6,
        "Composer": 5, "Record producer": 5, "Musician": 1,
    }
    if priority.get(str(incoming.get("role")), 0) > priority.get(str(existing.get("role")), 0):
        existing["role"] = incoming.get("role")
        existing["discipline"] = incoming.get("discipline")


def collect_candidates(
    session: requests.Session,
    needed: int,
    existing_names: set[str],
    existing_source_ids: set[str],
    minimum_sitelinks: int,
    recent_cutoff: int,
    per_occupation_limit: int,
    page_size: int,
    timeout: float,
    sleep_seconds: float,
) -> tuple[list[dict[str, Any]], list[str], int]:
    by_qid: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    requests_made = 0
    max_offset = max(page_size, per_occupation_limit)

    # Round-robin pagination gives the catalog a diverse mix of music professions
    # instead of exhausting one occupation before checking the others.
    for offset in range(0, max_offset, page_size):
        for qid, (role, discipline) in MUSIC_OCCUPATIONS.items():
            if offset >= per_occupation_limit:
                continue
            limit = min(page_size, per_occupation_limit - offset)
            try:
                rows = fetch_page(
                    session, qid, role, discipline, recent_cutoff,
                    limit, offset, timeout,
                )
                requests_made += 1
                for candidate in rows:
                    if not eligible(candidate, minimum_sitelinks, recent_cutoff):
                        continue
                    key = normalize(candidate.get("name"))
                    candidate_qid = str(candidate.get("qid") or "")
                    if not key or key in existing_names or candidate_qid in existing_source_ids:
                        continue
                    current = by_qid.get(candidate_qid)
                    if current is None:
                        by_qid[candidate_qid] = dict(candidate)
                    else:
                        merge_candidate(current, candidate)
            except Exception as exc:  # noqa: BLE001
                requests_made += 1
                errors.append(f"Music:{qid}:offset={offset}:{type(exc).__name__}:{exc}")
            if sleep_seconds:
                time.sleep(sleep_seconds)

        # Stop once we have a buffer above the requested amount. The final sort
        # favors stronger public documentation while maintaining occupation mix.
        if len(by_qid) >= math.ceil(needed * 1.15):
            break

    candidates = sorted(
        by_qid.values(),
        key=lambda row: (-int(row.get("sitelinks") or 0), str(row.get("name") or "")),
    )
    return candidates, errors, requests_made


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target-total", type=int, default=5000)
    parser.add_argument("--minimum-total", type=int, default=3500)
    parser.add_argument("--per-occupation-limit", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=250)
    parser.add_argument("--minimum-sitelinks", type=int, default=5)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--sleep", type=float, default=.12)
    parser.add_argument("--allow-shortfall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(seed, list):
        raise ValueError("current_seed.json must contain a JSON array")
    records = [record for record in seed if isinstance(record, dict)]
    music_before = sum(1 for record in records if record.get("primaryCategory") == "Music")
    target_total = max(music_before, int(args.target_total))
    needed = max(0, target_total - music_before)

    existing_names = {normalize(record.get("name")) for record in records if record.get("name")}
    existing_source_ids = {str(record.get("sourceRecordId")) for record in records if record.get("sourceRecordId")}
    used_ids = {str(record.get("id")) for record in records if record.get("id")}
    used_tickers = {str(record.get("ticker")) for record in records if record.get("ticker")}

    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    recent_cutoff = datetime.now(timezone.utc).year - RECENT_ACTIVITY_YEARS
    source_errors: list[str] = []
    requests_made = 0
    selected: list[dict[str, Any]] = []

    if needed:
        session = make_session()
        candidates, source_errors, requests_made = collect_candidates(
            session=session,
            needed=needed,
            existing_names=existing_names,
            existing_source_ids=existing_source_ids,
            minimum_sitelinks=max(1, args.minimum_sitelinks),
            recent_cutoff=recent_cutoff,
            per_occupation_limit=max(1, args.per_occupation_limit),
            page_size=max(1, min(args.page_size, args.per_occupation_limit)),
            timeout=args.request_timeout,
            sleep_seconds=max(0.0, args.sleep),
        )
        seen_names = set(existing_names)
        seen_qids = set(existing_source_ids)
        for candidate in candidates:
            key = normalize(candidate.get("name"))
            qid = str(candidate.get("qid") or "")
            if not key or key in seen_names or qid in seen_qids:
                continue
            selected.append(candidate)
            seen_names.add(key)
            seen_qids.add(qid)
            if len(selected) >= needed:
                break

    pool_size = music_before + len(selected)
    additions: list[dict[str, Any]] = []
    for offset, candidate in enumerate(selected, start=1):
        record = make_record(
            candidate=candidate,
            category="Music",
            benchmark_rank=music_before + offset,
            benchmark_pool_size=pool_size,
            used_ids=used_ids,
            used_tickers=used_tickers,
            verified_at=verified_at,
        )
        record["sourceNamespace"] = SOURCE_NAMESPACE
        record["discoveryFrameworkVersion"] = "music-2.0-paginated"
        record["pricingConfidence"] = min(float(record.get("pricingConfidence") or 0.0), 0.68)
        record["pricingDataStatus"] = "Expanded music discovery; streaming/chart/touring evidence pending"
        additions.append(record)

    combined = records + additions
    music_after = sum(1 for record in combined if record.get("primaryCategory") == "Music")
    ids = [str(record.get("id") or "") for record in combined]
    tickers = [str(record.get("ticker") or "") for record in combined]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate profile IDs after expanded Music discovery")
    if len(tickers) != len(set(tickers)):
        raise ValueError("Duplicate tickers after expanded Music discovery")

    shortfall = max(0, target_total - music_after)
    if music_after < max(0, args.minimum_total) and not args.allow_shortfall:
        raise RuntimeError(
            f"Music catalog reached only {music_after:,}; minimum is {args.minimum_total:,}. "
            f"Source errors: {len(source_errors)}"
        )
    if shortfall and not args.allow_shortfall:
        raise RuntimeError(f"Music catalog reached {music_after:,}; target is {target_total:,}.")

    manifest = {
        "version": "2.0-paginated",
        "generatedAt": verified_at,
        "source": SPARQL_ENDPOINT,
        "musicOccupations": [
            {"qid": qid, "role": role, "discipline": discipline}
            for qid, (role, discipline) in MUSIC_OCCUPATIONS.items()
        ],
        "musicBefore": music_before,
        "targetTotal": target_total,
        "minimumTotal": args.minimum_total,
        "actualAdditions": len(additions),
        "musicAfter": music_after,
        "shortfall": shortfall,
        "minimumSitelinks": args.minimum_sitelinks,
        "perOccupationLimit": args.per_occupation_limit,
        "pageSize": args.page_size,
        "requestsMade": requests_made,
        "sourceErrorCount": len(source_errors),
        "sourceErrors": source_errors,
        "pricingRule": "Wikidata-only expanded Music records are capped at 0.68 pricing confidence until stronger profession-specific evidence is available.",
        "statusLimitation": "Wikidata is a public identity/activity source, not a live music-industry employment roster.",
    }

    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return 0

    args.seed.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    update_taxonomy(args.taxonomy, combined)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Music catalog: {music_before:,} -> {music_after:,}; added {len(additions):,}.")
    if source_errors:
        print(f"Completed with {len(source_errors)} source request error(s).")
    if shortfall:
        print(f"Target shortfall: {shortfall:,}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
