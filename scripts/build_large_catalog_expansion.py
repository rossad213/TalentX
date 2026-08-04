#!/usr/bin/env python3
"""Build the large TalentX catalog expansion from Wikipedia and Wikidata.

This builder adds approximately 9,400 source-linked identities to the curated
seed before the live ESPN/NHL roster build runs:

- 5,000 Music listings
- 300 Actor listings
- 100 Creator listings
- 4,000 Athlete listings: 2,000 across baseball, tennis, golf, motorsport,
  combat sports, and cricket, plus 2,000 soccer players

The source data proves identity and broad profession/category only. It does not
prove current performance, achievements, audience size, or active career status.
Every generated record therefore receives a conservative provisional pricing
status and remains subject to the pricing model's limited-evidence cap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pricing_model import CATEGORY_WEIGHTS, apply_pricing_to_records, clamp, load_overrides

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_CONFIG = DATA / "catalog_expansion_config.json"
DEFAULT_SEED = DATA / "current_seed.json"
DEFAULT_MANIFEST = DATA / "catalog_expansion_manifest.json"
DEFAULT_TAXONOMY = DATA / "taxonomy.json"
DEFAULT_OVERRIDES = DATA / "pricing_overrides.json"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "TalentX-Catalog-Expansion/2.0 (+https://github.com/rossad213/TalentX)"

# Wikidata item types used only to distinguish a human from a musical group.
Q_HUMAN = "Q5"
Q_MUSICAL_GROUP = "Q215380"
Q_MUSICAL_ENSEMBLE = "Q2088357"
Q_GROUP_OF_HUMANS = "Q16334295"
MUSICAL_GROUP_TYPES = {Q_MUSICAL_GROUP, Q_MUSICAL_ENSEMBLE, Q_GROUP_OF_HUMANS}

TITLE_EXCLUSIONS = (
    r"^list of ",
    r"^lists of ",
    r"^index of ",
    r"^history of ",
    r"^timeline of ",
    r"^records of ",
    r"^statistics of ",
    r"^awards of ",
    r"^discography of ",
    r"^filmography of ",
    r"^bibliography of ",
    r"^national team$",
    r"\bseason\b",
    r"\bchampionships?\b",
    r"\btournaments?\b",
    r"\bcompetitions?\b",
    r"\bawards?\b",
    r"\bleagues?\b",
    r"\bfederations?\b",
    r"\bassociations?\b",
    r"\bclubs?\b",
    r"\bteams?\b",
    r"\brosters?\b",
    r"\bvenues?\b",
    r"\bstadiums?\b",
    r"\balbums?\b",
    r"\bsongs?\b",
    r"\bfilms?\b",
    r"\btelevision series\b",
)
SUBCATEGORY_EXCLUSIONS = (
    "births",
    "deaths",
    "lists",
    "awards",
    "discographies",
    "filmographies",
    "songs",
    "albums",
    "works",
    "competitions",
    "tournaments",
    "championships",
    "seasons",
    "teams",
    "clubs",
    "coaches",
    "managers",
    "referees",
    "umpires",
    "executives",
    "venues",
    "stadiums",
    "organizations",
    "associations",
    "federations",
    "national teams",
    "records and statistics",
)


@dataclass
class SourceCandidate:
    page_id: int
    title: str
    qid: str
    article_length: int
    root: str
    source_category: str
    canonical_url: str
    description: str = ""
    entity_types: set[str] = field(default_factory=set)
    has_death_date: bool = False
    has_dissolution_date: bool = False


@dataclass
class SourceStats:
    wikipedia_requests: int = 0
    wikidata_requests: int = 0
    categories_visited: int = 0
    pages_discovered: int = 0
    entities_checked: int = 0
    source_errors: list[str] = field(default_factory=list)

    def merge(self, other: "SourceStats") -> None:
        self.wikipedia_requests += other.wikipedia_requests
        self.wikidata_requests += other.wikidata_requests
        self.categories_visited += other.categories_visited
        self.pages_discovered += other.pages_discovered
        self.entities_checked += other.entities_checked
        self.source_errors.extend(other.source_errors)


class ApiClient:
    def __init__(self, timeout: int = 25) -> None:
        self.timeout = timeout
        self.stats = SourceStats()
        self.session = requests.Session()
        retries = Retry(
            total=1,
            connect=1,
            read=1,
            backoff_factor=0.25,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=12, pool_maxsize=12)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if "wikipedia.org" in url:
            self.stats.wikipedia_requests += 1
        elif "wikidata.org" in url:
            self.stats.wikidata_requests += 1
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Expected a JSON object from {url}")
        if payload.get("error"):
            raise RuntimeError(f"API error from {url}: {payload['error']}")
        return payload


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-") or "talent"


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
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest().upper()
    for length in range(1, 5):
        candidate = (base[: 4 - length] + digest[:length])[:4]
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


def title_is_usable(title: str) -> bool:
    cleaned = re.sub(r"\s+", " ", title).strip()
    if len(cleaned) < 2 or len(cleaned) > 120:
        return False
    lowered = cleaned.lower()
    if any(re.search(pattern, lowered) for pattern in TITLE_EXCLUSIONS):
        return False
    if lowered.startswith(("category:", "template:", "portal:", "draft:")):
        return False
    if re.fullmatch(r"\d{4}.*", cleaned):
        return False
    return True


def subcategory_is_usable(title: str) -> bool:
    lowered = title.lower()
    return not any(term in lowered for term in SUBCATEGORY_EXCLUSIONS)


def wikipedia_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()_-.,'")


def iter_category_pages(client: ApiClient, category: str) -> Iterable[dict[str, Any]]:
    continuation: dict[str, Any] = {}
    while True:
        params: dict[str, Any] = {
            "action": "query",
            "generator": "categorymembers",
            "gcmtitle": category,
            "gcmtype": "page",
            "gcmnamespace": "0",
            "gcmlimit": "max",
            "prop": "info|pageprops",
            "inprop": "url",
            "ppprop": "wikibase_item",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
        params.update(continuation)
        payload = client.get_json(WIKIPEDIA_API, params)
        for page in (payload.get("query") or {}).get("pages") or []:
            if isinstance(page, dict):
                yield page
        continuation = payload.get("continue") or {}
        if not continuation:
            break


def iter_subcategories(client: ApiClient, category: str) -> Iterable[str]:
    continuation: dict[str, Any] = {}
    while True:
        params: dict[str, Any] = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmtype": "subcat",
            "cmnamespace": "14",
            "cmlimit": "max",
            "cmprop": "title",
            "format": "json",
            "formatversion": "2",
        }
        params.update(continuation)
        payload = client.get_json(WIKIPEDIA_API, params)
        for item in (payload.get("query") or {}).get("categorymembers") or []:
            if isinstance(item, dict) and item.get("title"):
                yield str(item["title"])
        continuation = payload.get("continue") or {}
        if not continuation:
            break


def crawl_root(
    root: str,
    max_depth: int,
    candidate_limit: int,
    category_limit: int,
    timeout: int,
) -> tuple[list[SourceCandidate], SourceStats]:
    client = ApiClient(timeout)
    queue: deque[tuple[str, int]] = deque([(root, 0)])
    visited: set[str] = set()
    candidates: dict[str, SourceCandidate] = {}

    while queue and len(visited) < category_limit and len(candidates) < candidate_limit:
        category, depth = queue.popleft()
        if category in visited or not subcategory_is_usable(category):
            continue
        visited.add(category)
        client.stats.categories_visited += 1
        try:
            for page in iter_category_pages(client, category):
                title = str(page.get("title") or "").strip()
                qid = str((page.get("pageprops") or {}).get("wikibase_item") or "")
                if not title_is_usable(title) or not re.fullmatch(r"Q\d+", qid):
                    continue
                page_id = int(page.get("pageid") or 0)
                if page_id <= 0:
                    continue
                key = qid
                candidate = SourceCandidate(
                    page_id=page_id,
                    title=title,
                    qid=qid,
                    article_length=max(0, int(page.get("length") or 0)),
                    root=root,
                    source_category=category,
                    canonical_url=str(page.get("canonicalurl") or wikipedia_url(title)),
                )
                existing = candidates.get(key)
                if not existing or candidate.article_length > existing.article_length:
                    candidates[key] = candidate
                if len(candidates) >= candidate_limit:
                    break
        except Exception as exc:
            client.stats.source_errors.append(f"{root} / {category}: {type(exc).__name__}: {exc}")

        if depth < max_depth and len(visited) < category_limit and len(candidates) < candidate_limit:
            try:
                for subcategory in iter_subcategories(client, category):
                    if subcategory not in visited and subcategory_is_usable(subcategory):
                        queue.append((subcategory, depth + 1))
            except Exception as exc:
                client.stats.source_errors.append(f"{root} subcategories / {category}: {type(exc).__name__}: {exc}")

    client.stats.pages_discovered = len(candidates)
    return list(candidates.values()), client.stats


def claim_entity_ids(entity: dict[str, Any], property_id: str) -> set[str]:
    output: set[str] = set()
    claims = (entity.get("claims") or {}).get(property_id) or []
    for claim in claims:
        try:
            value = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if isinstance(value, dict) and value.get("id"):
            output.add(str(value["id"]))
    return output


def has_claim(entity: dict[str, Any], property_id: str) -> bool:
    return bool((entity.get("claims") or {}).get(property_id))


def fetch_entity_batch(qids: list[str], timeout: int) -> tuple[dict[str, dict[str, Any]], SourceStats]:
    client = ApiClient(timeout)
    payload = client.get_json(WIKIDATA_API, {
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "props": "labels|descriptions|claims",
        "languages": "en",
        "languagefallback": "1",
        "format": "json",
        "formatversion": "2",
    })
    entities = payload.get("entities") or {}
    if not isinstance(entities, dict):
        entities = {}
    client.stats.entities_checked = len(qids)
    return {str(key): value for key, value in entities.items() if isinstance(value, dict)}, client.stats


def enrich_candidates(
    candidates: list[SourceCandidate],
    timeout: int,
    workers: int,
) -> tuple[list[SourceCandidate], SourceStats]:
    stats = SourceStats()
    by_qid = {candidate.qid: candidate for candidate in candidates}
    qids = list(by_qid)
    batches = [qids[index:index + 50] for index in range(0, len(qids), 50)]
    entities: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fetch_entity_batch, batch, timeout) for batch in batches]
        for future in as_completed(futures):
            try:
                result, local_stats = future.result()
                entities.update(result)
                stats.merge(local_stats)
            except Exception as exc:
                stats.source_errors.append(f"Wikidata batch: {type(exc).__name__}: {exc}")

    enriched: list[SourceCandidate] = []
    for qid, candidate in by_qid.items():
        entity = entities.get(qid)
        if not entity or entity.get("missing"):
            continue
        candidate.description = str(((entity.get("descriptions") or {}).get("en") or {}).get("value") or "")
        candidate.entity_types = claim_entity_ids(entity, "P31")
        candidate.has_death_date = has_claim(entity, "P570")
        candidate.has_dissolution_date = has_claim(entity, "P576")
        enriched.append(candidate)
    return enriched, stats


def candidate_type(candidate: SourceCandidate) -> str:
    if Q_HUMAN in candidate.entity_types:
        return "human"
    if candidate.entity_types & MUSICAL_GROUP_TYPES:
        return "musical_group"
    description = candidate.description.lower()
    if any(term in description for term in ("band", "musical group", "music duo", "orchestra", "musical ensemble")):
        return "musical_group"
    return "other"


def candidate_matches_group(candidate: SourceCandidate, group: dict[str, Any]) -> bool:
    entity_type = candidate_type(candidate)
    if entity_type not in set(group.get("allowedEntityTypes") or []):
        return False
    if entity_type == "human" and candidate.has_death_date:
        return False
    if entity_type == "musical_group" and candidate.has_dissolution_date:
        return False
    description = candidate.description.lower()
    keywords = [str(value).lower() for value in group.get("descriptionKeywords") or []]
    if description and keywords and not any(keyword in description for keyword in keywords):
        # Category membership is strong discovery evidence, but the description
        # must agree when Wikidata provides one.
        return False
    return True


def balanced_select(candidates: list[SourceCandidate], target: int) -> list[SourceCandidate]:
    queues: dict[str, deque[SourceCandidate]] = {}
    for root, items in defaultdict(list, {
        root: [candidate for candidate in candidates if candidate.root == root]
        for root in sorted({candidate.root for candidate in candidates})
    }).items():
        ordered = sorted(items, key=lambda item: (-item.article_length, item.title.lower(), item.qid))
        queues[root] = deque(ordered)

    selected: list[SourceCandidate] = []
    used_qids: set[str] = set()
    roots = list(queues)
    while len(selected) < target and roots:
        next_roots: list[str] = []
        for root in roots:
            queue = queues[root]
            while queue and queue[0].qid in used_qids:
                queue.popleft()
            if queue:
                item = queue.popleft()
                selected.append(item)
                used_qids.add(item.qid)
                if len(selected) >= target:
                    break
            if queue:
                next_roots.append(root)
        roots = next_roots

    if len(selected) < target:
        remaining = sorted(
            (candidate for candidate in candidates if candidate.qid not in used_qids),
            key=lambda item: (-item.article_length, item.title.lower(), item.qid),
        )
        for candidate in remaining:
            selected.append(candidate)
            used_qids.add(candidate.qid)
            if len(selected) >= target:
                break
    return selected[:target]


def deterministic_rng(category: str, discipline: str, name: str) -> random.Random:
    digest = hashlib.sha256(f"expansion-v2:{category}:{discipline}:{name}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def provisional_metrics(
    category: str,
    discipline: str,
    name: str,
    rank: int,
    total: int,
) -> dict[str, float]:
    weights = CATEGORY_WEIGHTS[category]
    percentile = 0.5 if total <= 1 else clamp((rank - 1) / (total - 1), 0, 1)
    target_score = 43.0 + 31.0 * (1.0 - percentile) ** 0.72
    rng = deterministic_rng(category, discipline, name)
    raw = {key: target_score + rng.uniform(-7.0, 7.0) for key in weights}
    weighted = sum(raw[key] * weight for key, weight in weights.items())
    shift = target_score - weighted
    metrics = {key: round(clamp(value + shift, 32, 82), 1) for key, value in raw.items()}
    if category != "Athlete":
        metrics["availability"] = round(clamp(72 + rng.uniform(-6, 10), 60, 88), 1)
    return metrics


def build_record(
    candidate: SourceCandidate,
    group: dict[str, Any],
    rank: int,
    pool_size: int,
    used_ids: set[str],
    used_tickers: set[str],
    generated_at: str,
    expansion_version: str,
) -> dict[str, Any]:
    category = str(group["category"])
    discipline = str(group["discipline"])
    name = candidate.title.strip()
    profile_id = f"cur-wd-{candidate.qid.lower()}"
    if profile_id in used_ids:
        profile_id = f"cur-wd-{candidate.qid.lower()}-{slugify(discipline)}"
    used_ids.add(profile_id)
    ticker = unique_ticker(name, f"{candidate.qid}:{category}:{discipline}", used_tickers)
    metrics = provisional_metrics(category, discipline, name, rank, pool_size)
    source_url = candidate.canonical_url or wikipedia_url(name)
    entity_kind = candidate_type(candidate)
    description_source = candidate.description or f"Wikipedia category member associated with {discipline}"

    return {
        "id": profile_id,
        "name": name,
        "ticker": ticker,
        "primaryCategory": category,
        "discipline": discipline,
        "leagueOrMedium": str(group.get("leagueOrMedium") or discipline),
        "teamOrPlatform": str(group.get("teamOrPlatform") or "Not listed"),
        "role": str(group.get("role") or category),
        "country": "Not listed",
        "careerStatus": "Current-status verification pending",
        "marketSegment": "Current",
        "verificationStatus": "Identity and broad category verified; current career status requires source verification",
        "lastVerifiedAt": generated_at,
        "statusSource": "English Wikipedia category membership and Wikidata entity record",
        "sourceName": "Wikipedia category discovery + Wikidata identity check",
        "sourceUrl": source_url,
        "sourceRecordId": candidate.qid,
        "sourceNamespace": "wikipedia-wikidata",
        "sourceCategory": candidate.source_category,
        "sourceRootCategory": candidate.root,
        "wikipediaPageId": candidate.page_id,
        "wikipediaArticleLength": candidate.article_length,
        "wikidataDescription": description_source,
        "wikidataEntityType": entity_kind,
        "dataConfidence": 0.58,
        "pricingConfidence": 0.58,
        "activeMetrics": metrics,
        "modelType": "Active career model",
        "avatar": initials(name),
        "description": (
            f"Source-linked {discipline} listing discovered through {candidate.source_category}. "
            "Identity and broad profession are verified; career performance and current active status still require dedicated evidence."
        ),
        "searchText": " ".join([
            name,
            ticker,
            category,
            discipline,
            str(group.get("leagueOrMedium") or ""),
            str(group.get("teamOrPlatform") or ""),
            str(group.get("role") or ""),
            "Current status verification pending",
        ]).lower(),
        "careerStage": "Stage Under Review",
        "pricingDataStatus": "Provisional — identity and category evidence only",
        "pricingEvidence": [
            {
                "source": "English Wikipedia",
                "url": source_url,
                "field": "Identity and category membership",
            },
            {
                "source": "Wikidata",
                "url": f"https://www.wikidata.org/wiki/{candidate.qid}",
                "field": "Entity type and living/dissolution check",
            },
        ],
        "benchmarkRank": rank,
        "benchmarkPoolSize": pool_size,
        "catalogExpansionVersion": expansion_version,
    }


def update_taxonomy(path: Path, records: list[dict[str, Any]]) -> None:
    taxonomy = load_json(path) if path.exists() else {"categories": {}}
    categories = taxonomy.setdefault("categories", {})
    for category in ("Athlete", "Music", "Actor", "Creator"):
        block = categories.setdefault(category, {"label": category, "disciplines": [], "filters": []})
        disciplines = {str(value) for value in block.get("disciplines", []) if value}
        disciplines.update(
            str(record.get("discipline"))
            for record in records
            if record.get("primaryCategory") == category and record.get("discipline")
        )
        block["disciplines"] = sorted(disciplines)
    path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")


def load_fixture(path: Path) -> dict[str, list[SourceCandidate]]:
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise ValueError("Fixture must be an object keyed by group key")
    output: dict[str, list[SourceCandidate]] = {}
    for key, values in raw.items():
        if not isinstance(values, list):
            raise ValueError(f"Fixture group {key} must be an array")
        output[str(key)] = [SourceCandidate(
            page_id=int(item["page_id"]),
            title=str(item["title"]),
            qid=str(item["qid"]),
            article_length=int(item.get("article_length") or 0),
            root=str(item.get("root") or f"Category:{key}"),
            source_category=str(item.get("source_category") or f"Category:{key}"),
            canonical_url=str(item.get("canonical_url") or wikipedia_url(str(item["title"]))),
            description=str(item.get("description") or key),
            entity_types=set(item.get("entity_types") or [Q_HUMAN]),
            has_death_date=bool(item.get("has_death_date")),
            has_dissolution_date=bool(item.get("has_dissolution_date")),
        ) for item in values]
    return output


def collect_group(
    group: dict[str, Any],
    existing_names: set[str],
    used_qids: set[str],
    timeout: int,
    workers: int,
) -> tuple[list[SourceCandidate], SourceStats]:
    roots = [str(root) for root in group.get("roots") or []]
    if not roots:
        raise ValueError(f"Group {group.get('key')} has no roots")
    max_depth = int(group.get("maxDepth") or 2)
    candidate_limit = int(group.get("candidateLimitPerRoot") or 1000)
    category_limit = int(group.get("maxCategoriesPerRoot") or 500)
    stats = SourceStats()
    candidates: list[SourceCandidate] = []

    with ThreadPoolExecutor(max_workers=min(max(1, workers), len(roots))) as executor:
        futures = {
            executor.submit(crawl_root, root, max_depth, candidate_limit, category_limit, timeout): root
            for root in roots
        }
        for future in as_completed(futures):
            root = futures[future]
            try:
                result, local_stats = future.result()
                candidates.extend(result)
                stats.merge(local_stats)
                print(f"  {root}: {len(result):,} candidate pages", flush=True)
            except Exception as exc:
                stats.source_errors.append(f"{root}: {type(exc).__name__}: {exc}")

    unique_by_qid: dict[str, SourceCandidate] = {}
    for candidate in candidates:
        if candidate.qid in used_qids or normalize(candidate.title) in existing_names:
            continue
        existing = unique_by_qid.get(candidate.qid)
        if not existing or candidate.article_length > existing.article_length:
            unique_by_qid[candidate.qid] = candidate

    target = int(group["target"])
    # Validate a generous notability-sorted pool rather than every category page.
    preselected = sorted(
        unique_by_qid.values(),
        key=lambda item: (-item.article_length, item.title.lower(), item.qid),
    )[: max(target * 3, target + 500)]
    enriched, entity_stats = enrich_candidates(preselected, timeout, workers)
    stats.merge(entity_stats)
    valid = [candidate for candidate in enriched if candidate_matches_group(candidate, group)]
    selected = balanced_select(valid, target)
    return selected, stats


def validate_seed(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    expected_additions = sum(int(group["target"]) for group in config["groups"])
    if len(after) != len(before) + expected_additions:
        errors.append(
            f"Expected {len(before) + expected_additions:,} seed records after expansion; found {len(after):,}"
        )
    ids = [str(record.get("id") or "") for record in after]
    tickers = [str(record.get("ticker") or "") for record in after]
    names = [normalize(record.get("name", "")) for record in after]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate profile IDs after expansion")
    if len(tickers) != len(set(tickers)):
        errors.append("Duplicate ticker symbols after expansion")
    if len(names) != len(set(names)):
        duplicates = [name for name, count in Counter(names).items() if count > 1][:10]
        errors.append(f"Duplicate normalized names after expansion: {duplicates}")

    generated = [record for record in after if record.get("catalogExpansionVersion") == config["version"]]
    by_group = Counter(
        str(record.get("discipline")) if record.get("primaryCategory") == "Athlete" else str(record.get("primaryCategory"))
        for record in generated
    )
    for group in config["groups"]:
        key = str(group["discipline"] if group["category"] == "Athlete" else group["category"])
        expected = int(group["target"])
        if by_group[key] != expected:
            errors.append(f"Expected {expected:,} generated {key} records; found {by_group[key]:,}")
    for record in generated:
        if not record.get("sourceUrl") or not record.get("sourceRecordId"):
            errors.append(f"Generated record lacks source metadata: {record.get('name')}")
            break
        if not str(record.get("pricingDataStatus") or "").startswith("Provisional"):
            errors.append(f"Generated record is not provisional: {record.get('name')}")
            break
        if float(record.get("fundamentalValue") or record.get("fundamental") or 0) > 62.01:
            errors.append(f"Generated provisional record exceeded $62 cap: {record.get('name')}")
            break
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--fixture", type=Path, help="Offline fixture used only for tests")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--request-timeout", type=int, default=25)
    parser.add_argument("--allow-shortfall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_json(args.config)
    if not isinstance(config, dict) or not isinstance(config.get("groups"), list):
        raise ValueError("catalog_expansion_config.json must contain a groups array")
    expected = sum(int(group.get("target") or 0) for group in config["groups"])
    if expected != int(config.get("requestedTotalAdditions") or 0):
        raise ValueError("requestedTotalAdditions does not match the sum of group targets")

    seed = load_json(args.seed)
    if not isinstance(seed, list):
        raise ValueError("current_seed.json must be an array")

    # The workflow runs repeatedly. Remove records created by any earlier
    # Wikipedia/Wikidata expansion before rebuilding, otherwise every weekly
    # run would append another full expansion cohort.
    original_seed_count = len(seed)
    seed = [
        dict(record)
        for record in seed
        if record.get("sourceNamespace") != "wikipedia-wikidata"
        and not str(record.get("catalogExpansionVersion") or "").startswith(("2.0-", "2.1-"))
    ]
    removed_previous_expansion = original_seed_count - len(seed)
    before = [dict(record) for record in seed]
    existing_names = {normalize(record.get("name", "")) for record in seed}
    used_ids = {str(record.get("id")) for record in seed if record.get("id")}
    used_tickers = {str(record.get("ticker")) for record in seed if record.get("ticker")}
    used_qids = {
        str(record.get("sourceRecordId"))
        for record in seed
        if re.fullmatch(r"Q\d+", str(record.get("sourceRecordId") or ""))
    }
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    fixture = load_fixture(args.fixture) if args.fixture else None
    all_stats = SourceStats()
    generated_records: list[dict[str, Any]] = []
    group_results: list[dict[str, Any]] = []

    for group in config["groups"]:
        key = str(group["key"])
        target = int(group["target"])
        print(f"Collecting {target:,} additions for {key}...", flush=True)
        if fixture is not None:
            selected = [candidate for candidate in fixture.get(key, []) if normalize(candidate.title) not in existing_names]
            selected = selected[:target]
            stats = SourceStats(pages_discovered=len(selected), entities_checked=len(selected))
        else:
            selected, stats = collect_group(
                group,
                existing_names,
                used_qids,
                args.request_timeout,
                args.workers,
            )
        all_stats.merge(stats)
        if len(selected) < target and not args.allow_shortfall:
            print(
                f"ERROR: {key} produced {len(selected):,} valid unique identities; target is {target:,}. "
                "No incomplete seed was written.",
                file=sys.stderr,
            )
            for error in stats.source_errors[:20]:
                print(f"- {error}", file=sys.stderr)
            return 2

        category_existing = sum(1 for record in seed if record.get("primaryCategory") == group["category"])
        pool_size = category_existing + target
        for rank, candidate in enumerate(selected[:target], start=1):
            record = build_record(
                candidate,
                group,
                category_existing + rank,
                pool_size,
                used_ids,
                used_tickers,
                generated_at,
                str(config["version"]),
            )
            generated_records.append(record)
            existing_names.add(normalize(record["name"]))
            used_qids.add(candidate.qid)
        group_results.append({
            "key": key,
            "category": group["category"],
            "discipline": group["discipline"],
            "requested": target,
            "selected": min(len(selected), target),
            "candidatePages": stats.pages_discovered,
            "entitiesChecked": stats.entities_checked,
            "sourceErrors": stats.source_errors[:25],
        })
        print(f"  selected {min(len(selected), target):,}", flush=True)

    combined = seed + generated_records
    overrides = load_overrides(DEFAULT_OVERRIDES)
    combined = apply_pricing_to_records(
        combined,
        overrides,
        benchmark_records=combined,
        calibration_reference=combined,
    )
    errors = validate_seed(before, combined, config)
    if errors:
        print("Validation failed:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 3

    counts = Counter(str(record.get("primaryCategory")) for record in combined)
    discipline_counts = Counter(
        str(record.get("discipline"))
        for record in generated_records
        if record.get("primaryCategory") == "Athlete"
    )
    manifest = {
        "version": config["version"],
        "generatedAt": generated_at,
        "requestedTotalAdditions": expected,
        "generatedTotalAdditions": len(generated_records),
        "seedRecordsBefore": len(before),
        "previousExpansionRecordsRemoved": removed_previous_expansion,
        "seedRecordsAfter": len(combined),
        "finalSeedCategoryCounts": dict(counts),
        "generatedAthleteDisciplineCounts": dict(discipline_counts),
        "groups": group_results,
        "sourcePolicy": config.get("sourcePolicy"),
        "sourceRequestCounts": {
            "wikipedia": all_stats.wikipedia_requests,
            "wikidata": all_stats.wikidata_requests,
            "categoriesVisited": all_stats.categories_visited,
            "candidatePagesDiscovered": all_stats.pages_discovered,
            "entitiesChecked": all_stats.entities_checked,
        },
        "pricingStatus": "Provisional — identity and category evidence only; limited-evidence cap applies",
    }

    print(f"Expanded seed: {len(before):,} -> {len(combined):,}")
    print(f"Generated additions: {len(generated_records):,}")
    for category in ("Athlete", "Music", "Actor", "Creator"):
        print(f"- {category}: {counts.get(category, 0):,}")
    for discipline, count in discipline_counts.items():
        print(f"  {discipline}: +{count:,}")

    if args.dry_run:
        return 0
    args.seed.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    update_taxonomy(args.taxonomy, combined)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
