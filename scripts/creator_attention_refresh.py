#!/usr/bin/env python3
"""Verified Creator attention events for TalentX.

Creators do not have a universal box-score or release database comparable to
sports, MusicBrainz, or film box office. This adapter therefore uses a narrower
claim: verified English-Wikipedia audience attention. It resolves the curated
Creator identity to a non-disambiguation Wikipedia article, reads Wikimedia's
daily pageview series, and emits expectation-scaled attention-outcome events only when the
recent window materially differs from its prior baseline.

Live mode can move today's simulated market price. History-only mode generates
sparse 1Y ``historicalBackfill`` events and reconstructs the chart backward from
the unchanged current price. Pageviews are explicitly treated as attention, not
content quality, revenue, followers, views on a creator platform, or sentiment.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from non_athlete_outcome_refresh import apply_outcome_events, record_multiplier

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIMEDIA_PAGEVIEWS = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia.org/all-access/all-agents/{title}/daily/{start}/{end}"
)
MAX_PRICE_EVENTS = 2500
MAX_HISTORY_POINTS = 5000
TARGETS = {
    "cold": -0.50,
    "cool": -0.25,
    "warm": 0.25,
    "hot": 0.50,
    "breakout": 0.75,
}


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path, [])
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "TalentX-Creator-Attention/1.0 (+https://github.com/rossad213/TalentX)",
        "Accept": "application/json",
    })
    return session


def title_matches_name(title: str, name: str) -> bool:
    base = re.sub(r"\s*\([^)]*\)\s*$", "", str(title or "")).strip()
    return bool(base and norm(base) == norm(name))


def resolve_wikipedia_title(session: requests.Session, name: str, timeout: float) -> tuple[str, int | None] | None:
    response = session.get(
        WIKIPEDIA_API,
        params={
            "action": "query",
            "list": "search",
            "srsearch": f'"{name}"',
            "srlimit": 8,
            "format": "json",
            "utf8": 1,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    results = response.json().get("query", {}).get("search", [])
    candidates = [
        str(item.get("title") or "").strip()
        for item in results
        if isinstance(item, dict) and title_matches_name(str(item.get("title") or ""), name)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda title: (norm(title) != norm(name), len(title)))
    for title in candidates:
        check = session.get(
            WIKIPEDIA_API,
            params={
                "action": "query",
                "prop": "pageprops",
                "titles": title,
                "format": "json",
            },
            timeout=timeout,
        )
        check.raise_for_status()
        pages = check.json().get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if not isinstance(page, dict) or page.get("missing") is not None:
                continue
            pageprops = page.get("pageprops") if isinstance(page.get("pageprops"), dict) else {}
            if "disambiguation" in pageprops:
                continue
            try:
                numeric_id = int(page_id)
            except (TypeError, ValueError):
                numeric_id = None
            return str(page.get("title") or title), numeric_id
    return None


def pageview_series(
    session: requests.Session,
    title: str,
    start: datetime,
    end: datetime,
    timeout: float,
) -> tuple[list[tuple[datetime, int]], str]:
    encoded = quote(title.replace(" ", "_"), safe="")
    url = WIKIMEDIA_PAGEVIEWS.format(
        title=encoded,
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
    )
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    output: list[tuple[datetime, int]] = []
    for item in response.json().get("items", []):
        if not isinstance(item, dict):
            continue
        stamp = str(item.get("timestamp") or "")
        try:
            when = datetime.strptime(stamp[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        output.append((when, max(0, int(number(item.get("views"), 0)))))
    output.sort(key=lambda item: item[0])
    return output, url


def attention_target(ratio: float) -> tuple[str, float] | None:
    if ratio >= 1.45:
        bucket = "breakout" if ratio >= 3.0 else "hot" if ratio >= 2.0 else "warm"
        return bucket, 0.45 * math.log2(ratio)
    if 0 < ratio <= 0.65:
        bucket = "cold" if ratio <= 0.40 else "cool"
        return bucket, -0.45 * math.log2(1.0 / ratio)
    return None


def window_ratio(points: list[tuple[datetime, int]], end_day: datetime) -> tuple[float, float, float] | None:
    day = end_day.replace(hour=0, minute=0, second=0, microsecond=0)
    outcome_start = day - timedelta(days=6)
    baseline_end = outcome_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=20)
    baseline = [views for when, views in points if baseline_start <= when <= baseline_end]
    outcome = [views for when, views in points if outcome_start <= when <= day]
    if len(baseline) < 14 or len(outcome) < 5:
        return None
    baseline_avg = sum(baseline) / len(baseline)
    outcome_avg = sum(outcome) / len(outcome)
    if baseline_avg <= 0:
        return None
    return outcome_avg / baseline_avg, baseline_avg, outcome_avg


def creator_event(
    record: dict[str, Any],
    title: str,
    source_url: str,
    end_day: datetime,
    bucket: str,
    target: float,
    ratio: float,
    baseline: float,
    outcome: float,
    *,
    historical: bool,
) -> dict[str, Any]:
    date = end_day.date().isoformat()
    key = f"wikimedia-creator-attention:{record.get('id') or norm(record.get('name'))}:{date}:{bucket}"
    event = {
        "eventKey": key,
        "eventId": key,
        "eventType": "creator-attention-outcome",
        "provider": "Wikimedia Pageviews",
        "sourceUrl": source_url,
        "name": f"Audience attention {bucket}: {title}",
        "startedAt": f"{date}T00:00:00Z",
        "targetOutcomeMovePct": round(target, 3),
        "attentionBucket": bucket,
        "attentionRatio": round(ratio, 3),
        "baselineDailyViews": round(baseline, 1),
        "outcomeDailyViews": round(outcome, 1),
        "wikipediaTitle": title,
        "verified": True,
        "creatorAttentionModel": "wikimedia-attention-v1",
    }
    if historical:
        event["historicalBackfill"] = True
        event["movePct"] = round(target * record_multiplier(record), 3)
    return event


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_time(event: dict[str, Any]) -> str:
    return str(event.get("startedAt") or event.get("time") or event.get("date") or "").strip()


def move_for(event: dict[str, Any]) -> float:
    explicit = number(event.get("movePct"), float("nan"))
    if math.isfinite(explicit):
        return explicit
    before = number(event.get("priceBefore"), 0.0)
    after = number(event.get("priceAfter"), 0.0)
    if before > 0 and after > 0:
        return (after / before - 1.0) * 100.0
    return 0.0


def merge_events(record: dict[str, Any], generated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict):
            continue
        key = event_key(event)
        if key:
            by_key[key] = dict(event)
        else:
            anonymous.append(dict(event))
    for event in generated:
        key = event_key(event)
        if key and key not in by_key:
            by_key[key] = dict(event)
    combined = [*anonymous, *by_key.values()]
    combined = [event for event in combined if event_time(event)]
    combined.sort(key=event_time)
    return combined[-MAX_PRICE_EVENTS:]


def reconstruct_chain(current_price: float, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    after = max(0.01, current_price)
    rebuilt: list[dict[str, Any]] = []
    for event in reversed(events):
        result = dict(event)
        move = move_for(result)
        if abs(move) < 0.001:
            rebuilt.append(result)
            continue
        denominator = 1.0 + move / 100.0
        before = after / denominator if denominator > 0.0001 else after
        before = max(0.01, round(before, 2))
        after_rounded = max(0.01, round(after, 2))
        result["priceBefore"] = before
        result["priceAfter"] = after_rounded
        result["movePct"] = round((after_rounded / before - 1.0) * 100.0, 3)
        rebuilt.append(result)
        after = before
    rebuilt.reverse()
    return rebuilt


def history_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for event in events:
        key = event_key(event)
        when = parse_time(event_time(event))
        before = number(event.get("priceBefore"), 0.0)
        after = number(event.get("priceAfter"), 0.0)
        if not key or when is None or before <= 0 or after <= 0:
            continue
        points.extend([
            {
                "time": iso(when - timedelta(seconds=1)),
                "price": round(before, 2),
                "eventId": key,
                "label": str(event.get("name") or "Verified Creator attention event"),
                "phase": "open",
                "source": "verified-creator-attention-history",
                "historyType": "verified",
                "eventType": str(event.get("eventType") or "creator-attention-outcome"),
                "movePct": event.get("movePct"),
            },
            {
                "time": iso(when),
                "price": round(after, 2),
                "eventId": key,
                "label": str(event.get("name") or "Verified Creator attention event"),
                "phase": "close",
                "source": "verified-creator-attention-history",
                "historyType": "verified",
                "eventType": str(event.get("eventType") or "creator-attention-outcome"),
                "movePct": event.get("movePct"),
            },
        ])
    points.sort(key=lambda item: str(item.get("time") or ""))
    return points[-MAX_HISTORY_POINTS:]


def historical_events(
    record: dict[str, Any],
    title: str,
    source_url: str,
    points: list[tuple[datetime, int]],
) -> list[dict[str, Any]]:
    if len(points) < 28:
        return []
    output: list[dict[str, Any]] = []
    last_bucket = ""
    last_event_day: datetime | None = None
    first_day = points[0][0] + timedelta(days=27)
    end_day = points[-1][0]
    day = first_day
    while day <= end_day:
        evidence = window_ratio(points, day)
        if evidence:
            ratio, baseline, outcome = evidence
            target = attention_target(ratio)
            if target:
                bucket, move = target
                enough_time = last_event_day is None or (day - last_event_day).days >= 21
                if bucket != last_bucket or enough_time:
                    output.append(creator_event(
                        record,
                        title,
                        source_url,
                        day,
                        bucket,
                        move,
                        ratio,
                        baseline,
                        outcome,
                        historical=True,
                    ))
                    last_bucket = bucket
                    last_event_day = day
        day += timedelta(days=7)
    return output[-24:]


def recent_attention_event(
    record: dict[str, Any],
    title: str,
    source_url: str,
    points: list[tuple[datetime, int]],
) -> dict[str, Any] | None:
    if not points:
        return None
    end_day = points[-1][0]
    evidence = window_ratio(points, end_day)
    if not evidence:
        return None
    ratio, baseline, outcome = evidence
    target = attention_target(ratio)
    if not target:
        return None
    bucket, move = target

    prior_attention = [
        event for event in record.get("priceEvents", [])
        if isinstance(event, dict) and event.get("eventType") == "creator-attention-outcome"
    ] if isinstance(record.get("priceEvents"), list) else []
    if prior_attention:
        prior_attention.sort(key=event_time)
        latest = prior_attention[-1]
        latest_time = parse_time(event_time(latest))
        if latest.get("attentionBucket") == bucket and latest_time and (end_day - latest_time).days < 7:
            return None

    return creator_event(
        record,
        title,
        source_url,
        end_day,
        bucket,
        move,
        ratio,
        baseline,
        outcome,
        historical=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--history-only", action="store_true")
    parser.add_argument("--max-records", type=int, default=0)
    args = parser.parse_args()

    records = load_records(args.catalog)
    manifest = load_json(args.manifest, {})
    if not isinstance(manifest, dict):
        manifest = {}
    identities = manifest.get("identities") if isinstance(manifest.get("identities"), dict) else {}
    session = make_session()
    now = datetime.now(timezone.utc)
    end = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=max(28, args.lookback_days) - 1)

    output: list[dict[str, Any]] = []
    checked = 0
    resolved = 0
    events_added = 0
    warnings = 0

    for record in records:
        if str(record.get("primaryCategory") or "") != "Creator":
            output.append(dict(record))
            continue
        if args.max_records > 0 and checked >= args.max_records:
            output.append(dict(record))
            continue
        checked += 1
        result = dict(record)
        identity_key = str(record.get("id") or norm(record.get("name")))
        cached = identities.get(identity_key) if isinstance(identities.get(identity_key), dict) else {}
        title = str(cached.get("title") or "").strip()
        page_id = cached.get("pageId")
        if not title:
            try:
                resolved_identity = resolve_wikipedia_title(session, str(record.get("name") or ""), args.request_timeout)
            except Exception as exc:  # noqa: BLE001
                warnings += 1
                print(f"Creator identity warning for {record.get('name')}: {type(exc).__name__}: {exc}")
                output.append(result)
                continue
            if not resolved_identity:
                output.append(result)
                continue
            title, page_id = resolved_identity
            identities[identity_key] = {
                "title": title,
                "pageId": page_id,
                "resolvedAt": iso(now),
            }
        resolved += 1

        try:
            points, source_url = pageview_series(session, title, start, end, args.request_timeout)
        except Exception as exc:  # noqa: BLE001
            warnings += 1
            print(f"Creator pageview warning for {record.get('name')}: {type(exc).__name__}: {exc}")
            output.append(result)
            continue

        if args.history_only:
            generated = historical_events(result, title, source_url, points)
            before_keys = {event_key(event) for event in result.get("priceEvents", []) if isinstance(event, dict)} if isinstance(result.get("priceEvents"), list) else set()
            merged = merge_events(result, generated)
            after_keys = {event_key(event) for event in merged if event_key(event)}
            added = len(after_keys - before_keys)
            if added:
                current = max(0.01, number(result.get("marketPrice"), 0.01))
                rebuilt = reconstruct_chain(current, merged)
                result["priceEvents"] = rebuilt
                result["priceHistory"] = history_from_events(rebuilt)
                result["priceHistoryStatus"] = "verified-event-backfill"
                result["priceHistoryBackfilledAt"] = iso(now)
                result["priceHistoryBackfillDays"] = max(int(number(result.get("priceHistoryBackfillDays"), 0)), args.lookback_days)
                result["priceHistoryBackfillModel"] = "wikimedia-creator-attention-history-v1"
                events_added += added
        else:
            event = recent_attention_event(result, title, source_url, points)
            if event:
                result, added = apply_outcome_events(result, [event])
                events_added += added
                if added:
                    result["creatorAttentionVerifiedAt"] = iso(now)
                    result["creatorAttentionSource"] = source_url
                    result["creatorWikipediaTitle"] = title
        output.append(result)

    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    manifest.update({
        "version": "wikimedia-creator-attention-v1",
        "updatedAt": iso(now),
        "historyOnly": bool(args.history_only),
        "lookbackDays": args.lookback_days,
        "recordsChecked": checked,
        "identitiesResolved": resolved,
        "eventsAdded": events_added,
        "sourceWarnings": warnings,
        "identities": identities,
    })
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    mode = "historical" if args.history_only else "live"
    print(f"Creator {mode} attention: checked {checked:,}, resolved {resolved:,}, added {events_added:,} events, warnings {warnings:,}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
