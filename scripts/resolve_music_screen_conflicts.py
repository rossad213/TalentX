#!/usr/bin/env python3
"""Resolve source-discovered Music profiles whose dominant career is screen-based.

TalentX assigns one primary career category to source-discovered records. Wikidata
can legitimately attach a secondary music occupation to actors and filmmakers, so
an occupation match by itself is not enough to make somebody a Music listing.

This pass applies deterministic duplicate/category rules first, then reviews the
remaining source-discovered Music records through Wikidata's entity API. Screen-
first descriptions or screen occupations move the record into Actor. If an Actor
profile already exists, the source-discovered Music duplicate is removed.
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
ENTITY_ENDPOINT = "https://www.wikidata.org/w/api.php"
USER_AGENT = "TalentX-Music-Category-Resolver/2.0 (+https://github.com/rossad213/TalentX)"
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

SCREEN_TERMS = (
    "film director", "television director", "filmmaker", "film-maker",
    "screenwriter", "screen writer", "film actor", "television actor",
    "voice actor", "stage actor", "actor", "actress",
)
MUSIC_TERMS = (
    "singer-songwriter", "singer songwriter", "record producer", "rapper",
    "singer", "songwriter", "composer", "disc jockey", "dj",
    "guitarist", "pianist", "drummer", "bassist", "violinist", "saxophonist",
    "musician",
)

# Safety regressions reported from the live catalog. These do not substitute for
# the general rules below; they guarantee these known screen-first profiles can
# never silently return to Music if upstream metadata changes.
SCREEN_FIRST_REGRESSIONS = {
    "zacefron",
    "tomhanks",
    "quentintarantino",
}


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def primary_description_category(description: str) -> str | None:
    """Infer dominant career from the ordering of professions in a description."""
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

    # When the English description is unavailable, an explicit screen occupation
    # beats a generic "Musician" tag. A strong music profession can remain Music.
    strong_music_role_names = set(STRONG_MUSIC_OCCUPATIONS.values())
    return str(music_role or "") not in strong_music_role_names


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
        backoff_factor=.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=6, pool_maxsize=6)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def claim_item_ids(entity: dict[str, Any], property_id: str) -> set[str]:
    output: set[str] = set()
    claims = entity.get("claims", {}).get(property_id, [])
    if not isinstance(claims, list):
        return output
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict):
            qid = str(value.get("id") or "")
            if re.fullmatch(r"Q\d+", qid):
                output.add(qid)
    return output


def fetch_evidence(session: requests.Session, qids: list[str], timeout: float) -> dict[str, dict[str, Any]]:
    if not qids:
        return {}
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
    payload = response.json()
    entities = payload.get("entities", {})
    output: dict[str, dict[str, Any]] = {}
    if isinstance(entities, list):
        iterable = ((str(entity.get("id") or ""), entity) for entity in entities if isinstance(entity, dict))
    elif isinstance(entities, dict):
        iterable = ((str(qid), entity) for qid, entity in entities.items() if isinstance(entity, dict))
    else:
        iterable = []
    for qid, entity in iterable:
        if not re.fullmatch(r"Q\d+", qid):
            continue
        description_block = entity.get("descriptions", {})
        description = ""
        if isinstance(description_block, dict):
            en = description_block.get("en")
            if isinstance(en, dict):
                description = str(en.get("value") or "")
        output[qid] = {
            "description": description,
            "occupations": claim_item_ids(entity, "P106"),
        }
    return output


def move_record_to_actor(record: dict[str, Any], info: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    updated = dict(record)
    description = str((info or {}).get("description") or "")
    occupations = set((info or {}).get("occupations") or set())
    role, discipline = actor_role_and_discipline(occupations)
    updated.update({
        "primaryCategory": "Actor",
        "discipline": discipline,
        "leagueOrMedium": "Film & Television",
        "teamOrPlatform": "Independent / representation not listed",
        "role": role,
        "categoryResolution": reason,
        "categoryResolutionDescription": description,
        "pricingDataStatus": "Source-discovered screen career; profession performance evidence partial",
    })
    updated["searchText"] = " ".join([
        str(updated.get("name") or ""), "Actor", discipline,
        "Film & Television", role, str(updated.get("country") or ""),
        "Current active",
    ]).lower()
    updated.pop("benchmarkRank", None)
    updated.pop("benchmarkPoolSize", None)
    return updated


def resolve_records(records: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actor_names = {
        normalize(record.get("name"))
        for record in records
        if record.get("primaryCategory") == "Actor"
    }
    curated_music_names = {
        normalize(record.get("name"))
        for record in records
        if record.get("primaryCategory") == "Music"
        and str(record.get("sourceNamespace") or "") not in SOURCE_NAMESPACES
    }

    output: list[dict[str, Any]] = []
    moved: list[str] = []
    removed_duplicates: list[str] = []
    deterministic_removals: list[str] = []
    regression_moves: list[str] = []
    reviewed = 0

    for original in records:
        record = dict(original)
        if record.get("primaryCategory") != "Music" or str(record.get("sourceNamespace") or "") not in SOURCE_NAMESPACES:
            output.append(record)
            continue

        name = str(record.get("name") or "")
        name_key = normalize(name)
        qid = str(record.get("sourceRecordId") or "")
        info = evidence.get(qid)

        # A source-discovered Music copy never wins over an existing Actor primary
        # profile. Curated Music profiles are untouched, preserving real crossovers.
        if name_key in actor_names and name_key not in curated_music_names:
            removed_duplicates.append(name or qid)
            deterministic_removals.append(name or qid)
            continue

        # Hard regression guard for live misclassifications already observed.
        if name_key in SCREEN_FIRST_REGRESSIONS:
            moved_record = move_record_to_actor(
                record,
                info,
                "Moved from Music to Actor by screen-first regression guard",
            )
            actor_names.add(name_key)
            moved.append(name or qid)
            regression_moves.append(name or qid)
            output.append(moved_record)
            continue

        if not info:
            output.append(record)
            continue

        reviewed += 1
        description = str(info.get("description") or "")
        occupations = set(info.get("occupations") or set())
        if not should_move_to_actor(description, occupations, str(record.get("role") or "")):
            output.append(record)
            continue

        if name_key in actor_names:
            removed_duplicates.append(name or qid)
            continue

        moved_record = move_record_to_actor(
            record,
            info,
            "Moved from Music to Actor using dominant Wikidata screen-career evidence",
        )
        actor_names.add(name_key)
        moved.append(name or qid)
        output.append(moved_record)

    summary = {
        "reviewedWithEntityEvidence": reviewed,
        "movedToActor": len(moved),
        "removedDuplicateMusicCopies": len(removed_duplicates),
        "deterministicActorCollisionRemovals": len(deterministic_removals),
        "regressionGuardMoves": len(regression_moves),
        "movedNames": moved,
        "removedDuplicateNames": removed_duplicates,
    }
    return output, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--sleep", type=float, default=.04)
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
    batch_size = max(10, min(50, int(args.batch_size)))
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

    # These live regressions are not allowed to remain Music after this step.
    bad_regressions = [
        str(record.get("name") or "")
        for record in resolved
        if record.get("primaryCategory") == "Music"
        and normalize(record.get("name")) in SCREEN_FIRST_REGRESSIONS
    ]
    if bad_regressions:
        raise RuntimeError(f"Screen-first regression profiles still classified as Music: {bad_regressions}")

    args.seed.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "version": "2.0-entity-api",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": ENTITY_ENDPOINT,
        "candidateMusicProfiles": len(qids),
        "resolvedEvidenceProfiles": len(evidence),
        "sourceErrorCount": len(errors),
        "sourceErrors": errors,
        **summary,
        "rule": (
            "Source-discovered Actor/Music collisions resolve to Actor before network lookup; "
            "remaining records use English Wikidata descriptions and screen occupations to choose one primary category."
        ),
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Reviewed {summary['reviewedWithEntityEvidence']:,} Music profiles with entity evidence; "
        f"moved {summary['movedToActor']:,} to Actor and removed "
        f"{summary['removedDuplicateMusicCopies']:,} duplicate Music copies."
    )
    if errors:
        print(f"Completed with {len(errors)} Wikidata entity API batch error(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
