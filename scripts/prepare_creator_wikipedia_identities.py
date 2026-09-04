#!/usr/bin/env python3
"""Prepare high-confidence Creator -> English Wikipedia identity mappings.

TalentX creator discovery already stores a Wikidata QID for source-discovered
Creators. Historical Wikimedia pageview work should use that identity directly
rather than guessing from a same-name search. This script resolves each QID's
official ``enwiki`` sitelink and writes it into the attention manifest consumed
by ``creator_attention_refresh.py``.

It never guesses a page title. Records without a usable Wikidata QID or enwiki
sitelink are left unresolved so the existing conservative fallback can decide
whether an exact-name match is safe.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
QID_RE = re.compile(r"^Q\d+$", re.I)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def creator_qid(record: dict[str, Any]) -> str:
    candidates = [
        record.get("wikidataQid"),
        record.get("sourceRecordId") if str(record.get("sourceNamespace") or "").startswith("wikidata") else None,
    ]
    source_url = str(record.get("sourceUrl") or "")
    match = re.search(r"/wiki/(Q\d+)(?:$|[?#])", source_url, re.I)
    if match:
        candidates.append(match.group(1))
    for value in candidates:
        qid = str(value or "").strip().upper()
        if QID_RE.fullmatch(qid):
            return qid
    return ""


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "TalentX-Creator-Identity-Resolver/1.0 (+https://github.com/rossad213/TalentX)",
        "Accept": "application/json",
    })
    return session


def fetch_enwiki_sitelinks(session: requests.Session, qids: list[str], timeout: float) -> tuple[dict[str, str], list[str]]:
    resolved: dict[str, str] = {}
    warnings: list[str] = []
    for offset in range(0, len(qids), 50):
        batch = qids[offset:offset + 50]
        try:
            response = session.get(
                WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "sitelinks",
                    "sitefilter": "enwiki",
                    "format": "json",
                    "formatversion": 2,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            entities = response.json().get("entities", {})
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"batch {offset // 50 + 1}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(entities, dict):
            continue
        for raw_qid, entity in entities.items():
            if not isinstance(entity, dict) or entity.get("missing") is not None:
                continue
            sitelinks = entity.get("sitelinks") if isinstance(entity.get("sitelinks"), dict) else {}
            enwiki = sitelinks.get("enwiki") if isinstance(sitelinks.get("enwiki"), dict) else {}
            title = str(enwiki.get("title") or "").strip()
            qid = str(raw_qid or "").upper()
            if title and QID_RE.fullmatch(qid):
                resolved[qid] = title
    return resolved, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--request-timeout", type=float, default=20.0)
    args = parser.parse_args()

    payload = load_json(args.catalog, [])
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    creators = [record for record in records if str(record.get("primaryCategory") or "") == "Creator"]

    record_qids: dict[str, str] = {}
    for record in creators:
        identity_key = str(record.get("id") or "").strip()
        qid = creator_qid(record)
        if identity_key and qid:
            record_qids[identity_key] = qid

    session = make_session()
    sitelinks, warnings = fetch_enwiki_sitelinks(session, sorted(set(record_qids.values())), args.request_timeout)

    manifest = load_json(args.manifest, {})
    if not isinstance(manifest, dict):
        manifest = {}
    identities = manifest.get("identities") if isinstance(manifest.get("identities"), dict) else {}
    identities = {str(key): dict(value) for key, value in identities.items() if isinstance(value, dict)}
    resolved_at = iso_now()
    direct = 0
    no_enwiki = 0
    for identity_key, qid in record_qids.items():
        title = sitelinks.get(qid, "")
        if not title:
            no_enwiki += 1
            continue
        identities[identity_key] = {
            "title": title,
            "pageId": None,
            "wikidataQid": qid,
            "identitySource": "Wikidata enwiki sitelink",
            "resolvedAt": resolved_at,
        }
        direct += 1

    manifest["identities"] = identities
    manifest["identityResolutionVersion"] = "wikidata-enwiki-sitelink-v1"
    manifest["identityResolutionUpdatedAt"] = resolved_at
    manifest["creatorRecords"] = len(creators)
    manifest["creatorRecordsWithWikidataQid"] = len(record_qids)
    manifest["directEnwikiSitelinksResolved"] = direct
    manifest["qidRecordsWithoutEnwikiSitelink"] = no_enwiki
    manifest["identityResolutionWarnings"] = warnings[:50]
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"Creator identity preparation: {len(creators):,} Creator records; "
        f"{len(record_qids):,} with Wikidata QIDs; {direct:,} exact enwiki sitelinks; "
        f"{no_enwiki:,} QID records without enwiki sitelinks; {len(warnings):,} source warnings."
    )
    for warning in warnings[:10]:
        print(f"WARNING {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
