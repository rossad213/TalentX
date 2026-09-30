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

from build_non_athlete_catalog import metrics_from_rank
from discover_creators_only import creator_metrics
from pricing_model import clamp

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "market" / "creators.json"
DEFAULT_YOUTUBE_MANIFEST = ROOT / "data" / "creator_youtube_manifest.json"
DEFAULT_2026_ANCHORS = ROOT / "data" / "creator_career_anchors_2026.json"
MODEL_VERSION = "creator-fundamentals-v2.4-ranking-stability"

# The Creator model agreed for TalentX. Existing generic field names retain their
# storage compatibility while their Creator meaning is explicitly defined here.
CAREER_FUNDAMENTAL_WEIGHT = 0.70
RECENT_PRODUCTION_WEIGHT = 0.30
CAREER_BASELINE_REFRESH_WEIGHT = 0.10
CAREER_BASELINE_MIN_REFRESH_DAYS = 7
CAREER_BASELINE_VERSION = "creator-career-baseline-v1"
EVENT_HALF_LIFE_DAYS = 14.0
UNVERIFIED_CURATED_SHRINK_WEIGHT = 0.18
UNVERIFIED_CURATED_SHRINK_TARGET = 80.0

CREATOR_WEIGHTS: dict[str, float] = {
    "audience": 0.30,       # verified audience / reach production
    "performance": 0.25,    # recent engagement / view production
    "achievements": 0.20,   # sustained career production
    "potential": 0.10,      # growth / momentum
    "consistency": 0.10,    # output and performance consistency
    "careerRunway": 0.05,   # career runway / stage
}


