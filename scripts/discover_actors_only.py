#!/usr/bin/env python3
"""Discover source-backed Actor records without touching Music."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from expand_non_athlete_sources import (
    RECENT_ACTIVITY_YEARS,
    SPARQL_ENDPOINT,
    binding_value,
    discover_category,
    make_record,
    make_session,
    normalize,
    parse_year,
    update_taxonomy,
)
from pricing_model import apply_pricing_to_records, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_TAXONOMY = DATA / "taxonomy.json"
DEFAULT_MANIFEST = DATA / "actor_discovery_manifest.json"
DEFAULT_OVERRIDES = DATA / "pricing_overrides.json"
DEFAULT_CAREER_ANCHORS = DATA / "actor_career_anchors.json"


def fetch_actor_career_anchors(session, path: Path, timeout: float) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    anchors = payload.get("anchors", []) if isinstance(payload, dict) else []
    qids = [
        str(item.get("wikidataQid") or "")
        for item in anchors
        if isinstance(item, dict) and str(item.get("wikidataQid") or "").startswith("Q")
    ]
    if not qids:
        return []
    values = " ".join(f"wd:{qid}" for qid in qids)
    occupation_values = " ".join(f"wd:{qid}" for qid in ("Q33999","Q10800557","Q10798782","Q2259451","Q2405480"))
    query = f"""
