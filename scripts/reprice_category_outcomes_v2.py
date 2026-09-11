#!/usr/bin/env python3
"""One-time/idempotent repricing of existing verified category outcome events.

Music and Actor outcomes retain the v2 market-wide calibration. Creator YouTube
outcomes use the scale-aware Creator v2.1 policy so a tiny comparison-growth
baseline cannot by itself create a huge move. Existing event keys are preserved,
later prices are rescaled, and matching chart points are updated in place.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from market_outcome_calibration_v2 import (
    CREATOR_MODEL_VERSION,
    MODEL_VERSION,
    actor_box_office_target,
    amplify_legacy_direct_actor_target,
    creator_effective_ratio,
    creator_performance_target,
    music_chart_target,
    music_movement_target,
)


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


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


def category_event(category: str, event_type: str) -> bool:
    if category == "music":
        return event_type == "music-chart-outcome"
    if category == "actors":
        return event_type in {"actor-box-office-outcome", "actor-streaming-outcome"}
    if category == "creators":
        return event_type == "creator-youtube-outcome"
    return False


def category_model_version(category: str) -> str:
    return CREATOR_MODEL_VERSION if category == "creators" else MODEL_VERSION


def creator_snapshot_index(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Index the newest stored YouTube snapshots by verified channel."""
    raw = manifest.get("videoSnapshots") if isinstance(manifest.get("videoSnapshots"), dict) else {}
    channels: dict[str, list[dict[str, Any]]] = {}
    for video_id, snapshot in raw.items():
        if not isinstance(snapshot, dict):
            continue
        channel_id = str(snapshot.get("channelId") or "").strip()
        views = number(snapshot.get("views"), 0)
        if not channel_id or views <= 0:
            continue
        channels.setdefault(channel_id, []).append(
            {
                "videoId": str(video_id),
                "views": views,
                "publishedAt": str(snapshot.get("publishedAt") or ""),
            }
        )
    for channel_id, rows in channels.items():
        rows.sort(key=lambda row: str(row.get("publishedAt") or ""), reverse=True)
        channels[channel_id] = rows[:8]
    return channels


def creator_scale_details(event: dict[str, Any], snapshots: dict[str, list[dict[str, Any]]]) -> tuple[float, float, float]:
    raw_ratio = max(0.01, number(event.get("viewGrowthRatio"), 1.0))
    stored_scale = number(event.get("viewScaleRatio"), 0)
    stored_median = number(event.get("comparisonMedianViews"), 0)
    if stored_scale > 0:
        effective = creator_effective_ratio(raw_ratio, stored_scale)
        return stored_scale, stored_median, effective

    channel_id = str(event.get("youtubeChannelId") or "").strip()
    focus_id = str(event.get("youtubeVideoId") or "").strip()
    focus_views = number(event.get("youtubeViews"), 0)
    rows = snapshots.get(channel_id, []) if channel_id else []
    comparison_views = [
        number(row.get("views"), 0)
        for row in rows
        if str(row.get("videoId") or "") != focus_id and number(row.get("views"), 0) > 0
    ]
    if len(comparison_views) >= 2 and focus_views > 0:
        comparison_median = statistics.median(comparison_views)
        scale_ratio = focus_views / comparison_median if comparison_median > 0 else 1.0
    else:
        comparison_median = 0.0
        scale_ratio = 1.0
    effective = creator_effective_ratio(raw_ratio, scale_ratio)
    event["viewScaleRatio"] = round(scale_ratio, 4)
    event["comparisonMedianViews"] = round(comparison_median, 2)
    event["effectivePerformanceRatio"] = round(effective, 4)
    event["evidenceMethod"] = "YouTube snapshot velocity blended with same-channel total-view scale"
    return scale_ratio, comparison_median, effective


