#!/usr/bin/env python3
"""Expand TalentX's Current actor and music catalogs from Wikidata.

The curated top-100 rosters remain the editorial anchor. This builder discovers
additional living people with actor or music occupations, requires a minimum
Wikidata notability/activity signal, removes duplicate identities, adds
source/timestamp metadata, and creates conservative evidence-based metric inputs.

Wikidata does not provide a live employment roster. "Current" therefore means a
point-in-time proxy: living human, relevant occupation, and no recorded work-end
before the recent-activity cutoff. Every generated record states that limitation.
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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from build_non_athlete_catalog import initials, normalize, slugify, unique_ticker
from pricing_model import apply_pricing_to_records, clamp, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_TAXONOMY = DATA / "taxonomy.json"
DEFAULT_MANIFEST = DATA / "non_athlete_discovery_manifest.json"
DEFAULT_OVERRIDES = DATA / "pricing_overrides.json"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "TalentX-NonAthlete-Discovery/1.0 (+https://github.com/rossad213/TalentX)"

RECENT_ACTIVITY_YEARS = 3

CATEGORY_CONFIG: dict[str, dict[str, Any]] = {
    "Actor": {
        "occupations": {
            "Q33999": ("Actor", "Acting"),
            "Q10800557": ("Film actor", "Film"),
            "Q10798782": ("Television actor", "Television"),
            "Q2259451": ("Stage actor", "Theatre"),
            "Q2405480": ("Voice actor", "Voice Acting"),
        },
        "leagueOrMedium": "Film & Television",
        "teamOrPlatform": "Independent / representation not listed",
    },
    "Music": {
        "occupations": {
            "Q639669": ("Musician", "Music"),
            "Q177220": ("Singer", "Vocal"),
            "Q2252262": ("Rapper", "Hip-Hop"),
            "Q488205": ("Singer-songwriter", "Singer-Songwriter"),
            "Q130857": ("Disc jockey", "Electronic / DJ"),
        },
        "leagueOrMedium": "Music",
        "teamOrPlatform": "Independent / label not listed",
    },
}


def parse_year(value: Any) -> int | None:
    match = re.search(r"([12]\d{3})", str(value or ""))
    if not match:
        return None
    year = int(match.group(1))
    current = datetime.now(timezone.utc).year
    return year if 1850 <= year <= current + 1 else None


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=6, pool_maxsize=6)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    return session


def sparql_query(occupation_qid: str, limit: int, recent_cutoff: int) -> str:
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
""".strip()


def binding_value(binding: dict[str, Any], key: str) -> str:
    value = binding.get(key)
    return str(value.get("value") or "") if isinstance(value, dict) else ""


