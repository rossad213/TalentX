#!/usr/bin/env python3
"""Strict source discovery for TalentX Music listings.

This replaces broad Wikidata `musician` discovery with a deliberately conservative
pipeline. A discovered person may enter Music only when Wikidata says they have a
specific music profession, provides a MusicBrainz artist ID (P434), and does not
list a screen-first occupation such as actor, director, filmmaker, or screenwriter.

Curated Music records are preserved separately by build_non_athlete_catalog.py.
This script only adds new source-discovered records.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
DEFAULT_MANIFEST = DATA / "strict_music_discovery_manifest.json"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
SOURCE_NAMESPACE = "wikidata-music-strict"

# Intentionally excludes generic Wikidata occupation Q639669 ("musician").
# New Music listings must match a concrete music profession.
STRICT_MUSIC_OCCUPATIONS: dict[str, tuple[str, str]] = {
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

SCREEN_OCCUPATIONS = (
    "Q33999",      # actor
    "Q10800557",  # film actor
    "Q10798782",  # television actor
    "Q2259451",   # stage actor
    "Q2405480",   # voice actor
    "Q2526255",   # film director
    "Q6102247",   # film/television director
    "Q28389",     # screenwriter
    "Q1414443",   # filmmaker
)


def sparql_query(occupation_qid: str, recent_cutoff: int, limit: int, offset: int) -> str:
    screen_values = " ".join(f"wd:{qid}" for qid in SCREEN_OCCUPATIONS)
    return f"""
SELECT DISTINCT ?person ?personLabel ?sitelinks ?birth ?workStart ?workEnd ?countryLabel ?mbid WHERE {{
  ?person wdt:P31 wd:Q5;
          wdt:P106 wd:{occupation_qid};
          wdt:P434 ?mbid;
          wikibase:sitelinks ?sitelinks.
  FILTER NOT EXISTS {{ ?person wdt:P570 ?death. }}
  FILTER NOT EXISTS {{
    VALUES ?screenOccupation {{ {screen_values} }}
    ?person wdt:P106 ?screenOccupation.
  }}
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
    session: Any,
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
        data={"query": sparql_query(occupation_qid, recent_cutoff, page_size, offset), "format": "json"},
        timeout=timeout,
    )
    response.raise_for_status()
    rows: list[dict[str, Any]] = []
    for binding in response.json().get("results", {}).get("bindings", []):
        if not isinstance(binding, dict):
            continue
        uri = binding_value(binding, "person")
        match = re.search(r"/(Q\d+)$", uri)
        name = binding_value(binding, "personLabel").strip()
        mbid = binding_value(binding, "mbid").strip()
        if not match or not name or name == match.group(1) or not mbid:
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
            "musicBrainzArtistId": mbid,
        })
    return rows


def eligible(candidate: dict[str, Any], minimum_sitelinks: int, recent_cutoff: int) -> bool:
    qid = str(candidate.get("qid") or "")
    name = str(candidate.get("name") or "").strip()
    mbid = str(candidate.get("musicBrainzArtistId") or "").strip()
    sitelinks = int(candidate.get("sitelinks") or 0)
    if not re.fullmatch(r"Q\d+", qid) or not name or not mbid or sitelinks < minimum_sitelinks:
        return False
    current_year = datetime.now(timezone.utc).year
    birth = candidate.get("birthYear")
    if isinstance(birth, int):
        age = current_year - birth
        if age < 12 or age > 100:
            return False
    work_end = candidate.get("workEndYear")
    if isinstance(work_end, int) and work_end < recent_cutoff:
        return False
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
    # Prefer performance-facing roles over behind-the-scenes roles when multiple
    # qualifying music occupations exist.
    priority = {
        "Singer-songwriter": 12, "Rapper": 11, "Singer": 10, "Disc jockey": 9,
        "Guitarist": 8, "Pianist": 8, "Drummer": 8, "Bassist": 8,
        "Violinist": 8, "Saxophonist": 8, "Songwriter": 7,
        "Composer": 6, "Record producer": 6,
    }
    if priority.get(str(incoming.get("role")), 0) > priority.get(str(existing.get("role")), 0):
        existing["role"] = incoming.get("role")
        existing["discipline"] = incoming.get("discipline")
    if not existing.get("musicBrainzArtistId") and incoming.get("musicBrainzArtistId"):
        existing["musicBrainzArtistId"] = incoming["musicBrainzArtistId"]