def calibrated_target(
    event: dict[str, Any],
    now: datetime,
    creator_snapshots: dict[str, list[dict[str, Any]]] | None = None,
) -> float | None:
    event_type = str(event.get("eventType") or "")
    old_target = number(event.get("targetOutcomeMovePct"), 0.0)

    if event_type == "music-chart-outcome":
        rank = int(number(event.get("chartRank"), 0))
        if rank <= 0:
            return None
        previous_raw = event.get("previousChartRank")
        previous = int(number(previous_raw, 0)) if previous_raw not in {None, ""} else None
        status = str(event.get("chartStatus") or "ranked").lower()
        if previous is not None or status in {"new", "re-entry"}:
            return music_movement_target(rank, previous, status)
        return music_chart_target(rank)[1]

    if event_type == "actor-box-office-outcome":
        ratio = number(event.get("boxOfficeToCostRatio"), 0.0)
        if ratio > 0:
            occurred = parse_time(event.get("startedAt"))
            age_days = max(0.0, (now - occurred).total_seconds() / 86400.0) if occurred else 30.0
            result = actor_box_office_target(ratio, age_days)
            return result[1] if result else None
        return amplify_legacy_direct_actor_target(old_target)

    if event_type == "actor-streaming-outcome":
        return amplify_legacy_direct_actor_target(old_target)

    if event_type == "creator-youtube-outcome":
        raw_ratio = number(event.get("viewGrowthRatio"), 0.0)
        if raw_ratio <= 0:
            return None
        scale_ratio, _, effective = creator_scale_details(event, creator_snapshots or {})
        event["effectivePerformanceRatio"] = round(effective, 4)
        result = creator_performance_target(raw_ratio, scale_ratio)
        # If the old event was a velocity-only false positive, neutralize it.
        return result[1] if result else 0.0

    return None


def creator_explanation(event: dict[str, Any], price: float) -> dict[str, Any]:
    move = number(event.get("movePct"), 0)
    raw_ratio = number(event.get("viewGrowthRatio"), 0)
    scale_ratio = number(event.get("viewScaleRatio"), 0)
    effective = number(event.get("effectivePerformanceRatio"), 0)
    median_views = number(event.get("comparisonMedianViews"), 0)
    return {
        "version": CREATOR_MODEL_VERSION,
        "eventId": event.get("eventKey") or event.get("eventId"),
        "event": event.get("name"),
        "eventAt": event.get("startedAt"),
        "headline": "Direct YouTube performance outcome",
        "summary": [
            f"Recent verified view growth was {raw_ratio:.2f}× the same-channel growth baseline.",
            f"The video had {scale_ratio:.2f}× the median total views of recent same-channel comparison videos ({median_views:,.0f} median views).",
            f"TalentX blended velocity and scale into a {effective:.2f}× effective performance ratio so a tiny baseline cannot dominate the move.",
        ],
        "direction": "increased" if move > 0 else "decreased" if move < 0 else "held steady",
        "finalMovePct": round(move, 2),
        "recordedMarketPrice": round(price, 2),
        "pricingMode": "Verified direct Creator outcome; scale-aware event pricing",
        "source": event.get("provider"),
        "sourceUrl": event.get("sourceUrl"),
    }


