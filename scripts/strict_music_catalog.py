#!/usr/bin/env python3
"""Keep TalentX's Music category limited to source-verified musicians.

Curated TalentX Music records are preserved. Every source-discovered Music record
must have all of the following before it may remain in Music:
  * a Wikidata identity;
  * at least one specific music profession (not merely generic "musician");
  * a MusicBrainz artist identifier (Wikidata P434);
  * no screen-first English Wikidata description.

Unverifiable source-discovered records fail closed: they are removed from Music.
Screen-first people are moved to Actor unless an Actor copy already exists.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_seed.json"
DEFAULT_MANIFEST = ROOT / "data" / "strict_music_manifest.json"
ENTITY_ENDPOINT = "https://www.wikidata.org/w/api.php"
USER_AGENT = "TalentX-Strict-Music/1.0 (+https://github.com/rossad213/TalentX)"

STRONG_MUSIC_OCCUPATIONS = {
    "Q177220": "Singer",
    "Q2252262": "Rapper",
    "Q488205": "Singer-songwriter",
    "Q130857": "Disc jockey",
    "Q855091": "Guitarist",
    "Q486748": "Pianist",
    "Q386854": "Drummer",
    "Q584301": "Bassist",
    "Q1259917": "Violinist",
    "Q12800682": "Saxophonist",
    "Q753110": "Songwriter",
    "Q36834": "Composer",
    "Q183945": "Record producer",
}
SCREEN_OCCUPATIONS = {
    "Q33999", "Q10800557", "Q10798782", "Q2259451", "Q2405480",
    "Q2526255", "Q6102247", "Q28389", "Q1414443",
}
SCREEN_TERMS = (
    "film director", "television director", "filmmaker", "film-maker",
    "screenwriter", "screen writer", "film actor", "television actor",
    "voice actor", "stage actor", "actor", "actress",
)
MUSIC_TERMS = (
    "singer-songwriter", "singer songwriter", "record producer", "rapper",
    "singer", "songwriter", "composer", "disc jockey", "dj", "guitarist",
    "pianist", "drummer", "bassist", "violinist", "saxophonist",
)
KNOWN_SCREEN_FIRST = {"zacefron", "tomhanks", "quentintarantino"}


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def is_curated_music(record: dict[str, Any]) -> bool:
    return bool(record.get("nonAthleteRosterVersion")) or str(record.get("statusSource") or "") == "TalentX curated non-athlete roster"


def primary_description_category(description: str) -> str | None:
    text = str(description or "").lower()
    screen = [text.find(term) for term in SCREEN_TERMS if text.find(term) >= 0]
    music = [text.find(term) for term in MUSIC_TERMS if text.find(term) >= 0]
    if not screen and not music:
        return None
    if screen and not music:
        return "Actor"
    if music and not screen:
        return "Music"
    return "Actor" if min(screen) < min(music) else "Music"


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3, connect=3, read=3, backoff_factor=.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=6, pool_maxsize=6)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def item_claim_ids(entity: dict[str, Any], prop: str) -> set[str]:
    out: set[str] = set()
    for claim in entity.get("claims", {}).get(prop, []) if isinstance(entity.get("claims"), dict) else []:
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value") if isinstance(claim, dict) else None
        if isinstance(value, dict):
            qid = str(value.get("id") or "")
            if re.fullmatch(r"Q\d+", qid):
                out.add(qid)
    return out


def string_claim_values(entity: dict[str, Any], prop: str) -> set[str]:
    out: set[str] = set()
    for claim in entity.get("claims", {}).get(prop, []) if isinstance(entity.get("claims"), dict) else []:
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value") if isinstance(claim, dict) else None
        if isinstance(value, str) and value.strip():
            out.add(value.strip())
    return out


def fetch_entities(session: requests.Session, qids: list[str], timeout: float) -> dict[str, dict[str, Any]]:
    response = session.get(
        ENTITY_ENDPOINT,
        params={
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "descriptions|claims",
            "languages": "en",
            "languagefallback": "1",
            "format": "json",
            "formatversion": "2",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    entities = response.json().get("entities", {})
    iterable = entities if isinstance(entities, list) else list(entities.values()) if isinstance(entities, dict) else []
    out: dict[str, dict[str, Any]] = {}
    for entity in iterable:
        if not isinstance(entity, dict):
            continue
        qid = str(entity.get("id") or "")
        if not re.fullmatch(r"Q\d+", qid):
            continue
        desc = ""
        descriptions = entity.get("descriptions", {})
        if isinstance(descriptions, dict) and isinstance(descriptions.get("en"), dict):
            desc = str(descriptions["en"].get("value") or "")
        out[qid] = {
            "description": desc,
            "occupations": item_claim_ids(entity, "P106"),
            "musicbrainz": string_claim_values(entity, "P434"),
        }
    return out


def actor_role(occupations: set[str]) -> tuple[str, str]:
    priority = (
        ("Q2526255", "Film director", "Film"),
        ("Q6102247", "Film / television director", "Film & Television"),
        ("Q1414443", "Filmmaker", "Film"),
        ("Q28389", "Screenwriter", "Film & Television"),
        ("Q10800557", "Film actor", "Film"),
        ("Q10798782", "Television actor", "Television"),
        ("Q2405480", "Voice actor", "Voice Acting"),
        ("Q2259451", "Stage actor", "Theatre"),
        ("Q33999", "Actor", "Acting"),
    )
    for qid, role, discipline in priority:
        if qid in occupations:
            return role, discipline
    return "Actor / filmmaker", "Film & Television"


def filter_records(records: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actor_names = {normalize(r.get("name")) for r in records if r.get("primaryCategory") == "Actor"}
    output: list[dict[str, Any]] = []
    kept_verified: list[str] = []
    excluded: list[str] = []
    moved: list[str] = []
    duplicate_actor_removals: list[str] = []

    for original in records:
        record = dict(original)
        if record.get("primaryCategory") != "Music" or is_curated_music(record):
            output.append(record)
            continue

        name = str(record.get("name") or "")
        key = normalize(name)
        qid = str(record.get("sourceRecordId") or "")
        info = evidence.get(qid)

        if key in KNOWN_SCREEN_FIRST:
            if key in actor_names:
                duplicate_actor_removals.append(name)
                continue
            occupations = set((info or {}).get("occupations") or set())
            role, discipline = actor_role(occupations)
            record.update({"primaryCategory": "Actor", "discipline": discipline, "leagueOrMedium": "Film & Television", "role": role, "categoryResolution": "Strict Music audit: known screen-first career"})
            actor_names.add(key)
            moved.append(name)
            output.append(record)
            continue

        if not re.fullmatch(r"Q\d+", qid) or not info:
            excluded.append(name)
            continue

        occupations = set(info.get("occupations") or set())
        mbids = sorted(set(info.get("musicbrainz") or set()))
        strong_music = occupations & set(STRONG_MUSIC_OCCUPATIONS)
        dominant = primary_description_category(str(info.get("description") or ""))

        # Screen-first public identity always loses the Music primary category.
        if dominant == "Actor":
            if key in actor_names:
                duplicate_actor_removals.append(name)
                continue
            role, discipline = actor_role(occupations)
            record.update({"primaryCategory": "Actor", "discipline": discipline, "leagueOrMedium": "Film & Television", "role": role, "categoryResolution": "Strict Music audit: screen-first Wikidata description"})
            actor_names.add(key)
            moved.append(name)
            output.append(record)
            continue

        # A generic music tag is not enough. Require a specific profession and a
        # MusicBrainz artist identity. Ambiguous screen crossovers also fail closed.
        if not strong_music or not mbids:
            excluded.append(name)
            continue
        if dominant is None and occupations & SCREEN_OCCUPATIONS and key in actor_names:
            excluded.append(name)
            continue

        record["musicCategoryVerified"] = True
        record["musicCategoryVerification"] = "Specific Wikidata music profession + MusicBrainz artist ID; screen-first descriptions excluded"
        record["musicBrainzArtistIds"] = mbids
        record["verifiedMusicOccupations"] = sorted(STRONG_MUSIC_OCCUPATIONS[qid_] for qid_ in strong_music)
        kept_verified.append(name)
        output.append(record)

    return output, {
        "verifiedDiscoveredMusic": len(kept_verified),
        "excludedFromMusic": len(excluded),
        "movedToActor": len(moved),
        "removedActorDuplicates": len(duplicate_actor_removals),
        "verifiedNames": kept_verified,
        "excludedNames": excluded,
        "movedNames": moved,
        "removedDuplicateNames": duplicate_actor_removals,
    }


def update_manifest_counts(records: list[dict[str, Any]]) -> None:
    path = ROOT / "data" / "catalog_manifest.json"
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    counts = Counter(str(r.get("primaryCategory") or "Unknown") for r in records)
    manifest["categoryCounts"] = dict(sorted(counts.items()))
    manifest["currentCatalogRecords"] = len(records)
    manifest["currentSeedRecords"] = len(records)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--sleep", type=float, default=.03)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.catalog.name} must contain a JSON array")
    records = [r for r in payload if isinstance(r, dict)]

    qids = sorted({
        str(r.get("sourceRecordId") or "") for r in records
        if r.get("primaryCategory") == "Music" and not is_curated_music(r)
        and re.fullmatch(r"Q\d+", str(r.get("sourceRecordId") or ""))
    })

    session = make_session()
    evidence: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    batch_size = max(1, min(50, int(args.batch_size)))
    for start in range(0, len(qids), batch_size):
        batch = qids[start:start + batch_size]
        try:
            evidence.update(fetch_entities(session, batch, args.request_timeout))
        except Exception as exc:  # fail closed below: missing evidence is excluded
            errors.append(f"batch={start // batch_size + 1}:{type(exc).__name__}:{exc}")
        if args.sleep:
            time.sleep(max(0.0, args.sleep))

    filtered, summary = filter_records(records, evidence)
    bad = [r.get("name") for r in filtered if r.get("primaryCategory") == "Music" and normalize(r.get("name")) in KNOWN_SCREEN_FIRST]
    if bad:
        raise RuntimeError(f"Strict Music audit failed; screen-first names remain: {bad}")

    compact = args.catalog.name == "current_catalog.json"
    args.catalog.write_text(
        json.dumps(filtered, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(filtered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if compact:
        update_manifest_counts(filtered)

    manifest = {
        "version": "1.0-strict",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "catalog": str(args.catalog),
        "entitySource": ENTITY_ENDPOINT,
        "qualificationRule": "Discovered Music requires a specific Wikidata music profession and MusicBrainz artist ID; screen-first descriptions are excluded.",
        "candidateQids": len(qids),
        "resolvedQids": len(evidence),
        "sourceErrorCount": len(errors),
        "sourceErrors": errors,
        "recordsBefore": len(records),
        "recordsAfter": len(filtered),
        **summary,
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Strict Music audit: {summary['verifiedDiscoveredMusic']:,} discovered musicians verified; "
        f"{summary['excludedFromMusic']:,} excluded; {summary['movedToActor']:,} moved to Actor; "
        f"{summary['removedActorDuplicates']:,} Actor duplicates removed."
    )
    if errors:
        print(f"Entity API errors: {len(errors)} batch(es); unresolved records were excluded from Music.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
