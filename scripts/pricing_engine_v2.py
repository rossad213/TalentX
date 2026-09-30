#!/usr/bin/env python3
"""TalentX pricing engine v2: talent, market, confidence, situation, and rookie IPO continuity.

This module runs after the existing evidence-enrichment model. It preserves the
v1 fields for rollback/comparison, adds v2 scores, and reprices deterministically.
Drafted rookies retain a league-calibrated IPO anchor that fades only as verified
professional evidence accumulates instead of being erased by a generic confidence
discount immediately after the draft.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from soccer_metric_calibration import (
    SOCCER_METRIC_WEIGHTS,
    calibrate_record as calibrate_soccer_record,
    is_soccer,
)

MODEL_VERSION = "6.0-tennis-mature-ranking-scale"
MOTORSPORT_MODEL_VERSION = "6.1-motorsport-verified-race-ledger"
MUSIC_MODEL_VERSION = "6.2-music-evidence-confidence"
ACTOR_MODEL_VERSION = "6.11-actor-career-anchor-scale"
NFL_MODEL_VERSION = "6.4-nfl-career-tier-scale"
MOTORSPORT_UNVERIFIED_FAIR_VALUE_CEILING = 62.0

CATEGORY_METRICS = {
    "Athlete": {"performance": .34, "achievements": .24, "consistency": .18, "potential": .14, "availability": .10},
    "Music": {"performance": .24, "achievements": .22, "consistency": .20, "audience": .22, "potential": .12},
    "Actor": {"performance": .24, "achievements": .22, "consistency": .20, "audience": .22, "potential": .12},
    "Creator": {"performance": .24, "achievements": .14, "consistency": .18, "audience": .28, "potential": .16},
}

CURATED_NON_ATHLETE_CATEGORIES = {"Music", "Actor"}
GENERIC_DISCOVERY_CONFIDENCE_CAP = 76.0

# Rookie IPOs need to live on the same economic scale as established TalentX
# listings. These are ceilings, not guaranteed prices: the saved rookie score
# determines where a prospect lands below the ceiling. NBA receives the highest
# ceiling because top picks can become high-usage professionals immediately;
# MLB is discounted for the longer typical development runway.
ROOKIE_IPO_CEILINGS = {
    "NFL": 135.0,
    "NBA": 155.0,
    "WNBA": 95.0,
    "NHL": 120.0,
    "MLB": 95.0,
}


def num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, num(value)))


def optional_num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def is_nfl(record: dict[str, Any]) -> bool:
    return str(record.get("leagueOrMedium") or "").strip().upper() == "NFL"


def is_nhl(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("leagueOrMedium") or "").strip().upper() == "NHL"
    )


def is_basketball(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("leagueOrMedium") or "").strip().upper() in {"NBA", "WNBA"}
    )


def is_tennis(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() == "tennis"
    )


def is_motorsport(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("discipline") or "").strip().lower() == "motorsport"
    )


def is_music(record: dict[str, Any]) -> bool:
    return str(record.get("primaryCategory") or "") == "Music"


def is_actor(record: dict[str, Any]) -> bool:
    return str(record.get("primaryCategory") or "") == "Actor"


def is_strict_music_discovery(record: dict[str, Any]) -> bool:
    return (
        is_music(record)
        and not is_curated_non_athlete(record)
        and str(record.get("sourceNamespace") or "") in {
            "wikidata-music-strict", "wikidata-music-expanded", "wikidata-non-athlete",
        }
    )


def music_direct_evidence_weight(record: dict[str, Any]) -> float:
    weights = {
        "music-chart-outcome": 2.5,
        "music-release": 1.5,
        "award": 1.0,
        "nomination": 0.5,
        "music-attention-outcome": 0.75,
    }
    seen: set[str] = set()
    total = 0.0
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or event.get("verified") is False:
            continue
        event_type = str(event.get("eventType") or "")
        weight = weights.get(event_type, 0.0)
        if not weight:
            continue
        key = str(event.get("eventKey") or event.get("eventId") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        total += weight
    return round(total, 3)


def music_pricing_evidence_ceiling(record: dict[str, Any]) -> float:
    if not is_strict_music_discovery(record):
        return 99.0
    identity_verified = bool(record.get("musicCategoryVerified")) or bool(record.get("musicBrainzArtistIds"))
    base = 60.0 if identity_verified else 56.0
    direct = music_direct_evidence_weight(record)
    bonus = min(18.0, 5.5 * math.log1p(max(0.0, direct))) if direct else 0.0
    return round(min(78.0, base + bonus), 2)


def is_actor_discovery(record: dict[str, Any]) -> bool:
    return (
        is_actor(record)
        and not is_curated_non_athlete(record)
        and str(record.get("sourceNamespace") or "") in {
            "wikidata-non-athlete",
            "wikidata-actor-only",
            "wikidata-actor-resolved-from-music",
        }
    )


def actor_direct_evidence_weight(record: dict[str, Any]) -> float:
    weights = {
        "actor-box-office-outcome": 2.5,
        "actor-streaming-outcome": 2.25,
        "actor-release": 1.0,
        "award": 0.75,
        "nomination": 0.35,
        "actor-attention-outcome": 0.75,
    }
    seen: set[str] = set()
    total = 0.0
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or event.get("verified") is False:
            continue
        event_type = str(event.get("eventType") or "")
        weight = weights.get(event_type, 0.0)
        if not weight:
            continue
        key = str(event.get("eventKey") or event.get("eventId") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        total += weight
    return round(total, 3)


def actor_pricing_evidence_ceiling(record: dict[str, Any]) -> float:
    """Confidence guardrail for discovered Actors; evidence narrows uncertainty without repricing careers."""
    if not is_actor_discovery(record):
        return 99.0
    identity_verified = bool(str(record.get("sourceRecordId") or record.get("wikidataSourceRecordId") or "").strip())
    # Keep the evidence-driven confidence band intentionally narrow. A verified
    # identity is already meaningful evidence; additional outcome coverage may
    # improve certainty by only a few points, while actual outcomes themselves
    # move market price through the durable event ledger.
    base = 82.0 if identity_verified else 76.0
    direct = actor_direct_evidence_weight(record)
    bonus = min(4.0, 1.4 * math.log1p(max(0.0, direct))) if direct else 0.0
    return round(min(86.0, base + bonus), 2)


def actor_discovery_fair_value_multiplier(record: dict[str, Any]) -> float:
    """Retained for audit compatibility; discovered Actors no longer receive a discovery haircut."""
    return 1.0


def actor_verified_career_achievement_bonus(record: dict[str, Any]) -> float:
    """Bounded career bonus from verified acting-specific awards and nominations only."""
    if not is_actor_discovery(record):
        return 0.0
    awards = 0
    nominations = 0
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or event.get("verified") is False:
            continue
        event_type = str(event.get("eventType") or "")
        if event_type not in {"award", "nomination"}:
            continue
        name = str(event.get("name") or "").lower()
        if not any(token in name for token in ("actor", "actress", "acting", "performance", "cast", "ensemble")):
            continue
        if event_type == "award":
            awards += 1
        else:
            nominations += 1
    return round(min(5.0, awards * 0.8 + nominations * 0.2), 3)


def actor_career_scale_score(record: dict[str, Any]) -> float | None:
    """Career-first Actor score with proxy ceilings and career-anchor handling."""
    if not is_actor_discovery(record):
        return None
    if bool(record.get("actorCareerAnchor")):
        metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
        performance = clamp(metrics.get("performance", 50))
        achievements = clamp(metrics.get("achievements", 50))
        consistency = clamp(metrics.get("consistency", 50))
        audience = clamp(metrics.get("audience", 50))
        # Career anchors are judged on documented acting body of work rather
        # than age-based potential or generic discovery/legacy placeholders.
        proxy_score = (
            performance * .20
            + achievements * .40
            + consistency * .25
            + audience * .15
        )
        baseline = min(proxy_score, 90.0)
        bonus = actor_verified_career_achievement_bonus(record)
        return round(min(95.0, baseline + bonus), 2)
    proxy_score = clamp(record.get("careerScore", 0), 0, 100)
    # Source discovery uses broad public-footprint proxies. Those may establish
    # a strong career baseline, but they cannot alone place a listing in the
    # elite reviewed-Actor band. Verified acting achievements can lift it there.
    baseline = min(proxy_score, 86.0)
    bonus = actor_verified_career_achievement_bonus(record)
    return round(min(91.0, baseline + bonus), 2)


def actor_career_scale_fair_value(record: dict[str, Any]) -> float | None:
    """Price discovered Actors on the same career-score dollar curve as the curated Actor benchmark."""
    score = actor_career_scale_score(record)
    if score is None:
        return None
    return round(2.0 + 180.0 * (score / 100.0) ** 2, 2)


def actor_effective_career_score(record: dict[str, Any], discovery_score: float | None = None) -> float:
    """Comparable career score for both curated and source-discovered Actors."""
    if discovery_score is not None:
        return clamp(discovery_score)
    return clamp(record.get("careerScore", 0), 0, 100)


def actor_elite_scale_multiplier(score: float) -> float:
    """Lift only the elite Actor tier onto the broader TalentX market scale."""
    score = clamp(score)
    if score <= 90.0:
        return 1.0
    # Middle-market Actor values already align well cross-category. Only the
    # 90-95 elite band receives a gradual lift, topping out at +20%.
    return round(min(1.20, 1.0 + (score - 90.0) * 0.04), 4)


def actor_legacy_market_multiplier(record: dict[str, Any]) -> float:
    """Keep retired/legacy Actor anchors valuable but below comparable active listings."""
    if not is_actor(record):
        return 1.0
    return 0.85 if str(record.get("marketSegment") or "").lower() == "legacy" else 1.0


def motorsport_verified_race_count(record: dict[str, Any]) -> int:
    keys: set[str] = set()
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or event.get("verified") is False:
            continue
        if str(event.get("provider") or "") != "Jolpica F1":
            continue
        if str(event.get("eventType") or "").strip().lower() != "game":
            continue
        key = str(event.get("eventKey") or event.get("eventId") or "").strip()
        if key:
            keys.add(key)
    return len(keys)


def is_unverified_motorsport_discovery(record: dict[str, Any]) -> bool:
    if not is_motorsport(record) or bool(record.get("professionEvidenceVerified")):
        return False
    namespace = str(record.get("sourceNamespace") or "").strip().lower()
    segment = str(record.get("marketSegment") or "").strip().lower()
    return namespace == "wikidata-individual-sport" or segment == "under review"


def tennis_verified_match_count(record: dict[str, Any]) -> int:
    """Count unique source-backed Tennis matches already attached to the listing.

    Prefer provider competition/event IDs so the same ESPN match exposed on both
    ATP and WTA scoreboard surfaces is counted once rather than twice.
    """
    keys: set[str] = set()
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or event.get("verified") is False:
            continue
        if str(event.get("eventType") or "").strip().lower() != "game":
            continue
        tennis_event = (
            str(event.get("sport") or "").strip().lower() == "tennis"
            or str(event.get("tour") or "").strip().upper() in {"ATP", "WTA"}
            or str(event.get("eventKey") or "").startswith("espn-tennis:")
        )
        if not tennis_event:
            continue
        event_id = str(event.get("eventId") or event.get("competitionId") or "").strip()
        key = f"event:{event_id}" if event_id else str(event.get("eventKey") or "").strip()
        if key:
            keys.add(key)
    return len(keys)


def tennis_ranking_confidence_floor(record: dict[str, Any]) -> float:
    """Evidence floor from an official current ATP/WTA ranking snapshot."""
    rank = optional_num(record.get("sourceRank"))
    if rank is None or rank <= 0:
        rank = optional_num(record.get("rosterSourceRank"))
    if rank is None or rank <= 0:
        return 0.0
    # A current ATP/WTA ranking is itself the result of a rolling body of
    # professional match evidence. Top-ranked players should therefore receive
    # mature evidence certainty comparable to established stars in other sports,
    # rather than being discounted like thin-sample team-sport listings merely
    # because Tennis has no team-style professionalGames field.
    if rank <= 10:
        return 90.0
    if rank <= 25:
        return 86.0
    if rank <= 50:
        return 82.0
    if rank <= 100:
        return 78.0
    return 72.0


NFL_POSITION_MARKET_VALUE = {
    "quarterback": 100.0,
    "edge": 88.0,
    "defensive end": 86.0,
    "wide receiver": 84.0,
    "receiver": 84.0,
    "offensive tackle": 82.0,
    "cornerback": 80.0,
    "defensive tackle": 74.0,
    "linebacker": 72.0,
    "tight end": 70.0,
    "running back": 68.0,
    "safety": 66.0,
    "guard": 62.0,
    "center": 62.0,
    "kicker": 42.0,
    "punter": 38.0,
    "long snapper": 32.0,
}


def nfl_position_market_value(record: dict[str, Any]) -> float:
    """Modest economic/scarcity context; never a replacement for production."""
    role = str(record.get("role") or "").lower()
    for token, value in NFL_POSITION_MARKET_VALUE.items():
        if token in role:
            return value
    return 72.0


def nfl_injury_situation_ceiling(record: dict[str, Any]) -> float | None:
    if not bool(record.get("nflInjuryActive")):
        return None
    text = f"{record.get('nflInjuryStatus') or ''} {record.get('nflInjuryType') or ''}".lower()
    if any(token in text for token in ("physically unable", "pup", "injured reserve", "reserve/injured", "out", "inactive")):
        return 24.0
    if "doubtful" in text:
        return 34.0
    if "questionable" in text:
        return 44.0
    if any(token in text for token in ("limited", "day-to-day", "day to day")):
        return 50.0
    if "probable" in text:
        return 55.0
    return 42.0


def is_curated_non_athlete(record: dict[str, Any]) -> bool:
    category = str(record.get("primaryCategory") or "")
    return (
        category in CURATED_NON_ATHLETE_CATEGORIES
        and bool(record.get("nonAthleteRosterVersion"))
        and num(record.get("benchmarkRank")) > 0
    )


def curated_confidence_floor(record: dict[str, Any]) -> float:
    """Return the reviewed-roster evidence floor for Music and Actor records."""
    explicit = num(record.get("curatedEvidenceFloor"))
    if explicit > 0:
        return clamp(explicit, 0, 90)
    if not is_curated_non_athlete(record):
        return 0.0
    rank = max(1.0, num(record.get("benchmarkRank"), 100.0))
    pool = max(rank, num(record.get("benchmarkPoolSize"), 100.0))
    percentile = 1.0 if pool <= 1 else 1.0 - (rank - 1.0) / (pool - 1.0)
    return round(76.0 + 6.0 * clamp(percentile, 0, 1), 2)


def is_generic_wikidata_discovery(record: dict[str, Any]) -> bool:
    return (
        str(record.get("sourceNamespace") or "") == "wikidata-non-athlete"
        and not is_curated_non_athlete(record)
        and not bool(record.get("professionEvidenceVerified"))
    )


def evidence_confidence(record: dict[str, Any]) -> float:
    if is_motorsport(record) and bool(record.get("professionEvidenceVerified")):
        races = max(motorsport_verified_race_count(record), int(max(0.0, num(record.get("motorsportSeasonStarts")))))
        data = clamp(record.get("pricingConfidence", record.get("dataConfidence", 0.75)) * 100)
        metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
        consistency = clamp(metrics.get("consistency", 65))
        achievements = clamp(metrics.get("achievements", 55))
        sample = 100.0 * (1.0 - math.exp(-races / 9.0)) if races else 0.0
        sustained = consistency * .55 + achievements * .45
        confidence = data * .50 + sample * .35 + sustained * .15
        rank = optional_num(record.get("motorsportChampionshipRank"))
        if rank is None or rank <= 0:
            rank = optional_num(record.get("sourceRank"))
        if rank is not None and rank > 0:
            if rank <= 3:
                confidence = max(confidence, 90.0)
            elif rank <= 10:
                confidence = max(confidence, 86.0)
            elif rank <= 22:
                confidence = max(confidence, 80.0)
        return round(clamp(confidence, 25, 96), 2)

    if is_tennis(record):
        # Tennis listings often lack a team-sport-style professionalGames field,
        # even when hundreds of verified ATP/WTA matches are attached. Treat the
        # verified match ledger as the professional sample instead of assigning
        # an established player near-rookie confidence.
        matches = tennis_verified_match_count(record)
        data = clamp(record.get("pricingConfidence", record.get("dataConfidence", 0.45)) * 100)
        metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
        consistency = clamp(metrics.get("consistency", 55))
        achievements = clamp(metrics.get("achievements", 35))
        sustained = consistency * .55 + achievements * .45

        rank_floor = tennis_ranking_confidence_floor(record)
        if matches > 0:
            # Verified match history adds sample maturity. Official ranking
            # evidence provides a floor because a current ATP/WTA rank itself is
            # the outcome of a large body of professional match results.
            sample = 100.0 * (1.0 - math.exp(-matches / 60.0))
            confidence = data * .45 + sample * .35 + sustained * .20
            confidence = max(confidence, rank_floor)
            return round(clamp(confidence, 20, 96), 2)
        if rank_floor > 0:
            return round(clamp(max(data * .45 + sustained * .20, rank_floor), 20, 96), 2)
        # No verified match ledger or official rank yet: fall through to the
        # conservative generic pathway rather than inventing sample maturity.

    # NFL confidence measures certainty in the estimate, not career value.
    # Sample maturity rises quickly and then saturates; achievements and
    # consistency remain valuation inputs instead of being counted again here.
    if is_nfl(record):
        raw_data = optional_num(record.get("pricingConfidence"))
        if raw_data is None:
            raw_data = optional_num(record.get("dataConfidence"))
        if raw_data is None:
            raw_data = 0.45
        if raw_data > 1.0:
            raw_data /= 100.0
        raw_data = clamp(raw_data, 0.0, 1.0)
        data_quality = 65.0 + 35.0 * raw_data

        games = optional_num(record.get("professionalGames"))
        years = optional_num(record.get("experienceYears"))
        if games is not None and games > 0:
            # A 40-60 game NFL sample is already highly informative. Additional
            # veteran games still add certainty, but cannot dominate two players
            # whose football evidence is otherwise comparable.
            sample = 100.0 * (1.0 - math.exp(-games / 16.0))
        elif years is not None and years > 0:
            # Missing career-game totals are not equivalent to zero evidence.
            # Approximate one healthy season as fourteen representative games so
            # established players with a missing game field are not treated like
            # rookies (for example, a Year-3 player with a stale zero total).
            equivalent_games = years * 14.0
            sample = 100.0 * (1.0 - math.exp(-equivalent_games / 16.0))
        else:
            stage = str(record.get("careerStage") or "").lower()
            sample = 18.0 if "rookie" in stage else 25.0

        confidence = data_quality * 0.70 + sample * 0.30
        return round(clamp(confidence, 15, 99), 2)

    games = max(0.0, num(record.get("professionalGames")))
    years = max(0.0, num(record.get("yearsActive")))
    data = clamp(record.get("pricingConfidence", record.get("dataConfidence", 0.45)) * 100)
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    consistency = clamp(metrics.get("consistency", 55))
    achievements = clamp(metrics.get("achievements", 35))

    sample = 100 * (1 - math.exp(-games / 260.0)) if games else 100 * (1 - math.exp(-years / 5.0))
    sustained = consistency * .55 + achievements * .45
    confidence = data * .45 + sample * .35 + sustained * .20
    stage = str(record.get("careerStage") or "").lower()
    if "rookie" in stage or (games and games < 40):
        confidence = min(confidence, 58)
    elif "early" in stage or (games and games < 120):
        confidence = min(confidence, 72)

    if is_generic_wikidata_discovery(record) and not is_actor_discovery(record):
        confidence = min(confidence, GENERIC_DISCOVERY_CONFIDENCE_CAP)
    if is_strict_music_discovery(record):
        confidence = min(confidence, music_pricing_evidence_ceiling(record))
    if is_actor_discovery(record):
        # Verified Actor identity establishes a usable confidence floor.
        # Direct outcome evidence adds only a modest certainty bonus; it does
        # not set career value or apply a second fair-value multiplier.
        identity_verified = bool(str(record.get("sourceRecordId") or record.get("wikidataSourceRecordId") or "").strip())
        direct = actor_direct_evidence_weight(record)
        actor_floor = 72.0 if identity_verified else 68.0
        # Direct evidence improves certainty, but cannot become a substitute for
        # career strength. Even a dense verified ledger adds only a few points
        # of confidence; actual box-office/streaming/award outcomes move price
        # through the event ledger rather than a permanent evidence premium.
        evidence_bonus = min(5.0, 1.75 * math.log1p(max(0.0, direct))) if direct else 0.0
        confidence = max(confidence, actor_floor + evidence_bonus)
        confidence = min(confidence, actor_pricing_evidence_ceiling(record))
    if is_unverified_motorsport_discovery(record):
        confidence = min(confidence, 56.0)

    floor = curated_confidence_floor(record)
    if floor:
        confidence = max(confidence, floor)

    return round(clamp(confidence, 15, 99), 2)


def pricing_metrics(record: dict[str, Any]) -> dict[str, Any]:
    if is_soccer(record) and isinstance(record.get("soccerGlobalMetrics"), dict):
        return record["soccerGlobalMetrics"]
    return record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}


def talent_score(record: dict[str, Any]) -> float:
    category = str(record.get("primaryCategory") or "Athlete")
    weights = SOCCER_METRIC_WEIGHTS if is_soccer(record) else CATEGORY_METRICS.get(category, CATEGORY_METRICS["Athlete"])
    metrics = pricing_metrics(record)
    fallback = clamp(record.get("careerScore", 50))
    weighted = 0.0
    total = 0.0
    for key, weight in weights.items():
        value = metrics.get(key)
        weighted += clamp(value if value is not None else fallback) * weight
        total += weight
    return round(clamp(weighted / max(total, .001)), 2)


def market_score(record: dict[str, Any], talent: float) -> float:
    metrics = pricing_metrics(record)

    if is_nfl(record):
        # NFL market context stays independent of Talent and verified game moves.
        # A modest position-value component reflects scarcity/economic leverage
        # (especially elite quarterbacks) without overpowering production.
        audience = clamp(metrics.get("audience", record.get("audienceScore", 50)))
        attention = clamp(metrics.get("attention", record.get("nflAttentionScore", 50)))
        liquidity = clamp(record.get("nflLiquidityScore", record.get("tradeDemandScore", 50)))
        position_value = nfl_position_market_value(record)
        score = audience * .42 + attention * .28 + liquidity * .15 + position_value * .15
        return round(clamp(score), 2)

    audience = clamp(metrics.get("audience", record.get("audienceScore", talent)))
    momentum_pct = clamp(record.get("momentumPct", 0), -20, 20)
    demand_pct = clamp(record.get("demandPremiumPct", 0), -20, 20)

    # A live verified result may legitimately move by more than 2.5%. Do not
    # silently flatten that result here. Because marketScore is a normalized
    # 0–100 context score rather than the live market price itself, compress the
    # event contribution logarithmically instead of imposing a hard ceiling.
    # NHL and basketball verified game moves already live in the durable
    # market-price ledger. Reusing the previous game's move inside fair value
    # compounds the same event again on rebuilds. Keep event outcomes in the
    # observable market ledger while fundamentals remain evidence-driven.
    event_pct = 0.0 if (is_nhl(record) or is_basketball(record) or is_tennis(record) or is_motorsport(record) or is_music(record) or is_actor(record)) else num(record.get("lastGameMovePct", 0))
    event_signal = math.copysign(math.log1p(abs(event_pct)) * 2.5, event_pct) if event_pct else 0.0
    current_signal = 50 + momentum_pct * 1.25 + demand_pct * .8 + event_signal
    score = audience * .38 + talent * .37 + clamp(current_signal) * .25
    return round(clamp(score), 2)


def situation_score(record: dict[str, Any]) -> float:
    """Measure current opportunity/environment without changing underlying talent."""
    score = 50.0
    adjustment = clamp(record.get("situationAdjustmentPct", 0), -20, 20)
    score += adjustment * 1.5

    category = str(record.get("primaryCategory") or "")
    status = str(record.get("careerStatus") or "").lower()
    role_status = str(record.get("roleStatus") or "").lower()
    if category == "Athlete":
        if record.get("starter") is True or role_status in {"starter", "first team", "starting"}:
            score += 6
        elif role_status in {"bench", "reserve", "demoted"}:
            score -= 6
        if "injured" in status or "suspended" in status:
            score -= 10
        if is_nfl(record):
            injury_ceiling = nfl_injury_situation_ceiling(record)
            if injury_ceiling is not None:
                score = min(score, injury_ceiling)
    else:
        if role_status in {"lead", "headliner", "featured"}:
            score += 5
        elif role_status in {"inactive", "paused", "shelved"}:
            score -= 7

    return round(clamp(score, 20, 80), 2)


def fair_value(talent: float, market: float, confidence: float, situation: float) -> tuple[float, float]:
    certainty = .38 + .62 * confidence / 100.0
    expected = talent * certainty
    blended = expected * .74 + market * .20 + situation * .06
    value = 4.0 + .0325 * blended * blended
    return round(blended, 2), round(max(4.0, min(350.0, value)), 2)


def nfl_career_tier_multiplier(
    record: dict[str, Any],
    talent: float,
    rookie_influence: float = 0.0,
) -> float:
    """Spread the NFL middle/lower tiers without flattening elite careers."""
    if not is_nfl(record) or rookie_influence >= 0.50:
        return 1.0
    score = clamp(talent)
    if score >= 70.0:
        return 1.0
    if score >= 60.0:
        return round(0.84 + (score - 60.0) * (0.16 / 10.0), 4)
    if score >= 45.0:
        return round(0.68 + (score - 45.0) * (0.16 / 15.0), 4)
    if score <= 25.0:
        return 0.55
    return round(0.55 + (score - 25.0) * (0.13 / 20.0), 4)


def rookie_ipo_value(record: dict[str, Any]) -> tuple[float | None, float]:
    """Return a calibrated rookie IPO anchor and its remaining draft influence.

    v1 already stores the explainable rookie score and the percentage of the
    valuation that should still be driven by draft/pre-pro evidence. v2 should
    not discard that information simply because professional sample confidence
    is intentionally low for a rookie.
    """
    pricing = record.get("rookiePricing") if isinstance(record.get("rookiePricing"), dict) else None
    if not pricing:
        return None, 0.0
    league = str(pricing.get("draftSport") or record.get("leagueOrMedium") or "")
    ceiling = ROOKIE_IPO_CEILINGS.get(league)
    score = num(pricing.get("rookieScore"), -1)
    if ceiling is None or score < 0:
        return None, 0.0
    influence = clamp(pricing.get("draftInfluencePct", 0), 0, 100) / 100.0
    # Same non-linear shape as the broader TalentX fundamental curve, with a
    # league-specific rookie ceiling. A 90-score prospect reaches 81% of ceiling.
    anchor = 4.0 + ceiling * (clamp(score) / 100.0) ** 2
    return round(anchor, 2), influence


def apply_v2(record: dict[str, Any]) -> dict[str, Any]:
    result = calibrate_soccer_record(dict(record))
    v1_fundamental = optional_num(result.get("fundamentalValue"))
    talent = talent_score(result)
    confidence = evidence_confidence(result)
    market = market_score(result, talent)
    situation = situation_score(result)
    expected, generic_fair = fair_value(talent, market, confidence, situation)

    rookie_anchor, rookie_influence = rookie_ipo_value(result)
    nfl_tier_multiplier = nfl_career_tier_multiplier(result, talent, rookie_influence)
    career_fair = round(generic_fair * nfl_tier_multiplier, 2)
    actor_scale = actor_discovery_fair_value_multiplier(result)
    actor_career_score = actor_career_scale_score(result)
    actor_career_bonus = actor_verified_career_achievement_bonus(result)
    actor_career_fair = actor_career_scale_fair_value(result)
    actor_effective_score = actor_effective_career_score(result, actor_career_score)
    actor_elite_multiplier = actor_elite_scale_multiplier(actor_effective_score) if is_actor(result) else 1.0
    actor_legacy_multiplier = actor_legacy_market_multiplier(result)
    actor_curated_prior = None
    fair = career_fair
    if is_actor_discovery(result) and actor_career_fair is not None:
        # Career score sets the Actor baseline on the same dollar curve as the
        # reviewed benchmark. Confidence remains descriptive; verified outcomes
        # move the live market through the event ledger.
        fair = actor_career_fair
    elif is_actor(result) and is_curated_non_athlete(result):
        # Rebuild the reviewed Actor prior from career score every time. Using a
        # previously written fundamentalValue here compounds the elite scale on
        # repeated rebuilds, so curated Actor pricing must be idempotent.
        curated_score = clamp(result.get("careerScore", 0), 0, 100)
        actor_curated_prior = round(2.0 + 180.0 * (curated_score / 100.0) ** 2, 2)
        fair = actor_curated_prior
    if is_actor(result):
        fair = round(fair * actor_elite_multiplier * actor_legacy_multiplier, 2)
    if is_unverified_motorsport_discovery(result):
        fair = round(min(fair, MOTORSPORT_UNVERIFIED_FAIR_VALUE_CEILING), 2)
    if rookie_anchor is not None and rookie_influence > 0:
        # At IPO the draft/pre-pro anchor is fully authoritative. As verified
        # professional evidence accumulates, the career model takes over.
        fair = round(
            rookie_anchor * rookie_influence + career_fair * (1.0 - rookie_influence),
            2,
        )

    result.setdefault("pricingV1", {
        "marketPrice": result.get("marketPrice"),
        "fundamentalValue": result.get("fundamentalValue"),
        "careerScore": result.get("careerScore"),
        "pricingModelVersion": result.get("pricingModelVersion"),
    })
    result["talentScore"] = talent
    result["marketScore"] = market
    result["confidenceScore"] = confidence
    result["situationScore"] = situation
    result["expectedValueScore"] = expected
    result["fairValue"] = fair
    result["fundamentalValue"] = fair
    result["marketPrice"] = fair
    if is_nfl(result):
        result["pricingModelVersion"] = NFL_MODEL_VERSION
    elif is_motorsport(result):
        result["pricingModelVersion"] = MOTORSPORT_MODEL_VERSION
    elif is_music(result):
        result["pricingModelVersion"] = MUSIC_MODEL_VERSION
    elif is_actor(result):
        result["pricingModelVersion"] = ACTOR_MODEL_VERSION
    else:
        result["pricingModelVersion"] = MODEL_VERSION
    result["pricingEngine"] = "v2"
    result["pricingV2"] = {
        "talentScore": talent,
        "marketScore": market,
        "confidenceScore": confidence,
        "situationScore": situation,
        "expectedValueScore": expected,
        "genericFairValue": generic_fair,
        "nflCareerTierMultiplier": nfl_tier_multiplier if is_nfl(result) else None,
        "nflCareerTierFairValue": career_fair if is_nfl(result) else None,
        "rookieIpoAnchor": rookie_anchor,
        "rookieInfluence": round(rookie_influence, 4),
        "fairValue": fair,
        "soccerCalibrationVersion": result.get("soccerCalibrationVersion"),
        "motorsportEvidenceGateCeiling": (
            MOTORSPORT_UNVERIFIED_FAIR_VALUE_CEILING
            if is_unverified_motorsport_discovery(result)
            else None
        ),
        "motorsportVerifiedRaceCount": (
            motorsport_verified_race_count(result) if is_motorsport(result) else None
        ),
        "musicIdentityConfidenceScore": (
            95.0 if is_strict_music_discovery(result)
            and (bool(result.get("musicCategoryVerified")) or bool(result.get("musicBrainzArtistIds")))
            else None
        ),
        "musicPricingEvidenceCeiling": (
            music_pricing_evidence_ceiling(result) if is_strict_music_discovery(result) else None
        ),
        "musicDirectEvidenceWeight": (
            music_direct_evidence_weight(result) if is_music(result) else None
        ),
        "actorIdentityConfidenceScore": (
            90.0 if is_actor_discovery(result) and bool(str(result.get("sourceRecordId") or "").strip())
            else None
        ),
        "actorPricingEvidenceCeiling": (
            actor_pricing_evidence_ceiling(result) if is_actor_discovery(result) else None
        ),
        "actorDirectEvidenceWeight": (
            actor_direct_evidence_weight(result) if is_actor(result) else None
        ),
        "actorDiscoveryFairValueMultiplier": (
            actor_scale if is_actor_discovery(result) else None
        ),
        "actorCareerScaleScore": (
            actor_career_score if is_actor_discovery(result) else None
        ),
        "actorVerifiedCareerAchievementBonus": (
            actor_career_bonus if is_actor_discovery(result) else None
        ),
        "actorCareerScaleFairValue": (
            actor_career_fair if is_actor_discovery(result) else None
        ),
        "actorEffectiveCareerScore": (
            actor_effective_score if is_actor(result) else None
        ),
        "actorEliteScaleMultiplier": (
            actor_elite_multiplier if is_actor(result) else None
        ),
        "actorLegacyMarketMultiplier": (
            actor_legacy_multiplier if is_actor(result) else None
        ),
        "actorCuratedBaselinePrior": (
            actor_curated_prior if is_actor(result) and is_curated_non_athlete(result) else None
        ),
    }
    if isinstance(result.get("rookiePricing"), dict) and rookie_anchor is not None:
        result["rookiePricing"] = {
            **result["rookiePricing"],
            "calibratedIpoPrice": rookie_anchor,
            "v2GenericFairValue": generic_fair,
            "v2BlendedFairValue": fair,
        }
    trend = [num(x) for x in result.get("trend", []) if num(x) > 0]
    if trend:
        scale = fair / trend[-1] if trend[-1] else 1
        result["trend"] = [round(max(1, x * scale), 2) for x in trend[-18:]]
    else:
        result["trend"] = [fair]
    return result


def process(path: Path) -> int:
    if not path.exists():
        return 0
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{path} must contain an array")
    updated = [apply_v2(x) for x in records if isinstance(x, dict)]
    compact = path.name == "current_catalog.json"
    path.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    if path.name == "current_catalog.json":
        csv_path = path.with_suffix(".csv")
        fields = sorted({k for r in updated for k, v in r.items() if not isinstance(v, (dict, list))})
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader(); writer.writerows(updated)
    return len(updated)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    args = parser.parse_args()
    totals = {}
    for filename in ("current_seed.json", "current_catalog.json", "legacy_catalog_v2.json"):
        count = process(args.data_dir / filename)
        if count: totals[filename] = count
    manifest_path = args.data_dir / "catalog_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update({
        "pricingModelVersion": MODEL_VERSION,
        "pricingEngine": "v2",
        "pricingV2CatalogsProcessed": totals,
        "rookieIpoCeilings": ROOKIE_IPO_CEILINGS,
        "pricingRule": "Category-normalized talent is discounted by evidence confidence and adjusted by current market and situation evidence. Drafted rookies retain a league-calibrated IPO anchor that fades only as verified professional evidence accumulates. Verified game-event contributions are compressed smoothly in the normalized market score rather than hard-capped. Curated Music and Actor reviews receive a moderate evidence floor; generic Wikidata-only discoveries are capped until profession-specific evidence is present.",
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Applied pricing engine v2:", totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
