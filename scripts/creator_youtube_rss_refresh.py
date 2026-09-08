#!/usr/bin/env python3
"""Reliable direct YouTube performance outcomes from official channel RSS feeds.

This is the production collector used by the Creator market workflow.  It reuses
TalentX's conservative Wikidata P2397 channel-identity mapping but reads current
per-video view totals from the YouTube channel RSS ``media:statistics`` element.
That removes the brittle dependency on YouTube watch-page HTML.

Fresh pricing still requires two TalentX snapshots.  The first successful scan
only establishes view-count baselines.  Later scans compare view growth for a
recent upload with growth on other recent videos from the same creator.  No
verified growth evidence means no move, and there is no fixed percentage cap.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from creator_youtube_refresh import (
    MAX_SNAPSHOTS,
    YOUTUBE_FEED,
    cached_wikipedia_title,
    explanation,
    fetch_wikipedia_qids,
    fetch_youtube_channel_ids,
    growth_per_day,
    identity_qid,
    incremental_target,
    load_json,
    make_session,
    now_utc,
    performance_target,
    youtube_event,
)
from non_athlete_event_refresh import iso, number
from non_athlete_outcome_refresh import apply_outcome_events

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "market" / "creators.json"
DEFAULT_ATTENTION_MANIFEST = ROOT / "data" / "creator_attention_manifest.json"
DEFAULT_MANIFEST = ROOT / "data" / "creator_youtube_manifest.json"
MODEL_VERSION = "creator-youtube-rss-performance-v2"


def parse_rss_video_stats(xml_text: str) -> list[dict[str, Any]]:
    """Return YouTube feed entries only when an official RSS view count exists."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    output: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        video_id = str(entry.findtext("yt:videoId", default="", namespaces=ns)).strip()
        channel_id = str(entry.findtext("yt:channelId", default="", namespaces=ns)).strip()
        title = str(entry.findtext("atom:title", default="", namespaces=ns)).strip()
        published_text = str(entry.findtext("atom:published", default="", namespaces=ns)).strip()
        statistics = entry.find("media:group/media:community/media:statistics", ns)
        views_raw = statistics.get("views") if statistics is not None else None
        try:
            views = int(str(views_raw))
        except (TypeError, ValueError):
            continue
        try:
            published = datetime.fromisoformat(published_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if published.tzinfo is None:
            continue
        if video_id and title and views >= 0:
            output.append(
                {
                    "videoId": video_id,
                    "channelId": channel_id,
                    "title": title,
                    "publishedAt": published,
                    "views": views,
                }
            )
    output.sort(key=lambda item: item["publishedAt"], reverse=True)
    return output


def load_creator_qids(records: list[dict[str, Any]], attention_manifest: dict[str, Any], session: Any, timeout: float) -> tuple[dict[int, str], list[str]]:
    record_qids: dict[int, str] = {}
    unresolved_titles: dict[int, str] = {}
    warnings: list[str] = []
    for index, record in enumerate(records):
        if str(record.get("primaryCategory") or "") != "Creator":
            continue
        qid = identity_qid(record, attention_manifest)
        if qid:
            record_qids[index] = qid
            continue
        title = cached_wikipedia_title(record, attention_manifest)
        if title:
            unresolved_titles[index] = title
    if unresolved_titles:
        title_qids, title_warnings = fetch_wikipedia_qids(session, list(unresolved_titles.values()), timeout)
        warnings.extend(title_warnings)
        for index, title in unresolved_titles.items():
            qid = title_qids.get(title, "")
            if qid:
                record_qids[index] = qid
    return record_qids, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--attention-manifest", type=Path, default=DEFAULT_ATTENTION_MANIFEST)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lookback-days", type=int, default=21)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--max-records", type=int, default=500)
    parser.add_argument("--max-videos-per-record", type=int, default=8)
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
    qids, qid_warnings = load_creator_qids(records, attention_manifest, session, args.request_timeout)
    warnings.extend(qid_warnings)
    channel_by_qid, channel_warnings = fetch_youtube_channel_ids(session, list(qids.values()), args.request_timeout)
    warnings.extend(channel_warnings)

    candidates = [index for index, qid in qids.items() if channel_by_qid.get(qid)]
    candidates.sort(key=lambda index: str(records[index].get("id") or records[index].get("name") or ""))
    cursor = int(number(manifest.get("cursor"), 0))
    if candidates:
        cursor %= len(candidates)
        rotated = candidates[cursor:] + candidates[:cursor]
    else:
        rotated = []
    limit = max(0, args.max_records)
    selected = rotated if limit == 0 else rotated[:limit]
    next_cursor = (cursor + len(selected)) % len(candidates) if candidates else 0

    next_snapshots = dict(snapshots)
    next_state = dict(performance_state)
    checked = 0
    feeds_loaded = 0
    videos_snapshotted = 0
    changed = 0
    applied = 0
    largest: list[dict[str, Any]] = []
    cutoff = now - timedelta(days=max(1, args.lookback_days))

    for index in selected:
        record = records[index]
        qid = qids[index]
        channel_id = channel_by_qid.get(qid, "")
        if not channel_id:
            continue
        checked += 1
        try:
            response = session.get(YOUTUBE_FEED.format(channel_id=quote(channel_id)), timeout=args.request_timeout)
            response.raise_for_status()
            feed = parse_rss_video_stats(response.text)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"YouTube RSS {record.get('name')}: {type(exc).__name__}: {exc}")
            continue
        if not feed:
            warnings.append(f"YouTube RSS {record.get('name')}: no entries with public view statistics")
            continue
        feeds_loaded += 1
        feed = feed[: max(1, args.max_videos_per_record)]

        growth_rows: list[tuple[dict[str, Any], float]] = []
        for video in feed:
            video_id = str(video.get("videoId") or "")
            views = int(number(video.get("views"), 0))
            previous = snapshots.get(video_id) if isinstance(snapshots.get(video_id), dict) else {}
            growth = growth_per_day(previous, views, now) if previous else None
            next_snapshots[video_id] = {
                "views": views,
                "checkedAt": iso(now),
                "channelId": channel_id,
                "recordId": record.get("id"),
                "publishedAt": iso(video["publishedAt"]),
                "source": "YouTube channel RSS media:statistics",
            }
            videos_snapshotted += 1
            if growth is not None:
                growth_rows.append((video, growth))

        eligible = [
            (video, growth)
            for video, growth in growth_rows
            if cutoff <= video["publishedAt"] <= now
        ]
        if not eligible:
            continue
        eligible.sort(key=lambda item: item[0]["publishedAt"], reverse=True)
        focus, focus_growth = eligible[0]
        comparisons = [
            growth
            for video, growth in growth_rows
            if str(video.get("videoId")) != str(focus.get("videoId")) and growth > 0
        ]
        if len(comparisons) < 2:
            continue
        baseline_growth = statistics.median(comparisons)
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
        event["provider"] = "YouTube channel RSS"
        event["evidenceMethod"] = "YouTube RSS media:statistics view growth between TalentX snapshots"
        updated, added = apply_outcome_events(record, [event])
        if not added:
            continue
        latest = updated.get("priceEvents", [])[-1]
        updated["priceExplanation"] = explanation(latest, number(updated.get("marketPrice"), 0))
        updated["creatorYouTubeVerifiedAt"] = iso(now)
        updated["creatorYouTubeChannelId"] = channel_id
        records[index] = updated
        changed += 1
        applied += added
        largest.append(
            {
                "name": updated.get("name"),
                "video": focus.get("title"),
                "ratio": round(ratio, 3),
                "movePct": latest.get("movePct"),
                "priceAfter": latest.get("priceAfter"),
            }
        )

    ordered_snapshots = sorted(
        next_snapshots.items(),
        key=lambda item: str(item[1].get("checkedAt") or ""),
        reverse=True,
    )
    next_snapshots = dict(ordered_snapshots[:MAX_SNAPSHOTS])

    if warnings and not args.allow_source_errors and checked and not next_snapshots:
        raise RuntimeError("YouTube RSS failed without usable snapshots: " + "; ".join(warnings[-8:]))

    args.catalog.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(
            {
                "version": MODEL_VERSION,
                "updatedAt": iso(now),
                "cursor": next_cursor,
                "creatorRecordsWithWikidataQid": len(qids),
                "creatorRecordsWithYouTubeChannel": len(candidates),
                "recordsChecked": checked,
                "feedsLoaded": feeds_loaded,
                "videosSnapshottedThisRun": videos_snapshotted,
                "storedVideoSnapshots": len(next_snapshots),
                "recordsChanged": changed,
                "eventsApplied": applied,
                "videoSnapshots": next_snapshots,
                "performanceState": next_state,
                "sourceWarnings": warnings[-150:],
                "largestMoves": sorted(largest, key=lambda item: abs(number(item.get("movePct"), 0)), reverse=True)[:50],
                "policy": {
                    "noVerifiedPerformanceNoMove": True,
                    "maximumSingleOutcomeMovePct": None,
                    "identity": "Wikidata P2397 YouTube channel ID",
                    "viewSource": "Official YouTube channel RSS media:statistics",
                    "performance": "view growth between TalentX snapshots versus same-channel comparison videos",
                    "firstSuccessfulScan": "baseline snapshots only; no fabricated fresh move",
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
        f"Creator YouTube RSS refresh: {len(candidates):,} verified channels; checked {checked:,}; "
        f"feeds {feeds_loaded:,}; snapshots {videos_snapshotted:,}; stored {len(next_snapshots):,}; "
        f"applied {applied:,} events to {changed:,} profiles; warnings {len(warnings):,}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
