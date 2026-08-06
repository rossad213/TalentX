#!/usr/bin/env python3
"""Expand thin TalentX individual-sport categories with official current rosters.

The operation is deterministic and idempotent. Existing evidence-enriched records
keep their metrics, confidence, prices, IDs, and tickers. Missing names receive
conservative provisional records and proceed through the normal pricing pipeline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "data" / "current_seed.json"
DEFAULT_ROSTERS = ROOT / "data" / "individual_sport_rosters.json"
DEFAULT_MANIFEST = ROOT / "data" / "individual_sport_roster_manifest.json"
TARGET_DISCIPLINES = ("Tennis", "Golf", "Motorsport", "Combat Sports", "Cricket")
SOURCE_NAMESPACE = "curated-individual-sport-roster"


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def slug(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def expand_rosters(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Expand compact group/player rows into normalized roster entries."""
    categories = payload.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("Roster file must contain a categories object")
    expanded: dict[str, list[dict[str, Any]]] = {}
    for discipline in TARGET_DISCIPLINES:
        groups = categories.get(discipline)
        if not isinstance(groups, list):
            raise ValueError(f"Missing roster groups for {discipline}")
        items: list[dict[str, Any]] = []
        priority = 0
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError(f"{discipline} contains an invalid group")
            fields = group.get("playerFields")
            players = group.get("players")
            if not isinstance(fields, list) or not isinstance(players, list):
                raise ValueError(f"{discipline} group lacks playerFields or players")
            for row in players:
                if not isinstance(row, list) or len(row) != len(fields):
                    raise ValueError(f"Invalid player row in {discipline}: {row!r}")
                priority += 1
                player = dict(zip(fields, row))
                item = {
                    **{key: value for key, value in group.items() if key not in {"playerFields", "players", "ranked"}},
                    **player,
                    "rosterPriority": int(number(player.get("rosterPriority"), priority)),
                    "careerStatus": "Active",
                }
                item["group"] = str(player.get("rankingGroup") or group.get("group") or "Current roster")
                item["teamOrPlatform"] = player.get("teamOrPlatform") or group.get("leagueOrMedium") or discipline
                if group.get("ranked") is False:
                    item.pop("sourceRank", None)
                items.append(item)
        expanded[discipline] = items
    return expanded


def strength(record: dict[str, Any]) -> tuple[int, float, int]:
    evidence = record.get("pricingEvidence")
    summary = record.get("pricingEvidenceSummary")
    return (
        int(bool(record.get("activeMetrics"))) + int(isinstance(summary, dict) and bool(summary)),
        number(record.get("pricingConfidence", record.get("dataConfidence", 0))),
        len(evidence) if isinstance(evidence, list) else 0,
    )


def provisional_metrics(rank: int, group_size: int) -> dict[str, float]:
    rank, group_size = max(1, rank), max(1, group_size)
    pct = 1.0 if group_size == 1 else 1.0 - (rank - 1) / (group_size - 1)
    return {
        "performance": round(72 + 16 * pct, 1),
        "achievements": round(67 + 17 * pct, 1),
        "consistency": round(72 + 13 * pct, 1),
        "potential": round(72 + 10 * pct, 1),
        "availability": 88.0,
        "audience": round(69 + 16 * pct, 1),
    }