def normalize_name(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def creator_2026_anchor_map() -> dict[str, dict[str, Any]]:
    payload = load_json(DEFAULT_2026_ANCHORS, {})
    anchors = payload.get("anchors") if isinstance(payload, dict) else []
    output: dict[str, dict[str, Any]] = {}
    for item in anchors if isinstance(anchors, list) else []:
        if not isinstance(item, dict):
            continue
        key = normalize_name(item.get("name"))
        if key:
            output[key] = dict(item)
    return output


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


CREATOR_2026_ANCHORS = creator_2026_anchor_map()


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


def curated_creator_prior(record: dict[str, Any]) -> dict[str, float] | None:
    """Rebuild the curated Creator prior, applying only verified current anchors."""
    try:
        rank = int(record.get("benchmarkRank"))
        pool = int(record.get("benchmarkPoolSize"))
    except (TypeError, ValueError):
        return None
    if rank <= 0 or pool <= 0:
        return None

    anchor = CREATOR_2026_ANCHORS.get(normalize_name(record.get("name")))
    if anchor:
        try:
            anchor_rank = int(anchor.get("rank"))
        except (TypeError, ValueError):
            anchor_rank = rank
        if anchor_rank > 0:
            rank = anchor_rank
            # Keep the existing 100-person TalentX benchmark scale so a partial
            # 2026 anchor set does not masquerade as a complete 50-person roster.
            pool = max(pool, 100)

    prior = metrics_from_rank("Creator", str(record.get("name") or ""), rank, pool)
    prior["careerRunway"] = career_runway(record)
    prior["availability"] = clamp(prior.get("availability", 80.0), 0, 100)
    return {key: round(clamp(value, 0, 100), 1) for key, value in prior.items()}


def youtube_evidence_weight(record: dict[str, Any], has_curated_prior: bool = False) -> float:
    """Weight YouTube within the recent-production sleeve by platform centrality.

    The career baseline exists for every Creator in v2.3, so source-discovered
    creators no longer default to 100% YouTube just because they lack an
    editorial benchmark prior.
    """
    platform = str(record.get("teamOrPlatform") or "").strip().lower()
    if not platform:
        return 0.50
    parts = [part.strip() for part in platform.replace("&", "/").split("/") if part.strip()]
    has_youtube = any("youtube" in part for part in parts)
    non_youtube = [part for part in parts if "youtube" not in part]
    if has_youtube and not non_youtube:
        return 1.0
    if has_youtube:
        return 0.65
    if any(token in platform for token in ("tiktok", "instagram", "twitch", "podcast", "social platform", "streaming platform")):
        return 0.35
    return 0.50


def career_signal_metrics(record: dict[str, Any], direct: dict[str, float]) -> dict[str, float]:
    """Extract a slower career signal from verified production without recent growth."""
    audience = clamp(direct.get("audience", 50.0), 0, 100)
    achievements = clamp(direct.get("achievements", audience), 0, 100)
    consistency = clamp(direct.get("consistency", 50.0), 0, 100)
    runway = clamp(direct.get("careerRunway", career_runway(record)), 0, 100)
    return {
        "audience": round(audience, 1),
        "performance": round(clamp(audience * 0.65 + consistency * 0.35, 0, 100), 1),
        "achievements": round(achievements, 1),
        "potential": round(clamp(runway * 0.55 + 50.0 * 0.45, 0, 100), 1),
        "consistency": round(consistency, 1),
        "careerRunway": round(runway, 1),
        "availability": round(clamp(direct.get("availability", 80.0), 0, 100), 1),
    }


def normalized_metric_dict(metrics: dict[str, Any]) -> dict[str, float]:
    keys = ("audience", "performance", "achievements", "potential", "consistency", "careerRunway", "availability")
    return {key: round(clamp(metrics.get(key, 50.0 if key != "availability" else 80.0), 0, 100), 1) for key in keys}


def persistent_career_baseline(
    record: dict[str, Any],
    direct: dict[str, float] | None,
    curated_prior: dict[str, float] | None,
    evidence_checked_at: Any = None,
) -> tuple[dict[str, float], str, str | None]:
    """Return a slow-moving career baseline independent of discovery order.

    Verified career state refreshes at most weekly. This prevents a scheduled
    workflow from repeatedly applying the same short-window production evidence
    several times per day.
    """
    stored = record.get("creatorCareerBaselineMetrics")
    stored_update = parse_time(record.get("creatorCareerBaselineUpdatedAt"))
    observation = parse_time(evidence_checked_at) or datetime.now(timezone.utc)

    if isinstance(stored, dict):
        baseline = normalized_metric_dict(stored)
        source = "persisted verified career baseline"
        updated_at = record.get("creatorCareerBaselineUpdatedAt")
        if curated_prior and CREATOR_2026_ANCHORS.get(normalize_name(record.get("name"))):
            anchor_prior = normalized_metric_dict(curated_prior)
            baseline = {
                key: round(max(baseline.get(key, 0.0), anchor_prior.get(key, 0.0)), 1)
                for key in set(baseline) | set(anchor_prior)
            }
            source = "persisted career baseline + verified 2026 career anchor"
    elif curated_prior:
        baseline = normalized_metric_dict(curated_prior)
        source = "curated cross-platform career prior"
        updated_at = None
    elif direct:
        baseline = career_signal_metrics(record, direct)
        source = "verified production career signal"
        updated_at = observation.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    else:
        baseline = normalized_metric_dict(provisional_metrics(record))
        source = "conservative identity/activity career prior"
        updated_at = observation.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    refresh_due = (
        direct is not None
        and (
            not isinstance(stored, dict)
            or stored_update is None
            or (observation - stored_update).total_seconds() >= CAREER_BASELINE_MIN_REFRESH_DAYS * 86400
        )
    )
    if refresh_due and not (direct is not None and not isinstance(stored, dict) and not curated_prior):
        signal = career_signal_metrics(record, direct)
        refresh = CAREER_BASELINE_REFRESH_WEIGHT
        baseline = {
            key: round(
                clamp(
                    baseline.get(key, 50.0) * (1.0 - refresh)
                    + signal.get(key, baseline.get(key, 50.0)) * refresh,
                    0,
                    100,
                ),
                1,
            )
            for key in baseline
        }
        updated_at = observation.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    elif direct is not None and updated_at is None:
        updated_at = observation.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return baseline, source, str(updated_at) if updated_at else None


def blend_career_and_recent(
    baseline: dict[str, float],
    direct: dict[str, float],
    youtube_weight: float,
) -> dict[str, float]:
    effective_recent = RECENT_PRODUCTION_WEIGHT * clamp(youtube_weight, 0.0, 1.0)
    effective_career = 1.0 - effective_recent
    keys = set(baseline) | set(direct)
    return {
        key: round(
            clamp(
                baseline.get(key, direct.get(key, 50.0)) * effective_career
                + direct.get(key, baseline.get(key, 50.0)) * effective_recent,
                0,
                100,
            ),
            1,
        )
        for key in keys
    }


def event_multiplier(record: dict[str, Any], as_of: datetime | None = None) -> float:
    """Apply verified live events with exponential mean reversion to fundamentals."""
    multiplier = 1.0
    now = as_of or datetime.now(timezone.utc)
    events = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    for event in events:
        if not isinstance(event, dict) or event.get("historicalBackfill"):
            continue
        move = number(event.get("movePct"), 0.0)
        if move <= -99.0:
            continue
        when = parse_time(event.get("startedAt") or event.get("time") or event.get("date"))
        if when is None:
            decay = 0.50
        else:
            age_days = max(0.0, (now - when).total_seconds() / 86400.0)
            decay = 0.5 ** (age_days / EVENT_HALF_LIFE_DAYS)
        effective_move = move * decay
        factor = 1.0 + effective_move / 100.0
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
            direct_metrics = verified_metrics(record, evidence, median_pool, recent_pool)
            curated_prior = curated_creator_prior(record)
            career_baseline, baseline_source, baseline_updated_at = persistent_career_baseline(
                record, direct_metrics, curated_prior, evidence.get("checkedAt")
            )
            youtube_weight = youtube_evidence_weight(record, curated_prior is not None)
            metrics = blend_career_and_recent(career_baseline, direct_metrics, youtube_weight)
            effective_recent_weight = round(RECENT_PRODUCTION_WEIGHT * youtube_weight, 3)
            video_count = int(evidence["videoCount"])
            confidence = clamp(0.80 + min(0.08, max(0, video_count - 3) * 0.015) + (0.03 if evidence.get("checkedAt") else 0), 0.80, 0.92)
            if youtube_weight < 1.0:
                confidence = min(confidence, 0.88)
            record["pricingConfidence"] = round(confidence, 2)
            record["dataConfidence"] = max(number(record.get("dataConfidence"), 0), round(confidence, 2))
            record["pricingDataStatus"] = "Evidence enriched — persistent Creator career baseline + recent verified production"
            record["creatorCareerBaselineMetrics"] = career_baseline
            record["creatorCareerBaselineSource"] = baseline_source
            record["creatorCareerBaselineVersion"] = CAREER_BASELINE_VERSION
            if baseline_updated_at:
                record["creatorCareerBaselineUpdatedAt"] = baseline_updated_at
            record["creatorPlatformEvidencePolicy"] = {
                "primaryPlatformLabel": str(record.get("teamOrPlatform") or ""),
                "careerFundamentalWeight": CAREER_FUNDAMENTAL_WEIGHT,
                "recentProductionSleeveWeight": RECENT_PRODUCTION_WEIGHT,
                "youtubeCentralityWithinRecentSleeve": round(youtube_weight, 2),
                "effectiveRecentYouTubeWeight": effective_recent_weight,
                "effectiveCareerBaselineWeight": round(1.0 - effective_recent_weight, 3),
                "curatedPriorAvailable": curated_prior is not None,
                "principle": "Career fundamentals are slow-moving; recent verified YouTube production updates only the recent sleeve and is discounted when YouTube is not the primary platform. No unverified cross-platform counts are invented.",
            }
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
            metrics, baseline_source, baseline_updated_at = persistent_career_baseline(record, None, None)
            record["creatorCareerBaselineMetrics"] = metrics
            record["creatorCareerBaselineSource"] = baseline_source
            record["creatorCareerBaselineVersion"] = CAREER_BASELINE_VERSION
            if baseline_updated_at:
                record["creatorCareerBaselineUpdatedAt"] = baseline_updated_at
            record["pricingConfidence"] = round(clamp(record.get("pricingConfidence", 0.40), 0.32, 0.50), 2)
            record["pricingDataStatus"] = "Provisional — persistent conservative career baseline; platform production unverified"
            record["modelType"] = "Creator provisional career baseline"
            provisional_count += 1
        else:
            curated_prior = curated_creator_prior(record)
            if curated_prior:
                metrics, baseline_source, baseline_updated_at = persistent_career_baseline(record, None, curated_prior)
                record["creatorCareerBaselineMetrics"] = metrics
                record["creatorCareerBaselineSource"] = baseline_source
                record["creatorCareerBaselineVersion"] = CAREER_BASELINE_VERSION
                if baseline_updated_at:
                    record["creatorCareerBaselineUpdatedAt"] = baseline_updated_at
                record["pricingDataStatus"] = "Curated Creator career baseline — direct platform production evidence pending"
            else:
                existing = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
                metrics = {key: round(clamp(existing.get(key, 50.0), 0, 100), 1) for key in ("audience", "performance", "achievements", "potential", "consistency")}
                metrics["careerRunway"] = round(clamp(existing.get("careerRunway", career_runway(record)), 0, 100), 1)
                metrics["availability"] = round(clamp(existing.get("availability", 80.0), 0, 100), 1)
                if not str(record.get("pricingDataStatus") or ""):
                    record["pricingDataStatus"] = "Creator career baseline — direct platform production evidence pending"

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
        if status.startswith("Curated Creator career baseline"):
            universal = (
                universal * (1.0 - UNVERIFIED_CURATED_SHRINK_WEIGHT)
                + UNVERIFIED_CURATED_SHRINK_TARGET * UNVERIFIED_CURATED_SHRINK_WEIGHT
            )
            record["creatorEvidenceUncertainty"] = {
                "directProductionVerified": False,
                "shrinkWeight": UNVERIFIED_CURATED_SHRINK_WEIGHT,
                "shrinkTargetScore": UNVERIFIED_CURATED_SHRINK_TARGET,
                "principle": "Missing direct platform evidence increases uncertainty; it must not preserve an undiluted elite prior.",
            }
        else:
            record.pop("creatorEvidenceUncertainty", None)

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
            explanation["recordedFairValue"] = fundamental
            explanation["creatorAuthoritativeFundamental"] = fundamental
            record["priceExplanation"] = explanation

        # Creator v2.1 is authoritative for Creator fundamentals. Keep the
        # generic fairValue field synchronized so UI/comparison code cannot
        # accidentally display an obsolete generic-v2 valuation.
        record["careerScore"] = score
        record["fundamentalValue"] = fundamental
        record["fairValue"] = fundamental
        record["marketPrice"] = market
        record["pricingModelVersion"] = MODEL_VERSION
        record["creatorFundamentalModelVersion"] = MODEL_VERSION
        if str(record.get("sourceNamespace") or "") == "wikidata-creator":
            record.pop("benchmarkRank", None)
            record.pop("benchmarkPoolSize", None)
            record["rankingStatus"] = "Source-discovered; not part of curated benchmark ranking"
        pricing_v2 = record.get("pricingV2")
        if isinstance(pricing_v2, dict):
            pricing_v2 = dict(pricing_v2)
            pricing_v2["creatorAuthoritativeFundamental"] = fundamental
            pricing_v2["creatorAuthoritativeModelVersion"] = MODEL_VERSION
            record["pricingV2"] = pricing_v2
        record["creatorFundamentalComponents"] = {
            "weights": CREATOR_WEIGHTS,
            "absoluteCreatorScore": round(absolute, 1),
            "creatorPeerPercentile": round(peer_percentile, 4),
            "creatorPeerScore": round(peer_score, 1),
            "universalCareerScore": score,
            "eventMultiplier": round(multiplier, 6),
            "eventHalfLifeDays": EVENT_HALF_LIFE_DAYS,
            "currentCareerAnchor2026": CREATOR_2026_ANCHORS.get(normalize_name(record.get("name"))),
            "principle": "Fundamental price = persistent career baseline plus a 30% recent-production sleeve; verified live events decay toward fundamentals instead of compounding permanently.",
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