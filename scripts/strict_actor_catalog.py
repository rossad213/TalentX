#!/usr/bin/env python3
"""Keep source-discovered Actor listings screen-first rather than occupation-only."""
from __future__ import annotations

import argparse
import json
import re
import time
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
        dominant = primary_description_category(str(info.get("description") or ""))

        if dominant != "Music" or not strong_music or not mbids:
            output.append(record)
            continue

        name = str(record.get("name") or "")
        key = normalize(name)
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

    return output, {
        "reviewedSourceActors": reviewed,
        "movedToMusic": len(moved),
        "removedDuplicateActorCopies": len(removed_duplicates),
        "movedNames": moved,
        "removedDuplicateNames": removed_duplicates,
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
            "Source-discovered Actor rows remain Actor unless a music-first English Wikidata "
            "description, specific music profession, and MusicBrainz artist ID all support Music."
        ),
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Strict Actor audit: reviewed {summary['reviewedSourceActors']:,}; "
        f"moved {summary['movedToMusic']:,} music-first identities to Music; "
        f"removed {summary['removedDuplicateActorCopies']:,} duplicate Actor copies."
    )
    if errors:
        print(f"Completed with {len(errors):,} source warning(s); unresolved Actor rows were retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
