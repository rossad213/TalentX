#!/usr/bin/env python3
"""Verified event-driven market pricing for TalentX Music and Actor listings.

The refresh is intentionally conservative:
* Music: recent releases must be linked to the artist in Wikidata and confirmed
  against MusicBrainz before they can move price.
* Actors: recent film/TV releases come from Wikidata cast + publication-date
  statements. Newly observed future projects can create a smaller project event
  after the first baseline scan.
* Music/Actor awards and nominations are detected as newly appearing Wikidata
  claims after a baseline snapshot.
* Every move is persisted as a durable ``priceEvents`` item. No supported event
  means no price movement.

The script is designed to run inside the existing hourly TalentX workflow. A
minimum interval prevents hammering public metadata services on every hourly run.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DEFAULT_MANIFEST = ROOT / "data" / "non_athlete_event_manifest.json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
MUSICBRAINZ_SEARCH = "https://musicbrainz.org/ws/2/release-group/"
USER_AGENT = "TalentX-Event-Pricing/1.0 (+https://github.com/rossad213/TalentX)"
MODEL_VERSION = "1.0-verified-music-actor-events"
MAX_SAVED_EVENT_KEYS = 40000
MAX_PRICE_EVENTS = 1500

MUSIC_RELEASE_BASE = {
    "Album": 0.65,
    "EP": 0.42,
    "Single": 0.28,
    "Broadcast": 0.15,
    "Other": 0.18,
}
EVENT_BASE = {
    "actor-release": 0.30,
    "actor-upcoming-project": 0.16,
    "award": 0.72,
    "nomination": 0.30,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def clamp(value: Any, low: float, high: float) -> float:
    return max(low, min(high, number(value)))


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def qid_for(record: dict[str, Any]) -> str:
    candidates = [
        record.get("wikidataSourceRecordId"),
        record.get("sourceRecordId") if str(record.get("sourceNamespace") or "").startswith("wikidata") else None,
    ]
    for value in candidates:
        qid = str(value or "").strip()
        if re.fullmatch(r"Q\d+", qid):
            return qid
    return ""


def existing_mbid(record: dict[str, Any]) -> str:
    ids = record.get("musicBrainzArtistIds")
    if isinstance(ids, list):
        for item in ids:
            value = str(item or "").strip()
            if re.fullmatch(r"[0-9a-fA-F-]{36}", value):
                return value.lower()
    value = str(record.get("musicBrainzArtistId") or "").strip()
    return value.lower() if re.fullmatch(r"[0-9a-fA-F-]{36}", value) else ""


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def claim_item_ids(entity: dict[str, Any], prop: str) -> set[str]:
    output: set[str] = set()
    claims = entity.get("claims", {}) if isinstance(entity.get("claims"), dict) else {}
    for claim in claims.get(prop, []) if isinstance(claims.get(prop), list) else []:
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value") if isinstance(claim, dict) else None
        if isinstance(value, dict):
            qid = str(value.get("id") or "")
            if re.fullmatch(r"Q\d+", qid):
                output.add(qid)
    return output


def claim_strings(entity: dict[str, Any], prop: str) -> list[str]:
    output: list[str] = []
    claims = entity.get("claims", {}) if isinstance(entity.get("claims"), dict) else {}
    for claim in claims.get(prop, []) if isinstance(claims.get(prop), list) else []:
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value") if isinstance(claim, dict) else None
        if isinstance(value, str) and value.strip():
            output.append(value.strip())
    return output


def fetch_entities(session: requests.Session, qids: list[str], timeout: float) -> tuple[dict[str, dict[str, Any]], list[str]]:
    entities: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for start in range(0, len(qids), 50):
        batch = qids[start:start + 50]
        try:
            response = session.get(
                WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "claims|labels",
                    "languages": "en",
                    "format": "json",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json().get("entities", {})
            for qid, entity in payload.items():
                if isinstance(entity, dict) and not entity.get("missing"):
                    entities[str(qid)] = entity
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Wikidata entities batch {start // 50 + 1}: {type(exc).__name__}: {exc}")
    return entities, errors


def fetch_labels(session: requests.Session, qids: set[str], timeout: float) -> tuple[dict[str, str], list[str]]:
    if not qids:
        return {}, []
    entities, errors = fetch_entities(session, sorted(qids), timeout)
    labels: dict[str, str] = {}
    for qid, entity in entities.items():
        label = entity.get("labels", {}).get("en", {}) if isinstance(entity.get("labels"), dict) else {}
        labels[qid] = str(label.get("value") or qid) if isinstance(label, dict) else qid
    return labels, errors


def sparql_value(binding: dict[str, Any], key: str) -> str:
    value = binding.get(key)
    return str(value.get("value") or "") if isinstance(value, dict) else ""


def work_query(category: str, qids: list[str], start: datetime, end: datetime) -> str:
    values = " ".join(f"wd:{qid}" for qid in qids)
    date_start = start.strftime("%Y-%m-%dT00:00:00Z")
    date_end = end.strftime("%Y-%m-%dT23:59:59Z")
    if category == "Music":
        relation = "?work wdt:P175 ?person; wdt:P577 ?date."
        type_filter = ""
    else:
        relation = "?work wdt:P161 ?person; wdt:P577 ?date."
        type_filter = """
  VALUES ?rootType { wd:Q11424 wd:Q5398426 wd:Q506240 }
  ?work wdt:P31/wdt:P279* ?rootType.
