#!/usr/bin/env python3
"""Resolve source-discovered Music profiles whose dominant career is screen-based.

Wikidata can legitimately contain weak or secondary music occupations for people
whose public career is primarily acting or filmmaking. TalentX should not let the
first discovery query decide the category. This pass reviews source-discovered
Music records in batches, uses the English Wikidata description plus screen
occupations, and moves screen-first profiles into Actor. If an Actor profile with
the same normalized name already exists, the duplicate Music copy is removed.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "data" / "current_seed.json"
DEFAULT_MANIFEST = ROOT / "data" / "music_category_resolution_manifest.json"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "TalentX-Music-Category-Resolver/1.0 (+https://github.com/rossad213/TalentX)"
SOURCE_NAMESPACES = {"wikidata-non-athlete", "wikidata-music-expanded"}

SCREEN_OCCUPATIONS = {
    "Q33999": "Actor",
    "Q10800557": "Film actor",
    "Q10798782": "Television actor",
    "Q2259451": "Stage actor",
    "Q2405480": "Voice actor",
    "Q2526255": "Film director",
    "Q6102247": "Film or television director",
    "Q28389": "Screenwriter",
    "Q1414443": "Filmmaker",
}

SCREEN_TERMS = (
    "film director", "television director", "filmmaker", "film-maker",
    "screenwriter", "screen writer", "film actor", "television actor",
    "voice actor", "stage actor", "actor", "actress",
)
MUSIC_TERMS = (
    "singer-songwriter", "singer songwriter", "record producer", "rapper",
    "singer", "musician", "songwriter", "composer", "disc jockey", "dj",
    "guitarist", "pianist", "drummer", "bassist", "violinist", "saxophonist",
)


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def primary_description_category(description: str) -> str | None:
    """Infer dominant public career from the order of professions in a description.

    Wikidata descriptions commonly read like "American actor and singer" or
    "American singer and actress". Profession order is useful here because it
    avoids treating every crossover credit as an equal primary career.
    """
    text = str(description or "").strip().lower()
    if not text:
        return None
    screen_positions = [text.find(term) for term in SCREEN_TERMS if text.find(term) >= 0]
    music_positions = [text.find(term) for term in MUSIC_TERMS if text.find(term) >= 0]
    if not screen_positions and not music_positions:
        return None
    if screen_positions and not music_positions:
        return "Actor"
    if music_positions and not screen_positions:
        return "Music"
    return "Actor" if min(screen_positions) < min(music_positions) else "Music"


def should_move_to_actor(description: str, occupations: set[str], music_role: str = "") -> bool:
    dominant = primary_description_category(description)
    if dominant == "Actor":
        return True
    if dominant == "Music":
        return False
    has_screen = bool(occupations & set(SCREEN_OCCUPATIONS))
    if not has_screen:
        return False
    # Without a useful description, generic/secondary music roles should lose to
    # explicit screen occupations. Strong music-performance roles stay Music.
    strong_music_roles = {
        "Singer", "Rapper", "Singer-songwriter", "Disc jockey", "Guitarist",
        "Pianist", "Drummer", "Bassist", "Violinist", "Saxophonist",
    }
    return str(music_role or "") not in strong_music_roles


def actor_role_and_discipline(occupations: set[str]) -> tuple[str, str]:
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


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    return session


def query_for(qids: list[str]) -> str:
    values = " ".join(f"wd:{qid}" for qid in qids)
    return f"""