SELECT DISTINCT ?person ?personLabel ?sitelinks ?birth ?workStart ?workEnd ?countryLabel WHERE {{
  VALUES ?person {{ {values} }}
  VALUES ?occupation {{ {occupation_values} }}
  ?person wdt:P31 wd:Q5;
          wdt:P106 ?occupation;
          wikibase:sitelinks ?sitelinks.
  FILTER NOT EXISTS {{ ?person wdt:P570 ?death. }}
  OPTIONAL {{ ?person wdt:P569 ?birth. }}
  OPTIONAL {{ ?person wdt:P2031 ?workStart. }}
  OPTIONAL {{ ?person wdt:P2032 ?workEnd. }}
  OPTIONAL {{ ?person wdt:P27 ?country. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
""".strip()
    response = session.post(
        SPARQL_ENDPOINT,
        data={"query": query, "format": "json"},
        timeout=timeout,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])
    by_qid = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        uri = binding_value(binding, "person")
        qid = uri.rsplit("/", 1)[-1] if "/Q" in uri else ""
        name = binding_value(binding, "personLabel").strip()
        if not qid.startswith("Q") or not name or name == qid:
            continue
        try:
            sitelinks = int(float(binding_value(binding, "sitelinks") or 0))
        except ValueError:
            sitelinks = 0
        candidate = {
            "qid": qid,
            "name": name,
            "sitelinks": sitelinks,
            "birthYear": parse_year(binding_value(binding, "birth")),
            "workStartYear": parse_year(binding_value(binding, "workStart")),
            "workEndYear": parse_year(binding_value(binding, "workEnd")),
            "country": binding_value(binding, "countryLabel").strip() or "Not listed",
            "role": "Actor",
            "discipline": "Acting",
            "actorCareerAnchor": True,
        }
        prior = by_qid.get(qid)
        if prior is None or sitelinks > int(prior.get("sitelinks") or 0):
            by_qid[qid] = candidate
    # Wikidata Query Service can occasionally return a partial exact-ID batch.
    # Fill any missing anchors from the entity API so coverage is deterministic.
    missing = [qid for qid in qids if qid not in by_qid]
    if missing:
        api = session.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": "|".join(missing),
                "props": "labels|claims|sitelinks",
                "languages": "en",
                "format": "json",
            },
            timeout=timeout,
        )
        api.raise_for_status()
        entities = api.json().get("entities", {})
        def claim_year(entity, prop):
            claims = entity.get("claims", {}).get(prop, [])
            for claim in claims:
                try:
                    value = claim["mainsnak"]["datavalue"]["value"]["time"]
                except (KeyError, TypeError):
                    continue
                year = parse_year(value)
                if year is not None:
                    return year
            return None
        for qid in missing:
            entity = entities.get(qid, {})
            label = entity.get("labels", {}).get("en", {}).get("value")
            if not label:
                continue
            by_qid[qid] = {
                "qid": qid,
                "name": str(label),
                "sitelinks": len(entity.get("sitelinks", {})),
                "birthYear": claim_year(entity, "P569"),
                "workStartYear": claim_year(entity, "P2031"),
                "workEndYear": claim_year(entity, "P2032"),
                "country": "Not listed",
                "role": "Actor",
                "discipline": "Acting",
                "actorCareerAnchor": True,
            }
    # Final deterministic fallback from the reviewed anchor file itself.
    # This is used only when Wikidata endpoints return a partial exact-ID batch.
    anchors_by_qid = {
        str(item.get("wikidataQid") or ""): item
        for item in anchors
        if isinstance(item, dict)
    }
    # Overlay reviewed eligibility/status metadata on every exact anchor,
    # including rows that came back from SPARQL or wbgetentities.
    for qid, row in list(by_qid.items()):
        item = anchors_by_qid.get(qid)
        if not item:
            continue
        row["currentMarketEligible"] = item.get("currentMarketEligible", True)
        if "careerStatus" in item:
            row["careerStatus"] = item.get("careerStatus")
        if "statusNote" in item:
            row["statusNote"] = item.get("statusNote")
        if item.get("currentMarketEligible") is True and item.get("workEndYear") is None:
            row["workEndYear"] = None
    for qid in qids:
        if qid in by_qid:
            continue
        item = anchors_by_qid.get(qid, {})
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        by_qid[qid] = {
            "qid": qid,
            "name": name,
            "sitelinks": 80,
            "birthYear": item.get("birthYear"),
            "workStartYear": item.get("workStartYear"),
            "workEndYear": item.get("workEndYear"),
            "country": str(item.get("country") or "Not listed"),
            "role": str(item.get("role") or "Actor"),
            "discipline": str(item.get("discipline") or "Acting"),
            "actorCareerAnchor": True,
        }
    order = {qid: index for index, qid in enumerate(qids)}
    return sorted(by_qid.values(), key=lambda row: order.get(str(row.get("qid")), 9999))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--actor-additions", type=int, default=1000)
    parser.add_argument("--per-occupation-limit", type=int, default=900)
    parser.add_argument("--minimum-sitelinks", type=int, default=10)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--sleep", type=float, default=.2)
    parser.add_argument("--allow-shortfall", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")
    records = [record for record in payload if isinstance(record, dict)]
    requested = max(0, int(args.actor_additions))
    if requested == 0:
        return 0

    existing_names = {normalize(str(record.get("name") or "")) for record in records}
    existing_source_ids = {str(record.get("sourceRecordId") or "") for record in records if record.get("sourceRecordId")}
    used_ids = {str(record.get("id")) for record in records if record.get("id")}
    used_tickers = {str(record.get("ticker")) for record in records if record.get("ticker")}

    current_year = datetime.now(timezone.utc).year
    recent_cutoff = current_year - RECENT_ACTIVITY_YEARS
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    session = make_session()
    candidates, source_errors = discover_category(
        session,
        "Actor",
        max(1, args.per_occupation_limit),
        args.request_timeout,
        args.sleep,
        max(1, args.minimum_sitelinks),
        recent_cutoff,
    )
    try:
        anchor_candidates = fetch_actor_career_anchors(session, DEFAULT_CAREER_ANCHORS, args.request_timeout)
    except Exception as exc:
        anchor_candidates = []
        source_errors.append({"source": "actor-career-anchors", "error": str(exc)})

    anchor_qids = {str(row.get("qid") or "") for row in anchor_candidates}
    merged_candidates = anchor_candidates + [
        row for row in candidates if str(row.get("qid") or "") not in anchor_qids
    ]

    selected = []
    for candidate in merged_candidates:
        if candidate.get("actorCareerAnchor") and candidate.get("currentMarketEligible") is False:
            continue
        key = normalize(str(candidate.get("name") or ""))
        qid = str(candidate.get("qid") or "")
        if not key or key in existing_names or qid in existing_source_ids:
            continue
        selected.append(candidate)
        existing_names.add(key)
        existing_source_ids.add(qid)
        if len(selected) >= requested:
            break

    if len(selected) < requested and not args.allow_shortfall:
        raise RuntimeError(f"Only found {len(selected)} eligible Actor records; requested {requested}.")

    counts = Counter(str(record.get("primaryCategory") or "") for record in records)
    pool_size = counts["Actor"] + len(selected)
    additions = []
    for offset, candidate in enumerate(selected, start=1):
        record = make_record(
            candidate,
            "Actor",
            counts["Actor"] + offset,
            pool_size,
            used_ids,
            used_tickers,
            verified_at,
        )
        if candidate.get("actorCareerAnchor"):
            record["actorCareerAnchor"] = True
            record["careerAnchorSource"] = f"https://www.wikidata.org/wiki/{candidate.get('qid')}"
            record["verificationStatus"] = "Source-backed Actor career anchor; profession evidence verified separately"
            record["statusSource"] = "Wikidata Actor occupation and career-anchor coverage list"
            work_end = candidate.get("workEndYear")
            if isinstance(work_end, int) and work_end < recent_cutoff:
                record["careerStatus"] = "Retired / legacy"
                record["marketSegment"] = "Legacy"
                record["careerStage"] = "Legacy career"
                record["searchText"] = str(record.get("searchText") or "").replace("current active", "legacy retired")
        additions.append(record)
    combined = records + additions
    combined = apply_pricing_to_records(
        combined,
        load_overrides(DEFAULT_OVERRIDES),
        benchmark_records=combined,
        calibration_reference=combined,
    )
    args.seed.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    update_taxonomy(args.taxonomy, combined)
    manifest = {
        "version": "1.0-actor-only",
        "generatedAt": verified_at,
        "requestedActorAdditions": requested,
        "actualActorAdditions": len(additions),
        "musicRecordsAdded": 0,
        "sourceErrors": source_errors,
        "careerAnchorCandidatesFound": len(anchor_candidates),
        "careerAnchorsAdded": sum(1 for record in additions if record.get("actorCareerAnchor")),
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Actor-only discovery added {len(additions):,} Actor records and 0 Music records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