def collect_candidates(
    session: Any,
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

    for offset in range(0, max_offset, page_size):
        for qid, (role, discipline) in STRICT_MUSIC_OCCUPATIONS.items():
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
                    name_key = normalize(candidate.get("name"))
                    candidate_qid = str(candidate.get("qid") or "")
                    if not name_key or name_key in existing_names or candidate_qid in existing_source_ids:
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
                time.sleep(max(0.0, sleep_seconds))
        if len(by_qid) >= math.ceil(needed * 1.10):
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
    parser.add_argument("--minimum-total", type=int, default=1000)
    parser.add_argument("--per-occupation-limit", type=int, default=1200)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--minimum-sitelinks", type=int, default=5)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--sleep", type=float, default=.08)
    parser.add_argument("--allow-shortfall", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")
    records = [record for record in payload if isinstance(record, dict)]
    music_before = sum(1 for record in records if record.get("primaryCategory") == "Music")
    target_total = max(music_before, int(args.target_total))
    needed = max(0, target_total - music_before)

    existing_names = {normalize(record.get("name")) for record in records if record.get("name")}
    existing_source_ids = {str(record.get("sourceRecordId")) for record in records if record.get("sourceRecordId")}
    used_ids = {str(record.get("id")) for record in records if record.get("id")}
    used_tickers = {str(record.get("ticker")) for record in records if record.get("ticker")}
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    recent_cutoff = datetime.now(timezone.utc).year - RECENT_ACTIVITY_YEARS

    selected: list[dict[str, Any]] = []
    errors: list[str] = []
    requests_made = 0
    if needed:
        session = make_session()
        candidates, errors, requests_made = collect_candidates(
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
        record["discoveryFrameworkVersion"] = "music-3.0-strict-source"
        record["musicCategoryVerified"] = True
        record["musicCategoryVerification"] = "Specific Wikidata music profession + MusicBrainz artist ID + no screen occupation"
        record["musicBrainzArtistIds"] = [str(candidate["musicBrainzArtistId"])]
        record["verifiedMusicOccupations"] = [str(candidate["role"])]
        record["pricingConfidence"] = min(float(record.get("pricingConfidence") or 0.0), 0.68)
        record["pricingDataStatus"] = "Strict music-source identity verified; streaming/chart/touring evidence pending"
        additions.append(record)

    combined = records + additions
    music_after = sum(1 for record in combined if record.get("primaryCategory") == "Music")
    shortfall = max(0, target_total - music_after)
    if music_after < max(0, args.minimum_total) and not args.allow_shortfall:
        raise RuntimeError(f"Strict Music discovery reached {music_after:,}; minimum is {args.minimum_total:,}.")
    if shortfall and not args.allow_shortfall:
        raise RuntimeError(f"Strict Music discovery reached {music_after:,}; target is {target_total:,}.")

    args.seed.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    update_taxonomy(args.taxonomy, combined)
    manifest = {
        "version": "3.0-strict-source",
        "generatedAt": verified_at,
        "source": SPARQL_ENDPOINT,
        "genericMusicianOccupationExcluded": True,
        "screenOccupationsExcludedAtSource": list(SCREEN_OCCUPATIONS),
        "musicOccupations": [
            {"qid": qid, "role": role, "discipline": discipline}
            for qid, (role, discipline) in STRICT_MUSIC_OCCUPATIONS.items()
        ],
        "requiresMusicBrainzArtistId": True,
        "musicBefore": music_before,
        "targetTotal": target_total,
        "minimumTotal": args.minimum_total,
        "actualAdditions": len(additions),
        "musicAfter": music_after,
        "shortfall": shortfall,
        "requestsMade": requests_made,
        "sourceErrorCount": len(errors),
        "sourceErrors": errors,
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Strict Music catalog: {music_before:,} -> {music_after:,}; added {len(additions):,}.")
    if shortfall:
        print(f"Quality-first shortfall versus target: {shortfall:,}.")
    if errors:
        print(f"Completed with {len(errors)} source request error(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