def reprice_record(
    record: dict[str, Any],
    category: str,
    now: datetime,
    creator_snapshots: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any], int]:
    result = dict(record)
    raw_events = result.get("priceEvents") if isinstance(result.get("priceEvents"), list) else []
    events = [dict(item) for item in raw_events if isinstance(item, dict)]
    if not events:
        return result, 0

    version = category_model_version(category)
    migrated: set[str] = set()
    desired_moves: dict[str, float] = {}
    for event in events:
        event_type = str(event.get("eventType") or "")
        if not category_event(category, event_type):
            continue
        if str(event.get("pricingCalibrationVersion") or "") == version:
            continue
        if event.get("verified") is False:
            continue
        target = calibrated_target(event, now, creator_snapshots)
        if target is None:
            continue
        old_target = number(event.get("targetOutcomeMovePct"), 0.0)
        old_move = number(event.get("movePct"), 0.0)
        effective_multiplier = old_move / old_target if abs(old_target) >= 0.001 else 1.0
        effective_multiplier = min(1.35, max(0.65, effective_multiplier))
        desired = target * effective_multiplier
        key = str(event.get("eventKey") or event.get("eventId") or "")
        if not key:
            continue
        desired_moves[key] = desired
        migrated.add(key)
        event["targetOutcomeMovePct"] = round(target, 4)
        event["pricingCalibrationVersion"] = version

    if not migrated:
        return result, 0

    indexed = sorted(
        events,
        key=lambda item: (str(item.get("startedAt") or ""), str(item.get("eventKey") or item.get("eventId") or "")),
    )
    scale = 1.0
    for event in indexed:
        key = str(event.get("eventKey") or event.get("eventId") or "")
        old_before = number(event.get("priceBefore"), 0.0)
        old_after = number(event.get("priceAfter"), 0.0)
        old_move = number(event.get("movePct"), 0.0)
        if old_before <= 0 or old_after <= 0:
            if key in desired_moves:
                event["movePct"] = round(desired_moves[key], 3)
            continue

        new_before = max(0.01, round(old_before * scale, 2))
        move = desired_moves.get(key, old_move)
        new_after = max(0.01, round(new_before * (1.0 + move / 100.0), 2))
        event["priceBefore"] = new_before
        event["priceAfter"] = new_after
        event["movePct"] = round((new_after / new_before - 1.0) * 100.0, 3)
        scale = new_after / old_after

    result["priceEvents"] = indexed
    old_market = number(result.get("marketPrice"), 0.01)
    result["marketPrice"] = max(0.01, round(old_market * scale, 2))
    result["pricingCalibrationVersion"] = version

    event_map = {
        str(event.get("eventKey") or event.get("eventId") or ""): event
        for event in indexed
        if str(event.get("eventKey") or event.get("eventId") or "")
    }
    history = [dict(item) for item in result.get("priceHistory", []) if isinstance(item, dict)]
    for point in history:
        event = event_map.get(str(point.get("eventId") or ""))
        if not event:
            continue
        phase = str(point.get("phase") or "")
        if phase == "open" and number(event.get("priceBefore"), 0) > 0:
            point["price"] = round(number(event.get("priceBefore")), 2)
        elif phase == "close" and number(event.get("priceAfter"), 0) > 0:
            point["price"] = round(number(event.get("priceAfter")), 2)
    result["priceHistory"] = history

    latest = indexed[-1] if indexed else None
    if latest:
        result["previousMarketPrice"] = round(number(latest.get("priceBefore"), result.get("previousMarketPrice", old_market)), 2)
        result["lastPriceEventAt"] = latest.get("startedAt") or result.get("lastPriceEventAt")
        result["lastPriceEvent"] = latest.get("name") or result.get("lastPriceEvent")
        result["lastPriceEventId"] = latest.get("eventKey") or latest.get("eventId") or result.get("lastPriceEventId")
        result["hourlyChangePct"] = round(number(latest.get("movePct"), result.get("hourlyChangePct", 0)), 3)
        result["dailyChange"] = round(number(latest.get("movePct"), result.get("dailyChange", 0)), 3)
        if category == "creators" and str(latest.get("eventType") or "") == "creator-youtube-outcome":
            result["priceExplanation"] = creator_explanation(latest, result["marketPrice"])

    return result, len(migrated)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--category", choices=("music", "actors", "creators"), required=True)
    parser.add_argument("--youtube-manifest", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")

    creator_snapshots: dict[str, list[dict[str, Any]]] = {}
    if args.category == "creators" and args.youtube_manifest and args.youtube_manifest.exists():
        manifest = json.loads(args.youtube_manifest.read_text(encoding="utf-8"))
        if isinstance(manifest, dict):
            creator_snapshots = creator_snapshot_index(manifest)

    now = datetime.now(timezone.utc)
    output: list[dict[str, Any]] = []
    records_changed = 0
    events_changed = 0
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        record, changed = reprice_record(raw, args.category, now, creator_snapshots)
        output.append(record)
        if changed:
            records_changed += 1
            events_changed += changed

    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"Category calibration {category_model_version(args.category)}: repriced {events_changed:,} existing verified "
        f"outcome events across {records_changed:,} {args.category} records."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