SELECT ?person ?description ?occupation WHERE {{
  VALUES ?person {{ {values} }}
  OPTIONAL {{
    ?person schema:description ?description.
    FILTER(LANG(?description) = "en")
  }}
  OPTIONAL {{ ?person wdt:P106 ?occupation. }}
}}
""".strip()


def fetch_evidence(session: requests.Session, qids: list[str], timeout: float) -> dict[str, dict[str, Any]]:
    response = session.post(
        SPARQL_ENDPOINT,
        data={"query": query_for(qids), "format": "json"},
        timeout=timeout,
    )
    response.raise_for_status()
    output: dict[str, dict[str, Any]] = {qid: {"description": "", "occupations": set()} for qid in qids}
    for row in response.json().get("results", {}).get("bindings", []):
        if not isinstance(row, dict):
            continue
        person = str(row.get("person", {}).get("value") or "")
        match = re.search(r"/(Q\d+)$", person)
        if not match:
            continue
        qid = match.group(1)
        bucket = output.setdefault(qid, {"description": "", "occupations": set()})
        description = str(row.get("description", {}).get("value") or "")
        if description and not bucket["description"]:
            bucket["description"] = description
        occupation = str(row.get("occupation", {}).get("value") or "")
        occ_match = re.search(r"/(Q\d+)$", occupation)
        if occ_match:
            bucket["occupations"].add(occ_match.group(1))
    return output


def resolve_records(records: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actor_names = {
        normalize(record.get("name"))
        for record in records
        if record.get("primaryCategory") == "Actor"
    }
    output: list[dict[str, Any]] = []
    moved: list[str] = []
    removed_duplicates: list[str] = []
    reviewed = 0

    for original in records:
        record = dict(original)
        if record.get("primaryCategory") != "Music" or str(record.get("sourceNamespace") or "") not in SOURCE_NAMESPACES:
            output.append(record)
            continue
        qid = str(record.get("sourceRecordId") or "")
        info = evidence.get(qid)
        if not info:
            output.append(record)
            continue
        reviewed += 1
        description = str(info.get("description") or "")
        occupations = set(info.get("occupations") or set())
        if not should_move_to_actor(description, occupations, str(record.get("role") or "")):
            output.append(record)
            continue

        name_key = normalize(record.get("name"))
        if name_key in actor_names:
            removed_duplicates.append(str(record.get("name") or qid))
            continue

        role, discipline = actor_role_and_discipline(occupations)
        record.update({
            "primaryCategory": "Actor",
            "discipline": discipline,
            "leagueOrMedium": "Film & Television",
            "teamOrPlatform": "Independent / representation not listed",
            "role": role,
            "categoryResolution": "Moved from Music to Actor using dominant Wikidata screen-career evidence",
            "categoryResolutionDescription": description,
            "pricingDataStatus": "Source-discovered screen career; profession performance evidence partial",
        })
        record["searchText"] = " ".join([
            str(record.get("name") or ""), "Actor", discipline,
            "Film & Television", role, str(record.get("country") or ""),
            "Current active",
        ]).lower()
        record.pop("benchmarkRank", None)
        record.pop("benchmarkPoolSize", None)
        actor_names.add(name_key)
        moved.append(str(record.get("name") or qid))
        output.append(record)

    summary = {
        "reviewed": reviewed,
        "movedToActor": len(moved),
        "removedDuplicateMusicCopies": len(removed_duplicates),
        "movedNames": moved,
        "removedDuplicateNames": removed_duplicates,
    }
    return output, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--batch-size", type=int, default=120)
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--sleep", type=float, default=.08)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")
    records = [record for record in payload if isinstance(record, dict)]
    qids = sorted({
        str(record.get("sourceRecordId"))
        for record in records
        if record.get("primaryCategory") == "Music"
        and str(record.get("sourceNamespace") or "") in SOURCE_NAMESPACES
        and re.fullmatch(r"Q\d+", str(record.get("sourceRecordId") or ""))
    })

    session = make_session()
    evidence: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    batch_size = max(20, min(250, int(args.batch_size)))
    for start in range(0, len(qids), batch_size):
        batch = qids[start:start + batch_size]
        try:
            evidence.update(fetch_evidence(session, batch, args.request_timeout))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"batch={start // batch_size + 1}:{type(exc).__name__}:{exc}")
        if args.sleep:
            time.sleep(max(0.0, args.sleep))

    if errors and not args.allow_source_errors:
        raise RuntimeError(f"Music category resolution source errors: {errors[:5]}")

    resolved, summary = resolve_records(records, evidence)
    args.seed.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "version": "1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": SPARQL_ENDPOINT,
        "candidateMusicProfiles": len(qids),
        "resolvedEvidenceProfiles": len(evidence),
        "sourceErrorCount": len(errors),
        "sourceErrors": errors,
        **summary,
        "rule": "Screen-first Wikidata descriptions move source-discovered profiles to Actor; existing Actor profiles win duplicate-name conflicts.",
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Reviewed {summary['reviewed']:,} source-discovered Music profiles; "
        f"moved {summary['movedToActor']:,} to Actor and removed "
        f"{summary['removedDuplicateMusicCopies']:,} duplicate Music copies."
    )
    if errors:
        print(f"Completed with {len(errors)} Wikidata batch error(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
