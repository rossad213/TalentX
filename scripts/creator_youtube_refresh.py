#!/usr/bin/env python3
"""Direct YouTube performance outcomes for verified TalentX Creators.

Creator records are mapped conservatively to YouTube channel IDs through their
Wikidata identity (P2397).  The official YouTube channel RSS feed supplies the
latest video IDs and publication times.  Public watch-page metadata supplies
verified view counts when reachable.

Pricing is based on *view growth between TalentX snapshots*, not lifetime views.
The newest recent video's growth rate is compared with the same channel's other
recent videos over the same observation interval.  This avoids comparing a new
upload with years of accumulated views.  If YouTube blocks or omits the public
metadata, the adapter fails closed and makes no move.  No hard movement ceiling
is used.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from non_athlete_event_refresh import iso, number, parse_time
from non_athlete_outcome_refresh import apply_outcome_events
from prepare_creator_wikipedia_identities import creator_qid

ROOT = Path(__file__).resolve().parents[1]
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YOUTUBE_WATCH = "https://www.youtube.com/watch?v={video_id}"
DEFAULT_CATALOG = ROOT / "data" / "market" / "creators.json"
DEFAULT_ATTENTION_MANIFEST = ROOT / "data" / "creator_attention_manifest.json"
DEFAULT_MANIFEST = ROOT / "data" / "creator_youtube_manifest.json"
MODEL_VERSION = "creator-youtube-performance-v1"
MAX_SNAPSHOTS = 12000
QID_RE = re.compile(r"^Q\d+$", re.I)
CHANNEL_RE = re.compile(r"^UC[A-Za-z0-9_-]{20,}$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; TalentX-Creator-YouTube/1.0; +https://github.com/rossad213/TalentX)",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


def identity_qid(record: dict[str, Any], attention_manifest: dict[str, Any]) -> str:
    direct = creator_qid(record)
    if direct:
        return direct
    identities = attention_manifest.get("identities") if isinstance(attention_manifest.get("identities"), dict) else {}
    identity = identities.get(str(record.get("id") or "")) if isinstance(identities.get(str(record.get("id") or "")), dict) else {}
    qid = str(identity.get("wikidataQid") or "").strip().upper()
    return qid if QID_RE.fullmatch(qid) else ""


def cached_wikipedia_title(record: dict[str, Any], attention_manifest: dict[str, Any]) -> str:
    identities = attention_manifest.get("identities") if isinstance(attention_manifest.get("identities"), dict) else {}
    identity = identities.get(str(record.get("id") or "")) if isinstance(identities.get(str(record.get("id") or "")), dict) else {}
    return str(identity.get("title") or "").strip()


def fetch_wikipedia_qids(session: requests.Session, titles: list[str], timeout: float) -> tuple[dict[str, str], list[str]]:
    output: dict[str, str] = {}
    warnings: list[str] = []
    unique = [title for title in dict.fromkeys(titles) if title]
    for offset in range(0, len(unique), 40):
        batch = unique[offset : offset + 40]
        try:
            response = session.get(
                WIKIPEDIA_API,
                params={"action": "query", "prop": "pageprops", "titles": "|".join(batch), "format": "json", "formatversion": 2},
                timeout=timeout,
            )
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", [])
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Wikipedia pageprops batch {offset // 40 + 1}: {type(exc).__name__}: {exc}")
            continue
        for page in pages if isinstance(pages, list) else []:
            if not isinstance(page, dict) or page.get("missing") is not None:
                continue
            title = str(page.get("title") or "").strip()
            props = page.get("pageprops") if isinstance(page.get("pageprops"), dict) else {}
            qid = str(props.get("wikibase_item") or "").strip().upper()
            if title and QID_RE.fullmatch(qid):
                output[title] = qid
    return output, warnings


def claim_strings(entity: dict[str, Any], prop: str) -> list[str]:
    output: list[str] = []
    claims = entity.get("claims") if isinstance(entity.get("claims"), dict) else {}
    for claim in claims.get(prop, []) if isinstance(claims.get(prop), list) else []:
        if not isinstance(claim, dict):
            continue
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, str) and value.strip():
            output.append(value.strip())
    return output


def fetch_youtube_channel_ids(session: requests.Session, qids: list[str], timeout: float) -> tuple[dict[str, str], list[str]]:
    output: dict[str, str] = {}
    warnings: list[str] = []
    ordered = sorted({qid.upper() for qid in qids if QID_RE.fullmatch(qid)})
    for offset in range(0, len(ordered), 50):
        batch = ordered[offset : offset + 50]
        try:
            response = session.get(
                WIKIDATA_API,
                params={"action": "wbgetentities", "ids": "|".join(batch), "props": "claims", "format": "json"},
                timeout=timeout,
            )
            response.raise_for_status()
            entities = response.json().get("entities", {})
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Wikidata YouTube batch {offset // 50 + 1}: {type(exc).__name__}: {exc}")
            continue
        for qid, entity in entities.items() if isinstance(entities, dict) else []:
            if not isinstance(entity, dict):
                continue
            channels = [value for value in claim_strings(entity, "P2397") if CHANNEL_RE.fullmatch(value)]
            if channels:
                output[str(qid).upper()] = channels[0]
    return output, warnings


def parse_youtube_feed(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    output: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        video_id = str(entry.findtext("yt:videoId", default="", namespaces=ns)).strip()
        title = str(entry.findtext("atom:title", default="", namespaces=ns)).strip()
        published = parse_time(entry.findtext("atom:published", default="", namespaces=ns))
        channel_id = str(entry.findtext("yt:channelId", default="", namespaces=ns)).strip()
        if video_id and title and published:
            output.append({"videoId": video_id, "title": title, "publishedAt": published, "channelId": channel_id})
    output.sort(key=lambda item: item["publishedAt"], reverse=True)
    return output


def extract_json_after_marker(text: str, marker: str) -> dict[str, Any] | None:
    position = text.find(marker)
    if position < 0:
        return None
    start = text.find("{", position + len(marker))
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def parse_watch_page(html_text: str, expected_video_id: str, expected_channel_id: str = "") -> dict[str, Any] | None:
    payload = None
    for marker in ("ytInitialPlayerResponse =", "var ytInitialPlayerResponse =", '"ytInitialPlayerResponse":'):
        payload = extract_json_after_marker(html_text, marker)
        if payload:
            break
    details = payload.get("videoDetails") if isinstance(payload, dict) and isinstance(payload.get("videoDetails"), dict) else {}
    video_id = str(details.get("videoId") or "")
    channel_id = str(details.get("channelId") or "")
    views = number(details.get("viewCount"), float("nan"))
    if video_id == expected_video_id and math.isfinite(views) and views >= 0:
        if expected_channel_id and channel_id and channel_id != expected_channel_id:
            return None
        return {
            "videoId": video_id,
            "channelId": channel_id or expected_channel_id,
            "title": str(details.get("title") or ""),
            "views": int(views),
        }
    # Best-effort fallback for layouts where the player object is serialized differently.
    match = re.search(r'"videoId"\s*:\s*"' + re.escape(expected_video_id) + r'".{0,8000}?"viewCount"\s*:\s*"?(\d+)"?', html_text, flags=re.S)
    if match:
        return {"videoId": expected_video_id, "channelId": expected_channel_id, "title": "", "views": int(match.group(1))}
    return None


def fetch_video_stats(
    session: requests.Session,
    channel_id: str,
    feed_entries: list[dict[str, Any]],
    timeout: float,
    max_videos: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    output: list[dict[str, Any]] = []
    warnings: list[str] = []
    for entry in feed_entries[: max(1, max_videos)]:
        video_id = str(entry.get("videoId") or "")
        try:
            response = session.get(YOUTUBE_WATCH.format(video_id=quote(video_id)), params={"hl": "en", "gl": "US"}, timeout=timeout)
            response.raise_for_status()
            parsed = parse_watch_page(response.text, video_id, channel_id)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"YouTube video {video_id}: {type(exc).__name__}: {exc}")
            continue
        if parsed:
            output.append({**entry, **parsed})
        else:
            warnings.append(f"YouTube video {video_id}: public view count unavailable")
    return output, warnings


def growth_per_day(previous: dict[str, Any], current_views: int, checked_at: datetime) -> float | None:
    prior_views = number(previous.get("views"), float("nan"))
    prior_time = parse_time(previous.get("checkedAt"))
    if not math.isfinite(prior_views) or prior_time is None or current_views < prior_views:
        return None
    elapsed_days = (checked_at - prior_time).total_seconds() / 86400.0
    if elapsed_days < (4.0 / 24.0):
        return None
    return (current_views - prior_views) / elapsed_days


def performance_target(ratio: float) -> tuple[str, float] | None:
    if ratio >= 1.60:
        bucket = "breakout" if ratio >= 5.0 else "hot" if ratio >= 2.5 else "warm"
        return bucket, 0.55 * math.log2(ratio)
    if 0 < ratio <= 0.50:
        bucket = "cold" if ratio <= 0.25 else "cool"
        return bucket, -0.50 * math.log2(1.0 / ratio)
    return None


def incremental_target(previous_target: float, target: float) -> float:
    """Add only newly verified strength/weakness; do not undo a past outcome."""
    if previous_target == 0:
        return target
    if previous_target > 0 and target > 0:
        return max(0.0, target - previous_target)
    if previous_target < 0 and target < 0:
        return min(0.0, target - previous_target)
    return 0.0


def youtube_event(
    record: dict[str, Any],
    channel_id: str,
    video: dict[str, Any],
    checked_at: datetime,
    bucket: str,
    target_delta: float,
    ratio: float,
    recent_growth: float,
    baseline_growth: float,
) -> dict[str, Any]:
    video_id = str(video.get("videoId") or "")
    key = f"youtube-performance:{record.get('id')}:{video_id}:{checked_at.date().isoformat()}:{bucket}"
    return {
        "eventKey": key,
        "eventId": key,
        "eventType": "creator-youtube-outcome",
        "provider": "YouTube public performance",
        "sourceUrl": YOUTUBE_WATCH.format(video_id=video_id),
        "name": f"YouTube {bucket}: {video.get('title') or video_id}",
        "startedAt": iso(checked_at),
        "targetOutcomeMovePct": round(target_delta, 4),
        "creator": record.get("name"),
        "youtubeChannelId": channel_id,
        "youtubeVideoId": video_id,
        "youtubeViews": int(number(video.get("views"), 0)),
        "viewGrowthRatio": round(ratio, 4),
        "recentViewsPerDay": round(recent_growth, 2),
        "baselineViewsPerDay": round(baseline_growth, 2),
        "performanceBucket": bucket,
        "videoPublishedAt": iso(video["publishedAt"]) if isinstance(video.get("publishedAt"), datetime) else None,
    }


def explanation(event: dict[str, Any], price: float) -> dict[str, Any]:
    move = number(event.get("movePct"), 0)
    ratio = number(event.get("viewGrowthRatio"), 0)
    return {
        "version": MODEL_VERSION,
        "eventId": event.get("eventKey"),
        "event": event.get("name"),
        "eventAt": event.get("startedAt"),
        "headline": "Direct YouTube performance outcome",
        "summary": [
            f"Recent verified view growth was {ratio:.2f}× the channel's comparison-video baseline.",
            "TalentX uses view growth between its own public snapshots, not lifetime views, likes, sentiment, or estimated revenue.",
        ],
        "direction": "increased" if move > 0 else "decreased" if move < 0 else "held steady",
        "finalMovePct": round(move, 2),
        "recordedMarketPrice": round(price, 2),
        "pricingMode": "Verified direct Creator outcome; event market price preserved",
        "source": event.get("provider"),
        "sourceUrl": event.get("sourceUrl"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--attention-manifest", type=Path, default=DEFAULT_ATTENTION_MANIFEST)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lookback-days", type=int, default=21)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--max-records", type=int, default=120)
    parser.add_argument("--max-videos-per-record", type=int, default=5)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    payload = load_json(args.catalog, [])
    if not isinstance(payload, list) or not payload:
        raise SystemExit(f"{args.catalog} must contain a non-empty array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    attention_manifest = load_json(args.attention_manifest, {})
    attention_manifest = attention_manifest if isinstance(attention_manifest, dict) else {}
    manifest = load_json(args.manifest, {})
    manifest = manifest if isinstance(manifest, dict) else {}
    snapshots = manifest.get("videoSnapshots") if isinstance(manifest.get("videoSnapshots"), dict) else {}
    snapshots = {str(key): dict(value) for key, value in snapshots.items() if isinstance(value, dict)}
    performance_state = manifest.get("performanceState") if isinstance(manifest.get("performanceState"), dict) else {}
    performance_state = {str(key): dict(value) for key, value in performance_state.items() if isinstance(value, dict)}
    now = now_utc()
    session = make_session()
    warnings: list[str] = []

    record_qids: dict[int, str] = {}
    unresolved_titles: dict[int, str] = {}
    for index, record in enumerate(records):
        if str(record.get("primaryCategory") or "") != "Creator":
            continue
        qid = identity_qid(record, attention_manifest)
        if qid:
            record_qids[index] = qid
        else:
            title = cached_wikipedia_title(record, attention_manifest)
            if title:
                unresolved_titles[index] = title

    if unresolved_titles:
        title_qids, title_warnings = fetch_wikipedia_qids(session, list(unresolved_titles.values()), args.request_timeout)
        warnings.extend(title_warnings)
        for index, title in unresolved_titles.items():
            qid = title_qids.get(title, "")
            if qid:
                record_qids[index] = qid

    channel_by_qid, channel_warnings = fetch_youtube_channel_ids(session, list(record_qids.values()), args.request_timeout)
    warnings.extend(channel_warnings)
    candidates = [index for index, qid in record_qids.items() if channel_by_qid.get(qid)]
    candidates.sort(key=lambda index: str(records[index].get("id") or records[index].get("name") or ""))

    cursor = int(number(manifest.get("cursor"), 0))
    if candidates:
        cursor %= len(candidates)
        rotated = candidates[cursor:] + candidates[:cursor]
    else:
        rotated = []
    selected = rotated[: max(0, args.max_records) or len(rotated)]
    next_cursor = (cursor + len(selected)) % len(candidates) if candidates else 0

    checked = 0
    applied = 0
    changed = 0
    next_snapshots = dict(snapshots)
    next_state = dict(performance_state)
    largest: list[dict[str, Any]] = []

    for index in selected:
        record = records[index]
        qid = record_qids[index]
        channel_id = channel_by_qid.get(qid, "")
        if not channel_id:
            continue
        checked += 1
        try:
            response = session.get(YOUTUBE_FEED.format(channel_id=quote(channel_id)), timeout=args.request_timeout)
            response.raise_for_status()
            feed = parse_youtube_feed(response.text)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"YouTube RSS {record.get('name')}: {type(exc).__name__}: {exc}")
            continue
        if not feed:
            continue
        stats, video_warnings = fetch_video_stats(session, channel_id, feed, args.request_timeout, args.max_videos_per_record)
        warnings.extend(f"{record.get('name')}: {warning}" for warning in video_warnings)
        if not stats:
            continue

        growth_rows: list[tuple[dict[str, Any], float]] = []
        for video in stats:
            video_id = str(video.get("videoId") or "")
            previous = snapshots.get(video_id) if isinstance(snapshots.get(video_id), dict) else {}
            growth = growth_per_day(previous, int(number(video.get("views"), 0)), now) if previous else None
            next_snapshots[video_id] = {
                "views": int(number(video.get("views"), 0)),
                "checkedAt": iso(now),
                "channelId": channel_id,
                "recordId": record.get("id"),
                "publishedAt": iso(video["publishedAt"]) if isinstance(video.get("publishedAt"), datetime) else None,
            }
            if growth is not None:
                growth_rows.append((video, growth))

        cutoff = now - timedelta(days=max(1, args.lookback_days))
        eligible = [(video, growth) for video, growth in growth_rows if isinstance(video.get("publishedAt"), datetime) and cutoff <= video["publishedAt"] <= now]
        if not eligible:
            continue
        eligible.sort(key=lambda item: item[0]["publishedAt"], reverse=True)
        focus, focus_growth = eligible[0]
        baseline_values = [growth for video, growth in growth_rows if str(video.get("videoId")) != str(focus.get("videoId")) and growth > 0]
        if len(baseline_values) < 2:
            continue
        baseline_growth = statistics.median(baseline_values)
        if baseline_growth <= 0:
            continue
        ratio = focus_growth / baseline_growth
        target_info = performance_target(ratio)
        if not target_info:
            continue
        bucket, target = target_info
        state_key = f"{record.get('id')}:{focus.get('videoId')}"
        old_state = performance_state.get(state_key) if isinstance(performance_state.get(state_key), dict) else {}
        previous_target = number(old_state.get("targetMovePct"), 0)
        delta = incremental_target(previous_target, target)
        next_state[state_key] = {
            "targetMovePct": round(target, 6),
            "ratio": round(ratio, 6),
            "bucket": bucket,
            "checkedAt": iso(now),
            "youtubeChannelId": channel_id,
        }
        if abs(delta) < 0.08:
            continue

        event = youtube_event(record, channel_id, focus, now, bucket, delta, ratio, focus_growth, baseline_growth)
        updated, added = apply_outcome_events(record, [event])
        if not added:
            continue
        latest = updated.get("priceEvents", [])[-1]
        updated["priceExplanation"] = explanation(latest, number(updated.get("marketPrice"), 0))
        updated["creatorYouTubeVerifiedAt"] = iso(now)
        updated["creatorYouTubeChannelId"] = channel_id
        records[index] = updated
        applied += added
        changed += 1
        largest.append(
            {
                "name": updated.get("name"),
                "video": focus.get("title"),
                "ratio": round(ratio, 3),
                "movePct": latest.get("movePct"),
                "priceAfter": latest.get("priceAfter"),
            }
        )

    # Keep the manifest bounded while preferring the newest snapshots.
    ordered_snapshots = sorted(next_snapshots.items(), key=lambda item: str(item[1].get("checkedAt") or ""), reverse=True)
    next_snapshots = dict(ordered_snapshots[:MAX_SNAPSHOTS])

    if warnings and not args.allow_source_errors and checked > 0 and not next_snapshots:
        raise RuntimeError("YouTube Creator sources failed without usable snapshots: " + "; ".join(warnings[-8:]))

    args.catalog.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(
            {
                "version": MODEL_VERSION,
                "updatedAt": iso(now),
                "cursor": next_cursor,
                "creatorRecordsWithWikidataQid": len(record_qids),
                "creatorRecordsWithYouTubeChannel": len(candidates),
                "recordsChecked": checked,
                "recordsChanged": changed,
                "eventsApplied": applied,
                "videoSnapshots": next_snapshots,
                "performanceState": next_state,
                "sourceWarnings": warnings[-100:],
                "largestMoves": sorted(largest, key=lambda item: abs(number(item.get("movePct"), 0)), reverse=True)[:50],
                "policy": {
                    "noVerifiedPerformanceNoMove": True,
                    "maximumSingleOutcomeMovePct": None,
                    "identity": "Wikidata P2397 YouTube channel ID",
                    "performance": "view growth between TalentX snapshots versus same-channel comparison videos",
                    "sourceFailure": "fail closed",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Creator YouTube refresh: {len(candidates):,} verified channels; checked {checked:,}; "
        f"applied {applied:,} events to {changed:,} profiles; warnings {len(warnings):,}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
