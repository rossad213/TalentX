#!/usr/bin/env python3
"""Enrich TalentX current listings with real career evidence before pricing.

The roster builder proves that a person is currently attached to a roster. This
script adds a second layer: recent statistics, career statistics, awards counts,
age/experience, and role-aware cohort ranking. It then recalculates every current
price through the existing TalentX active-career formula:

30% performance + 25% achievements + 20% potential + 15% audience + 10% availability.

Records without usable evidence stay conservative and explicitly provisional.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pricing_model import apply_pricing_to_records, clamp, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CATALOG = DATA / "current_catalog.json"
CATALOG_CSV = DATA / "current_catalog.csv"
CATALOG_MANIFEST = DATA / "catalog_manifest.json"
PRICING_OVERRIDES = DATA / "pricing_overrides.json"
DRAFT_METADATA_OVERRIDES = DATA / "draft_metadata_overrides.json"
ENRICHMENT_MANIFEST = DATA / "pricing_enrichment_manifest.json"

ESPN_OVERVIEW = "https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{athlete_id}/overview"
ESPN_ATHLETE_PROFILE = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/athletes/{athlete_id}"
ESPN_CORE_ATHLETE = "https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/athletes/{athlete_id}?lang=en&region=us"
ESPN_AWARDS = "https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/athletes/{athlete_id}/awards?limit=100"
NHL_LANDING = "https://api-web.nhle.com/v1/player/{athlete_id}/landing"
USER_AGENT = "TalentX-Pricing-Enricher/3.4 (+https://github.com/rossad213/TalentX)"

SPORT_PATH = {
    "American Football": "football",
    "Basketball": "basketball",
    "Baseball": "baseball",
    "Soccer": "soccer",
    "Hockey": "hockey",
}

_thread_local = threading.local()


def session() -> requests.Session:
    existing = getattr(_thread_local, "session", None)
    if existing is not None:
        return existing
    s = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.35,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=40, pool_maxsize=40)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    _thread_local.session = s
    return s


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    response = session().get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"--", "-", "—", "N/A", "NA"}:
        return None
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group()) if match else None


def positive_int(value: Any) -> int | None:
    parsed = number(value)
    if parsed is None or parsed < 0:
        return None
    return int(round(parsed))


def extract_profile_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract age, experience and draft information from varied athlete payloads."""
    output: dict[str, Any] = {}
    draft_candidates: list[dict[str, Any]] = []

    def parse_draft(candidate: dict[str, Any]) -> None:
        normalized = {norm_key(key): value for key, value in candidate.items()}
        aliases = {
            "draftYear": ("draftyear", "year"),
            "draftRound": ("draftround", "round"),
            "draftPick": ("draftpick", "overallpick", "overall", "selection", "pick"),
        }
        for target, keys in aliases.items():
            if output.get(target) is not None:
                continue
            for key in keys:
                if key in normalized:
                    value = positive_int(normalized[key])
                    if value is not None:
                        output[target] = value
                        break
        display = " ".join(
            str(candidate.get(key) or "")
            for key in ("displayText", "displayName", "description", "shortDisplayName", "text")
        )
        if display:
            year_match = re.search(r"\b(19|20)\d{2}\b", display)
            round_match = re.search(r"(?:round|r)\s*([1-9]\d*)", display, re.I)
            pick_match = re.search(r"(?:pick|overall|selection)\s*(?:no\.?\s*)?#?\s*([1-9]\d*)", display, re.I)
            if year_match and output.get("draftYear") is None:
                output["draftYear"] = int(year_match.group())
            if round_match and output.get("draftRound") is None:
                output["draftRound"] = int(round_match.group(1))
            if pick_match and output.get("draftPick") is None:
                output["draftPick"] = int(pick_match.group(1))

    def walk(node: Any, parent_key: str = "") -> None:
        if isinstance(node, dict):
            normalized_keys = {norm_key(key) for key in node}
            if "draft" in norm_key(parent_key) or normalized_keys.intersection(
                {"draftyear", "draftround", "draftpick", "overallpick", "selection"}
            ):
                draft_candidates.append(node)
            experience = node.get("experience")
            if output.get("experienceYears") is None:
                if isinstance(experience, dict):
                    output["experienceYears"] = positive_int(experience.get("years") or experience.get("value"))
                elif experience is not None:
                    output["experienceYears"] = positive_int(experience)
                for key in ("experienceYears", "yearsPro"):
                    if output.get("experienceYears") is None and node.get(key) is not None:
                        output["experienceYears"] = positive_int(node.get(key))
            if output.get("age") is None and node.get("age") is not None:
                output["age"] = positive_int(node.get("age"))
            for key, value in node.items():
                walk(value, str(key))
        elif isinstance(node, list):
            for value in node:
                walk(value, parent_key)

    walk(payload)
    for candidate in draft_candidates:
        parse_draft(candidate)
    return {key: value for key, value in output.items() if value is not None}


