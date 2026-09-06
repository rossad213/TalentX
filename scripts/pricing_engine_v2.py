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

MODEL_VERSION = "5.4-uncapped-event-signal"

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

    if is_generic_wikidata_discovery(record):
        confidence = min(confidence, GENERIC_DISCOVERY_CONFIDENCE_CAP)

    floor = curated_confidence_floor(record)
    if floor:
        confidence = max(confidence, floor)

    return round(clamp(confidence, 15, 99), 2)


def talent_score(record: dict[str, Any]) -> float:
    category = str(record.get("primaryCategory") or "Athlete")
    weights = CATEGORY_METRICS.get(category, CATEGORY_METRICS["Athlete"])
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    fallback = clamp(record.get("careerScore", 50))
    weighted = 0.0
    total = 0.0
    for key, weight in weights.items():
        value = metrics.get(key)
        weighted += clamp(value if value is not None else fallback) * weight
        total += weight
    return round(clamp(weighted / max(total, .001)), 2)


def market_score(record: dict[str, Any], talent: float) -> float:
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    audience = clamp(metrics.get("audience", record.get("audienceScore", talent)))
    momentum_pct = clamp(record.get("momentumPct", 0), -20, 20)
    demand_pct = clamp(record.get("demandPremiumPct", 0), -20, 20)

    # A live verified result may legitimately move by more than 2.5%. Do not
    # silently flatten that result here. Because marketScore is a normalized
    # 0–100 context score rather than the live market price itself, compress the
    # event contribution logarithmically instead of imposing a hard ceiling.
    event_pct = num(record.get("lastGameMovePct", 0))
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
    result = dict(record)
    talent = talent_score(result)
    confidence = evidence_confidence(result)
    market = market_score(result, talent)
    situation = situation_score(result)
    expected, generic_fair = fair_value(talent, market, confidence, situation)

    rookie_anchor, rookie_influence = rookie_ipo_value(result)
    fair = generic_fair
    if rookie_anchor is not None and rookie_influence > 0:
        # At IPO the draft/pre-pro anchor is fully authoritative. As verified
        # professional evidence accumulates, the generic v2 career value takes
        # over smoothly according to the already-saved transition percentage.
        fair = round(
            rookie_anchor * rookie_influence + generic_fair * (1.0 - rookie_influence),
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
    result["pricingModelVersion"] = MODEL_VERSION
    result["pricingEngine"] = "v2"
    result["pricingV2"] = {
        "talentScore": talent,
        "marketScore": market,
        "confidenceScore": confidence,
        "situationScore": situation,
        "expectedValueScore": expected,
        "genericFairValue": generic_fair,
        "rookieIpoAnchor": rookie_anchor,
        "rookieInfluence": round(rookie_influence, 4),
        "fairValue": fair,
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
