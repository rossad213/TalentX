#!/usr/bin/env python3
"""Build TalentX's curated non-athlete seed catalog.

The current athlete catalog is sourced from roster feeds. Music, acting, and
creator coverage currently has no comparable single public roster API, so this
builder maintains an explicit, reviewable roster and deterministic benchmark
inputs until profession-specific evidence ingestion is available.

The builder:
- preserves athlete seed records;
- rebuilds Music, Actor, and Creator records from data/non_athlete_roster.json;
- assigns stable IDs/tickers and profession-specific metric profiles;
- records an explicit benchmark rank instead of depending on JSON array order;
- reprices the complete seed with the authoritative pricing model;
- updates taxonomy disciplines and writes a build manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pricing_model import (
    CATEGORY_WEIGHTS,
    apply_pricing_to_records,
    benchmark_score,
    clamp,
    load_overrides,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_ROSTER = DATA / "non_athlete_roster.json"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_TAXONOMY = DATA / "taxonomy.json"
DEFAULT_MANIFEST = DATA / "non_athlete_manifest.json"
DEFAULT_OVERRIDES = DATA / "pricing_overrides.json"
SUPPORTED = ("Music", "Actor", "Creator")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text or "talent"


def initials(name: str) -> str:
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    if not parts:
        return "TX"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def unique_ticker(name: str, key: str, used: set[str]) -> str:
    letters = re.sub(
        r"[^A-Z]",
        "",
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").upper(),
    )
    base = (letters[:4] or "TALX").ljust(4, "X")
    if base not in used:
        used.add(base)
        return base
    suffix = hashlib.sha1(key.encode("utf-8")).hexdigest().upper()
    for length in range(1, 5):
        candidate = (base[: 4 - length] + suffix[:length])[:4]
        if candidate not in used:
            used.add(candidate)
            return candidate
    index = 0
    while True:
        candidate = f"{base[:2]}{index:02d}"[-4:]
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def deterministic_rng(category: str, name: str) -> random.Random:
    digest = hashlib.sha256(f"non-athlete:{category}:{name}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def metrics_from_rank(category: str, name: str, rank: int, total: int) -> dict[str, float]:
    """Create interpretable placeholder metrics centered on the benchmark rank.

    These are temporary curated inputs, not verified statistics. The weighted
    category score is intentionally close to the benchmark score so the profile
    breakdown and the temporary benchmark prior do not contradict each other.
    """
    target = benchmark_score(rank, total)
    weights = CATEGORY_WEIGHTS[category]
    rng = deterministic_rng(category, name)

    spread = {
        "Music": {
            "performance": 7.0,
            "consistency": 6.0,
            "achievements": 7.0,
            "audience": 6.0,
            "potential": 9.0,
        },
        "Actor": {
            "performance": 7.0,
            "consistency": 6.0,
            "achievements": 7.0,
            "audience": 6.0,
            "potential": 9.0,
        },
        "Creator": {
            "audience": 7.0,
            "performance": 7.0,
            "potential": 9.0,
            "consistency": 6.0,
            "achievements": 8.0,
        },
    }[category]

    raw = {key: target + rng.uniform(-spread[key], spread[key]) for key in weights}
    weighted = sum(raw[key] * weight for key, weight in weights.items())
    shift = target - weighted
    output = {key: round(clamp(value + shift, 35, 99), 1) for key, value in raw.items()}
    output["availability"] = round(clamp(78 + rng.uniform(-6, 13), 65, 96), 1)
    return output


def legacy_metrics(active: dict[str, float], category: str, name: str) -> dict[str, float]:
    rng = deterministic_rng(f"legacy:{category}", name)
    achievements = active.get("achievements", 60.0)
    audience = active.get("audience", 60.0)
    consistency = active.get("consistency", active.get("performance", 60.0))
    return {
        "legacy": round(clamp(achievements * 0.68 + consistency * 0.32, 0, 100), 1),
        "audience": round(clamp(audience, 0, 100), 1),
        "postCareer": round(clamp(55 + rng.uniform(-8, 30), 35, 95), 1),
        "recognition": round(clamp(achievements * 0.62 + audience * 0.38, 0, 100), 1),
        "liquidity": round(clamp(50 + rng.uniform(-8, 35), 30, 94), 1),
    }


def build_record(
    category: str,
    entry: dict[str, Any],
    existing: dict[str, Any] | None,
    used_ids: set[str],
    used_tickers: set[str],
    source_url: str,
    roster_version: str,
) -> dict[str, Any]:
    name = str(entry["name"]).strip()
    rank = int(entry["benchmarkRank"])
    total = int(entry["benchmarkPoolSize"])
    record = dict(existing or {})

    profile_id = str(record.get("id") or f"cur-{slugify(name)}")
    if profile_id in used_ids and (not existing or profile_id != existing.get("id")):
        profile_id = f"cur-{slugify(name)}-{category.lower()}"
    used_ids.add(profile_id)

    ticker = str(record.get("ticker") or "")
    if ticker:
        used_tickers.add(ticker)
    else:
        ticker = unique_ticker(name, f"{category}:{name}", used_tickers)

    active = metrics_from_rank(category, name, rank, total)
    legacy = legacy_metrics(active, category, name)
    medium = str(entry.get("leagueOrMedium") or category)
    platform = str(entry.get("teamOrPlatform") or medium)
    role = str(entry.get("role") or category)
    country = str(entry.get("country") or "Not listed")
    status = str(entry.get("careerStatus") or "Active")
    discipline = str(entry.get("discipline") or category)
    career_stage = "Active career" if status in {"Active", "Touring", "Currently filming", "Upcoming project"} else status

    record.update({
        "id": profile_id,
        "name": name,
        "ticker": ticker,
        "primaryCategory": category,
        "discipline": discipline,
        "leagueOrMedium": medium,
        "teamOrPlatform": platform,
        "role": role,
        "country": country,
        "careerStatus": status,
        "marketSegment": "Current",
        "verificationStatus": "Curated non-athlete expansion seed — profession evidence required",
        "lastVerifiedAt": None,
        "statusSource": "TalentX curated non-athlete roster",
        "sourceName": "TalentX non-athlete roster",
        "sourceUrl": source_url,
        "dataConfidence": 0.70,
        "activeMetrics": active,
        "legacyMetrics": legacy,
        "modelType": "Active career model",
        "avatar": initials(name),
        "description": (
            f"{role} in {discipline}. This listing belongs to the curated TalentX "
            "non-athlete expansion and requires profession-specific evidence verification."
        ),
        "searchText": " ".join([
            name, category, discipline, medium, platform, role, country, status, "Current", career_stage
        ]).lower(),
        "careerStage": career_stage,
        "pricingDataStatus": "Curated benchmark prior — profession evidence required",
        "pricingConfidence": 0.70,
        "pricingEvidence": [],
        "benchmarkRank": rank,
        "benchmarkPoolSize": total,
        "nonAthleteRosterVersion": roster_version,
    })
    return record


def update_taxonomy(path: Path, roster: dict[str, Any]) -> None:
    taxonomy = load_json(path) if path.exists() else {"categories": {}}
    categories = taxonomy.setdefault("categories", {})
    for category in SUPPORTED:
        category_block = categories.setdefault(category, {"label": category, "disciplines": [], "filters": []})
        disciplines = set(str(item) for item in category_block.get("disciplines", []) if item)
        disciplines.update(
            str(entry.get("discipline"))
            for entry in roster["categories"][category]
            if entry.get("discipline")
        )
        category_block["disciplines"] = sorted(disciplines)
    path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")


def validate(records: list[dict[str, Any]], target: int, roster_version: str) -> None:
    ids = [str(record.get("id") or "") for record in records]
    tickers = [str(record.get("ticker") or "") for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate profile IDs after non-athlete build")
    if len(tickers) != len(set(tickers)):
        raise ValueError("Duplicate tickers after non-athlete build")
    curated = [
        record for record in records
        if record.get("nonAthleteRosterVersion") == roster_version
    ]
    counts = Counter(str(record.get("primaryCategory")) for record in curated)
    for category in SUPPORTED:
        if counts[category] != target:
            raise ValueError(f"Expected exactly {target} curated {category} records, found {counts[category]}")
        ranks = sorted(
            int(record.get("benchmarkRank"))
            for record in curated
            if record.get("primaryCategory") == category
        )
        if ranks != list(range(1, target + 1)):
            raise ValueError(f"{category} benchmark ranks must be exactly 1 through {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    roster = load_json(args.roster)
    if not isinstance(roster, dict) or not isinstance(roster.get("categories"), dict):
        raise ValueError("non_athlete_roster.json must contain a categories object")
    target = int(roster.get("targetPerCategory") or 0)
    if target <= 0:
        raise ValueError("targetPerCategory must be positive")

    seed = load_json(args.seed)
    if not isinstance(seed, list):
        raise ValueError("current_seed.json must be an array")

    existing_by_key = {
        (str(record.get("primaryCategory")), normalize(record.get("name", ""))): record
        for record in seed
    }
    athletes = [record for record in seed if record.get("primaryCategory") == "Athlete"]
    expansion_non_athletes = [
        record for record in seed
        if record.get("primaryCategory") != "Athlete"
        and record.get("sourceNamespace") == "wikipedia-wikidata"
    ]
    # Reserve every existing identity before generating additions. Otherwise a
    # newly inserted higher-ranked name could claim a ticker that belongs to an
    # existing record processed later in the curated order.
    used_ids = {str(record.get("id")) for record in seed if record.get("id")}
    used_tickers = {str(record.get("ticker")) for record in seed if record.get("ticker")}
    source_by_category = {
        str(source.get("category")): str(source.get("url") or "")
        for source in roster.get("selectionSources", [])
        if isinstance(source, dict)
    }

    non_athletes: list[dict[str, Any]] = []
    roster_version = str(roster.get("version") or "unversioned")
    for category in SUPPORTED:
        entries = roster["categories"].get(category)
        if not isinstance(entries, list) or len(entries) != target:
            raise ValueError(f"{category} roster must contain exactly {target} entries")
        ordered = sorted(entries, key=lambda entry: int(entry.get("benchmarkRank") or 999999))
        for entry in ordered:
            name = str(entry.get("name") or "").strip()
            if not name:
                raise ValueError(f"{category} roster contains a blank name")
            existing = existing_by_key.get((category, normalize(name)))
            non_athletes.append(build_record(
                category,
                entry,
                existing,
                used_ids,
                used_tickers,
                source_by_category.get(category, ""),
                roster_version,
            ))

    combined = athletes + expansion_non_athletes + non_athletes
    overrides = load_overrides(DEFAULT_OVERRIDES)
    combined = apply_pricing_to_records(
        combined,
        overrides,
        benchmark_records=combined,
        calibration_reference=combined,
    )
    validate(combined, target, roster_version)

    if args.dry_run:
        counts = Counter(str(record.get("primaryCategory")) for record in combined)
        print(json.dumps({"records": len(combined), "categoryCounts": counts}, default=dict, indent=2))
        return 0

    args.seed.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    update_taxonomy(args.taxonomy, roster)
    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    counts = Counter(str(record.get("primaryCategory")) for record in combined)
    manifest = {
        "version": roster_version,
        "generatedAt": built_at,
        "targetPerCategory": target,
        "totalNonAthleteRecords": sum(counts[category] for category in SUPPORTED),
        "categoryCounts": {category: counts[category] for category in SUPPORTED},
        "athleteSeedRecordsPreserved": counts.get("Athlete", 0),
        "selectionMethod": roster.get("selectionMethod"),
        "selectionSources": roster.get("selectionSources", []),
        "pricingStatus": "Curated benchmark prior — profession evidence required",
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Built {len(combined):,} seed records")
    for category in ("Athlete",) + SUPPORTED:
        print(f"- {category}: {counts.get(category, 0):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