"""
    return f"""
SELECT DISTINCT ?person ?work ?workLabel ?date WHERE {{
  VALUES ?person {{ {values} }}
  {relation}
  {type_filter}
  FILTER(?date >= \"{date_start}\"^^xsd:dateTime && ?date <= \"{date_end}\"^^xsd:dateTime)
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language \"en\". }}
}}
ORDER BY ?date
""".strip()


def fetch_works(
    session: requests.Session,
    category: str,
    qids: list[str],
    start: datetime,
    end: datetime,
    batch_size: int,
    timeout: float,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    for offset in range(0, len(qids), batch_size):
        batch = qids[offset:offset + batch_size]
        try:
            response = session.post(
                WIKIDATA_SPARQL,
                data={"query": work_query(category, batch, start, end), "format": "json"},
                timeout=timeout,
            )
            response.raise_for_status()
            rows = response.json().get("results", {}).get("bindings", [])
            for binding in rows:
                if not isinstance(binding, dict):
                    continue
                person_url = sparql_value(binding, "person")
                work_url = sparql_value(binding, "work")
                qid_match = re.search(r"/(Q\d+)$", person_url)
                work_match = re.search(r"/(Q\d+)$", work_url)
                when = parse_time(sparql_value(binding, "date"))
                title = sparql_value(binding, "workLabel")
                if not qid_match or not work_match or when is None or not title:
                    continue
                output[qid_match.group(1)].append({
                    "personQid": qid_match.group(1),
                    "workQid": work_match.group(1),
                    "title": title,
                    "date": when,
                })
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{category} work batch {offset // batch_size + 1}: {type(exc).__name__}: {exc}")
    for qid in output:
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for item in output[qid]:
            key = (str(item["workQid"]), item["date"].date().isoformat())
            unique[key] = item
        output[qid] = sorted(unique.values(), key=lambda item: item["date"])
    return dict(output), errors


def musicbrainz_release_match(
    session: requests.Session,
    mbid: str,
    candidate: dict[str, Any],
    timeout: float,
) -> dict[str, Any] | None:
    date = candidate["date"]
    start = (date - timedelta(days=3)).date().isoformat()
    end = (date + timedelta(days=3)).date().isoformat()
    query = f'arid:{mbid} AND firstreleasedate:[{start} TO {end}]'
    response = session.get(MUSICBRAINZ_SEARCH, params={"query": query, "fmt": "json", "limit": 15}, timeout=timeout)
    response.raise_for_status()
    target = normalize(candidate.get("title"))
    best: dict[str, Any] | None = None
    for release in response.json().get("release-groups", []):
        if not isinstance(release, dict) or not release.get("id"):
            continue
        title = str(release.get("title") or "")
        release_key = normalize(title)
        if not target or not release_key:
            continue
        exact = target == release_key
        close = min(len(target), len(release_key)) >= 5 and (target in release_key or release_key in target)
        if not exact and not close:
            continue
        score = number(release.get("score"), 0)
        candidate_match = {
            "id": str(release["id"]),
            "title": title,
            "date": parse_time(release.get("first-release-date")) or date,
            "primaryType": str(release.get("primary-type") or "Other"),
            "score": score,
        }
        if best is None or (exact, score) > (normalize(best["title"]) == target, number(best.get("score"), 0)):
            best = candidate_match
    return best


def record_prominence_multiplier(record: dict[str, Any]) -> float:
    """Scale a verified event by how surprising it is for this profile."""
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    audience = clamp(metrics.get("audience", 50), 0, 100)
    confidence_raw = number(record.get("confidenceScore"), -1)
    if confidence_raw < 0:
        raw = number(record.get("pricingConfidence", record.get("dataConfidence", .6)), .6)
        confidence = clamp(raw * 100 if raw <= 1 else raw, 0, 100)
    else:
        confidence = clamp(confidence_raw, 0, 100)
    expectedness = clamp((audience * 0.60 + confidence * 0.40) / 100.0, 0.0, 1.0)
    return 1.15 - 0.30 * expectedness


def event_move_pct(record: dict[str, Any], event: dict[str, Any]) -> float:
    event_type = str(event.get("eventType") or "")
    if event_type == "music-release":
        primary = str(event.get("releaseType") or "Other")
        base = MUSIC_RELEASE_BASE.get(primary, MUSIC_RELEASE_BASE["Other"])
    else:
        base = EVENT_BASE.get(event_type, 0.0)
    return round(base * record_prominence_multiplier(record), 3)


def explanation_for(event: dict[str, Any], move: float, price: float) -> dict[str, Any]:
    event_type = str(event.get("eventType") or "")
    if event_type == "music-release":
        release_type = str(event.get("releaseType") or "release")
        headline = f"New {release_type.lower()} release"
        summary = ["A verified new music release added current career activity.", "No chart or streaming success is assumed from the release alone."]
    elif event_type == "actor-release":
        headline = "New screen release"
        summary = ["A verified film or television project was released with this performer in the cast.", "The move reflects confirmed career activity, not assumed box-office or review success."]
    elif event_type == "actor-upcoming-project":
        headline = "New upcoming project verified"
        summary = ["A future film or television project newly appeared in verified cast data.", "The move is intentionally smaller until release/performance evidence arrives."]
    elif event_type == "award":
        headline = "New award evidence"
        summary = ["A new verified award claim appeared in the source record."]
    else:
        headline = "New nomination evidence"
        summary = ["A new verified award nomination appeared in the source record."]
    return {
        "version": MODEL_VERSION,
        "eventId": event.get("eventKey"),
        "event": event.get("name"),
        "eventAt": event.get("startedAt"),
        "headline": headline,
        "summary": summary,
        "direction": "increased" if move > 0 else "decreased" if move < 0 else "held steady",
        "finalMovePct": round(move, 2),
        "recordedMarketPrice": round(price, 2),
        "pricingMode": "Verified non-athlete career event; event market price preserved",
        "source": event.get("provider"),
        "sourceUrl": event.get("sourceUrl"),
    }


def apply_events(record: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    result = dict(record)
    prior_events = [dict(item) for item in result.get("priceEvents", []) if isinstance(item, dict)]
    known = {str(item.get("eventKey") or item.get("eventId") or "") for item in prior_events}
    new_events = [event for event in events if str(event.get("eventKey") or "") not in known]
    new_events.sort(key=lambda event: str(event.get("startedAt") or ""))
    if not new_events:
        return result, 0

    old_price = max(.01, number(result.get("marketPrice"), .01))
    price = old_price
    trend = [number(item) for item in result.get("trend", []) if number(item) > 0]
    if not trend:
        trend = [old_price]
    stored: list[dict[str, Any]] = []
    for event in new_events:
        move = event_move_pct(result, event)
        if abs(move) < .001:
            continue
        before = price
        after = max(0.01, round(before * (1 + move / 100), 2))
        actual_move = round((after / before - 1) * 100, 3)
        price = after
        stored_event = {
            **event,
            "priceBefore": round(before, 2),
            "priceAfter": round(after, 2),
            "movePct": actual_move,
            "verified": True,
        }
        stored.append(stored_event)
        trend = trend[-17:] + [after]

    if not stored:
        return result, 0
    result["priceEvents"] = (prior_events + stored)[-MAX_PRICE_EVENTS:]
    result["previousMarketPrice"] = round(old_price, 2)
    result["marketPrice"] = round(price, 2)
    cumulative = round((price / old_price - 1) * 100, 2)
    result["dailyChange"] = cumulative
    result["hourlyChangePct"] = cumulative
    result["trend"] = [round(item, 2) for item in trend[-18:]]
    latest = stored[-1]
    result["lastPriceEventAt"] = latest.get("startedAt")
    result["lastPriceEvent"] = latest.get("name")
    result["lastPriceEventId"] = latest.get("eventKey")
    result["lastEventMovePct"] = latest.get("movePct")
    result["lastEventType"] = latest.get("eventType")
    result["lastEventSource"] = latest.get("provider")
    result["lastPriceRefreshAt"] = iso(utc_now())
    result["priceExplanation"] = explanation_for(latest, number(latest.get("movePct")), price)
    return result, len(stored)


def release_event(record: dict[str, Any], candidate: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    rgid = str(match["id"])
    title = str(match.get("title") or candidate.get("title") or "New release")
    when = match.get("date") if isinstance(match.get("date"), datetime) else candidate["date"]
    return {
        "eventKey": f"musicbrainz:{rgid}",
        "eventId": rgid,
        "eventType": "music-release",
        "provider": "MusicBrainz + Wikidata",
        "sourceUrl": f"https://musicbrainz.org/release-group/{rgid}",
        "secondarySourceUrl": f"https://www.wikidata.org/wiki/{candidate['workQid']}",
        "name": f"{title} — {match.get('primaryType') or 'Release'}",
        "startedAt": iso(when),
        "releaseType": str(match.get("primaryType") or "Other"),
        "workQid": candidate["workQid"],
        "artist": record.get("name"),
    }


def actor_release_event(record: dict[str, Any], candidate: dict[str, Any], upcoming: bool = False) -> dict[str, Any]:
    kind = "actor-upcoming-project" if upcoming else "actor-release"
    prefix = "Upcoming project" if upcoming else "Released project"
    return {
        "eventKey": f"wikidata:{kind}:{candidate['personQid']}:{candidate['workQid']}:{candidate['date'].date().isoformat()}",
        "eventId": candidate["workQid"],
        "eventType": kind,
        "provider": "Wikidata",
        "sourceUrl": f"https://www.wikidata.org/wiki/{candidate['workQid']}",
        "name": f"{prefix}: {candidate['title']}",
        "startedAt": iso(candidate["date"]),
        "workQid": candidate["workQid"],
        "artist": record.get("name"),
    }


def claim_event(record: dict[str, Any], qid: str, claim_qid: str, label: str, event_type: str, when: datetime) -> dict[str, Any]:
    return {
        "eventKey": f"wikidata:{event_type}:{qid}:{claim_qid}",
        "eventId": claim_qid,
        "eventType": event_type,
        "provider": "Wikidata",
        "sourceUrl": f"https://www.wikidata.org/wiki/{claim_qid}",
        "name": f"{'Award' if event_type == 'award' else 'Nomination'}: {label}",
        "startedAt": iso(when),
        "claimQid": claim_qid,
        "artist": record.get("name"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lookback-days", type=int, default=21)
    parser.add_argument("--bootstrap-lookback-days", type=int, default=7)
    parser.add_argument("--lookahead-days", type=int, default=365)
    parser.add_argument("--minimum-interval-hours", type=float, default=6.0)
    parser.add_argument("--wikidata-batch-size", type=int, default=40)
    parser.add_argument("--request-timeout", type=float, default=25.0)
    parser.add_argument("--max-music-confirmations", type=int, default=80)
    parser.add_argument("--max-events", type=int, default=250)
    parser.add_argument("--max-events-per-record", type=int, default=3)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    manifest = {}
    if args.manifest.exists():
        try:
            loaded = json.loads(args.manifest.read_text(encoding="utf-8"))
            manifest = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            manifest = {}

    now = utc_now()
    previous_run = parse_time(manifest.get("lastSuccessfulAt"))
    if previous_run and (now - previous_run).total_seconds() < max(0.0, args.minimum_interval_hours) * 3600:
        print(f"Non-athlete event refresh skipped; last successful scan was {manifest.get('lastSuccessfulAt')}.")
        return 0

    qid_to_indexes: dict[str, list[int]] = defaultdict(list)
    music_qids: list[str] = []
    actor_qids: list[str] = []
    for index, record in enumerate(records):
        category = str(record.get("primaryCategory") or "")
        if category not in {"Music", "Actor"}:
            continue
        qid = qid_for(record)
        if not qid:
            continue
        qid_to_indexes[qid].append(index)
        if category == "Music":
            music_qids.append(qid)
        else:
            actor_qids.append(qid)
    music_qids = sorted(set(music_qids))
    actor_qids = sorted(set(actor_qids))

    session = make_session()
    entities, errors = fetch_entities(session, sorted(set(music_qids + actor_qids)), args.request_timeout)
    mbids_by_qid: dict[str, str] = {}
    for qid in music_qids:
        record = records[qid_to_indexes[qid][0]]
        mbid = existing_mbid(record)
        if not mbid:
            values = claim_strings(entities.get(qid, {}), "P434")
            mbid = values[0].lower() if values and re.fullmatch(r"[0-9a-fA-F-]{36}", values[0]) else ""
        if mbid:
            mbids_by_qid[qid] = mbid

    lookback_days = args.bootstrap_lookback_days if previous_run is None else args.lookback_days
    recent_start = now - timedelta(days=max(1, lookback_days))
    recent_music, music_errors = fetch_works(
        session, "Music", music_qids, recent_start, now,
        max(1, args.wikidata_batch_size), args.request_timeout,
    )
    recent_actor, actor_errors = fetch_works(
        session, "Actor", actor_qids, recent_start, now,
        max(1, args.wikidata_batch_size), args.request_timeout,
    )
    future_actor, future_errors = fetch_works(
        session, "Actor", actor_qids, now + timedelta(days=1), now + timedelta(days=max(2, args.lookahead_days)),
        max(1, args.wikidata_batch_size), args.request_timeout,
    )
    errors.extend(music_errors + actor_errors + future_errors)

    seen_keys = [str(key) for key in manifest.get("seenEventKeys", []) if key]
    seen = set(seen_keys)
    prior_snapshots = manifest.get("claimSnapshots") if isinstance(manifest.get("claimSnapshots"), dict) else {}
    next_snapshots: dict[str, dict[str, list[str]]] = {}
    new_claims: list[tuple[str, str, str]] = []
    for qid in sorted(set(music_qids + actor_qids)):
        entity = entities.get(qid, {})
        awards = sorted(claim_item_ids(entity, "P166"))
        nominations = sorted(claim_item_ids(entity, "P1411"))
        next_snapshots[qid] = {"awards": awards, "nominations": nominations}
        prior = prior_snapshots.get(qid) if isinstance(prior_snapshots.get(qid), dict) else None
        if prior is None:
            continue
        old_awards = {str(item) for item in prior.get("awards", [])}
        old_nominations = {str(item) for item in prior.get("nominations", [])}
        new_claims.extend((qid, item, "award") for item in awards if item not in old_awards)
        new_claims.extend((qid, item, "nomination") for item in nominations if item not in old_nominations)
    claim_labels, label_errors = fetch_labels(session, {item for _qid, item, _kind in new_claims}, args.request_timeout)
    errors.extend(label_errors)

    events_by_index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    event_budget = max(0, args.max_events)
    confirmations = 0
    last_mb_request = 0.0

    # Music releases: require two-source confirmation.
    music_candidates: list[tuple[str, dict[str, Any]]] = []
    for qid, items in recent_music.items():
        for candidate in items:
            music_candidates.append((qid, candidate))
    music_candidates.sort(key=lambda pair: pair[1]["date"], reverse=True)
    for qid, candidate in music_candidates:
        if event_budget <= 0 or confirmations >= max(0, args.max_music_confirmations):
            break
        mbid = mbids_by_qid.get(qid)
        if not mbid:
            continue
        elapsed = time.time() - last_mb_request
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        try:
            match = musicbrainz_release_match(session, mbid, candidate, args.request_timeout)
            last_mb_request = time.time()
            confirmations += 1
        except Exception as exc:  # noqa: BLE001
            last_mb_request = time.time()
            confirmations += 1
            errors.append(f"MusicBrainz {qid}/{candidate['workQid']}: {type(exc).__name__}: {exc}")
            continue
        if not match:
            continue
        for index in qid_to_indexes.get(qid, []):
            if records[index].get("primaryCategory") != "Music":
                continue
            event = release_event(records[index], candidate, match)
            if event["eventKey"] in seen:
                continue
            events_by_index[index].append(event)
            seen.add(event["eventKey"])
            event_budget -= 1
            if event_budget <= 0:
                break

    # Actor releases: the publication date + cast relation is the supported event.
    for qid, items in recent_actor.items():
        for candidate in items:
            if event_budget <= 0:
                break
            for index in qid_to_indexes.get(qid, []):
                if records[index].get("primaryCategory") != "Actor":
                    continue
                event = actor_release_event(records[index], candidate, upcoming=False)
                if event["eventKey"] in seen:
                    continue
                events_by_index[index].append(event)
                seen.add(event["eventKey"])
                event_budget -= 1
                if event_budget <= 0:
                    break

    # Future projects are baseline-only on the first scan. Later newly observed
    # cast+future-date evidence creates a small project event.
    for qid, items in future_actor.items():
        for candidate in items:
            for index in qid_to_indexes.get(qid, []):
                if records[index].get("primaryCategory") != "Actor":
                    continue
                event = actor_release_event(records[index], candidate, upcoming=True)
                if event["eventKey"] in seen:
                    continue
                seen.add(event["eventKey"])
                if previous_run is not None and event_budget > 0:
                    events_by_index[index].append(event)
                    event_budget -= 1

    # Newly appearing award/nominations are evented only after a prior snapshot.
    for qid, claim_qid, event_type in new_claims:
        if event_budget <= 0:
            break
        label = claim_labels.get(claim_qid, claim_qid)
        for index in qid_to_indexes.get(qid, []):
            category = str(records[index].get("primaryCategory") or "")
            if category not in {"Music", "Actor"}:
                continue
            event = claim_event(records[index], qid, claim_qid, label, event_type, now)
            if event["eventKey"] in seen:
                continue
            events_by_index[index].append(event)
            seen.add(event["eventKey"])
            event_budget -= 1
            if event_budget <= 0:
                break

    changed_records = 0
    applied_events = 0
    largest_moves: list[dict[str, Any]] = []
    for index, pending in events_by_index.items():
        pending = sorted(pending, key=lambda item: str(item.get("startedAt") or ""))[-max(1, args.max_events_per_record):]
        updated, added = apply_events(records[index], pending)
        if added:
            changed_records += 1
            applied_events += added
            records[index] = updated
            for event in updated.get("priceEvents", [])[-added:]:
                largest_moves.append({
                    "name": updated.get("name"),
                    "category": updated.get("primaryCategory"),
                    "event": event.get("name"),
                    "eventType": event.get("eventType"),
                    "movePct": event.get("movePct"),
                    "priceAfter": event.get("priceAfter"),
                })

    if errors and not args.allow_source_errors and not applied_events:
        raise RuntimeError("Non-athlete event sources failed without producing usable events: " + "; ".join(errors[-8:]))

    args.catalog.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    manifest_out = {
        "version": MODEL_VERSION,
        "generatedAt": iso(now),
        "lastSuccessfulAt": iso(now),
        "previousSuccessfulAt": manifest.get("lastSuccessfulAt"),
        "lookbackDays": lookback_days,
        "lookaheadDays": args.lookahead_days,
        "musicProfilesWithWikidataIdentity": len(music_qids),
        "musicProfilesWithMusicBrainzIdentity": len(mbids_by_qid),
        "actorProfilesWithWikidataIdentity": len(actor_qids),
        "musicBrainzConfirmationsAttempted": confirmations,
        "recordsChanged": changed_records,
        "eventsApplied": applied_events,
        "seenEventKeys": list(dict.fromkeys([*seen_keys, *sorted(seen)]))[-MAX_SAVED_EVENT_KEYS:],
        "claimSnapshots": next_snapshots,
        "sourceErrorCount": len(errors),
        "sourceErrors": errors[-100:],
        "largestMoves": sorted(largest_moves, key=lambda item: abs(number(item.get("movePct"))), reverse=True)[:50],
        "policy": {
            "maximumSingleEventMovePct": None,
            "movementPolicy": "verified evidence versus expectations; no fixed percentage ceiling",
            "musicReleaseBases": MUSIC_RELEASE_BASE,
            "actorReleaseBasePct": EVENT_BASE["actor-release"],
            "actorUpcomingProjectBasePct": EVENT_BASE["actor-upcoming-project"],
            "awardBasePct": EVENT_BASE["award"],
            "nominationBasePct": EVENT_BASE["nomination"],
            "noEventNoMove": True,
            "musicRequiresMusicBrainzConfirmation": True,
        },
    }
    args.manifest.write_text(json.dumps(manifest_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Music/Actor event refresh: {applied_events:,} verified events changed {changed_records:,} records; "
        f"MusicBrainz checks: {confirmations:,}; source warnings: {len(errors):,}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
