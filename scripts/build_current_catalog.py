#!/usr/bin/env python3
"""Build TalentX's point-in-time current-athlete catalog.

This script is designed to run in GitHub Actions, where outbound network access is
available. It collects names only from current team-roster endpoints (plus the
official NHL current-roster endpoint), attaches source/timestamp metadata, then
creates deterministic *simulated* TalentX market fields.

The roster status is a point-in-time automated check, not a permanent guarantee.
All prices, scores, charts, volumes and changes are simulated.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pricing_model import apply_pricing_to_records, load_overrides, provisional_active_metrics

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SEED_FILE = DATA_DIR / "current_seed.json"
OUTPUT_FILE = DATA_DIR / "current_catalog.json"
CSV_FILE = DATA_DIR / "current_catalog.csv"
MANIFEST_FILE = DATA_DIR / "catalog_manifest.json"
SOURCE_MANIFEST_FILE = DATA_DIR / "current_source_manifest.json"
PRICING_OVERRIDES_FILE = DATA_DIR / "pricing_overrides.json"

USER_AGENT = "TalentX-Catalog-Builder/3.0 (+https://github.com/rossad213/TalentX)"
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports"
NHL_API = "https://api-web.nhle.com/v1"

# Ordered from most reliable/high-value coverage to broader international coverage.
# Invalid/unavailable league slugs are harmless: they are recorded as source errors
# and the builder continues.
ESPN_LEAGUES: list[dict[str, str]] = [
    {"sport": "football", "league": "nfl", "discipline": "American Football", "label": "NFL"},
    {"sport": "basketball", "league": "nba", "discipline": "Basketball", "label": "NBA"},
    {"sport": "basketball", "league": "wnba", "discipline": "Basketball", "label": "WNBA"},
    {"sport": "baseball", "league": "mlb", "discipline": "Baseball", "label": "MLB"},
    # Major men's soccer leagues and lower divisions.
    {"sport": "soccer", "league": "eng.1", "discipline": "Soccer", "label": "Premier League"},
    {"sport": "soccer", "league": "eng.2", "discipline": "Soccer", "label": "English Championship"},
    {"sport": "soccer", "league": "eng.3", "discipline": "Soccer", "label": "English League One"},
    {"sport": "soccer", "league": "eng.4", "discipline": "Soccer", "label": "English League Two"},
    {"sport": "soccer", "league": "esp.1", "discipline": "Soccer", "label": "LaLiga"},
    {"sport": "soccer", "league": "esp.2", "discipline": "Soccer", "label": "LaLiga 2"},
    {"sport": "soccer", "league": "ger.1", "discipline": "Soccer", "label": "Bundesliga"},
    {"sport": "soccer", "league": "ger.2", "discipline": "Soccer", "label": "2. Bundesliga"},
    {"sport": "soccer", "league": "ita.1", "discipline": "Soccer", "label": "Serie A"},
    {"sport": "soccer", "league": "ita.2", "discipline": "Soccer", "label": "Serie B"},
    {"sport": "soccer", "league": "fra.1", "discipline": "Soccer", "label": "Ligue 1"},
    {"sport": "soccer", "league": "fra.2", "discipline": "Soccer", "label": "Ligue 2"},
    {"sport": "soccer", "league": "ned.1", "discipline": "Soccer", "label": "Eredivisie"},
    {"sport": "soccer", "league": "por.1", "discipline": "Soccer", "label": "Portuguese Primeira Liga"},
    {"sport": "soccer", "league": "bel.1", "discipline": "Soccer", "label": "Belgian Pro League"},
    {"sport": "soccer", "league": "sco.1", "discipline": "Soccer", "label": "Scottish Premiership"},
    {"sport": "soccer", "league": "tur.1", "discipline": "Soccer", "label": "Turkish Super Lig"},
    {"sport": "soccer", "league": "gre.1", "discipline": "Soccer", "label": "Greek Super League"},
    {"sport": "soccer", "league": "aut.1", "discipline": "Soccer", "label": "Austrian Bundesliga"},
    {"sport": "soccer", "league": "sui.1", "discipline": "Soccer", "label": "Swiss Super League"},
    {"sport": "soccer", "league": "den.1", "discipline": "Soccer", "label": "Danish Superliga"},
    {"sport": "soccer", "league": "nor.1", "discipline": "Soccer", "label": "Norwegian Eliteserien"},
    {"sport": "soccer", "league": "swe.1", "discipline": "Soccer", "label": "Swedish Allsvenskan"},
    {"sport": "soccer", "league": "fin.1", "discipline": "Soccer", "label": "Finnish Veikkausliiga"},
    {"sport": "soccer", "league": "irl.1", "discipline": "Soccer", "label": "League of Ireland Premier"},
    {"sport": "soccer", "league": "nirl.1", "discipline": "Soccer", "label": "NIFL Premiership"},
    {"sport": "soccer", "league": "cze.1", "discipline": "Soccer", "label": "Czech First League"},
    {"sport": "soccer", "league": "pol.1", "discipline": "Soccer", "label": "Polish Ekstraklasa"},
    {"sport": "soccer", "league": "rou.1", "discipline": "Soccer", "label": "Romanian Liga 1"},
    {"sport": "soccer", "league": "hun.1", "discipline": "Soccer", "label": "Hungarian NB I"},
    {"sport": "soccer", "league": "cro.1", "discipline": "Soccer", "label": "Croatian HNL"},
    {"sport": "soccer", "league": "srb.1", "discipline": "Soccer", "label": "Serbian SuperLiga"},
    {"sport": "soccer", "league": "svn.1", "discipline": "Soccer", "label": "Slovenian PrvaLiga"},
    {"sport": "soccer", "league": "svk.1", "discipline": "Soccer", "label": "Slovak Super Liga"},
    {"sport": "soccer", "league": "bul.1", "discipline": "Soccer", "label": "Bulgarian First League"},
    {"sport": "soccer", "league": "ukr.1", "discipline": "Soccer", "label": "Ukrainian Premier League"},
    {"sport": "soccer", "league": "isr.1", "discipline": "Soccer", "label": "Israeli Premier League"},
    {"sport": "soccer", "league": "cyp.1", "discipline": "Soccer", "label": "Cypriot First Division"},
    {"sport": "soccer", "league": "isl.1", "discipline": "Soccer", "label": "Icelandic Besta deild"},
    # North/Central/South America.
    {"sport": "soccer", "league": "usa.1", "discipline": "Soccer", "label": "Major League Soccer"},
    {"sport": "soccer", "league": "usa.2", "discipline": "Soccer", "label": "USL Championship"},
    {"sport": "soccer", "league": "mex.1", "discipline": "Soccer", "label": "Liga MX"},
    {"sport": "soccer", "league": "mex.2", "discipline": "Soccer", "label": "Liga de Expansion MX"},
    {"sport": "soccer", "league": "bra.1", "discipline": "Soccer", "label": "Brazil Serie A"},
    {"sport": "soccer", "league": "bra.2", "discipline": "Soccer", "label": "Brazil Serie B"},
    {"sport": "soccer", "league": "arg.1", "discipline": "Soccer", "label": "Argentine Liga Profesional"},
    {"sport": "soccer", "league": "arg.2", "discipline": "Soccer", "label": "Argentina Primera Nacional"},
    {"sport": "soccer", "league": "col.1", "discipline": "Soccer", "label": "Colombian Primera A"},
    {"sport": "soccer", "league": "ecu.1", "discipline": "Soccer", "label": "Ecuador LigaPro"},
    {"sport": "soccer", "league": "per.1", "discipline": "Soccer", "label": "Peru Liga 1"},
    {"sport": "soccer", "league": "chi.1", "discipline": "Soccer", "label": "Chilean Primera Division"},
    {"sport": "soccer", "league": "uru.1", "discipline": "Soccer", "label": "Uruguayan Primera Division"},
    {"sport": "soccer", "league": "par.1", "discipline": "Soccer", "label": "Paraguayan Primera Division"},
    {"sport": "soccer", "league": "bol.1", "discipline": "Soccer", "label": "Bolivian Primera Division"},
    {"sport": "soccer", "league": "ven.1", "discipline": "Soccer", "label": "Venezuelan Primera Division"},
    {"sport": "soccer", "league": "crc.1", "discipline": "Soccer", "label": "Costa Rican Primera Division"},
    {"sport": "soccer", "league": "hon.1", "discipline": "Soccer", "label": "Honduran Liga Nacional"},
    {"sport": "soccer", "league": "gua.1", "discipline": "Soccer", "label": "Guatemalan Liga Nacional"},
    # Asia / Oceania / Middle East / Africa.
    {"sport": "soccer", "league": "jpn.1", "discipline": "Soccer", "label": "J1 League"},
    {"sport": "soccer", "league": "jpn.2", "discipline": "Soccer", "label": "J2 League"},
    {"sport": "soccer", "league": "kor.1", "discipline": "Soccer", "label": "K League 1"},
    {"sport": "soccer", "league": "chn.1", "discipline": "Soccer", "label": "Chinese Super League"},
    {"sport": "soccer", "league": "aus.1", "discipline": "Soccer", "label": "A-League Men"},
    {"sport": "soccer", "league": "ind.1", "discipline": "Soccer", "label": "Indian Super League"},
    {"sport": "soccer", "league": "idn.1", "discipline": "Soccer", "label": "Indonesia Liga 1"},
    {"sport": "soccer", "league": "tha.1", "discipline": "Soccer", "label": "Thai League 1"},
    {"sport": "soccer", "league": "vie.1", "discipline": "Soccer", "label": "Vietnam V.League 1"},
    {"sport": "soccer", "league": "mas.1", "discipline": "Soccer", "label": "Malaysia Super League"},
    {"sport": "soccer", "league": "ksa.1", "discipline": "Soccer", "label": "Saudi Pro League"},
    {"sport": "soccer", "league": "qat.1", "discipline": "Soccer", "label": "Qatar Stars League"},
    {"sport": "soccer", "league": "uae.1", "discipline": "Soccer", "label": "UAE Pro League"},
    {"sport": "soccer", "league": "irn.1", "discipline": "Soccer", "label": "Iran Persian Gulf Pro League"},
    {"sport": "soccer", "league": "rsa.1", "discipline": "Soccer", "label": "South African Premiership"},
    {"sport": "soccer", "league": "egy.1", "discipline": "Soccer", "label": "Egyptian Premier League"},
    {"sport": "soccer", "league": "mar.1", "discipline": "Soccer", "label": "Moroccan Botola Pro"},
    {"sport": "soccer", "league": "alg.1", "discipline": "Soccer", "label": "Algerian Ligue 1"},
    {"sport": "soccer", "league": "tun.1", "discipline": "Soccer", "label": "Tunisian Ligue 1"},
    # Women's soccer.
    {"sport": "soccer", "league": "usa.nwsl", "discipline": "Soccer", "label": "NWSL"},
    {"sport": "soccer", "league": "eng.w.1", "discipline": "Soccer", "label": "Women's Super League"},
    {"sport": "soccer", "league": "esp.w.1", "discipline": "Soccer", "label": "Liga F"},
    {"sport": "soccer", "league": "fra.w.1", "discipline": "Soccer", "label": "Premiere Ligue Women"},
    {"sport": "soccer", "league": "ger.w.1", "discipline": "Soccer", "label": "Frauen-Bundesliga"},
    {"sport": "soccer", "league": "ita.w.1", "discipline": "Soccer", "label": "Serie A Women"},
    {"sport": "soccer", "league": "aus.w.1", "discipline": "Soccer", "label": "A-League Women"},
]


@dataclass(frozen=True)
class SourceResult:
    name: str
    records: list[dict[str, Any]]
    error: str | None = None
    requests: int = 0


def make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.45,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=30, pool_maxsize=30)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def fetch_json(session: requests.Session, url: str, timeout: int = 25) -> dict[str, Any]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"Expected object from {url}")
    return data


def clean_text(value: Any, fallback: str = "") -> str:
    if isinstance(value, dict):
        value = value.get("default") or value.get("displayName") or value.get("name") or ""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if not parts:
        return "TX"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else parts[0][1:2])).upper()


def country_from(item: dict[str, Any]) -> str:
    candidates: list[Any] = [
        item.get("citizenship"),
        item.get("nationality"),
        item.get("country"),
        (item.get("birthPlace") or {}).get("country") if isinstance(item.get("birthPlace"), dict) else None,
        item.get("birthCountry"),
    ]
    for value in candidates:
        text = clean_text(value)
        if text:
            return text
    return "Not listed"


def experience_years(item: dict[str, Any]) -> int | None:
    exp = item.get("experience")
    if isinstance(exp, dict):
        for key in ("years", "value"):
            if exp.get(key) is not None:
                try:
                    return int(exp[key])
                except (TypeError, ValueError):
                    pass
    for key in ("experienceYears", "yearsPro"):
        if item.get(key) is not None:
            try:
                return int(item[key])
            except (TypeError, ValueError):
                pass
    return None


def athlete_age(item: dict[str, Any]) -> int | None:
    value = item.get("age")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    dob = item.get("dateOfBirth") or item.get("birthDate")
    if isinstance(dob, str) and len(dob) >= 10:
        try:
            born = datetime.fromisoformat(dob[:10]).date()
            today = datetime.now(timezone.utc).date()
            return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except ValueError:
            pass
    return None


def draft_evidence(item: dict[str, Any]) -> dict[str, Any]:
    draft = item.get("draft")
    if not isinstance(draft, dict):
        return {}
    output: dict[str, Any] = {}
    mapping = {"year": "draftYear", "round": "draftRound", "selection": "draftPick", "overall": "draftPick"}
    for source, target in mapping.items():
        if draft.get(source) is not None:
            try:
                output[target] = int(draft[source])
            except (TypeError, ValueError):
                output[target] = draft[source]
    return output


def career_stage(exp: int | None) -> str:
    if exp is None:
        return "Stage Under Review"
    if exp <= 0:
        return "Active Rookie"
    if exp <= 3:
        return "Early Career"
    if exp <= 8:
        return "Established"
    return "Veteran"


def position_from(item: dict[str, Any], inherited: str = "") -> str:
    pos = item.get("position")
    if isinstance(pos, dict):
        return clean_text(pos.get("displayName") or pos.get("name") or pos.get("abbreviation"), inherited or "Player")
    return clean_text(pos, inherited or "Player")


def extract_espn_roster(data: dict[str, Any], *, cfg: dict[str, str], team: dict[str, Any], source_url: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    groups = data.get("athletes") or data.get("roster") or []
    if isinstance(groups, dict):
        groups = groups.get("items") or groups.get("athletes") or [groups]
    if not isinstance(groups, list):
        return output

    def add(item: dict[str, Any], inherited_position: str = "") -> None:
        name = clean_text(item.get("fullName") or item.get("displayName") or item.get("name"))
        athlete_id = clean_text(item.get("id") or item.get("uid") or item.get("guid"))
        # Coaches/front-office entries sometimes appear in adjacent payload sections.
        if len(name.split()) < 2 or not athlete_id:
            return
        active = item.get("active")
        if active is False:
            return
        output.append(
            {
                "sourceNamespace": "espn",
                "sourceRecordId": athlete_id,
                "name": name,
                "discipline": cfg["discipline"],
                "leagueOrMedium": cfg["label"],
                "teamOrPlatform": clean_text(team.get("displayName") or team.get("name"), "Team not listed"),
                "role": position_from(item, inherited_position),
                "country": country_from(item),
                "experienceYears": experience_years(item),
                "age": athlete_age(item),
                "starter": bool(item.get("starter") or item.get("isStarter")),
                **draft_evidence(item),
                "sourceName": "ESPN current team roster endpoint",
                "sourceUrl": source_url,
                "sourceLeagueSlug": cfg["league"],
                "statusConfidence": 0.94,
            }
        )

    for group in groups:
        if not isinstance(group, dict):
            continue
        inherited = clean_text(group.get("position") or group.get("name") or group.get("displayName"))
        items = group.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    add(item, inherited)
        else:
            add(group, inherited)
    return output


def espn_teams(data: dict[str, Any]) -> list[dict[str, Any]]:
    teams: list[dict[str, Any]] = []
    for sport in data.get("sports") or []:
        if not isinstance(sport, dict):
            continue
        for league in sport.get("leagues") or []:
            if not isinstance(league, dict):
                continue
            for wrapper in league.get("teams") or []:
                team = wrapper.get("team") if isinstance(wrapper, dict) else None
                if isinstance(team, dict) and team.get("id") and team.get("isActive") is not False:
                    teams.append(team)
    # Some endpoints return a flat teams array.
    if not teams:
        for wrapper in data.get("teams") or []:
            team = wrapper.get("team", wrapper) if isinstance(wrapper, dict) else None
            if isinstance(team, dict) and team.get("id") and team.get("isActive") is not False:
                teams.append(team)
    unique: dict[str, dict[str, Any]] = {}
    for team in teams:
        unique[str(team["id"])] = team
    return list(unique.values())


def collect_espn_league(cfg: dict[str, str], workers: int = 16) -> SourceResult:
    session = make_session()
    teams_url = f"{ESPN_SITE}/{cfg['sport']}/{cfg['league']}/teams"
    try:
        teams = espn_teams(fetch_json(session, teams_url))
    except Exception as exc:  # source may not exist for every configured slug
        return SourceResult(cfg["label"], [], f"teams: {type(exc).__name__}: {exc}", 1)
    if not teams:
        return SourceResult(cfg["label"], [], "teams: no active teams returned", 1)

    records: list[dict[str, Any]] = []
    request_count = 1

    def fetch_team(team: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        local = make_session()
        roster_url = f"{ESPN_SITE}/{cfg['sport']}/{cfg['league']}/teams/{team['id']}/roster"
        try:
            data = fetch_json(local, roster_url)
            return extract_espn_roster(data, cfg=cfg, team=team, source_url=roster_url), None
        except Exception as exc:
            return [], f"{team.get('displayName', team['id'])}: {type(exc).__name__}: {exc}"

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(teams)))) as executor:
        futures = [executor.submit(fetch_team, team) for team in teams]
        for future in as_completed(futures):
            request_count += 1
            rows, error = future.result()
            records.extend(rows)
            if error:
                errors.append(error)
    error_text = "; ".join(errors[:5]) if errors and not records else None
    return SourceResult(cfg["label"], records, error_text, request_count)


def collect_nhl(workers: int = 16) -> SourceResult:
    session = make_session()
    standings_url = f"{NHL_API}/standings/now"
    try:
        data = fetch_json(session, standings_url)
        abbreviations: set[str] = set()
        team_names: dict[str, str] = {}
        for row in data.get("standings") or []:
            if not isinstance(row, dict):
                continue
            abbr = clean_text(row.get("teamAbbrev"))
            if not abbr:
                continue
            abbreviations.add(abbr)
            team_names[abbr] = clean_text(row.get("teamName"), abbr)
    except Exception as exc:
        return SourceResult("NHL", [], f"standings: {type(exc).__name__}: {exc}", 1)

    def fetch_team(abbr: str) -> tuple[list[dict[str, Any]], str | None]:
        local = make_session()
        url = f"{NHL_API}/roster/{abbr}/current"
        try:
            roster = fetch_json(local, url)
            rows: list[dict[str, Any]] = []
            for section, inherited in (("forwards", "Forward"), ("defensemen", "Defenseman"), ("goalies", "Goalie")):
                for item in roster.get(section) or []:
                    if not isinstance(item, dict):
                        continue
                    name = f"{clean_text(item.get('firstName'))} {clean_text(item.get('lastName'))}".strip()
                    athlete_id = clean_text(item.get("id"))
                    if len(name.split()) < 2 or not athlete_id:
                        continue
                    rows.append(
                        {
                            "sourceNamespace": "nhl",
                            "sourceRecordId": athlete_id,
                            "name": name,
                            "discipline": "Hockey",
                            "leagueOrMedium": "NHL",
                            "teamOrPlatform": team_names.get(abbr, abbr),
                            "role": clean_text(item.get("positionCode"), inherited),
                            "country": clean_text(item.get("birthCountry"), "Not listed"),
                            "experienceYears": None,
                            "age": athlete_age(item),
                            "starter": False,
                            "sourceName": "NHL current roster API",
                            "sourceUrl": url,
                            "sourceLeagueSlug": "nhl",
                            "statusConfidence": 0.98,
                        }
                    )
            return rows, None
        except Exception as exc:
            return [], f"{abbr}: {type(exc).__name__}: {exc}"

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(abbreviations)))) as executor:
        futures = [executor.submit(fetch_team, abbr) for abbr in sorted(abbreviations)]
        for future in as_completed(futures):
            rows, error = future.result()
            records.extend(rows)
            if error:
                errors.append(error)
    error_text = "; ".join(errors[:5]) if errors and not records else None
    return SourceResult("NHL", records, error_text, 1 + len(abbreviations))


def unique_ticker(name: str, source_key: str, used: set[str]) -> str:
    letters = re.sub(r"[^A-Z]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").upper())
    base = (letters[:4] or "TALX").ljust(4, "X")
    if base not in used:
        used.add(base)
        return base
    suffix = hashlib.sha1(source_key.encode()).hexdigest().upper()
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


def build_market_fields(raw: dict[str, Any], verified_at: str, used_tickers: set[str]) -> dict[str, Any]:
    source_key = f"{raw['sourceNamespace']}:{raw['sourceRecordId']}:{raw['discipline']}"
    exp = raw.get("experienceYears")
    stage = career_stage(exp)
    name = raw["name"]
    role = raw.get("role") or "Player"
    team = raw.get("teamOrPlatform") or "Team not listed"
    ticker = unique_ticker(name, source_key, used_tickers)
    profile_id = f"live-{raw['sourceNamespace']}-{normalize(raw['discipline'])}-{raw['sourceRecordId']}"

    record = {
        "id": profile_id,
        "name": name,
        "ticker": ticker,
        "primaryCategory": "Athlete",
        "discipline": raw["discipline"],
        "leagueOrMedium": raw["leagueOrMedium"],
        "teamOrPlatform": team,
        "role": role,
        "country": raw.get("country") or "Not listed",
        "careerStatus": "Active",
        "marketSegment": "Current",
        "verificationStatus": "Automated current-roster verification — point-in-time snapshot",
        "lastVerifiedAt": verified_at,
        "statusSource": raw["sourceName"],
        "sourceName": raw["sourceName"],
        "sourceUrl": raw["sourceUrl"],
        "sourceRecordId": raw["sourceRecordId"],
        "sourceNamespace": raw["sourceNamespace"],
        "sourceLeagueSlug": raw.get("sourceLeagueSlug", ""),
        "dataConfidence": round(float(raw.get("statusConfidence", 0.92)), 2),
        "activeMetrics": provisional_active_metrics(raw),
        "legacyMetrics": {},
        "modelType": "Active career model",
        "avatar": initials(name),
        "description": (
            f"Current {raw['leagueOrMedium']} roster listing for {role} with {team}. "
            "Roster identity is source-backed. Pricing stays provisional until performance, awards, age, availability and audience evidence are enriched."
        ),
        "searchText": " ".join(
            [name, "Athlete", raw["discipline"], raw["leagueOrMedium"], team, role, raw.get("country", ""), "Active", "Current", stage]
        ).lower(),
        "careerStage": stage,
        "experienceYears": exp,
        "age": raw.get("age"),
        "starter": bool(raw.get("starter")),
        "draftYear": raw.get("draftYear"),
        "draftRound": raw.get("draftRound"),
        "draftPick": raw.get("draftPick"),
        "pricingDataStatus": "Provisional — roster, experience and role evidence only",
        "pricingConfidence": 0.48 if exp is not None else 0.38,
    }
    # Pricing is applied after curated-seed merging so every current record goes
    # through the same audited model and evidence overrides.
    return record


def load_seed() -> list[dict[str, Any]]:
    if not SEED_FILE.exists():
        return []
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("current_seed.json must be an array")
    return data


def merge_seed(seed: list[dict[str, Any]], live: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live_by_name = {(normalize(r["name"]), normalize(r["discipline"])): r for r in live}
    merged: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for item in seed:
        key = (normalize(item.get("name", "")), normalize(item.get("discipline", "")))
        found = live_by_name.get(key)
        if found:
            # Preserve the original curated market simulation/profile identity while
            # replacing status metadata with the current roster source.
            updated = dict(item)
            for field in (
                "careerStatus", "marketSegment", "verificationStatus", "lastVerifiedAt",
                "statusSource", "sourceName", "sourceUrl", "sourceRecordId",
                "sourceNamespace", "sourceLeagueSlug", "dataConfidence", "teamOrPlatform",
                "role", "country", "careerStage", "experienceYears", "age", "starter",
                "draftYear", "draftRound", "draftPick",
            ):
                if field in found:
                    updated[field] = found[field]
            updated["searchText"] = found["searchText"]
            updated["description"] = found["description"]
            merged.append(updated)
            consumed.add(found["id"])
        else:
            merged.append(item)
    merged.extend(r for r in live if r["id"] not in consumed)
    return merged


def validate_records(records: list[dict[str, Any]], seed_count: int, target_additions: int) -> list[str]:
    errors: list[str] = []
    if len(records) < seed_count + target_additions:
        errors.append(f"Expected at least {seed_count + target_additions} current records, found {len(records)}")
    ids = [r.get("id") for r in records]
    tickers = [r.get("ticker") for r in records]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate profile IDs found")
    if len(tickers) != len(set(tickers)):
        errors.append("Duplicate tickers found")
    required = ("name", "primaryCategory", "discipline", "leagueOrMedium", "careerStatus", "marketSegment")
    for idx, record in enumerate(records):
        missing = [key for key in required if not record.get(key)]
        if missing:
            errors.append(f"Record {idx} missing {missing}")
            if len(errors) > 25:
                break
    return errors


def write_outputs(records: list[dict[str, Any]], source_results: list[SourceResult], seed_count: int, target_additions: int, built_at: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    csv_fields = [
        "id", "name", "ticker", "primaryCategory", "discipline", "leagueOrMedium",
        "teamOrPlatform", "role", "country", "careerStatus", "marketSegment",
        "careerStage", "lastVerifiedAt", "verificationStatus", "sourceName",
        "sourceUrl", "sourceRecordId", "dataConfidence", "pricingConfidence",
        "pricingDataStatus", "pricingModelVersion", "marketPrice", "careerScore",
    ]
    with CSV_FILE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    category_counts = Counter(r.get("primaryCategory", "Unknown") for r in records)
    sport_counts = Counter(r.get("discipline", "Unknown") for r in records if r.get("primaryCategory") == "Athlete")
    league_counts = Counter(r.get("leagueOrMedium", "Unknown") for r in records if r.get("primaryCategory") == "Athlete")
    live_verified = sum(1 for r in records if r.get("sourceNamespace") in {"espn", "nhl"})
    source_errors = {result.name: result.error for result in source_results if result.error}

    previous: dict[str, Any] = {}
    if MANIFEST_FILE.exists():
        try:
            previous = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    manifest = {
        **previous,
        "version": "3.2-current-10000-achievements-weighted",
        "generatedAt": built_at,
        "currentSeedRecords": len(records),
        "currentCatalogRecords": len(records),
        "newCurrentTarget": target_additions,
        "curatedSeedInputRecords": seed_count,
        "automatedRosterVerifiedRecords": live_verified,
        "categoryCounts": dict(sorted(category_counts.items())),
        "sportCounts": dict(sorted(sport_counts.items())),
        "leagueCounts": dict(league_counts.most_common()),
        "sourceErrorCount": len(source_errors),
        "currentCatalogFile": "data/current_catalog.json",
        "currentCatalogCsv": "data/current_catalog.csv",
        "marketDataMode": "Simulated evidence-weighted pricing",
        "pricingModelVersion": "3.2-achievements-weighted",
        "pricingRule": "High valuations require curated or verified evidence; roster-only records are conservative and capped.",
        "statusDataMode": "Automated point-in-time roster snapshot",
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    sources = {
        "generatedAt": built_at,
        "targetAdditions": target_additions,
        "totalRecords": len(records),
        "rules": {
            "currentEntry": "A person is added from a current team-roster endpoint and is not marked inactive by the source.",
            "priceDisclaimer": "All TalentX prices, scores, charts, volumes and changes are simulated.",
            "freshness": "Current status is a point-in-time snapshot and must be refreshed regularly.",
        },
        "sources": [
            {
                "name": result.name,
                "recordsReturned": len(result.records),
                "requests": result.requests,
                "error": result.error,
            }
            for result in source_results
        ],
    }
    SOURCE_MANIFEST_FILE.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-additions", type=int, default=10_000)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--sleep-between-leagues", type=float, default=0.15)
    parser.add_argument("--allow-shortfall", action="store_true", help="Write a partial catalog instead of failing")
    args = parser.parse_args()

    seed = load_seed()
    seed_keys = {(normalize(r.get("name", "")), normalize(r.get("discipline", ""))) for r in seed}
    used_tickers = {str(r.get("ticker", "")) for r in seed if r.get("ticker")}
    verified_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    raw_unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    name_sport_unique: set[tuple[str, str]] = set()
    source_results: list[SourceResult] = []

    # NHL official current rosters first.
    nhl = collect_nhl(args.workers)
    source_results.append(nhl)
    for raw in nhl.records:
        source_key = (raw["sourceNamespace"], raw["sourceRecordId"], raw["discipline"])
        name_key = (normalize(raw["name"]), normalize(raw["discipline"]))
        if name_key not in name_sport_unique:
            raw_unique[source_key] = raw
            name_sport_unique.add(name_key)
    print(f"NHL: {len(nhl.records):,} roster rows; unique total {len(raw_unique):,}", flush=True)

    # Then configured team-based leagues. Stop once the requested number of new
    # names (not already in the curated seed) is available.
    for cfg in ESPN_LEAGUES:
        current_new = sum(1 for raw in raw_unique.values() if (normalize(raw["name"]), normalize(raw["discipline"])) not in seed_keys)
        if current_new >= args.target_additions:
            break
        result = collect_espn_league(cfg, args.workers)
        source_results.append(result)
        before = len(raw_unique)
        for raw in result.records:
            source_key = (raw["sourceNamespace"], raw["sourceRecordId"], raw["discipline"])
            name_key = (normalize(raw["name"]), normalize(raw["discipline"]))
            if name_key in name_sport_unique:
                continue
            raw_unique[source_key] = raw
            name_sport_unique.add(name_key)
        print(
            f"{cfg['label']}: {len(result.records):,} roster rows, +{len(raw_unique)-before:,}; unique total {len(raw_unique):,}"
            + (f"; warning: {result.error}" if result.error else ""),
            flush=True,
        )
        if args.sleep_between_leagues:
            time.sleep(args.sleep_between_leagues)

    # Exactly target the requested *additional* records, while retaining any live
    # matches that refresh the existing curated seed.
    matching_seed_raw: list[dict[str, Any]] = []
    additional_raw: list[dict[str, Any]] = []
    for raw in raw_unique.values():
        key = (normalize(raw["name"]), normalize(raw["discipline"]))
        (matching_seed_raw if key in seed_keys else additional_raw).append(raw)
    additional_raw.sort(key=lambda r: (r["discipline"], r["leagueOrMedium"], r["teamOrPlatform"], r["name"]))
    selected_raw = matching_seed_raw + additional_raw[: args.target_additions]

    if len(additional_raw) < args.target_additions and not args.allow_shortfall:
        print(
            f"ERROR: only {len(additional_raw):,} unique new current athletes were returned; "
            f"target is {args.target_additions:,}. No incomplete catalog was written.",
            file=sys.stderr,
        )
        return 2

    live_records = [build_market_fields(raw, verified_at, used_tickers) for raw in selected_raw]
    records = merge_seed(seed, live_records)
    overrides = load_overrides(PRICING_OVERRIDES_FILE)
    records = apply_pricing_to_records(records, overrides)
    errors = validate_records(records, len(seed), min(args.target_additions, len(additional_raw)))
    if errors:
        print("Validation failed:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 3

    write_outputs(records, source_results, len(seed), min(args.target_additions, len(additional_raw)), verified_at)
    print(f"Wrote {len(records):,} current records to {OUTPUT_FILE}")
    print(f"Automated roster records: {sum(1 for r in records if r.get('sourceNamespace') in {'espn','nhl'}):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
