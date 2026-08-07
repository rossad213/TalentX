#!/usr/bin/env python3
"""Top up thin TalentX individual-sport categories from Wikidata.

The official/ranked roster file remains the editorial anchor. This script adds
living, age-plausible people with a relevant sport occupation and no recorded
career end before the recent cutoff. Because Wikidata is not a live league
roster, generated listings are explicitly marked as conservative current-career
proxies and receive capped provisional pricing confidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_MANIFEST = DATA / "individual_sport_discovery_manifest.json"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "TalentX-IndividualSport-Discovery/1.0 (+https://github.com/rossad213/TalentX)"
SOURCE_NAMESPACE = "wikidata-individual-sport"
RECENT_ACTIVITY_YEARS = 3

SPORT_CONFIG: dict[str, dict[str, Any]] = {
    "Tennis": {
        "occupations": {"Q10833314": "Tennis Player"},
        "leagueOrMedium": "Professional Tennis",
        "minAge": 15,
        "maxAge": 42,
    },
    "Golf": {
        "occupations": {"Q11303721": "Golfer", "Q490253": "Professional Golfer"},
        "leagueOrMedium": "Professional Golf",
        "minAge": 16,
        "maxAge": 60,
    },
    "Motorsport": {
        "occupations": {"Q378622": "Racing Driver", "Q3014296": "Motorcycle Racer"},
        "leagueOrMedium": "International Motorsport",
        "minAge": 15,
        "maxAge": 55,
    },
    "Combat Sports": {
        "occupations": {
            "Q11607585": "Mixed Martial Artist",
            "Q11338576": "Boxer",
            "Q11296761": "Kickboxer",
        },
        "leagueOrMedium": "Professional Combat Sports",
        "minAge": 18,
        "maxAge": 45,
    },
    "Cricket": {
        "occupations": {"Q12299841": "Cricketer"},
        "leagueOrMedium": "International Cricket",
        "minAge": 16,
        "maxAge": 45,
    },
}


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def slug(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def parse_year(value: Any) -> int | None:
    match = re.search(r"([12]\d{3})", str(value or ""))
    if not match:
        return None
    year = int(match.group(1))
    current = datetime.now(timezone.utc).year
    return year if 1850 <= year <= current + 1 else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    return session


def sparql_query(
    occupation_qid: str,
    min_birth_year: int,
    max_birth_year: int,
    recent_cutoff: int,
    limit: int,
    offset: int,
) -> str:
    return f"""
SELECT DISTINCT ?person ?personLabel ?sitelinks ?birth ?workStart ?workEnd ?countryLabel WHERE {{
  ?person wdt:P31 wd:Q5;
          wdt:P106 wd:{occupation_qid};
          wdt:P569 ?birth;
          wikibase:sitelinks ?sitelinks.
  FILTER NOT EXISTS {{ ?person wdt:P570 ?death. }}
  FILTER(YEAR(?birth) >= {min_birth_year} && YEAR(?birth) <= {max_birth_year})
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


def fetch_candidates_page(
    session: requests.Session,
    discipline: str,
    occupation_qid: str,
    role: str,
    limit: int,
    offset: int,
    timeout: float,
    recent_cutoff: int,
) -> list[dict[str, Any]]:
    config = SPORT_CONFIG[discipline]
    current_year = datetime.now(timezone.utc).year
    response = session.post(
        SPARQL_ENDPOINT,
        data={
            "query": sparql_query(
                occupation_qid,
                current_year - int(config["maxAge"]),
                current_year - int(config["minAge"]),
                recent_cutoff,
                limit,
                offset,
            ),
            "format": "json",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])
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
            "discipline": discipline,
            "role": role,
            "sitelinks": sitelinks,
            "birthYear": parse_year(binding_value(binding, "birth")),
            "workStartYear": parse_year(binding_value(binding, "workStart")),
            "workEndYear": parse_year(binding_value(binding, "workEnd")),
            "country": binding_value(binding, "countryLabel").strip() or "Not listed",
        })
    return output


def candidate_is_eligible(candidate: dict[str, Any], minimum_sitelinks: int, recent_cutoff: int) -> bool:
    discipline = str(candidate.get("discipline") or "")
    config = SPORT_CONFIG.get(discipline)
    if config is None:
        return False
    name = str(candidate.get("name") or "").strip()
    qid = str(candidate.get("qid") or "")
    if not name or not re.fullmatch(r"Q\d+", qid):
        return False
    if int(candidate.get("sitelinks") or 0) < minimum_sitelinks:
        return False
    birth_year = candidate.get("birthYear")
    if not isinstance(birth_year, int):
        return False
    age = datetime.now(timezone.utc).year - birth_year
    if age < int(config["minAge"]) or age > int(config["maxAge"]):
        return False
    work_end = candidate.get("workEndYear")
    if isinstance(work_end, int) and work_end < recent_cutoff:
        return False
    if candidate.get("workStartYear") is None and int(candidate.get("sitelinks") or 0) < max(5, minimum_sitelinks * 2):
        return False
    return True


def merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_qid: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        qid = str(candidate.get("qid") or "")
        current = by_qid.get(qid)
        if current is None:
            by_qid[qid] = dict(candidate)
            continue
        if int(candidate.get("sitelinks") or 0) > int(current.get("sitelinks") or 0):
            current["sitelinks"] = candidate.get("sitelinks")
        if current.get("country") == "Not listed" and candidate.get("country") != "Not listed":
            current["country"] = candidate.get("country")
        if current.get("workStartYear") is None and candidate.get("workStartYear") is not None:
            current["workStartYear"] = candidate.get("workStartYear")
        role_priority = {"Professional Golfer": 3, "Mixed Martial Artist": 3, "Racing Driver": 3, "Tennis Player": 3, "Cricketer": 3, "Motorcycle Racer": 2, "Boxer": 2, "Kickboxer": 1, "Golfer": 1}
        if role_priority.get(str(candidate.get("role")), 0) > role_priority.get(str(current.get("role")), 0):
            current["role"] = candidate.get("role")
    return sorted(by_qid.values(), key=lambda row: (-int(row.get("sitelinks") or 0), str(row.get("name") or "")))


def provisional_metrics(candidate: dict[str, Any]) -> dict[str, float]:
    current_year = datetime.now(timezone.utc).year
    age = current_year - int(candidate["birthYear"])
    start = candidate.get("workStartYear")
    years_active = max(1, current_year - int(start)) if isinstance(start, int) else 4
    sitelinks = max(0, int(candidate.get("sitelinks") or 0))
    audience = clamp(50 + math.log1p(sitelinks) * 6.5, 52, 82)
    consistency = clamp(58 + min(18, years_active * 1.25), 58, 78)
    achievements = clamp(54 + math.log1p(sitelinks) * 5.5 + min(10, years_active * .45), 55, 78)
    potential = clamp(88 - max(0, age - 21) * 1.45, 52, 88)
    performance = clamp(achievements * .42 + consistency * .36 + audience * .22, 57, 79)
    return {
        "performance": round(performance, 2),
        "achievements": round(achievements, 2),
        "consistency": round(consistency, 2),
        "potential": round(potential, 2),
        "availability": 76.0,
        "audience": round(audience, 2),
    }


def discovery_confidence(candidate: dict[str, Any]) -> float:
    completeness = sum(candidate.get(key) is not None for key in ("birthYear", "workStartYear", "workEndYear"))
    country = candidate.get("country") not in {None, "", "Not listed"}
    sitelinks = int(candidate.get("sitelinks") or 0)
    value = .48 + completeness * .025 + int(country) * .02 + min(.06, math.log1p(sitelinks) / 80)
    return round(clamp(value, .48, .62), 2)


