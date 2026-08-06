#!/usr/bin/env python3
"""Enrich TalentX's existing curated Music and Actor records with Wikidata.

The source-discovery pipeline already adds new names, but it historically skipped
names that were present in the reviewed top-100 rosters. That left curated stars
with less career evidence than newly discovered records. This adapter resolves the
existing curated identities, adds durable career metadata, and records a moderate
review-evidence floor without replacing the curated category metrics.

Wikidata identity and work-period statements are not a substitute for streaming,
charts, awards databases, touring, box office, billing, or current-project feeds.
The pricing engine therefore treats this as supporting evidence rather than a
complete profession-performance feed.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_MANIFEST = DATA / "curated_non_athlete_evidence_manifest.json"
API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "TalentX-Curated-NonAthlete-Evidence/1.0 (+https://github.com/rossad213/TalentX)"
SUPPORTED = {"Music", "Actor"}

CATEGORY_TERMS = {
    "Music": (
        "singer", "musician", "rapper", "songwriter", "disc jockey", "dj",
        "record producer", "musical group", "band", "girl group", "boy band",
        "music duo", "composer",
    ),
    "Actor": (
        "actor", "actress", "film", "television", "stage", "voice actor",
        "performer",
    ),
}

_thread_state = threading.local()


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def worker_session() -> requests.Session:
    session = getattr(_thread_state, "session", None)
    if session is None:
        session = make_session()
        _thread_state.session = session
    return session


def identity_score(name: str, category: str, result: dict[str, Any]) -> int:
    label = str(result.get("label") or "")
    description = str(result.get("description") or "").lower()
    aliases = [str(alias) for alias in result.get("aliases", []) if alias]
    target = normalize(name)
    score = 0
    if normalize(label) == target:
        score += 100
    elif any(normalize(alias) == target for alias in aliases):
        score += 92
    elif target and (target in normalize(label) or normalize(label) in target):
        score += 45

    matches = sum(1 for term in CATEGORY_TERMS.get(category, ()) if term in description)
    score += min(55, matches * 24)
    if "disambiguation" in description or "given name" in description or "surname" in description:
        score -= 80
    return score


def search_identity(name: str, category: str, timeout: float) -> tuple[str, int] | None:
    session = worker_session()
    response = session.get(API, params={
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "uselang": "en",
        "format": "json",
        "limit": 10,
        "type": "item",
    }, timeout=timeout)
    response.raise_for_status()
    results = response.json().get("search", [])
    ranked: list[tuple[int, dict[str, Any]]] = []
    for result in results:
        if not isinstance(result, dict) or not result.get("id"):
            continue
        ranked.append((identity_score(name, category, result), result))
    if not ranked:
        return None
    score, best = max(ranked, key=lambda pair: pair[0])
    if score < 116:
        return None
    qid = str(best.get("id") or "")
    return (qid, score) if re.fullmatch(r"Q\d+", qid) else None


def fetch_entities(qids: list[str], timeout: float) -> tuple[dict[str, dict[str, Any]], list[str]]:
    session = make_session()
    entities: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for start in range(0, len(qids), 50):
        batch = qids[start:start + 50]
        try:
            response = session.get(API, params={
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "claims|sitelinks|labels|descriptions",
                "languages": "en",
                "format": "json",
            }, timeout=timeout)
            response.raise_for_status()
            payload = response.json().get("entities", {})
            for qid, entity in payload.items():
                if isinstance(entity, dict) and not entity.get("missing"):
                    entities[str(qid)] = entity
        except Exception as exc:  # noqa: BLE001
            errors.append(f"entity batch {start // 50 + 1}: {type(exc).__name__}: {exc}")
    return entities, errors


def claim_values(entity: dict[str, Any], prop: str) -> list[Any]:
    values: list[Any] = []
    claims = entity.get("claims", {}).get(prop, [])
    for claim in claims if isinstance(claims, list) else []:
        snak = claim.get("mainsnak", {}) if isinstance(claim, dict) else {}
        datavalue = snak.get("datavalue") if isinstance(snak, dict) else None
        if isinstance(datavalue, dict) and "value" in datavalue:
            values.append(datavalue["value"])
    return values


def parse_year(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("time")
    match = re.search(r"([12]\d{3})", str(value or ""))
    if not match:
        return None
    year = int(match.group(1))
    current = datetime.now(timezone.utc).year
    return year if 1850 <= year <= current + 1 else None


def derive_evidence(entity: dict[str, Any]) -> dict[str, Any]:
    current_year = datetime.now(timezone.utc).year
    birth_year = next((year for year in (parse_year(value) for value in claim_values(entity, "P569")) if year), None)
    start_year = next((year for year in (parse_year(value) for value in claim_values(entity, "P2031")) if year), None)
    end_year = next((year for year in (parse_year(value) for value in claim_values(entity, "P2032")) if year), None)
    age = current_year - birth_year if birth_year else None
    years_active = max(0, current_year - start_year) if start_year else None
    sitelinks = len(entity.get("sitelinks", {})) if isinstance(entity.get("sitelinks"), dict) else 0
    awards = len(claim_values(entity, "P166"))
    nominations = len(claim_values(entity, "P1411"))
    occupations = len(claim_values(entity, "P106"))
    completeness = sum(value is not None for value in (birth_year, start_year))
    completeness += int(sitelinks > 0) + int(awards > 0) + int(occupations > 0)
    confidence = min(.90, .76 + completeness * .025 + min(.035, math.log1p(max(0, sitelinks)) / 180))
    return {
        "birthYear": birth_year,
        "age": age,
        "workStartYear": start_year,
        "workEndYear": end_year,
        "yearsActive": years_active,
        "wikidataSitelinks": sitelinks,
        "wikidataAwardsCount": awards,
        "wikidataNominationsCount": nominations,
        "wikidataOccupationClaims": occupations,
        "identityEvidenceConfidence": round(confidence, 2),
    }


def curated_floor(record: dict[str, Any]) -> float:
    rank = max(1.0, float(record.get("benchmarkRank") or 100))
    pool = max(rank, float(record.get("benchmarkPoolSize") or 100))
    percentile = 1.0 if pool <= 1 else 1.0 - (rank - 1.0) / (pool - 1.0)
    return round(76.0 + 6.0 * max(0.0, min(1.0, percentile)), 2)


def merge_evidence(
    record: dict[str, Any],
    qid: str | None,
    evidence: dict[str, Any] | None,
    verified_at: str,
) -> dict[str, Any]:
    result = dict(record)
    result["curatedEvidenceFloor"] = curated_floor(result)
    result["rankingStatus"] = "Curated benchmark ranking"
    result["curatedEvidenceVerifiedAt"] = verified_at

    if not qid or not evidence:
        result["curatedEvidenceStatus"] = "Curated review retained; Wikidata identity unresolved this run"
        return result

    source_url = f"https://www.wikidata.org/wiki/{qid}"
    result["wikidataSourceRecordId"] = qid
    result["curatedEvidenceSource"] = source_url
    result["curatedIdentityEvidenceVerified"] = True
    result["curatedEvidenceStatus"] = "Wikidata identity and durable career evidence merged"
    for key in (
        "birthYear", "age", "workStartYear", "workEndYear", "yearsActive",
        "wikidataSitelinks", "wikidataAwardsCount", "wikidataNominationsCount",
        "wikidataOccupationClaims",
    ):
        if evidence.get(key) is not None:
            result[key] = evidence[key]

    evidence_confidence = float(evidence.get("identityEvidenceConfidence") or 0)
    result["pricingConfidence"] = round(max(float(result.get("pricingConfidence") or 0), evidence_confidence), 2)
    result["dataConfidence"] = round(max(float(result.get("dataConfidence") or 0), evidence_confidence), 2)
    pricing_evidence = result.get("pricingEvidence") if isinstance(result.get("pricingEvidence"), list) else []
    result["pricingEvidence"] = list(dict.fromkeys([*pricing_evidence, source_url]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--minimum-resolved", type=int, default=100)
    parser.add_argument("--allow-shortfall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")
    records = [record for record in payload if isinstance(record, dict)]
    targets = [
        record for record in records
        if str(record.get("primaryCategory") or "") in SUPPORTED
        and bool(record.get("nonAthleteRosterVersion"))
        and int(record.get("benchmarkRank") or 0) > 0
    ]
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    resolved: dict[tuple[str, str], tuple[str, int]] = {}
    errors: list[str] = []

    workers = max(1, min(16, int(args.workers)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(search_identity, str(record.get("name") or ""), str(record.get("primaryCategory") or ""), args.request_timeout): record
            for record in targets
        }
        for future in as_completed(futures):
            record = futures[future]
            key = (str(record.get("primaryCategory") or ""), normalize(record.get("name")))
            try:
                match = future.result()
                if match:
                    resolved[key] = match
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{record.get('name')}: {type(exc).__name__}: {exc}")

    qids = sorted({qid for qid, _score in resolved.values()})
    entities, entity_errors = fetch_entities(qids, args.request_timeout)
    errors.extend(entity_errors)

    updated: list[dict[str, Any]] = []
    merged_count = 0
    resolved_by_category = {category: 0 for category in SUPPORTED}
    for record in records:
        category = str(record.get("primaryCategory") or "")
        is_target = category in SUPPORTED and bool(record.get("nonAthleteRosterVersion")) and int(record.get("benchmarkRank") or 0) > 0
        if not is_target:
            updated.append(record)
            continue
        key = (category, normalize(record.get("name")))
        match = resolved.get(key)
        qid = match[0] if match else None
        entity = entities.get(qid) if qid else None
        evidence = derive_evidence(entity) if entity else None
        merged = merge_evidence(record, qid, evidence, verified_at)
        if qid and evidence:
            merged_count += 1
            resolved_by_category[category] += 1
        updated.append(merged)

    if merged_count < max(0, args.minimum_resolved) and not args.allow_shortfall:
        raise RuntimeError(
            f"Resolved only {merged_count} curated Music/Actor records; "
            f"minimum is {args.minimum_resolved}. Recent errors: {'; '.join(errors[-5:])}"
        )

    manifest = {
        "version": "1.0",
        "generatedAt": verified_at,
        "source": API,
        "targetRecords": len(targets),
        "resolvedRecords": merged_count,
        "resolvedByCategory": dict(sorted(resolved_by_category.items())),
        "unresolvedRecords": len(targets) - merged_count,
        "sourceErrors": errors,
        "pricingTreatment": (
            "Wikidata identity and work-period evidence supplements, but does not replace, "
            "curated profession metrics. Curated reviews receive a moderate rank-sensitive "
            "confidence floor; generic Wikidata-only discoveries remain confidence-capped."
        ),
    }

    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    args.seed.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Enriched {merged_count:,}/{len(targets):,} curated Music and Actor records.")
    if errors:
        print(f"Source warnings: {len(errors):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