def merge_profile_evidence(record: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    for key, value in evidence.items():
        if record.get(key) in (None, ""):
            record[key] = value
    return record


def professional_games_from_stats(recent: dict[str, float], career: dict[str, float]) -> int:
    aliases = ("gamesPlayed", "games", "appearances", "gp")
    for stats in (career, recent):
        value = get(stats, *aliases)
        if value > 0:
            return max(0, int(round(value)))
    return 0


def load_draft_metadata(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("draft_metadata_overrides.json must contain a records array")
    output: dict[tuple[str, str], dict[str, Any]] = {}
    source = payload.get("source") if isinstance(payload, dict) else None
    for item in records:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        enriched = dict(item)
        if source and not enriched.get("sourceUrl"):
            enriched["sourceUrl"] = source
        output[(norm_key(item["name"]), norm_key(item.get("league", "")))] = enriched
    return output


def apply_draft_metadata(record: dict[str, Any], metadata: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    result = dict(record)
    item = metadata.get((norm_key(result.get("name")), norm_key(result.get("leagueOrMedium"))))
    if not item:
        return result
    for field in ("draftYear", "draftRound", "draftPick"):
        if item.get(field) is not None:
            result[field] = item[field]
    result["draftMetadataSource"] = item.get("sourceUrl")
    return result


def merge_stat_map(target: dict[str, float], names: Iterable[Any], values: Iterable[Any]) -> None:
    for name, value in zip(names, values):
        parsed = number(value)
        if parsed is None:
            continue
        key = norm_key(name)
        if not key:
            continue
        target[key] = parsed


def extract_stat_maps(payload: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    """Extract recent-season and career stat maps from varied ESPN payloads."""
    recent_candidates: list[dict[str, float]] = []
    career_candidates: list[dict[str, float]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            names = node.get("names") or node.get("labels") or node.get("displayNames")
            splits = node.get("splits")
            if isinstance(names, list) and isinstance(splits, list):
                for split in splits:
                    if not isinstance(split, dict):
                        continue
                    values = split.get("stats") or split.get("values")
                    if not isinstance(values, list):
                        continue
                    mapped: dict[str, float] = {}
                    merge_stat_map(mapped, names, values)
                    if not mapped:
                        continue
                    label = " ".join(
                        str(split.get(key) or "")
                        for key in ("displayName", "name", "abbreviation", "type")
                    ).lower()
                    if "career" in label:
                        career_candidates.append(mapped)
                    else:
                        recent_candidates.append(mapped)
            # Some endpoints expose direct stat objects.
            if isinstance(node.get("statistics"), dict):
                direct = node["statistics"]
                mapped = {}
                for key, value in direct.items():
                    parsed = number(value)
                    if parsed is not None:
                        mapped[norm_key(key)] = parsed
                if mapped:
                    recent_candidates.append(mapped)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)

    def best(candidates: list[dict[str, float]]) -> dict[str, float]:
        if not candidates:
            return {}
        return max(candidates, key=lambda item: (len(item), sum(abs(v) for v in item.values())))

    recent = best(recent_candidates)
    career = best(career_candidates)
    if not career and len(recent_candidates) > 1:
        # The largest map is usually career when the endpoint omits a label.
        ordered = sorted(recent_candidates, key=lambda item: len(item), reverse=True)
        career = ordered[0]
        recent = ordered[1] if len(ordered) > 1 else ordered[0]
    return recent, career


def recursively_collect_numbers(payload: Any, output: dict[str, float], prefix: str = "") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            name = f"{prefix}{key}"
            parsed = number(value)
            if parsed is not None and not isinstance(value, (dict, list)):
                output[norm_key(name)] = parsed
            else:
                recursively_collect_numbers(value, output, f"{name}_")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            recursively_collect_numbers(value, output, f"{prefix}{index}_")


def award_points(payload: dict[str, Any]) -> tuple[float, list[str]]:
    count = number(payload.get("count"))
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if count is None:
        count = float(len(items))
    names: list[str] = []
    bonus = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("displayName") or item.get("name") or item.get("description") or "").strip()
        if title:
            names.append(title)
            low = title.lower()
            if any(token in low for token in ("most valuable", "mvp", "player of the year", "cy young", "golden boot")):
                bonus += 8
            elif any(token in low for token in ("all-pro", "all nba", "all-star", "pro bowl", "gold glove", "silver slugger")):
                bonus += 4
            elif any(token in low for token in ("rookie", "champion", "championship", "world series", "super bowl")):
                bonus += 3
    return min(100.0, count * 3.0 + bonus), names[:12]


def get(stats: dict[str, float], *aliases: str) -> float:
    for alias in aliases:
        key = norm_key(alias)
        if key in stats:
            return stats[key]
    # Flexible suffix match handles prefixes inserted by nested payloads.
    for alias in aliases:
        key = norm_key(alias)
        for existing, value in stats.items():
            if existing.endswith(key):
                return value
    return 0.0


def role_group(record: dict[str, Any]) -> str:
    role = str(record.get("role") or "").lower()
    league = str(record.get("leagueOrMedium") or "")
    if league == "NFL":
        if any(token in role for token in ("quarterback", " qb")) or role.strip() == "qb":
            return "QB"
        if any(token in role for token in ("running back", "fullback", " rb", " fb")):
            return "RB"
        if any(token in role for token in ("wide receiver", "receiver", "tight end", " wr", " te")):
            return "REC"
        if any(token in role for token in ("kicker", "punter", "long snapper")):
            return "ST"
        if any(token in role for token in ("tackle", "guard", "center", "offensive line")):
            return "OL"
        return "DEF"
    if league in {"NBA", "WNBA"}:
        return "BASKETBALL"
    if league == "MLB":
        return "PITCHER" if any(token in role for token in ("pitcher", "starter", "relief")) else "HITTER"
    if league == "NHL":
        return "GOALIE" if "goal" in role else "SKATER"
    if record.get("discipline") == "Soccer":
        if any(token in role for token in ("goalkeeper", "keeper")):
            return "GOALKEEPER"
        if any(token in role for token in ("forward", "striker", "wing")):
            return "ATTACKER"
        if any(token in role for token in ("defender", "back")):
            return "DEFENDER"
        return "MIDFIELDER"
    return norm_key(role)[:24] or "GENERAL"


def signal_bundle(record: dict[str, Any], recent: dict[str, float], career: dict[str, float], awards: float) -> dict[str, float]:
    league = str(record.get("leagueOrMedium") or "")
    group = role_group(record)

    if league == "NFL":
        if group == "QB":
            recent_prod = (
                get(recent, "passingYards") / 90
                + get(recent, "passingTouchdowns") * 5.5
                - get(recent, "interceptions") * 3.5
                + get(recent, "rushingYards") / 35
                + get(recent, "rushingTouchdowns") * 4
            )
            career_prod = (
                get(career, "passingYards") / 350
                + get(career, "passingTouchdowns") * 2.8
                - get(career, "interceptions") * 1.1
                + get(career, "rushingYards") / 140
                + get(career, "rushingTouchdowns") * 2
            )
            efficiency = (
                get(recent, "QBRating", "passerRating")
                + get(recent, "completionPct", "completionPercentage") * 0.7
                + get(recent, "yardsPerPassAttempt") * 5
            )
        elif group == "RB":
            recent_prod = (
                get(recent, "rushingYards") / 12
                + get(recent, "rushingTouchdowns") * 8
                + get(recent, "receivingYards") / 24
                + get(recent, "receptions") * 0.8
            )
            career_prod = (
                get(career, "rushingYards") / 65
                + get(career, "rushingTouchdowns") * 4
                + get(career, "receivingYards") / 120
                + get(career, "receptions") * 0.35
            )
            efficiency = get(recent, "yardsPerRushAttempt") * 18 + get(recent, "yardsPerReception") * 4
        elif group == "REC":
            recent_prod = (
                get(recent, "receivingYards") / 12
                + get(recent, "receivingTouchdowns") * 9
                + get(recent, "receptions") * 1.0
            )
            career_prod = (
                get(career, "receivingYards") / 60
                + get(career, "receivingTouchdowns") * 4.5
                + get(career, "receptions") * 0.4
            )
            efficiency = get(recent, "yardsPerReception") * 6 + get(recent, "catchPct", "catchPercentage")
        elif group == "ST":
            recent_prod = get(recent, "fieldGoalsMade") * 4 + get(recent, "extraPointsMade") + get(recent, "puntingYards") / 100
            career_prod = get(career, "fieldGoalsMade") * 1.6 + get(career, "extraPointsMade") * 0.4 + get(career, "puntingYards") / 500
            efficiency = get(recent, "fieldGoalPct", "fieldGoalPercentage") + get(recent, "grossAveragePuntYards")
        elif group == "OL":
            recent_prod = get(recent, "gamesStarted") * 5 + get(recent, "gamesPlayed") * 2
            career_prod = get(career, "gamesStarted") * 1.4 + get(career, "gamesPlayed") * 0.5
            efficiency = max(0.0, 120 - get(recent, "sacksAllowed") * 8 - get(recent, "penalties") * 3)
        else:
            recent_prod = (
                get(recent, "totalTackles", "tackles") * 0.8
                + get(recent, "sacks") * 9
                + get(recent, "interceptions") * 11
                + get(recent, "forcedFumbles") * 9
                + get(recent, "passesDefended") * 2
            )
            career_prod = (
                get(career, "totalTackles", "tackles") * 0.25
                + get(career, "sacks") * 4
                + get(career, "interceptions") * 5
                + get(career, "forcedFumbles") * 4
            )
            efficiency = get(recent, "sacks") * 8 + get(recent, "interceptions") * 8 + get(recent, "tacklesForLoss") * 3
    elif league in {"NBA", "WNBA"}:
        recent_prod = (
            get(recent, "avgPoints", "pointsPerGame", "points") * 3
            + get(recent, "avgRebounds", "reboundsPerGame", "rebounds") * 2
            + get(recent, "avgAssists", "assistsPerGame", "assists") * 2.4
            + get(recent, "avgSteals", "stealsPerGame", "steals") * 5
            + get(recent, "avgBlocks", "blocksPerGame", "blocks") * 5
            - get(recent, "avgTurnovers", "turnoversPerGame", "turnovers") * 1.7
        )
        career_prod = (
            get(career, "points") / 80
            + get(career, "rebounds") / 55
            + get(career, "assists") / 45
            + get(career, "steals") / 20
            + get(career, "blocks") / 20
        )
        efficiency = (
            get(recent, "fieldGoalPct", "fieldGoalPercentage")
            + get(recent, "threePointFieldGoalPct", "threePointPercentage") * 0.5
            + get(recent, "freeThrowPct", "freeThrowPercentage") * 0.25
            + get(recent, "playerEfficiencyRating") * 2
        )
    elif league == "MLB":
        if group == "PITCHER":
            recent_prod = (
                get(recent, "strikeouts") * 1.1
                + get(recent, "wins") * 10
                + get(recent, "saves") * 7
                + get(recent, "inningsPitched") * 0.7
                - get(recent, "earnedRunAverage", "era") * 10
            )
            career_prod = (
                get(career, "strikeouts") * 0.24
                + get(career, "wins") * 4
                + get(career, "saves") * 2.5
                + get(career, "inningsPitched") * 0.12
            )
            efficiency = 200 - get(recent, "earnedRunAverage", "era") * 25 - get(recent, "whip") * 35 + get(recent, "strikeoutsPerNineInnings") * 8
        else:
            recent_prod = (
                get(recent, "homeRuns") * 12
                + get(recent, "runsBattedIn", "rbi") * 2.5
                + get(recent, "hits") * 0.8
                + get(recent, "stolenBases") * 3
                + get(recent, "runs") * 1.2
            )
            career_prod = (
                get(career, "homeRuns") * 4
                + get(career, "runsBattedIn", "rbi") * 0.8
                + get(career, "hits") * 0.22
                + get(career, "stolenBases") * 1.2
            )
            efficiency = get(recent, "ops") * 100 + get(recent, "battingAverage", "avg") * 100 + get(recent, "onBasePct", "obp") * 70
    elif league == "NHL":
        if group == "GOALIE":
            recent_prod = get(recent, "wins") * 6 + get(recent, "shutouts") * 12 + get(recent, "saves") * 0.12
            career_prod = get(career, "wins") * 2.2 + get(career, "shutouts") * 5 + get(career, "saves") * 0.025
            efficiency = get(recent, "savePct", "savePercentage") * 200 - get(recent, "goalsAgainstAverage") * 18
        else:
            recent_prod = (
                get(recent, "goals") * 9
                + get(recent, "assists") * 5.5
                + get(recent, "points") * 3
                + get(recent, "plusMinus") * 1.2
                + get(recent, "shots") * 0.18
            )
            career_prod = get(career, "goals") * 3.5 + get(career, "assists") * 2.2 + get(career, "points") * 1.1
            efficiency = get(recent, "shootingPct", "shootingPercentage") * 5 + get(recent, "plusMinus")
    elif record.get("discipline") == "Soccer":
        recent_prod = (
            get(recent, "goals") * 12
            + get(recent, "assists") * 9
            + get(recent, "appearances", "gamesPlayed") * 1.5
            + get(recent, "shotsOnTarget") * 0.6
            + get(recent, "cleanSheets") * 6
            + get(recent, "saves") * 0.3
        )
        career_prod = (
            get(career, "goals") * 5
            + get(career, "assists") * 3.5
            + get(career, "appearances", "gamesPlayed") * 0.6
            + get(career, "cleanSheets") * 2.5
        )
        efficiency = get(recent, "shotConversionPct") + get(recent, "passCompletionPct") * 0.6 + get(recent, "savePct")
    else:
        # Generic fallback: positive counting stats create signal, percentages create efficiency.
        recent_prod = sum(max(0.0, value) for key, value in recent.items() if not any(token in key for token in ("pct", "percentage", "average", "rating")))
        career_prod = sum(max(0.0, value) for key, value in career.items() if not any(token in key for token in ("pct", "percentage", "average", "rating")))
        efficiency = sum(value for key, value in recent.items() if any(token in key for token in ("pct", "percentage", "average", "rating")))

    usage = (
        get(recent, "gamesStarted", "starts") * 3
        + get(recent, "gamesPlayed", "games", "appearances")
        + get(recent, "minutes", "minutesPlayed") / 50
    )
    career_usage = (
        get(career, "gamesStarted", "starts") * 1.4
        + get(career, "gamesPlayed", "games", "appearances") * 0.5
        + get(career, "minutes", "minutesPlayed") / 220
    )
    return {
        "recentProduction": max(0.0, recent_prod),
        "careerProduction": max(0.0, career_prod),
        "efficiency": efficiency,
        "usage": max(0.0, usage),
        "careerUsage": max(0.0, career_usage),
        "awardPoints": max(0.0, awards),
    }


def percentile(value: float, values: list[float]) -> float:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return 0.5
    if len(clean) == 1:
        return 0.5
    below = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    return clamp((below + 0.5 * equal) / len(clean), 0, 1)


def potential_prior(record: dict[str, Any], recent_pct: float) -> float:
    age = number(record.get("age"))
    exp = number(record.get("experienceYears"))
    league = str(record.get("leagueOrMedium") or "")
    peak = 27.0
    if league == "NFL":
        group = role_group(record)
        peak = 29 if group in {"QB", "OL"} else 26
    elif league in {"NBA", "WNBA"}:
        peak = 27
    elif league == "MLB":
        peak = 28
    elif league == "NHL":
        peak = 27
    elif record.get("discipline") == "Soccer":
        peak = 26

    if age is not None:
        if age <= peak - 5:
            age_component = 92
        elif age <= peak:
            age_component = 92 - (age - (peak - 5)) * 4.2
        else:
            age_component = 71 - (age - peak) * 5.2
    elif exp is not None:
        age_component = 88 - exp * 5.2
    else:
        age_component = 58

    draft_bonus = 0.0
    round_value = number(record.get("draftRound"))
    pick_value = number(record.get("draftPick"))
    if round_value is not None:
        draft_bonus += max(0.0, 12 - (round_value - 1) * 2.2)
    if pick_value is not None and pick_value <= 10:
        draft_bonus += 5

    return clamp(age_component * 0.70 + (28 + recent_pct * 68) * 0.30 + draft_bonus, 18, 97)


def enrich_one(record: dict[str, Any], timeout: float) -> dict[str, Any]:
    result = dict(record)
    source_namespace = str(result.get("sourceNamespace") or "")
    athlete_id = str(result.get("sourceRecordId") or "").strip()
    if not athlete_id:
        return {"record": result, "ok": False, "reason": "missing athlete id"}

    recent: dict[str, float] = {}
    career: dict[str, float] = {}
    awards_score = 0.0
    awards_names: list[str] = []
    evidence_urls: list[str] = []
    if result.get("draftMetadataSource"):
        evidence_urls.append(str(result["draftMetadataSource"]))
    errors: list[str] = []
    news_count = 0

    if source_namespace == "espn":
        sport = SPORT_PATH.get(str(result.get("discipline") or ""))
        league = str(result.get("sourceLeagueSlug") or "").strip()
        if not sport or not league:
            return {"record": result, "ok": False, "reason": "unsupported ESPN sport/league"}
        overview_url = ESPN_OVERVIEW.format(sport=sport, league=league, athlete_id=athlete_id)
        profile_url = ESPN_ATHLETE_PROFILE.format(sport=sport, league=league, athlete_id=athlete_id)
        awards_url = ESPN_AWARDS.format(sport=sport, league=league, athlete_id=athlete_id)
        try:
            overview = fetch_json(overview_url, timeout)
            recent, career = extract_stat_maps(overview)
            merge_profile_evidence(result, extract_profile_evidence(overview))
            news = overview.get("news")
            news_count = len(news) if isinstance(news, list) else 0
            evidence_urls.append(overview_url)
        except Exception as exc:  # noqa: BLE001 - source failures are recorded, not fatal per record
            errors.append(f"overview {type(exc).__name__}")
        # Roster endpoints often omit draft fields. Only rookie/early-career
        # records need the extra identity request used to establish an IPO anchor.
        experience = positive_int(result.get("experienceYears"))
        if experience is None or experience <= 1 or result.get("draftPick") is not None:
            try:
                profile_payload = fetch_json(profile_url, timeout)
                merge_profile_evidence(result, extract_profile_evidence(profile_payload))
                evidence_urls.append(profile_url)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"profile {type(exc).__name__}")
            if result.get("draftPick") is None:
                core_url = ESPN_CORE_ATHLETE.format(sport=sport, league=league, athlete_id=athlete_id)
                try:
                    core_payload = fetch_json(core_url, timeout)
                    merge_profile_evidence(result, extract_profile_evidence(core_payload))
                    evidence_urls.append(core_url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"core profile {type(exc).__name__}")
        try:
            awards_payload = fetch_json(awards_url, timeout)
            awards_score, awards_names = award_points(awards_payload)
            evidence_urls.append(awards_url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"awards {type(exc).__name__}")
    elif source_namespace == "nhl":
        url = NHL_LANDING.format(athlete_id=athlete_id)
        try:
            landing = fetch_json(url, timeout)
            merge_profile_evidence(result, extract_profile_evidence(landing))
            flattened: dict[str, float] = {}
            recursively_collect_numbers(landing, flattened)
            # NHL payloads expose seasonTotals/careerTotals under nested keys.
            recent = {key: value for key, value in flattened.items() if "seasontotals" in key or "featuredstats" in key}
            career = {key: value for key, value in flattened.items() if "careertotals" in key}
            awards_payload = landing.get("awards") if isinstance(landing.get("awards"), dict) else {}
            awards_score, awards_names = award_points(awards_payload)
            evidence_urls.append(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"nhl landing {type(exc).__name__}")
    else:
        return {"record": result, "ok": False, "reason": "not an automated roster record"}

    has_draft_evidence = positive_int(result.get("draftPick")) is not None
    if not recent and not career and awards_score <= 0 and not has_draft_evidence:
        return {"record": result, "ok": False, "reason": "; ".join(errors) or "no usable evidence"}

    games = professional_games_from_stats(recent, career)
    result["professionalGames"] = games
    signals = signal_bundle(result, recent, career, awards_score)
    return {
        "record": result,
        "ok": True,
        "hasProfessionalEvidence": bool(recent or career or awards_score > 0),
        "hasDraftEvidence": has_draft_evidence,
        "professionalGames": games,
        "recent": recent,
        "career": career,
        "signals": signals,
        "awards": awards_names,
        "newsCount": news_count,
        "evidenceUrls": evidence_urls,
        "errors": errors,
    }


def cohort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("leagueOrMedium") or "Unknown"), role_group(record))


