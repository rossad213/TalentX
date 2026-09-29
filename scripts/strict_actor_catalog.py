#!/usr/bin/env python3
"""Keep source-discovered Actor listings screen-first rather than occupation-only."""
from __future__ import annotations

import argparse
import json
import re
import time
from difflib import SequenceMatcher
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strict_music_catalog import (
    MUSIC_DISCIPLINES,
    STRONG_MUSIC_OCCUPATIONS,
    fetch_entities,
    make_session,
    normalize,
    preferred_music_role,
    primary_description_category,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "data" / "current_seed.json"
DEFAULT_MANIFEST = ROOT / "data" / "strict_actor_manifest.json"
SOURCE_NAMESPACES = {
    "wikidata-non-athlete",
    "wikidata-actor-only",
    "wikidata-actor-resolved-from-music",
}

ACTING_TERMS = (
    "film actor", "television actor", "voice actor", "stage actor",
    "actor", "actress",
)
NON_ACTING_SCREEN_TERMS = (
    "film director", "television director", "filmmaker", "film-maker",
    "screenwriter", "screen writer", "producer", "cinematographer",
)


def primary_actor_description(description: str) -> bool:
    """Require acting to be the primary screen identity, not merely a secondary occupation."""
    text = str(description or "").lower()
    acting = [text.find(term) for term in ACTING_TERMS if text.find(term) >= 0]
    if not acting:
        return False
    competing = [text.find(term) for term in NON_ACTING_SCREEN_TERMS if text.find(term) >= 0]
    music = [
        text.find(term)
        for term in (
            "singer-songwriter", "singer songwriter", "record producer", "rapper",
            "singer", "songwriter", "composer", "disc jockey", "guitarist",
            "pianist", "drummer", "bassist", "violinist", "saxophonist",
        )
        if text.find(term) >= 0
    ]
    blockers = competing + music
    return not blockers or min(acting) < min(blockers)


def actor_role_from_occupations(occupations: set[str]) -> tuple[str, str]:
    priority = (
        ("Q10800557", "Film actor", "Film"),
        ("Q10798782", "Television actor", "Television"),
        ("Q2405480", "Voice actor", "Voice Acting"),
        ("Q2259451", "Stage actor", "Theatre"),
        ("Q33999", "Actor", "Acting"),
    )
    for qid, role, discipline in priority:
        if qid in occupations:
            return role, discipline
    return "Actor", "Acting"


def clean_verified_actor_payload(record: dict[str, Any], info: dict[str, Any], canonical_label: str) -> dict[str, Any]:
    result = dict(record)
    occupations = set(info.get("occupations") or set())
    role, discipline = actor_role_from_occupations(occupations)
    result["primaryCategory"] = "Actor"
    result["role"] = role
    result["discipline"] = discipline
    result["leagueOrMedium"] = "Film & Television" if discipline in {"Acting", "Film", "Television"} else discipline
    result["actorCategoryVerified"] = True
    result["actorCategoryVerification"] = "Acting-first Wikidata description"
    result["wikidataCanonicalLabel"] = canonical_label or str(record.get("name") or "")
    result["pricingDataStatus"] = "Screen-career identity verified; direct performance evidence may be partial"
    result["description"] = str(info.get("description") or record.get("description") or "")
    for field in ("musicCategoryVerified", "musicCategoryVerification", "musicBrainzArtistIds", "verifiedMusicOccupations"):
        result.pop(field, None)
    namespace = str(result.get("sourceNamespace") or "")
    if namespace.startswith("wikidata-music"):
        result["categoryOriginSourceNamespace"] = namespace
        result["sourceNamespace"] = "wikidata-actor-resolved-from-music"
    result["searchText"] = " ".join([
        str(result.get("name") or ""), "Actor", discipline,
        str(result.get("leagueOrMedium") or ""), role,
        str(result.get("country") or ""), "Current active",
    ]).lower()
    return result


def source_name_matches_label(name: Any, label: Any) -> bool:
    left = normalize(name)
    right = normalize(label)
    if not left or not right:
        return True
    if left == right or left in right or right in left:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.70


def is_curated_actor(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Actor"
        and (
            bool(record.get("nonAthleteRosterVersion"))
            or str(record.get("statusSource") or "") == "TalentX curated non-athlete roster"
        )
    )


def move_to_music(record: dict[str, Any], info: dict[str, Any], reason: str) -> dict[str, Any]:
    result = dict(record)
    occupations = set(info.get("occupations") or set())
    strong_music = occupations & set(STRONG_MUSIC_OCCUPATIONS)
    mbids = sorted(set(info.get("musicbrainz") or set()))
    role = preferred_music_role(strong_music)
    discipline = MUSIC_DISCIPLINES.get(role, "Music")
    result.update({
        "primaryCategory": "Music",
        "discipline": discipline,
        "leagueOrMedium": "Music",
        "teamOrPlatform": "Independent / label not listed",
        "role": role,
        "categoryResolution": reason,
        "categoryResolutionDescription": str(info.get("description") or ""),
        "categoryOriginSourceNamespace": str(record.get("sourceNamespace") or ""),
        "sourceNamespace": "wikidata-music-resolved-from-actor",
        "musicCategoryVerified": True,
        "musicCategoryVerification": "Music-first Wikidata description + specific music profession + MusicBrainz artist ID",
        "musicBrainzArtistIds": mbids,
        "verifiedMusicOccupations": sorted(STRONG_MUSIC_OCCUPATIONS[qid] for qid in strong_music),
        "pricingDataStatus": "Strict music-source identity verified; streaming/chart/touring evidence pending",
        "description": f"{role} in {discipline}. Reclassified from Actor using source-backed music-first career evidence.",
    })
    result.pop("benchmarkRank", None)
    result.pop("benchmarkPoolSize", None)
    result["searchText"] = " ".join([
        str(result.get("name") or ""), "Music", discipline, "Music", role,
        str(result.get("country") or ""), "Current active",
    ]).lower()
    return result


def resolve_records(
    records: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    music_names = {
        normalize(record.get("name"))
        for record in records
        if str(record.get("primaryCategory") or "") == "Music"
    }
    output: list[dict[str, Any]] = []
    reviewed = 0
    moved: list[str] = []
    removed_duplicates: list[str] = []
    removed_non_screen_first: list[str] = []
    removed_name_mismatches: list[str] = []
    verified_screen_first: list[str] = []

    for original in records:
        record = dict(original)
        if (
            str(record.get("primaryCategory") or "") != "Actor"
            or is_curated_actor(record)
            or str(record.get("sourceNamespace") or "") not in SOURCE_NAMESPACES
        ):
            output.append(record)
            continue

        qid = str(record.get("sourceRecordId") or "")
        info = evidence.get(qid)
        if not re.fullmatch(r"Q\d+", qid) or not info:
            output.append(record)
            continue

        reviewed += 1
        occupations = set(info.get("occupations") or set())
        strong_music = occupations & set(STRONG_MUSIC_OCCUPATIONS)
        mbids = set(info.get("musicbrainz") or set())
        description = str(info.get("description") or "")
        canonical_label = str(info.get("label") or "")
        dominant = primary_description_category(description)
        name = str(record.get("name") or "")
        key = normalize(name)

        if canonical_label and not source_name_matches_label(name, canonical_label):
            removed_name_mismatches.append(name or qid)
            continue

        if dominant == "Actor" and primary_actor_description(description):
            record = clean_verified_actor_payload(record, info, canonical_label)
            verified_screen_first.append(name or qid)
            output.append(record)
            continue

        if dominant == "Music" and strong_music and mbids:
            if key in music_names:
                removed_duplicates.append(name or qid)
                continue
            moved_record = move_to_music(
                record,
                info,
                "Strict Actor audit: music-first source identity moved to Music",
            )
            music_names.add(key)
            moved.append(name or qid)
            output.append(moved_record)
            continue

        # A secondary acting occupation is not enough for the Actor exchange.
        # Source-discovered records must be screen-first in their source identity.
        removed_non_screen_first.append(name or qid)

    return output, {
        "reviewedSourceActors": reviewed,
        "movedToMusic": len(moved),
        "removedDuplicateActorCopies": len(removed_duplicates),
        "removedNonScreenFirst": len(removed_non_screen_first),
        "removedNameMismatches": len(removed_name_mismatches),
        "verifiedScreenFirst": len(verified_screen_first),
        "movedNames": moved,
        "removedDuplicateNames": removed_duplicates,
        "removedNonScreenFirstNames": removed_non_screen_first,
        "removedNameMismatchNames": removed_name_mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--sleep", type=float, default=.03)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.seed.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")
    records = [record for record in payload if isinstance(record, dict)]
    qids = sorted({
        str(record.get("sourceRecordId") or "")
        for record in records
        if str(record.get("primaryCategory") or "") == "Actor"
        and not is_curated_actor(record)
        and str(record.get("sourceNamespace") or "") in SOURCE_NAMESPACES
        and re.fullmatch(r"Q\d+", str(record.get("sourceRecordId") or ""))
    })

    session = make_session()
    evidence: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    batch_size = max(1, min(50, int(args.batch_size)))
    for start in range(0, len(qids), batch_size):
        batch = qids[start:start + batch_size]
        try:
            evidence.update(fetch_entities(session, batch, args.request_timeout))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"batch={start // batch_size + 1}:{type(exc).__name__}:{exc}")
        if args.sleep:
            time.sleep(max(0.0, args.sleep))

    if errors and not args.allow_source_errors:
        raise RuntimeError(f"Actor category source errors: {errors[:5]}")

    resolved, summary = resolve_records(records, evidence)
    args.seed.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = Counter(str(record.get("primaryCategory") or "Unknown") for record in resolved)
    manifest = {
        "version": "1.0-screen-first",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "candidateActorQids": len(qids),
        "resolvedActorQids": len(evidence),
        "sourceErrorCount": len(errors),
        "sourceErrors": errors,
        "categoryCounts": dict(sorted(counts.items())),
        **summary,
        "rule": (
            "Source-discovered Actor rows must have an acting-first English Wikidata description and a "
            "compatible canonical identity label. Music-first identities move to Music only with a specific "
            "music profession plus MusicBrainz proof; other secondary-occupation rows are removed from Current."
        ),
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Strict Actor audit: reviewed {summary['reviewedSourceActors']:,}; "
        f"verified {summary['verifiedScreenFirst']:,} screen-first identities; "
        f"moved {summary['movedToMusic']:,} music-first identities to Music; "
        f"removed {summary['removedNonScreenFirst']:,} non-screen-first rows, "
        f"{summary['removedNameMismatches']:,} name/QID mismatches, and "
        f"{summary['removedDuplicateActorCopies']:,} duplicate Actor copies."
    )
    if errors:
        print(f"Completed with {len(errors):,} source warning(s); unresolved Actor rows were retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