def fetch_occupation_candidates(
    session: requests.Session,
    occupation_qid: str,
    role: str,
    discipline: str,
    limit: int,
    timeout: float,
    recent_cutoff: int,
) -> list[dict[str, Any]]:
    response = session.post(
        SPARQL_ENDPOINT,
        data={"query": sparql_query(occupation_qid, limit, recent_cutoff), "format": "json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    bindings = payload.get("results", {}).get("bindings", [])
    output: list[dict[str, Any]] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        uri = binding_value(binding, "person")
        qid_match = re.search(r"/(Q\d+)$", uri)
        name = binding_value(binding, "personLabel").strip()
        if not qid_match or not name or name == qid_match.group(1):
            continue
        try:
            sitelinks = int(float(binding_value(binding, "sitelinks") or 0))
        except ValueError:
            sitelinks = 0
        output.append({
            "qid": qid_match.group(1),
            "name": name,
            "sitelinks": sitelinks,
            "birthYear": parse_year(binding_value(binding, "birth")),
            "workStartYear": parse_year(binding_value(binding, "workStart")),
            "workEndYear": parse_year(binding_value(binding, "workEnd")),
            "country": binding_value(binding, "countryLabel").strip() or "Not listed",
            "role": role,
            "discipline": discipline,
        })
    return output


def candidate_is_eligible(candidate: dict[str, Any], minimum_sitelinks: int, recent_cutoff: int) -> bool:
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
    if not candidate.get("workStartYear") and sitelinks < max(40, minimum_sitelinks * 3):
        return False
    return True


def merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by Wikidata ID and keep the strongest occupation evidence."""
    by_qid: dict[str, dict[str, Any]] = {}
    discipline_priority = {
        "Film": 5,
        "Television": 4,
        "Voice Acting": 3,
        "Theatre": 2,
        "Acting": 1,
        "Hip-Hop": 5,
        "Singer-Songwriter": 4,
        "Electronic / DJ": 3,
        "Vocal": 2,
        "Music": 1,
    }
    for candidate in candidates:
        qid = str(candidate.get("qid") or "")
        existing = by_qid.get(qid)
        if existing is None:
            by_qid[qid] = dict(candidate)
            continue
        if int(candidate.get("sitelinks") or 0) > int(existing.get("sitelinks") or 0):
            existing["sitelinks"] = candidate["sitelinks"]
        if discipline_priority.get(str(candidate.get("discipline")), 0) > discipline_priority.get(str(existing.get("discipline")), 0):
            existing["discipline"] = candidate.get("discipline")
            existing["role"] = candidate.get("role")
        for key in ("birthYear", "workStartYear", "workEndYear"):
            if existing.get(key) is None and candidate.get(key) is not None:
                existing[key] = candidate[key]
        if existing.get("country") == "Not listed" and candidate.get("country") != "Not listed":
            existing["country"] = candidate["country"]
    return sorted(by_qid.values(), key=lambda row: (-int(row.get("sitelinks") or 0), str(row.get("name") or "")))


def evidence_metrics(candidate: dict[str, Any], category: str) -> dict[str, float]:
    current_year = datetime.now(timezone.utc).year
    birth_year = candidate.get("birthYear")
    start_year = candidate.get("workStartYear")
    age = current_year - birth_year if isinstance(birth_year, int) else None
    years_active = max(0, current_year - start_year) if isinstance(start_year, int) else None
    sitelinks = max(0, int(candidate.get("sitelinks") or 0))

    audience = clamp(32 + math.log1p(sitelinks) * 10.5, 35, 97)
    consistency = clamp(48 + min(40, (years_active or 4) * 2.2), 45, 96)
    achievements = clamp(42 + math.log1p(sitelinks) * 8.0 + min(12, (years_active or 0) * .35), 42, 96)
    potential = clamp(92 - max(0, (age or 28) - 22) * 2.25, 34, 95)
    availability = 78.0
    performance = clamp(achievements * .45 + consistency * .35 + audience * .20, 45, 96)
    if category == "Music":
        performance = clamp(performance + 1.5, 45, 97)
    return {
        "performance": round(performance, 2),
        "achievements": round(achievements, 2),
        "consistency": round(consistency, 2),
        "potential": round(potential, 2),
        "availability": availability,
        "audience": round(audience, 2),
    }


def confidence(candidate: dict[str, Any]) -> float:
    completeness = sum(candidate.get(key) is not None for key in ("birthYear", "workStartYear", "workEndYear"))
    has_country = candidate.get("country") not in {None, "", "Not listed"}
    sitelinks = int(candidate.get("sitelinks") or 0)
    value = .72 + completeness * .035 + int(has_country) * .025 + min(.10, math.log1p(sitelinks) / 70)
    return round(float(clamp(value, .72, .94)), 2)


def make_record(
    candidate: dict[str, Any],
    category: str,
    benchmark_rank: int,
    benchmark_pool_size: int,
    used_ids: set[str],
    used_tickers: set[str],
    verified_at: str,
) -> dict[str, Any]:
    name = str(candidate["name"]).strip()
    qid = str(candidate["qid"])
    base_id = f"cur-{slugify(name)}"
    profile_id = base_id if base_id not in used_ids else f"{base_id}-{category.lower()}-{qid.lower()}"
    used_ids.add(profile_id)
    ticker = unique_ticker(name, f"wikidata:{category}:{qid}", used_tickers)
    start_year = candidate.get("workStartYear")
    birth_year = candidate.get("birthYear")
    current_year = datetime.now(timezone.utc).year
    age = current_year - birth_year if isinstance(birth_year, int) else None
    years_active = max(0, current_year - start_year) if isinstance(start_year, int) else None
    source_url = f"https://www.wikidata.org/wiki/{qid}"
    cfg = CATEGORY_CONFIG[category]
    role = str(candidate.get("role") or category)
    discipline = str(candidate.get("discipline") or category)
    proxy_note = "Living-person and recent work-period proxy; not a live employment roster"
    record: dict[str, Any] = {
        "id": profile_id,
        "name": name,
        "ticker": ticker,
        "primaryCategory": category,
        "discipline": discipline,
        "leagueOrMedium": cfg["leagueOrMedium"],
        "teamOrPlatform": cfg["teamOrPlatform"],
        "role": role,
        "country": str(candidate.get("country") or "Not listed"),
        "careerStatus": "Active-status proxy",
        "marketSegment": "Current",
        "careerStage": "Active career",
        "verificationStatus": proxy_note,
        "lastVerifiedAt": verified_at,
        "statusSource": "Wikidata occupation, living-person, and work-period statements",
        "sourceName": "Wikidata",
        "sourceUrl": source_url,
        "sourceRecordId": qid,
        "sourceNamespace": "wikidata-non-athlete",
        "dataConfidence": confidence(candidate),
        "pricingConfidence": confidence(candidate),
        "pricingDataStatus": "Public identity/activity evidence; profession performance evidence partial",
        "pricingEvidence": [source_url],
        "activeMetrics": evidence_metrics(candidate, category),
        "modelType": "Active career model",
        "avatar": initials(name),
        "description": f"{role} in {discipline}. {proxy_note}.",
        "searchText": " ".join([
            name, category, discipline, str(cfg["leagueOrMedium"]), role,
            str(candidate.get("country") or ""), "Current active"
        ]).lower(),
        "benchmarkRank": benchmark_rank,
        "benchmarkPoolSize": benchmark_pool_size,
        "wikidataSitelinks": int(candidate.get("sitelinks") or 0),
        "wikidataWorkStartYear": start_year,
        "wikidataWorkEndYear": candidate.get("workEndYear"),
        "discoveryFrameworkVersion": "1.0",
        "discoveryEvidence": proxy_note,
    }
    if age is not None:
        record["age"] = age
    if years_active is not None:
        record["yearsActive"] = years_active
        record["debutYear"] = start_year
    return record


def update_taxonomy(path: Path, records: list[dict[str, Any]]) -> None:
    taxonomy = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"categories": {}}
    categories = taxonomy.setdefault("categories", {})
    for category in CATEGORY_CONFIG:
        block = categories.setdefault(category, {"label": category, "disciplines": [], "filters": []})
        disciplines = {str(value) for value in block.get("disciplines", []) if value}
        disciplines.update(
            str(record.get("discipline"))
            for record in records
            if record.get("primaryCategory") == category and record.get("discipline")
        )
        block["disciplines"] = sorted(disciplines)
    path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_category(
    session: requests.Session,
    category: str,
    per_occupation_limit: int,
    timeout: float,
    sleep_seconds: float,
    minimum_sitelinks: int,
    recent_cutoff: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw: list[dict[str, Any]] = []
    errors: list[str] = []
    for qid, (role, discipline) in CATEGORY_CONFIG[category]["occupations"].items():
        try:
            raw.extend(fetch_occupation_candidates(
                session, qid, role, discipline, per_occupation_limit, timeout, recent_cutoff
            ))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{category}:{qid}:{type(exc).__name__}:{exc}")
        if sleep_seconds:
            time.sleep(sleep_seconds)
    merged = merge_candidates(raw)
    eligible = [row for row in merged if candidate_is_eligible(row, minimum_sitelinks, recent_cutoff)]
    return eligible, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--actor-additions", type=int, default=1000)
    parser.add_argument("--music-additions", type=int, default=1000)
    parser.add_argument("--per-occupation-limit", type=int, default=650)
    parser.add_argument("--minimum-sitelinks", type=int, default=10)
    parser.add_argument("--request-timeout", type=float, default=45.0)
    parser.add_argument("--sleep", type=float, default=.25)
    parser.add_argument("--allow-shortfall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(seed, list):
        raise ValueError("current_seed.json must contain a JSON array")
    records = [record for record in seed if isinstance(record, dict)]
    existing_names = {normalize(str(record.get("name") or "")) for record in records}
    existing_source_ids = {str(record.get("sourceRecordId") or "") for record in records if record.get("sourceRecordId")}
    used_ids = {str(record.get("id")) for record in records if record.get("id")}
    used_tickers = {str(record.get("ticker")) for record in records if record.get("ticker")}

    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    current_year = datetime.now(timezone.utc).year
    recent_cutoff = current_year - RECENT_ACTIVITY_YEARS
    session = make_session()
    additions_by_category: dict[str, list[dict[str, Any]]] = {}
    source_errors: list[str] = []

    requested = {"Music": max(0, args.music_additions), "Actor": max(0, args.actor_additions)}
    for category in ("Music", "Actor"):
        candidates, errors = discover_category(
            session,
            category,
            max(1, args.per_occupation_limit),
            args.request_timeout,
            args.sleep,
            max(1, args.minimum_sitelinks),
            recent_cutoff,
        )
        source_errors.extend(errors)
        selected: list[dict[str, Any]] = []
        for candidate in candidates:
            key = normalize(str(candidate.get("name") or ""))
            qid = str(candidate.get("qid") or "")
            if not key or key in existing_names or qid in existing_source_ids:
                continue
            selected.append(candidate)
            existing_names.add(key)
            existing_source_ids.add(qid)
            if len(selected) >= requested[category]:
                break
        additions_by_category[category] = selected
        if len(selected) < requested[category] and not args.allow_shortfall:
            detail = "; ".join(source_errors[-5:]) if source_errors else "no source error reported"
            raise RuntimeError(
                f"Only found {len(selected)} eligible new {category} records; "
                f"requested {requested[category]}. Recent source detail: {detail}"
            )

    existing_category_counts = Counter(str(record.get("primaryCategory") or "") for record in records)
    additions: list[dict[str, Any]] = []
    for category in ("Music", "Actor"):
        selected = additions_by_category[category]
        pool_size = existing_category_counts[category] + len(selected)
        for offset, candidate in enumerate(selected, start=1):
            additions.append(make_record(
                candidate,
                category,
                existing_category_counts[category] + offset,
                pool_size,
                used_ids,
                used_tickers,
                verified_at,
            ))

    combined = records + additions
    if len({str(record.get("id")) for record in combined}) != len(combined):
        raise ValueError("Duplicate profile IDs after non-athlete discovery")
    tickers = [str(record.get("ticker") or "") for record in combined]
    if len(set(tickers)) != len(tickers):
        raise ValueError("Duplicate tickers after non-athlete discovery")

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
        "requestedAdditions": requested,
        "actualAdditions": {category: len(additions_by_category[category]) for category in requested},
        "categoryCountsAfterDiscovery": dict(sorted(counts.items())),
        "sourceErrors": source_errors,
        "statusLimitation": (
            "Wikidata is not a live employment roster. Current eligibility uses living-person, "
            "occupation, sitelink, and work-end proxies and should be refreshed regularly."
        ),
    }

    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return 0

    args.seed.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    update_taxonomy(args.taxonomy, combined)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Added {len(additions_by_category['Music']):,} Music and {len(additions_by_category['Actor']):,} Actor records.")
    if source_errors:
        print(f"Completed with {len(source_errors)} source request error(s); target counts were still met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