def ticker_base(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii"))
    base = "".join(word[0] for word in words[:5]) if len(words) > 1 else "".join(words)[:5]
    return (re.sub(r"[^A-Za-z0-9]", "", base).upper() or "TX")[:5]


def unique_ticker(name: str, used: set[str]) -> str:
    base = ticker_base(name)
    if base not in used:
        used.add(base)
        return base
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest().upper()
    for width in range(1, 5):
        candidate = (base[: max(1, 5 - width)] + digest[:width])[:5]
        if candidate not in used:
            used.add(candidate)
            return candidate
    index = 2
    while True:
        suffix = str(index)
        candidate = (base[: max(1, 5 - len(suffix))] + suffix)[:5]
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def unique_id(discipline: str, name: str, used: set[str]) -> str:
    base = f"athlete-{slug(discipline)}-{slug(name)}"
    candidate, index = base, 2
    while candidate in used:
        candidate, index = f"{base}-{index}", index + 1
    used.add(candidate)
    return candidate


def build_record(
    discipline: str,
    item: dict[str, Any],
    version: str,
    group_size: int,
    used_ids: set[str],
    used_tickers: set[str],
) -> dict[str, Any]:
    name = str(item["name"]).strip()
    rank = int(number(item.get("sourceRank", item.get("rosterPriority", group_size)), group_size))
    confidence = 0.64 if item.get("sourceRank") is not None else 0.61
    source_as_of = str(item.get("sourceAsOf") or "")
    league = str(item.get("leagueOrMedium") or discipline)
    team = str(item.get("teamOrPlatform") or league)
    role = str(item.get("role") or "Athlete")
    country = str(item.get("country") or "—")
    return {
        "id": unique_id(discipline, name, used_ids),
        "name": name,
        "ticker": unique_ticker(name, used_tickers),
        "primaryCategory": "Athlete",
        "discipline": discipline,
        "leagueOrMedium": league,
        "teamOrPlatform": team,
        "role": role,
        "country": country,
        "careerStatus": "Active",
        "marketSegment": "Current",
        "verificationStatus": "Official current ranking or active-season roster snapshot",
        "lastVerifiedAt": f"{source_as_of}T00:00:00Z" if source_as_of else None,
        "statusSource": item.get("sourceName"),
        "sourceName": item.get("sourceName"),
        "sourceUrl": item.get("sourceUrl"),
        "sourceNamespace": SOURCE_NAMESPACE,
        "sourceType": "official-ranking-roster",
        "sourceAsOf": source_as_of,
        "rosterVersion": version,
        "rosterGroup": item.get("group"),
        "rosterPriority": int(number(item.get("rosterPriority"), rank)),
        "sourceRank": int(number(item.get("sourceRank"), rank)) if item.get("sourceRank") is not None else None,
        "pricingDataStatus": "Curated individual-sport roster; profession-specific statistics pending",
        "pricingConfidence": confidence,
        "dataConfidence": confidence,
        "activeMetrics": provisional_metrics(rank, group_size),
        "legacyMetrics": {},
        "modelType": "Active career model",
        "careerStage": "Established",
        "avatar": "".join(part[0] for part in name.split()[:2]).upper(),
        "description": (
            f"Current {discipline} listing sourced from {item.get('sourceName') or 'an official roster'}. "
            "Pricing is provisional until profession-specific performance and career evidence is enriched."
        ),
        "searchText": " ".join([name, "Athlete", discipline, league, team, role, country, "Active", "Current"]).lower(),
    }


def merge_existing(record: dict[str, Any], discipline: str, item: dict[str, Any], version: str, group_size: int) -> bool:
    before = json.dumps(record, sort_keys=True, ensure_ascii=False)
    rank = int(number(item.get("sourceRank", item.get("rosterPriority", group_size)), group_size))
    record["primaryCategory"] = "Athlete"
    record["discipline"] = discipline
    record.setdefault("marketSegment", "Current")
    record.setdefault("verificationStatus", "Official current ranking or active-season roster snapshot")
    if item.get("sourceAsOf"):
        record.setdefault("lastVerifiedAt", f"{item['sourceAsOf']}T00:00:00Z")
    if item.get("sourceName"):
        record.setdefault("statusSource", item["sourceName"])
    for key in ("leagueOrMedium", "teamOrPlatform", "role", "country", "careerStatus", "sourceName", "sourceUrl"):
        if not record.get(key) and item.get(key):
            record[key] = item[key]
    record.update({
        "rosterSourceName": item.get("sourceName"),
        "rosterSourceUrl": item.get("sourceUrl"),
        "rosterSourceAsOf": item.get("sourceAsOf"),
        "rosterVersion": version,
        "rosterGroup": item.get("group"),
        "rosterPriority": int(number(item.get("rosterPriority"), rank)),
    })
    if item.get("sourceRank") is not None:
        record["rosterSourceRank"] = int(number(item.get("sourceRank"), rank))
    if not isinstance(record.get("activeMetrics"), dict) or not record["activeMetrics"]:
        record["activeMetrics"] = provisional_metrics(rank, group_size)
    if record.get("pricingConfidence") is None:
        record["pricingConfidence"] = 0.64 if item.get("sourceRank") is not None else 0.61
    record.setdefault("dataConfidence", record["pricingConfidence"])
    record.setdefault("pricingDataStatus", "Curated individual-sport roster; profession-specific statistics pending")
    return before != json.dumps(record, sort_keys=True, ensure_ascii=False)


def upsert_rosters(records: list[dict[str, Any]], payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rosters = expand_rosters(payload)
    version = str(payload.get("version") or "unversioned")
    output = [dict(record) for record in records if isinstance(record, dict)]
    used_ids = {str(record.get("id")) for record in output if record.get("id")}
    used_tickers = {str(record.get("ticker")).upper() for record in output if record.get("ticker")}
    by_name: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(output):
        if normalize(record.get("name")):
            by_name[normalize(record["name"])].append(index)

    before_counts = Counter(str(record.get("discipline") or "") for record in output)
    changes: dict[str, dict[str, int]] = {}
    for discipline in TARGET_DISCIPLINES:
        items = rosters[discipline]
        groups = Counter(str(item.get("group") or "Current roster") for item in items)
        group_spans = {
            group: max(
                count,
                max(
                    (int(number(item.get("sourceRank"), 0)) for item in items if str(item.get("group") or "Current roster") == group),
                    default=0,
                ),
            )
            for group, count in groups.items()
        }
        seen: set[str] = set()
        added = updated = preserved = 0
        for item in items:
            key = normalize(item.get("name"))
            if not key or key in seen:
                raise ValueError(f"Invalid or duplicate {discipline} roster name: {item.get('name')!r}")
            seen.add(key)
            group_size = group_spans[str(item.get("group") or "Current roster")]
            matches = by_name.get(key, [])
            if matches:
                best = max(matches, key=lambda idx: strength(output[idx]))
                if merge_existing(output[best], discipline, item, version, group_size):
                    updated += 1
                else:
                    preserved += 1
            else:
                output.append(build_record(discipline, item, version, group_size, used_ids, used_tickers))
                by_name[key].append(len(output) - 1)
                added += 1
        changes[discipline] = {"added": added, "updated": updated, "preserved": preserved}

    after_counts = Counter(str(record.get("discipline") or "") for record in output)
    minimum = int(number(payload.get("targetMinimumPerDiscipline"), 20))
    failures = {d: after_counts[d] for d in TARGET_DISCIPLINES if after_counts[d] < minimum}
    if failures:
        raise ValueError(f"Individual-sport roster minimum not met: {failures}")
    summary = {
        "version": version,
        "targetMinimumPerDiscipline": minimum,
        "recordsBefore": len(records),
        "recordsAfter": len(output),
        "netAdded": len(output) - len(records),
        "countsBefore": {d: before_counts[d] for d in TARGET_DISCIPLINES},
        "countsAfter": {d: after_counts[d] for d in TARGET_DISCIPLINES},
        "changes": changes,
    }
    return output, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--rosters", type=Path, default=DEFAULT_ROSTERS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    records = json.loads(args.seed.read_text(encoding="utf-8"))
    payload = json.loads(args.rosters.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{args.seed.name} must contain a JSON array")
    updated, summary = upsert_rosters(records, payload)
    args.seed.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    sources = sorted({
        (str(group.get("sourceName") or ""), str(group.get("sourceUrl") or ""), str(group.get("sourceAsOf") or ""))
        for groups in payload.get("categories", {}).values() if isinstance(groups, list)
        for group in groups if isinstance(group, dict) and group.get("sourceUrl")
    })
    manifest = {
        **summary,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceNamespace": SOURCE_NAMESPACE,
        "sources": [{"name": n, "url": u, "asOf": a} for n, u, a in sources],
        "pricingRule": (
            "New listings receive conservative rank-within-group provisional metrics. "
            "Existing evidence-enriched records retain their stronger metrics and confidence."
        ),
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Expanded individual-sport rosters:", json.dumps(summary["countsAfter"], sort_keys=True))
    print("Net new listings:", summary["netAdded"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