def apply_ranked_metrics(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cohorts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    leagues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        if not item.get("ok") or not item.get("hasProfessionalEvidence"):
            continue
        record = item["record"]
        cohorts[cohort_key(record)].append(item)
        leagues[str(record.get("leagueOrMedium") or "Unknown")].append(item)

    def pool(item: dict[str, Any]) -> list[dict[str, Any]]:
        record = item["record"]
        cohort = cohorts[cohort_key(record)]
        if len(cohort) >= 8:
            return cohort
        league_pool = leagues[str(record.get("leagueOrMedium") or "Unknown")]
        return league_pool if league_pool else cohort

    enriched: list[dict[str, Any]] = []
    for item in results:
        record = dict(item["record"])
        if not item.get("ok"):
            record["pricingDataStatus"] = "Provisional — roster, experience and role evidence only"
            record["pricingEnrichmentError"] = item.get("reason")
            enriched.append(record)
            continue

        record["professionalGames"] = int(item.get("professionalGames") or 0)
        if item.get("hasDraftEvidence") and not item.get("hasProfessionalEvidence"):
            # Drafted players with no professional sample receive a true Rookie
            # IPO anchor. The active metrics remain conservative until games exist.
            existing_metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
            record["activeMetrics"] = existing_metrics or {
                "performance": 36.0,
                "achievements": 14.0,
                "potential": 92.0,
                "audience": 48.0,
                "availability": 82.0,
                "consistency": 38.0,
            }
            record["pricingDataStatus"] = "Rookie IPO — verified draft position; awaiting professional statistics"
            record["pricingConfidence"] = 0.82
            record["pricingEvidence"] = item.get("evidenceUrls", [])
            record["pricingEvidenceSummary"] = {
                "cohort": f"{cohort_key(record)[0]} · {cohort_key(record)[1]}",
                "recentStatFields": 0,
                "careerStatFields": 0,
                "awardNames": item.get("awards", []),
                "draftYear": record.get("draftYear"),
                "draftRound": record.get("draftRound"),
                "draftPick": record.get("draftPick"),
                "professionalGames": 0,
                "percentiles": {},
                "rawSignals": {key: round(value, 4) for key, value in item["signals"].items()},
            }
            if item.get("errors"):
                record["pricingEvidenceWarnings"] = item["errors"]
            enriched.append(record)
            continue

        peers = pool(item)
        signals = item["signals"]
        pcts: dict[str, float] = {}
        for key, value in signals.items():
            pcts[key] = percentile(value, [peer["signals"].get(key, 0.0) for peer in peers])

        recent_pct = pcts["recentProduction"]
        career_pct = max(pcts["careerProduction"], pcts["careerUsage"] * 0.82)
        efficiency_pct = pcts["efficiency"]
        usage_pct = pcts["usage"]
        award_pct = pcts["awardPoints"]

        performance = clamp(24 + 72 * (recent_pct * 0.56 + efficiency_pct * 0.24 + usage_pct * 0.20), 20, 98)
        achievements = clamp(8 + 88 * (career_pct * 0.63 + award_pct * 0.27 + pcts["careerUsage"] * 0.10), 8, 99)
        potential = potential_prior(record, recent_pct)

        base_audience = number((record.get("activeMetrics") or {}).get("audience")) or 40.0
        news_boost = min(12.0, math.log1p(item.get("newsCount", 0)) * 4.0)
        audience = clamp(base_audience * 0.68 + recent_pct * 18 + award_pct * 10 + news_boost, 20, 97)

        games_value = max(signals.get("usage", 0.0), 0.0)
        if games_value > 0:
            availability = clamp(52 + usage_pct * 42, 48, 96)
        else:
            availability = 72 if record.get("careerStatus") == "Active" else 55

        consistency = clamp(30 + career_pct * 35 + recent_pct * 25 + availability * 0.10, 28, 96)
        completeness = sum(
            1
            for condition in (
                bool(item.get("recent")),
                bool(item.get("career")),
                signals.get("usage", 0) > 0,
                signals.get("awardPoints", 0) > 0,
                number(record.get("age")) is not None,
            )
            if condition
        )
        confidence = clamp(0.62 + completeness * 0.055, 0.62, 0.90)

        record["activeMetrics"] = {
            "performance": round(performance, 1),
            "achievements": round(achievements, 1),
            "potential": round(potential, 1),
            "audience": round(audience, 1),
            "availability": round(availability, 1),
            "consistency": round(consistency, 1),
        }
        status_parts = ["Evidence enriched"]
        if item.get("recent"):
            status_parts.append("recent statistics")
        if item.get("career"):
            status_parts.append("career statistics")
        if item.get("awards") or signals.get("awardPoints", 0) > 0:
            status_parts.append("awards")
        record["pricingDataStatus"] = " — ".join([status_parts[0], ", ".join(status_parts[1:])])
        record["pricingConfidence"] = round(confidence, 2)
        record["pricingEvidence"] = item.get("evidenceUrls", [])
        record["pricingEvidenceSummary"] = {
            "cohort": f"{cohort_key(record)[0]} · {cohort_key(record)[1]}",
            "recentStatFields": len(item.get("recent", {})),
            "careerStatFields": len(item.get("career", {})),
            "awardNames": item.get("awards", []),
            "draftYear": record.get("draftYear"),
            "draftRound": record.get("draftRound"),
            "draftPick": record.get("draftPick"),
            "professionalGames": record.get("professionalGames", 0),
            "percentiles": {key: round(value, 4) for key, value in pcts.items()},
            "rawSignals": {key: round(value, 4) for key, value in signals.items()},
        }
        if item.get("errors"):
            record["pricingEvidenceWarnings"] = item["errors"]
        enriched.append(record)
    return enriched


def rewrite_csv(records: list[dict[str, Any]]) -> None:
    fields = [
        "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
        "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
        "careerStage", "lastVerifiedAt", "verificationStatus", "sourceName",
        "sourceUrl", "sourceRecordId", "dataConfidence", "pricingConfidence",
        "pricingDataStatus", "pricingModelVersion", "marketPrice", "careerScore",
        "fundamentalValue", "draftYear", "draftRound", "draftPick",
        "professionalGames",
    ]
    with CATALOG_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--minimum-enriched", type=int, default=2500)
    parser.add_argument("--max-records", type=int, default=0, help="For testing only; 0 enriches all automated records")
    args = parser.parse_args()

    if not CATALOG.exists():
        raise SystemExit("data/current_catalog.json does not exist; run build_current_catalog.py first")
    records = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise SystemExit("current_catalog.json must be an array")
    draft_metadata = load_draft_metadata(DRAFT_METADATA_OVERRIDES)
    records = [apply_draft_metadata(record, draft_metadata) for record in records]

    automated_indexes = [
        index for index, record in enumerate(records)
        if record.get("sourceNamespace") in {"espn", "nhl"} and record.get("sourceRecordId")
    ]
    if args.max_records > 0:
        automated_indexes = automated_indexes[: args.max_records]

    started = time.time()
    results_by_index: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(enrich_one, records[index], args.request_timeout): index
            for index in automated_indexes
        }
        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            try:
                results_by_index[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                results_by_index[index] = {"record": records[index], "ok": False, "reason": f"worker {type(exc).__name__}: {exc}"}
            completed += 1
            if completed % 250 == 0 or completed == len(futures):
                successes = sum(1 for item in results_by_index.values() if item.get("ok"))
                print(f"Evidence requests: {completed:,}/{len(futures):,}; usable: {successes:,}", flush=True)

    ordered_results: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        ordered_results.append(results_by_index.get(index, {"record": record, "ok": False, "reason": "not selected for automated enrichment"}))

    ranked = apply_ranked_metrics(ordered_results)
    overrides = load_overrides(PRICING_OVERRIDES)
    repriced = apply_pricing_to_records(ranked, overrides)

    enriched_count = sum(1 for record in repriced if str(record.get("pricingDataStatus", "")).startswith("Evidence enriched"))
    provisional_count = sum(1 for record in repriced if str(record.get("pricingDataStatus", "")).startswith("Provisional"))
    rookie_count = sum(1 for record in repriced if isinstance(record.get("rookiePricing"), dict))
    if enriched_count < args.minimum_enriched:
        raise SystemExit(
            f"Only {enriched_count:,} records received usable evidence; minimum is {args.minimum_enriched:,}. "
            "The site was not redeployed with insufficient pricing evidence."
        )

    CATALOG.write_text(json.dumps(repriced, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    rewrite_csv(repriced)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    league_counts = Counter(
        record.get("leagueOrMedium", "Unknown")
        for record in repriced
        if str(record.get("pricingDataStatus", "")).startswith("Evidence enriched")
    )
    error_counts = Counter(
        str(record.get("pricingEnrichmentError") or "")
        for record in repriced
        if record.get("pricingEnrichmentError")
    )
    manifest = {
        "version": "3.4-rookie-transition-inputs",
        "generatedAt": generated_at,
        "elapsedSeconds": round(time.time() - started, 1),
        "catalogRecords": len(repriced),
        "automatedRecordsAttempted": len(automated_indexes),
        "pricingEnrichedRecords": enriched_count,
        "pricingProvisionalRecords": provisional_count,
        "rookieTransitionRecords": rookie_count,
        "minimumRequired": args.minimum_enriched,
        "enrichedByLeague": dict(league_counts.most_common()),
        "topFailureReasons": dict(error_counts.most_common(12)),
        "formula": {
            "performance": 0.30,
            "achievements": 0.25,
            "potential": 0.20,
            "audience": 0.15,
            "availability": 0.10,
        },
        "method": "Recent and career statistics plus awards are converted to position-and-league cohort percentiles before the TalentX formula is applied.",
    }
    ENRICHMENT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if CATALOG_MANIFEST.exists():
        existing = json.loads(CATALOG_MANIFEST.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
        existing.update(
            {
                "version": "3.4-current-10000-rookie-transition",
                "pricingEvidenceGeneratedAt": generated_at,
                "pricingEnrichedRecords": enriched_count,
                "pricingProvisionalRecords": provisional_count,
                "rookieTransitionRecords": rookie_count,
                "pricingInputMethod": "Position-and-league normalized statistics and awards plus draft-based rookie IPO transitions",
                "pricingEnrichmentManifest": "data/pricing_enrichment_manifest.json",
            }
        )
        CATALOG_MANIFEST.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    scores = [float(record.get("careerScore", 0)) for record in repriced]
    prices = [float(record.get("marketPrice", 0)) for record in repriced]
    print(f"Evidence-enriched records: {enriched_count:,}")
    print(f"Provisional records: {provisional_count:,}")
    print(f"Rookie transition records: {rookie_count:,}")
    if scores:
        print(f"Career-score range: {min(scores):.1f}–{max(scores):.1f}; median {statistics.median(scores):.1f}")
    if prices:
        print(f"Price range: ${min(prices):.2f}–${max(prices):.2f}; median ${statistics.median(prices):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
