#!/usr/bin/env python3
"""Rebuild Creator fundamentals from verified production evidence.

Creator v2 separates two concepts:

* Fundamental value = demonstrated creator production plus modest career runway.
* Price movement = verified outcomes versus that creator's own expectations.

Wikidata is never interpreted as audience, engagement, views, or production. A
Wikidata-only profile remains a low-confidence provisional prior. When official
YouTube RSS snapshots exist, this migration derives comparable Creator metrics
from recent view scale, recent performance, growth, consistency, and career
context, then rebases the durable event chain onto the new fundamental value.

No random jitter is added. Identical evidence is allowed to produce a legitimate
tie; the purpose of this migration is to remove unsupported proxy ties, not to
manufacture unique pennies.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from discover_creators_only import creator_metrics
from pricing_model import clamp

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "market" / "creators.json"
DEFAULT_YOUTUBE_MANIFEST = ROOT / "data" / "creator_youtube_manifest.json"
MODEL_VERSION = "creator-fundamentals-v2"

# The Creator model agreed for TalentX. Existing generic field names retain their
# storage compatibility while their Creator meaning is explicitly defined here.
CREATOR_WEIGHTS: dict[str, float] = {
    "audience": 0.30,       # verified audience / reach production
    "performance": 0.25,    # recent engagement / view production
    "achievements": 0.20,   # sustained career production
    "potential": 0.10,      # growth / momentum
    "consistency": 0.10,    # output and performance consistency
    "careerRunway": 0.05,   # career runway / stage
}


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def percentile_position(value: float, values: list[float]) -> float:
    clean = sorted(item for item in values if math.isfinite(item))
    if len(clean) <= 1:
        return 0.5
    below = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    return clamp((below + 0.5 * equal) / len(clean), 0.0, 1.0)


def percentile_score(value: float, values: list[float]) -> float:
    """Map a same-market percentile to a conservative 35-95 production score."""
    return round(35.0 + 60.0 * percentile_position(value, values), 1)


def career_runway(record: dict[str, Any]) -> float:
    age = number(record.get("age"), -1)
    if age < 0:
        return 55.0
    return round(clamp(88.0 - max(0.0, age - 20.0) * 1.8, 30.0, 88.0), 1)


def years_active(record: dict[str, Any]) -> float | None:
    direct = number(record.get("yearsActive"), -1)
    if direct >= 0:
        return direct
    debut = number(record.get("debutYear", record.get("wikidataWorkStartYear")), -1)
    if debut < 0:
        return None
    return max(0.0, datetime.now(timezone.utc).year - debut)


def tenure_score(record: dict[str, Any]) -> float:
    years = years_active(record)
    if years is None:
        return 50.0
    return round(clamp(45.0 + min(years, 20.0) * 2.0, 45.0, 85.0), 1)


def latest_performance_state(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    states = manifest.get("performanceState") if isinstance(manifest.get("performanceState"), dict) else {}
    output: dict[str, dict[str, Any]] = {}
    for key, value in states.items():
        if not isinstance(value, dict):
            continue
        record_id = str(key).rsplit(":", 1)[0]
        if not record_id:
            continue
        previous = output.get(record_id)
        previous_time = parse_time(previous.get("checkedAt")) if isinstance(previous, dict) else None
        current_time = parse_time(value.get("checkedAt"))
        if previous is None or (current_time is not None and (previous_time is None or current_time >= previous_time)):
            output[record_id] = dict(value)
    return output


def video_rows_by_record(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    snapshots = manifest.get("videoSnapshots") if isinstance(manifest.get("videoSnapshots"), dict) else {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for video_id, value in snapshots.items():
        if not isinstance(value, dict):
            continue
        record_id = str(value.get("recordId") or "")
        views = number(value.get("views"), -1)
        published = parse_time(value.get("publishedAt"))
        if not record_id or views < 0 or published is None:
            continue
        grouped.setdefault(record_id, []).append(
            {
                **value,
                "videoId": str(video_id),
                "views": views,
                "publishedAtParsed": published,
            }
        )
    for record_id, rows in grouped.items():
        rows.sort(key=lambda item: item["publishedAtParsed"], reverse=True)
        grouped[record_id] = rows[:8]
    return grouped


def creator_feature_rows(records: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped = video_rows_by_record(manifest)
    state = latest_performance_state(manifest)
    output: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = str(record.get("id") or "")
        rows = grouped.get(record_id, [])
        positive = [row for row in rows if number(row.get("views"), 0) > 0]
        if len(positive) < 3:
            continue
        views = [number(row.get("views"), 0) for row in positive]
        recent_views = views[: min(3, len(views))]
        median_views = statistics.median(views)
        recent_median_views = statistics.median(recent_views)
        if median_views <= 0 or recent_median_views <= 0:
            continue

        deviations = [abs(value - median_views) for value in views]
        mad = statistics.median(deviations)
        view_consistency = clamp(92.0 - (mad / median_views) * 70.0, 35.0, 95.0)

        cadence_score = view_consistency
        if len(positive) >= 4:
            dates = [row["publishedAtParsed"] for row in positive]
            intervals = [
                max(0.25, (dates[index] - dates[index + 1]).total_seconds() / 86400.0)
                for index in range(len(dates) - 1)
            ]
            mean_interval = statistics.mean(intervals)
            if mean_interval > 0 and len(intervals) >= 2:
                cadence_cv = statistics.pstdev(intervals) / mean_interval
                cadence_score = clamp(90.0 - cadence_cv * 35.0, 35.0, 95.0)

        latest_state = state.get(record_id, {})
        effective_ratio = number(latest_state.get("effectiveRatio"), 1.0)
        growth_score = 50.0
        if effective_ratio > 0:
            growth_score = clamp(50.0 + 22.0 * math.log2(effective_ratio), 20.0, 95.0)

        output[record_id] = {
            "medianViews": float(median_views),
            "recentMedianViews": float(recent_median_views),
            "viewConsistency": float(view_consistency),
            "cadenceConsistency": float(cadence_score),
            "growthScore": float(growth_score),
            "effectiveRatio": float(effective_ratio),
            "videoCount": len(positive),
            "channelId": str(positive[0].get("channelId") or record.get("creatorYouTubeChannelId") or ""),
            "checkedAt": latest_state.get("checkedAt"),
        }
    return output


def verified_metrics(
    record: dict[str, Any],
    features: dict[str, Any],
    median_view_pool: list[float],
    recent_view_pool: list[float],
) -> dict[str, float]:
    audience = percentile_score(math.log1p(features["medianViews"]), median_view_pool)
    recent = percentile_score(math.log1p(features["recentMedianViews"]), recent_view_pool)
    growth = round(clamp(features["growthScore"], 20.0, 95.0), 1)
    performance = round(clamp(recent * 0.75 + growth * 0.25, 25.0, 97.0), 1)
    sustained = round(clamp(audience * 0.75 + tenure_score(record) * 0.25, 25.0, 97.0), 1)
    consistency = round(
        clamp(features["viewConsistency"] * 0.70 + features["cadenceConsistency"] * 0.30, 30.0, 96.0),
        1,
    )
    return {
        "audience": audience,
        "performance": performance,
        "achievements": sustained,
        "potential": growth,
        "consistency": consistency,
        "careerRunway": career_runway(record),
        "availability": 80.0,
    }


def provisional_metrics(record: dict[str, Any]) -> dict[str, float]:
    candidate = {
        "birthYear": None,
        "workStartYear": None,
    }
    age = number(record.get("age"), -1)
    if age >= 0:
        candidate["birthYear"] = datetime.now(timezone.utc).year - int(round(age))
    start = number(record.get("debutYear", record.get("wikidataWorkStartYear")), -1)
    if start >= 0:
        candidate["workStartYear"] = int(round(start))
    return creator_metrics(candidate)


def active_score(metrics: dict[str, Any]) -> float:
    return round(sum(clamp(metrics.get(key), 0, 100) * weight for key, weight in CREATOR_WEIGHTS.items()), 1)


def event_multiplier(record: dict[str, Any]) -> float:
    multiplier = 1.0
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        move = number(event.get("movePct"), 0.0)
        if move <= -99.0:
            continue
        factor = 1.0 + move / 100.0
        if factor > 0 and math.isfinite(factor):
            multiplier *= factor
    return multiplier


def rebase_price_events(events: Any, scale: float) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        return []
    output: list[dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        event = dict(item)
        for key in ("priceBefore", "priceAfter"):
            value = number(event.get(key), -1)
            if value >= 0:
                event[key] = round(value * scale, 2)
        output.append(event)
    return output


def reprice_records(records: list[dict[str, Any]], manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    features = creator_feature_rows(records, manifest)
    median_pool = [math.log1p(item["medianViews"]) for item in features.values()]
    recent_pool = [math.log1p(item["recentMedianViews"]) for item in features.values()]

    staged: list[dict[str, Any]] = []
    raw_scores: list[float] = []
    verified_count = 0
    provisional_count = 0

    for item in records:
        record = dict(item)
        if str(record.get("primaryCategory") or "") != "Creator":
            staged.append(record)
            raw_scores.append(0.0)
            continue
        record_id = str(record.get("id") or "")
        evidence = features.get(record_id)
        if evidence:
            metrics = verified_metrics(record, evidence, median_pool, recent_pool)
            video_count = int(evidence["videoCount"])
            confidence = clamp(0.80 + min(0.08, max(0, video_count - 3) * 0.015) + (0.03 if evidence.get("checkedAt") else 0), 0.80, 0.92)
            record["pricingConfidence"] = round(confidence, 2)
            record["dataConfidence"] = max(number(record.get("dataConfidence"), 0), round(confidence, 2))
            record["pricingDataStatus"] = "Evidence enriched — verified Creator production (YouTube RSS)"
            record["modelType"] = "Creator production model"
            record["creatorProductionEvidence"] = {
                "provider": "YouTube channel RSS media:statistics",
                "videoCount": video_count,
                "medianViews": round(evidence["medianViews"], 2),
                "recentMedianViews": round(evidence["recentMedianViews"], 2),
                "effectivePerformanceRatio": round(evidence["effectiveRatio"], 4),
                "channelId": evidence.get("channelId"),
                "checkedAt": evidence.get("checkedAt"),
            }
            channel_id = str(evidence.get("channelId") or "")
            if channel_id:
                source = f"https://www.youtube.com/channel/{channel_id}"
                prior_evidence = record.get("pricingEvidence") if isinstance(record.get("pricingEvidence"), list) else []
                record["pricingEvidence"] = list(dict.fromkeys([*prior_evidence, source]))
                record["verifiedCreatorPlatforms"] = sorted(set([*(record.get("verifiedCreatorPlatforms") or []), "YouTube"]))
            verified_count += 1
        elif str(record.get("sourceNamespace") or "") == "wikidata-creator" or str(record.get("pricingDataStatus") or "").startswith("Provisional"):
            metrics = provisional_metrics(record)
            record["pricingConfidence"] = round(clamp(record.get("pricingConfidence", 0.40), 0.32, 0.50), 2)
            record["pricingDataStatus"] = "Provisional — creator identity/activity evidence only; platform production unverified"
            record["modelType"] = "Creator provisional prior"
            provisional_count += 1
        else:
            existing = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
            metrics = {key: round(clamp(existing.get(key, 50.0), 0, 100), 1) for key in ("audience", "performance", "achievements", "potential", "consistency")}
            metrics["careerRunway"] = round(clamp(existing.get("careerRunway", career_runway(record)), 0, 100), 1)
            metrics["availability"] = round(clamp(existing.get("availability", 80.0), 0, 100), 1)
            if not str(record.get("pricingDataStatus") or ""):
                record["pricingDataStatus"] = "Curated Creator prior — direct platform production evidence pending"

        record["activeMetrics"] = metrics
        raw = active_score(metrics)
        record["_creatorAbsoluteScore"] = raw
        staged.append(record)
        raw_scores.append(raw)

    creator_score_pool = [
        number(record.get("_creatorAbsoluteScore"), 0)
        for record in staged
        if str(record.get("primaryCategory") or "") == "Creator"
    ]

    output: list[dict[str, Any]] = []
    for record in staged:
        if str(record.get("primaryCategory") or "") != "Creator":
            output.append(record)
            continue
        absolute = number(record.pop("_creatorAbsoluteScore", 0), 0)
        peer_percentile = percentile_position(absolute, creator_score_pool)
        peer_score = 45.0 + 50.0 * peer_percentile
        universal = absolute * 0.70 + peer_score * 0.30

        status = str(record.get("pricingDataStatus") or "")
        if status.startswith("Provisional"):
            score_cap = 72.0
        elif status.startswith("Evidence enriched"):
            score_cap = 97.0
        else:
            score_cap = 92.0
        score = round(min(score_cap, universal), 1)
        confidence = clamp(record.get("pricingConfidence", record.get("dataConfidence", 0.50)), 0, 1)
        confidence_factor = 0.92 + 0.08 * confidence
        fundamental = round(max(2.0, (2.0 + 180.0 * (score / 100.0) ** 2) * confidence_factor), 2)
        multiplier = event_multiplier(record)
        market = round(max(0.01, fundamental * multiplier), 2)

        old_market = max(0.01, number(record.get("marketPrice"), market))
        scale = market / old_market if old_market > 0 else 1.0
        trend = [number(value, -1) for value in record.get("trend", [])] if isinstance(record.get("trend"), list) else []
        trend = [value for value in trend if value > 0]
        record["trend"] = [round(value * scale, 2) for value in trend[-18:]] if trend else [market] * 18
        if record["trend"]:
            record["trend"][-1] = market
        record["priceEvents"] = rebase_price_events(record.get("priceEvents"), scale)
        previous = number(record.get("previousMarketPrice"), -1)
        if previous >= 0:
            record["previousMarketPrice"] = round(previous * scale, 2)

        explanation = record.get("priceExplanation")
        if isinstance(explanation, dict):
            explanation = dict(explanation)
            explanation["recordedMarketPrice"] = market
            record["priceExplanation"] = explanation

        record["careerScore"] = score
        record["fundamentalValue"] = fundamental
        record["marketPrice"] = market
        record["pricingModelVersion"] = MODEL_VERSION
        record["creatorFundamentalModelVersion"] = MODEL_VERSION
        record["creatorFundamentalComponents"] = {
            "weights": CREATOR_WEIGHTS,
            "absoluteCreatorScore": round(absolute, 1),
            "creatorPeerPercentile": round(peer_percentile, 4),
            "creatorPeerScore": round(peer_score, 1),
            "universalCareerScore": score,
            "eventMultiplier": round(multiplier, 6),
            "principle": "Fundamental price = demonstrated creator production + modest career runway; price movement = verified new production versus expectation.",
        }
        output.append(record)

    summary = {
        "modelVersion": MODEL_VERSION,
        "creatorCount": sum(1 for record in output if record.get("primaryCategory") == "Creator"),
        "verifiedProductionCount": verified_count,
        "provisionalCount": provisional_count,
        "weights": CREATOR_WEIGHTS,
    }
    return output, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--youtube-manifest", type=Path, default=DEFAULT_YOUTUBE_MANIFEST)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    payload = load_json(args.catalog, [])
    if not isinstance(payload, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    manifest = load_json(args.youtube_manifest, {})
    manifest = manifest if isinstance(manifest, dict) else {}

    repriced, summary = reprice_records(records, manifest)
    args.catalog.write_text(json.dumps(repriced, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())