def ticker_base(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii"))
    base = "".join(word[0] for word in words[:5]) if len(words) > 1 else "".join(words)[:5]
    return (re.sub(r"[^A-Za-z0-9]", "", base).upper() or "TX")[:5]


def unique_ticker(name: str, used: set[str]) -> str:
    base = ticker_base(name)
    if base not in used:
        used.add(base)
        return base
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest().upper()
    for width in range(1, 5):
        candidate = (base[: max(1, 5 - width)] + digest[:width])[:5]
        if candidate not in used:
            used.add(candidate)
            return candidate
    index = 2
    while True:
        suffix = str(index)
        candidate = (base[: max(1, 5 - len(suffix))] + suffix)[:5]
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def unique_id(discipline: str, name: str, used: set[str]) -> str:
    base = f"athlete-{slug(discipline)}-{slug(name)}"
    candidate, index = base, 2
    while candidate in used:
        candidate, index = f"{base}-{index}", index + 1
    used.add(candidate)
    return candidate


def make_record(candidate: dict[str, Any], used_ids: set[str], used_tickers: set[str], verified_at: str) -> dict[str, Any]:
    name = str(candidate["name"]).strip()
    discipline = str(candidate["discipline"])
    config = SPORT_CONFIG[discipline]
    qid = str(candidate["qid"])
    confidence = discovery_confidence(candidate)
    role = str(candidate.get("role") or "Athlete")
    league = str(config["leagueOrMedium"])
    country = str(candidate.get("country") or "Not listed")
    return {
        "id": unique_id(discipline, name, used_ids),
        "name": name,
        "ticker": unique_ticker(name, used_tickers),
        "primaryCategory": "Athlete",
        "discipline": discipline,
        "leagueOrMedium": league,
        "teamOrPlatform": league,
        "role": role,
        "country": country,
        "careerStatus": "Active",
        "marketSegment": "Current",
        "verificationStatus": "Wikidata living/recent-career proxy — current participation not guaranteed",
        "lastVerifiedAt": verified_at,
        "statusSource": "Wikidata",
        "sourceName": "Wikidata",
        "sourceUrl": f"https://www.wikidata.org/wiki/{qid}",
        "sourceRecordId": qid,
        "sourceNamespace": SOURCE_NAMESPACE,
        "sourceType": "living-recent-career-proxy",
        "dataConfidence": confidence,
        "pricingConfidence": confidence,
        "pricingDataStatus": "Provisional — profession-specific results and rankings pending",
        "activeMetrics": provisional_metrics(candidate),
        "legacyMetrics": {},
        "modelType": "Active career model",
        "careerStage": "Current-career proxy",
        "birthYear": candidate.get("birthYear"),
        "workStartYear": candidate.get("workStartYear"),
        "workEndYear": candidate.get("workEndYear"),
        "wikidataSitelinks": int(candidate.get("sitelinks") or 0),
        "avatar": "".join(part[0] for part in name.split()[:2]).upper(),
        "description": (
            f"Source-backed {discipline} discovery listing for {role}. Wikidata confirms a living person, "
            "the relevant occupation, an age-plausible career window, and no older recorded career end. "
            "Current competition status and pricing remain provisional until sport-specific evidence is added."
        ),
        "searchText": " ".join([name, "Athlete", discipline, league, role, country, "Active", "Current"]).lower(),
    }


def merge_existing(record: dict[str, Any], candidate: dict[str, Any], verified_at: str) -> bool:
    before = json.dumps(record, sort_keys=True, ensure_ascii=False)
    qid = str(candidate["qid"])
    record.setdefault("wikidataId", qid)
    record.setdefault("wikidataUrl", f"https://www.wikidata.org/wiki/{qid}")
    record["individualSportDiscoveryVerifiedAt"] = verified_at
    record["individualSportDiscoverySitelinks"] = int(candidate.get("sitelinks") or 0)
    if not record.get("country") and candidate.get("country"):
        record["country"] = candidate["country"]
    for key in ("birthYear", "workStartYear", "workEndYear"):
        if record.get(key) is None and candidate.get(key) is not None:
            record[key] = candidate[key]
    return before != json.dumps(record, sort_keys=True, ensure_ascii=False)


def top_up_records(
    records: list[dict[str, Any]],
    candidates_by_discipline: dict[str, list[dict[str, Any]]],
    target_per_discipline: int,
    verified_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output = [dict(record) for record in records if isinstance(record, dict)]
    used_ids = {str(record.get("id")) for record in output if record.get("id")}
    used_tickers = {str(record.get("ticker")).upper() for record in output if record.get("ticker")}
    by_name: dict[str, int] = {}
    by_qid: dict[str, int] = {}
    for index, record in enumerate(output):
        name_key = normalize(record.get("name"))
        if name_key and name_key not in by_name:
            by_name[name_key] = index
        qid = str(record.get("sourceRecordId") or record.get("wikidataId") or "")
        if re.fullmatch(r"Q\d+", qid) and qid not in by_qid:
            by_qid[qid] = index

    counts_before = Counter(str(record.get("discipline") or "") for record in output)
    changes: dict[str, dict[str, int]] = {}
    for discipline in SPORT_CONFIG:
        current = counts_before[discipline]
        needed = max(0, target_per_discipline - current)
        added = enriched = skipped = 0
        seen_candidates: set[str] = set()
        for candidate in candidates_by_discipline.get(discipline, []):
            qid = str(candidate.get("qid") or "")
            name_key = normalize(candidate.get("name"))
            if not qid or not name_key or qid in seen_candidates:
                continue
            seen_candidates.add(qid)
            existing_index = by_qid.get(qid)
            if existing_index is None:
                existing_index = by_name.get(name_key)
            if existing_index is not None:
                if merge_existing(output[existing_index], candidate, verified_at):
                    enriched += 1
                else:
                    skipped += 1
                continue
            if added >= needed:
                break
            record = make_record(candidate, used_ids, used_tickers, verified_at)
            output.append(record)
            index = len(output) - 1
            by_name[name_key] = index
            by_qid[qid] = index
            added += 1
        changes[discipline] = {"before": current, "needed": needed, "added": added, "enriched": enriched, "skipped": skipped}

    counts_after = Counter(str(record.get("discipline") or "") for record in output)
    summary = {
        "targetPerDiscipline": target_per_discipline,
        "recordsBefore": len(records),
        "recordsAfter": len(output),
        "netAdded": len(output) - len(records),
        "countsBefore": {discipline: counts_before[discipline] for discipline in SPORT_CONFIG},
        "countsAfter": {discipline: counts_after[discipline] for discipline in SPORT_CONFIG},
        "changes": changes,
    }
    return output, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target-per-discipline", type=int, default=200)
    parser.add_argument("--minimum-per-discipline", type=int, default=150)
    parser.add_argument("--per-occupation-limit", type=int, default=440)
    parser.add_argument("--page-size", type=int, default=220)
    parser.add_argument("--minimum-sitelinks", type=int, default=2)
    parser.add_argument("--request-timeout", type=float, default=45.0)
    parser.add_argument("--allow-shortfall", action="store_true")
    args = parser.parse_args()

    if args.target_per_discipline < 1 or args.minimum_per_discipline < 1:
        raise ValueError("Targets must be positive")
    if args.minimum_per_discipline > args.target_per_discipline:
        raise ValueError("minimum-per-discipline cannot exceed target-per-discipline")

    records = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")

    current_year = datetime.now(timezone.utc).year
    recent_cutoff = current_year - RECENT_ACTIVITY_YEARS
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    existing_counts = Counter(str(record.get("discipline") or "") for record in records if isinstance(record, dict))
    session = make_session()
    candidates_by_discipline: dict[str, list[dict[str, Any]]] = {}
    source_errors: dict[str, list[str]] = {}

    for discipline, config in SPORT_CONFIG.items():
        if existing_counts[discipline] >= args.target_per_discipline:
            candidates_by_discipline[discipline] = []
            source_errors[discipline] = []
            continue
        gathered: list[dict[str, Any]] = []
        errors: list[str] = []
        for occupation_qid, role in config["occupations"].items():
            for offset in range(0, args.per_occupation_limit, args.page_size):
                limit = min(args.page_size, args.per_occupation_limit - offset)
                try:
                    page = fetch_candidates_page(
                        session,
                        discipline,
                        occupation_qid,
                        role,
                        limit,
                        offset,
                        args.request_timeout,
                        recent_cutoff,
                    )
                    gathered.extend(page)
                    if len(page) < limit:
                        break
                except Exception as exc:
                    errors.append(f"{occupation_qid}@{offset}:{type(exc).__name__}:{exc}")
                    break
                time.sleep(0.35)
        eligible = [candidate for candidate in merge_candidates(gathered) if candidate_is_eligible(candidate, args.minimum_sitelinks, recent_cutoff)]
        candidates_by_discipline[discipline] = eligible
        source_errors[discipline] = errors
        print(f"{discipline}: {len(gathered):,} source rows; {len(eligible):,} eligible candidates; {len(errors)} source errors", flush=True)

    updated, summary = top_up_records(records, candidates_by_discipline, args.target_per_discipline, verified_at)
    shortfalls = {
        discipline: count
        for discipline, count in summary["countsAfter"].items()
        if count < args.minimum_per_discipline
    }
    if shortfalls and not args.allow_shortfall:
        raise RuntimeError(f"Individual-sport discovery minimums were not met: {shortfalls}")

    args.seed.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        **summary,
        "generatedAt": verified_at,
        "minimumPerDiscipline": args.minimum_per_discipline,
        "minimumSitelinks": args.minimum_sitelinks,
        "recentCareerCutoffYear": recent_cutoff,
        "sourceNamespace": SOURCE_NAMESPACE,
        "sourceEndpoint": SPARQL_ENDPOINT,
        "sourceErrors": source_errors,
        "shortfalls": shortfalls,
        "statusRule": (
            "Living human with a relevant Wikidata occupation, a discipline-specific age window, "
            "and no recorded work end before the recent cutoff. This is a current-career proxy, not a live roster guarantee."
        ),
        "pricingRule": (
            "Wikidata discoveries receive capped conservative confidence and provisional metrics. "
            "Official ranked roster records and existing evidence-enriched records are preserved."
        ),
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Individual-sport counts:", json.dumps(summary["countsAfter"], sort_keys=True))
    print("Net new discovery listings:", summary["netAdded"])
    if shortfalls:
        print("WARNING: source shortfalls:", json.dumps(shortfalls, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
