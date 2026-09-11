#!/usr/bin/env python3
"""Reliable direct YouTube performance outcomes from official channel RSS feeds.

This is the production collector used by the Creator market workflow. It reuses
TalentX's conservative Wikidata P2397 channel-identity mapping but reads current
per-video view totals from the YouTube channel RSS ``media:statistics`` element.
That removes the brittle dependency on YouTube watch-page HTML.

Fresh pricing still requires two TalentX snapshots. The first successful scan
only establishes view-count baselines. Later scans blend recent view-growth
velocity with same-channel total-view scale so a tiny dormant-video denominator
cannot by itself create a huge move. No verified evidence means no move, and
there is no fixed percentage cap.
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
    fetch_wikipedia_qids,
    fetch_youtube_channel_ids,
    growth_per_day,
    identity_qid,
    incremental_target,
    load_json,
    make_session,
    now_utc,
    youtube_event,
)
from market_outcome_calibration_v2 import (
    CREATOR_MODEL_VERSION,
    creator_effective_ratio,
    creator_performance_target,
)
from non_athlete_event_refresh import iso, number
from non_athlete_outcome_refresh import apply_outcome_events

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "market" / "creators.json"
DEFAULT_ATTENTION_MANIFEST = ROOT / "data" / "creator_attention_manifest.json"
DEFAULT_MANIFEST = ROOT / "data" / "creator_youtube_manifest.json"
MODEL_VERSION = "creator-youtube-rss-performance-v3"


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


def scale_aware_explanation(event: dict[str, Any], price: float) -> dict[str, Any]:
    move = number(event.get("movePct"), 0)
    raw_ratio = number(event.get("viewGrowthRatio"), 0)
    scale_ratio = number(event.get("viewScaleRatio"), 0)
    effective_ratio = number(event.get("effectivePerformanceRatio"), 0)
    comparison_views = number(event.get("comparisonMedianViews"), 0)
    summary = [
        f"Recent verified view growth was {raw_ratio:.2f}× the same-channel growth baseline.",
        f"The video had {scale_ratio:.2f}× the median total views of recent same-channel comparison videos ({comparison_views:,.0f} median views).",
        f"TalentX blended velocity and scale into a {effective_ratio:.2f}× effective performance ratio so a tiny dormant-video baseline cannot dominate the price move.",
    ]
    return {
        "version": CREATOR_MODEL_VERSION,
        "eventId": event.get("eventKey"),
        "event": event.get("name"),
        "eventAt": event.get("startedAt"),
        "headline": "Direct YouTube performance outcome",
        "summary": summary,
        "direction": "increased" if move > 0 else "decreased" if move < 0 else "held steady",
        "finalMovePct": round(move, 2),
        "recordedMarketPrice": round(price, 2),
        "pricingMode": "Verified direct Creator outcome; scale-aware event pricing",
        "source": event.get("provider"),
        "sourceUrl": event.get("sourceUrl"),
    }


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
        focus_id = str(focus.get("videoId") or "")
        comparisons = [
            growth
            for video, growth in growth_rows
            if str(video.get("videoId")) != focus_id and growth > 0
        ]
        if len(comparisons) < 2:
            continue
        baseline_growth = statistics.median(comparisons)
        if baseline_growth <= 0:
            continue

        comparison_views = [
            number(video.get("views"), 0)
            for video in feed
            if str(video.get("videoId")) != focus_id and number(video.get("views"), 0) > 0
        ]
        if len(comparison_views) < 2:
            continue
        comparison_median_views = statistics.median(comparison_views)
        focus_views = number(focus.get("views"), 0)
        if comparison_median_views <= 0 or focus_views <= 0:
            continue

        growth_ratio = focus_growth / baseline_growth
        scale_ratio = focus_views / comparison_median_views
        effective_ratio = creator_effective_ratio(growth_ratio, scale_ratio)
        target_info = creator_performance_target(growth_ratio, scale_ratio)
        if not target_info:
            continue
        bucket, target = target_info
        state_key = f"{record.get('id')}:{focus_id}"
        old_state = performance_state.get(state_key) if isinstance(performance_state.get(state_key), dict) else {}
        previous_target = number(old_state.get("targetMovePct"), 0)
        delta = incremental_target(previous_target, target)
        next_state[state_key] = {
            "targetMovePct": round(target, 6),
            "growthRatio": round(growth_ratio, 6),
            "scaleRatio": round(scale_ratio, 6),
            "effectiveRatio": round(effective_ratio, 6),
            "comparisonMedianViews": round(comparison_median_views, 2),
            "bucket": bucket,
            "checkedAt": iso(now),
            "youtubeChannelId": channel_id,
            "pricingCalibrationVersion": CREATOR_MODEL_VERSION,
        }
        if abs(delta) < 0.08:
            continue

        event = youtube_event(record, channel_id, focus, now, bucket, delta, growth_ratio, focus_growth, baseline_growth)
        event["provider"] = "YouTube channel RSS"
        event["evidenceMethod"] = "YouTube RSS media:statistics; snapshot velocity blended with same-channel total-view scale"
        event["viewScaleRatio"] = round(scale_ratio, 4)
        event["effectivePerformanceRatio"] = round(effective_ratio, 4)
        event["comparisonMedianViews"] = round(comparison_median_views, 2)
        event["pricingCalibrationVersion"] = CREATOR_MODEL_VERSION
        updated, added = apply_outcome_events(record, [event])
        if not added:
            continue
        latest = updated.get("priceEvents", [])[-1]
        updated["priceExplanation"] = scale_aware_explanation(latest, number(updated.get("marketPrice"), 0))
        updated["creatorYouTubeVerifiedAt"] = iso(now)
        updated["creatorYouTubeChannelId"] = channel_id
        records[index] = updated
        changed += 1
        applied += added
        largest.append(
            {
                "name": updated.get("name"),
                "video": focus.get("title"),
                "growthRatio": round(growth_ratio, 3),
                "scaleRatio": round(scale_ratio, 3),
                "effectiveRatio": round(effective_ratio, 3),
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
                "pricingCalibrationVersion": CREATOR_MODEL_VERSION,
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
                    "performance": "25% log-weight snapshot velocity + 75% log-weight same-channel total-view scale",
                    "smallBaselineProtection": "tiny dormant-video growth denominators cannot dominate without matching absolute channel-relative scale",
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
