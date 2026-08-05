#!/usr/bin/env python3
"""Add structured career evidence for priority soccer players.

This source adapter uses Wikidata's public API to resolve identity, birth date,
career longevity, awards, club history, and global notability. It is intentionally
limited to the curated priority-soccer list so the full catalog build remains
fast and respectful of the public endpoint.

The adapter does not hard-code prices. It supplies evidence that the shared
TalentX enrichment framework and pricing engine can evaluate.
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

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_CATALOG = DATA / "current_catalog.json"
DEFAULT_PRIORITY = DATA / "priority_soccer_names.json"
API = "https://www.wikidata.org/w/api.php"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
USER_AGENT = "TalentX-Soccer-Evidence/1.0 (+https://github.com/rossad213/TalentX)"


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, connect=3, read=3, backoff_factor=.5,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def claim_values(entity: dict[str, Any], prop: str) -> list[Any]:
    output: list[Any] = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {}) if isinstance(claim, dict) else {}
        datavalue = snak.get("datavalue") if isinstance(snak, dict) else None
        if isinstance(datavalue, dict) and "value" in datavalue:
            output.append(datavalue["value"])
    return output


def parse_year(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("time")
    match = re.search(r"([12]\d{3})", str(value or ""))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1900 <= year <= datetime.now(timezone.utc).year else None


def search_player(session: requests.Session, name: str, timeout: float) -> tuple[str, dict[str, Any]] | None:
    response = session.get(API, params={
        "action": "wbsearchentities", "search": name, "language": "en",
        "format": "json", "limit": 8, "type": "item",
    }, timeout=timeout)
    response.raise_for_status()
    results = response.json().get("search", [])
    exact = norm(name)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in results:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        label = str(item.get("label") or "")
        description = str(item.get("description") or "").lower()
        score = 0
        if norm(label) == exact:
            score += 100
        if "football" in description or "soccer" in description:
            score += 50
        if "player" in description:
            score += 10
        ranked.append((score, item))
    if not ranked:
        return None
    best = max(ranked, key=lambda pair: pair[0])
    if best[0] < 50:
        return None
    qid = str(best[1]["id"])
    entity_response = session.get(ENTITY.format(qid=qid), timeout=timeout)
    entity_response.raise_for_status()
    entity = entity_response.json().get("entities", {}).get(qid)
    return (qid, entity) if isinstance(entity, dict) else None


def derive_evidence(entity: dict[str, Any]) -> dict[str, Any]:
    now_year = datetime.now(timezone.utc).year
    birth_years = [parse_year(value) for value in claim_values(entity, "P569")]
    birth_year = next((year for year in birth_years if year), None)
    age = now_year - birth_year if birth_year else None

    start_years: list[int] = []
    for claim in entity.get("claims", {}).get("P54", []):
        if not isinstance(claim, dict):
            continue
        for qualifier in claim.get("qualifiers", {}).get("P580", []):
            datavalue = qualifier.get("datavalue") if isinstance(qualifier, dict) else None
            year = parse_year(datavalue.get("value") if isinstance(datavalue, dict) else None)
            if year:
                start_years.append(year)
    debut_year = min(start_years) if start_years else None
    years_active = max(0, now_year - debut_year) if debut_year else None

    awards_count = len(claim_values(entity, "P166"))
    clubs_count = len(claim_values(entity, "P54"))
    national_teams_count = len(claim_values(entity, "P1532"))
    sitelinks = len(entity.get("sitelinks", {}))

    # Evidence scores are source-derived and deliberately conservative. They do
    # not assign a player-specific price; they summarize durable evidence.
    longevity = clamp((years_active or 0) * 5.0, 15, 99)
    achievements = clamp(38 + awards_count * 5.5 + min(15, clubs_count * 1.2), 38, 99)
    audience = clamp(35 + math.log1p(max(0, sitelinks)) * 12.0, 35, 99)
    consistency = clamp(40 + (years_active or 0) * 3.2 + min(10, clubs_count), 40, 98)
    availability = 78.0
    potential = clamp(100 - max(0, (age or 27) - 22) * 3.2, 28, 97)
    performance = clamp(45 + achievements * .28 + consistency * .24, 45, 96)

    completeness = sum(value is not None for value in (age, debut_year, years_active))
    completeness += int(awards_count > 0) + int(clubs_count > 0) + int(sitelinks > 0)
    confidence = clamp(.66 + completeness * .05, .66, .96)

    return {
        "age": age,
        "debutYear": debut_year,
        "yearsActive": years_active,
        "wikidataAwardsCount": awards_count,
        "wikidataClubClaims": clubs_count,
        "wikidataNationalTeamClaims": national_teams_count,
        "wikidataSitelinks": sitelinks,
        "pricingConfidence": confidence,
        "activeMetrics": {
            "performance": performance,
            "achievements": achievements,
            "consistency": consistency,
            "potential": potential,
            "availability": availability,
            "audience": audience,
        },
    }


def merge_record(record: dict[str, Any], qid: str, evidence: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    for key in ("age", "debutYear", "yearsActive"):
        if evidence.get(key) is not None:
            result[key] = evidence[key]
    result["pricingConfidence"] = max(float(result.get("pricingConfidence") or 0), float(evidence["pricingConfidence"]))
    existing = result.get("activeMetrics") if isinstance(result.get("activeMetrics"), dict) else {}
    result["activeMetrics"] = {
        key: round(max(float(existing.get(key) or 0), float(value)), 2)
        for key, value in evidence["activeMetrics"].items()
    }
    for key in ("wikidataAwardsCount", "wikidataClubClaims", "wikidataNationalTeamClaims", "wikidataSitelinks"):
        result[key] = evidence[key]
    result["soccerEvidenceSource"] = f"https://www.wikidata.org/wiki/{qid}"
    result["soccerEvidenceStatus"] = "Wikidata identity and career evidence"
    urls = result.get("pricingEvidence") if isinstance(result.get("pricingEvidence"), list) else []
    source_url = result["soccerEvidenceSource"]
    result["pricingEvidence"] = list(dict.fromkeys([*urls, source_url]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--priority", type=Path, default=DEFAULT_PRIORITY)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--sleep", type=float, default=.15)
    args = parser.parse_args()

    records = json.loads(args.catalog.read_text(encoding="utf-8"))
    priority = json.loads(args.priority.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not isinstance(priority, list):
        raise SystemExit("Catalog and priority files must contain JSON arrays")

    wanted = {norm(name) for name in priority if str(name).strip()}
    session = make_session()
    updated: list[dict[str, Any]] = []
    enriched = 0
    failures: list[str] = []

    for record in records:
        if not isinstance(record, dict) or norm(record.get("discipline")) != "soccer" or norm(record.get("name")) not in wanted:
            updated.append(record)
            continue
        name = str(record.get("name") or "").strip()
        try:
            resolved = search_player(session, name, args.timeout)
            if not resolved:
                failures.append(name)
                updated.append(record)
                continue
            qid, entity = resolved
            updated.append(merge_record(record, qid, derive_evidence(entity)))
            enriched += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name} ({type(exc).__name__})")
            updated.append(record)
        if args.sleep:
            time.sleep(args.sleep)

    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wikidata soccer evidence enriched {enriched}/{len(wanted)} priority names.")
    if failures:
        print("Unresolved: " + ", ".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
