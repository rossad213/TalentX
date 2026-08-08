#!/usr/bin/env python3
"""Outcome-driven TalentX pricing for recent Music and Actor releases.

Supported outcome evidence:
* Music: Wikidata chart placements (P2291 with ranking qualifier P1352).
* Actors: Wikidata box office (P2142) versus production cost (P2130) when
  both values use the same currency.
* Music + Actors: Wikimedia pageview momentum measured around a verified
  release event. Pageviews are treated as audience-attention evidence, not
  as proof of artistic or commercial success.

The script only evaluates profiles that already have a verified release event
in ``priceEvents``. Outcome events are durable, deduplicated, and bounded.
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

from non_athlete_event_refresh import clamp, iso, make_session, number, parse_time, qid_for, utc_now

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DEFAULT_MANIFEST = ROOT / "data" / "non_athlete_outcome_manifest.json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIMEDIA_PAGEVIEWS = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia.org/all-access/all-agents/{title}/daily/{start}/{end}"
)
MODEL_VERSION = "1.0-outcome-driven-music-actor-pricing"
MAX_OUTCOME_MOVE_PCT = 1.50
MAX_PRICE_EVENTS = 1500

MUSIC_CHART_TARGET = {1: 1.25, 5: 0.95, 10: 0.70, 40: 0.35, 100: 0.12}
ACTOR_BOX_OFFICE_TARGETS = {
    "severe-underperform": -0.90,
    "underperform": -0.55,
    "cost-recovered": 0.35,
    "strong": 0.90,
    "breakout": 1.35,
}
ATTENTION_TARGETS = {"cool": -0.25, "warm": 0.25, "hot": 0.50, "breakout": 0.75}


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def wikidata_entities(session: requests.Session, qids: set[str], timeout: float) -> tuple[dict[str, dict[str, Any]], list[str]]:
    output: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    ordered = sorted(qid for qid in qids if re.fullmatch(r"Q\d+", qid))
    for start in range(0, len(ordered), 50):
        batch = ordered[start:start + 50]
        if not batch:
            continue
        try:
            response = session.get(
                WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "claims|labels|sitelinks",
                    "languages": "en",
                    "sitefilter": "enwiki",
                    "format": "json",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json().get("entities", {})
            for qid, entity in payload.items():
                if isinstance(entity, dict) and not entity.get("missing"):
                    output[str(qid)] = entity
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Wikidata outcome batch {start // 50 + 1}: {type(exc).__name__}: {exc}")
    return output, errors


def item_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    qid = str(value.get("id") or "")
    return qid if re.fullmatch(r"Q\d+", qid) else ""


def quantity_claims(entity: dict[str, Any], prop: str) -> list[tuple[float, str]]:
    output: list[tuple[float, str]] = []
    claims = entity.get("claims", {}) if isinstance(entity.get("claims"), dict) else {}
    for claim in claims.get(prop, []) if isinstance(claims.get(prop), list) else []:
        if not isinstance(claim, dict):
            continue
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if not isinstance(value, dict):
            continue
        amount = number(value.get("amount"), float("nan"))
        unit = str(value.get("unit") or "")
        if math.isfinite(amount) and amount >= 0 and unit:
            output.append((amount, unit))
    return output


def best_box_office_ratio(entity: dict[str, Any]) -> tuple[float, float, float, str] | None:
    candidates: list[tuple[float, float, float, str]] = []
    for gross, gross_unit in quantity_claims(entity, "P2142"):
        for cost, cost_unit in quantity_claims(entity, "P2130"):
            if gross_unit == cost_unit and cost > 0:
                candidates.append((gross / cost, gross, cost, gross_unit))
    return max(candidates, key=lambda item: item[0]) if candidates else None


def rank_value(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("amount")
    parsed = number(value, float("nan"))
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return int(round(parsed))


def music_chart_positions(entity: dict[str, Any]) -> list[tuple[int, str]]:
    output: list[tuple[int, str]] = []
    claims = entity.get("claims", {}) if isinstance(entity.get("claims"), dict) else {}
    for claim in claims.get("P2291", []) if isinstance(claims.get("P2291"), list) else []:
        if not isinstance(claim, dict):
            continue
        chart = item_id(claim.get("mainsnak", {}).get("datavalue", {}).get("value"))
        qualifiers = claim.get("qualifiers", {}) if isinstance(claim.get("qualifiers"), dict) else {}
        for qualifier in qualifiers.get("P1352", []) if isinstance(qualifiers.get("P1352"), list) else []:
            value = qualifier.get("datavalue", {}).get("value") if isinstance(qualifier, dict) else None
            rank = rank_value(value)
            if rank is not None:
                output.append((rank, chart))
    return sorted(output)


def chart_target(rank: int) -> tuple[str, float]:
    for threshold, move in MUSIC_CHART_TARGET.items():
        if rank <= threshold:
            return f"top-{threshold}", move
    return "charted", 0.05


def actor_box_office_target(ratio: float, age_days: float) -> tuple[str, float] | None:
    if ratio >= 3.0:
        return "breakout", ACTOR_BOX_OFFICE_TARGETS["breakout"]
    if ratio >= 2.0:
        return "strong", ACTOR_BOX_OFFICE_TARGETS["strong"]
    if ratio >= 1.0:
        return "cost-recovered", ACTOR_BOX_OFFICE_TARGETS["cost-recovered"]
    if age_days >= 21 and ratio < 0.50:
        return "severe-underperform", ACTOR_BOX_OFFICE_TARGETS["severe-underperform"]
    if age_days >= 14 and ratio < 0.75:
        return "underperform", ACTOR_BOX_OFFICE_TARGETS["underperform"]
    return None


def enwiki_title(entity: dict[str, Any]) -> str:
    sitelinks = entity.get("sitelinks", {}) if isinstance(entity.get("sitelinks"), dict) else {}
    enwiki = sitelinks.get("enwiki") if isinstance(sitelinks.get("enwiki"), dict) else {}
    return str(enwiki.get("title") or "").strip()


def pageview_series(session: requests.Session, title: str, start: datetime, end: datetime, timeout: float) -> list[tuple[datetime, int]]:
    if not title or end < start:
        return []
    url = WIKIMEDIA_PAGEVIEWS.format(
        title=quote(title.replace(" ", "_"), safe=""),
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
    return output


def attention_ratio(points: list[tuple[datetime, int]], event_time: datetime) -> tuple[float, float, float] | None:
    event_day = event_time.replace(hour=0, minute=0, second=0, microsecond=0)
    baseline_start = event_day - timedelta(days=21)
    baseline_end = event_day - timedelta(days=1)
    outcome_start = event_day
    outcome_end = event_day + timedelta(days=6)
    baseline = [views for when, views in points if baseline_start <= when <= baseline_end]
    outcome = [views for when, views in points if outcome_start <= when <= outcome_end]
    if len(baseline) < 14 or len(outcome) < 5:
        return None
    baseline_avg = sum(baseline) / len(baseline)
    outcome_avg = sum(outcome) / len(outcome)
    if baseline_avg <= 0:
        return None
    return outcome_avg / baseline_avg, baseline_avg, outcome_avg


def attention_target(ratio: float) -> tuple[str, float] | None:
    if ratio >= 3.0:
        return "breakout", ATTENTION_TARGETS["breakout"]
    if ratio >= 2.0:
        return "hot", ATTENTION_TARGETS["hot"]
    if ratio >= 1.5:
        return "warm", ATTENTION_TARGETS["warm"]
    if ratio <= 0.65:
        return "cool", ATTENTION_TARGETS["cool"]
    return None


def record_multiplier(record: dict[str, Any]) -> float:
    metrics = record.get("activeMetrics") if isinstance(record.get("activeMetrics"), dict) else {}
    audience = clamp(metrics.get("audience", 50), 0, 100)
    confidence = number(record.get("confidenceScore"), -1)
    if confidence < 0:
        raw = number(record.get("pricingConfidence", record.get("dataConfidence", 0.6)), 0.6)
        confidence = raw * 100 if raw <= 1 else raw
    return clamp(0.82 + audience / 650 + confidence / 1200, 0.82, 1.08)


def outcome_event(record: dict[str, Any], event_key: str, event_type: str, label: str, provider: str, source_url: str, target_move: float, occurred_at: datetime, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventKey": event_key,
        "eventId": event_key,
        "eventType": event_type,
        "provider": provider,
        "sourceUrl": source_url,
        "name": label,
        "startedAt": iso(occurred_at),
        "targetOutcomeMovePct": round(target_move, 3),
        "artist": record.get("name"),
        **details,
    }


def explanation(event: dict[str, Any], move: float, price: float) -> dict[str, Any]:
    kind = str(event.get("eventType") or "")
    if kind == "music-chart-outcome":
        rank = event.get("chartRank")
        headline = f"Chart performance: #{rank}" if rank else "Chart performance update"
        summary = [
            "A verified chart placement was added for a recent release.",
            "The move reflects chart outcome evidence rather than the release announcement alone.",
        ]
    elif kind == "actor-box-office-outcome":
        ratio = number(event.get("boxOfficeToCostRatio"), 0)
        headline = "Strong box-office outcome" if move > 0 else "Weak box-office outcome"
        summary = [
            f"Verified box office is about {ratio:.2f}× the recorded production cost.",
            "The calculation is used only when both Wikidata money values use the same currency.",
        ]
    else:
        ratio = number(event.get("attentionRatio"), 0)
        headline = "Audience attention surge" if move > 0 else "Audience attention cooled"
        summary = [
            f"Post-event English Wikipedia pageviews were {ratio:.2f}× the pre-event baseline.",
            "Pageviews measure attention, not reviews, sales, streams, or box-office success.",
        ]
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
        "pricingMode": "Verified outcome event; event market price preserved",
        "source": event.get("provider"),
        "sourceUrl": event.get("sourceUrl"),
    }


def apply_outcome_events(record: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    result = dict(record)
    prior = [dict(item) for item in result.get("priceEvents", []) if isinstance(item, dict)]
    known = {str(item.get("eventKey") or item.get("eventId") or "") for item in prior}
    pending = [event for event in events if str(event.get("eventKey") or "") not in known]
    pending.sort(key=lambda item: str(item.get("startedAt") or ""))
    if not pending:
        return result, 0

    old_price = max(0.01, number(result.get("marketPrice"), 0.01))
    price = old_price
    trend = [number(item) for item in result.get("trend", []) if number(item) > 0] or [old_price]
    stored: list[dict[str, Any]] = []
    for event in pending:
        target = clamp(number(event.get("targetOutcomeMovePct"), 0), -MAX_OUTCOME_MOVE_PCT, MAX_OUTCOME_MOVE_PCT)
        move = clamp(target * record_multiplier(result), -MAX_OUTCOME_MOVE_PCT, MAX_OUTCOME_MOVE_PCT)
        if abs(move) < 0.01:
            continue
        before = price
        after = round(before * (1 + move / 100), 2)
        actual = round((after / before - 1) * 100, 3)
        price = after
        stored.append({**event, "priceBefore": round(before, 2), "priceAfter": round(after, 2), "movePct": actual, "verified": True})
        trend = trend[-17:] + [after]

    if not stored:
        return result, 0
    result["priceEvents"] = (prior + stored)[-MAX_PRICE_EVENTS:]
    result["previousMarketPrice"] = round(old_price, 2)
    result["marketPrice"] = round(price, 2)
    cumulative = round((price / old_price - 1) * 100, 2)
    result["dailyChange"] = cumulative
    result["hourlyChangePct"] = cumulative
    result["trend"] = [round(value, 2) for value in trend[-18:]]
    latest = stored[-1]
    result["lastPriceEventAt"] = latest.get("startedAt")
    result["lastPriceEvent"] = latest.get("name")
    result["lastPriceEventId"] = latest.get("eventKey")
    result["lastEventMovePct"] = latest.get("movePct")
    result["lastEventType"] = latest.get("eventType")
    result["lastEventSource"] = latest.get("provider")
    result["lastPriceRefreshAt"] = iso(utc_now())
    result["priceExplanation"] = explanation(latest, number(latest.get("movePct"), 0), price)
    return result, len(stored)


def recent_release_events(record: dict[str, Any], now: datetime, lookback_days: int) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=max(1, lookback_days))
    output: list[dict[str, Any]] = []
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or str(event.get("eventType") or "") not in {"music-release", "actor-release"}:
            continue
        when = parse_time(event.get("startedAt"))
        if when is None or when < cutoff or when > now:
            continue
        work_qid = str(event.get("workQid") or event.get("eventId") or "")
        if not re.fullmatch(r"Q\d+", work_qid):
            continue
        output.append({**event, "_when": when, "_workQid": work_qid})
    return output


def state_delta(previous_target: float, target: float) -> float:
    return clamp(target - previous_target, -MAX_OUTCOME_MOVE_PCT, MAX_OUTCOME_MOVE_PCT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--minimum-interval-hours", type=float, default=6.0)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--max-pageview-checks", type=int, default=80)
    parser.add_argument("--max-events", type=int, default=200)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    records = load_json(args.catalog, [])
    if not isinstance(records, list) or not records:
        raise SystemExit(f"{args.catalog} must contain a non-empty array")
    records = [dict(item) for item in records if isinstance(item, dict)]
    manifest = load_json(args.manifest, {})
    manifest = manifest if isinstance(manifest, dict) else {}

    now = utc_now()
    previous_run = parse_time(manifest.get("lastSuccessfulAt"))
    if previous_run and (now - previous_run).total_seconds() < max(0.0, args.minimum_interval_hours) * 3600:
        print(f"Outcome refresh skipped; last successful scan was {manifest.get('lastSuccessfulAt')}.")
        return 0

    candidate_records: dict[int, list[dict[str, Any]]] = {}
    work_qids: set[str] = set()
    person_qids: set[str] = set()
    for index, record in enumerate(records):
        if str(record.get("primaryCategory") or "") not in {"Music", "Actor"}:
            continue
        releases = recent_release_events(record, now, args.lookback_days)
        if not releases:
            continue
        candidate_records[index] = releases
        work_qids.update(str(event["_workQid"]) for event in releases)
        qid = qid_for(record)
        if qid:
            person_qids.add(qid)

    session = make_session()
    work_entities, errors = wikidata_entities(session, work_qids, args.request_timeout)
    person_entities, person_errors = wikidata_entities(session, person_qids, args.request_timeout)
    errors.extend(person_errors)

    chart_labels_needed: set[str] = set()
    chart_positions_by_work: dict[str, list[tuple[int, str]]] = {}
    for qid, entity in work_entities.items():
        positions = music_chart_positions(entity)
        chart_positions_by_work[qid] = positions
        chart_labels_needed.update(chart for _rank, chart in positions if chart)
    chart_entities, chart_errors = wikidata_entities(session, chart_labels_needed, args.request_timeout)
    errors.extend(chart_errors)
    chart_labels = {qid: str(entity.get("labels", {}).get("en", {}).get("value") or qid) for qid, entity in chart_entities.items()}

    chart_state = manifest.get("musicChartState") if isinstance(manifest.get("musicChartState"), dict) else {}
    box_state = manifest.get("actorBoxOfficeState") if isinstance(manifest.get("actorBoxOfficeState"), dict) else {}
    attention_state = manifest.get("attentionState") if isinstance(manifest.get("attentionState"), dict) else {}
    next_chart_state = dict(chart_state)
    next_box_state = dict(box_state)
    next_attention_state = dict(attention_state)
    events_by_index: dict[int, list[dict[str, Any]]] = {}
    pageview_checks = 0
    event_budget = max(0, args.max_events)

    for index, releases in candidate_records.items():
        if event_budget <= 0:
            break
        record = records[index]
        category = str(record.get("primaryCategory") or "")
        pending: list[dict[str, Any]] = []
        person_qid = qid_for(record)
        title = enwiki_title(person_entities.get(person_qid, {}))

        for base_event in sorted(releases, key=lambda item: item["_when"]):
            if event_budget <= 0:
                break
            work_qid = str(base_event["_workQid"])
            when = base_event["_when"]
            age_days = (now - when).total_seconds() / 86400.0
            work_entity = work_entities.get(work_qid, {})

            if category == "Music":
                positions = chart_positions_by_work.get(work_qid, [])
                if positions:
                    best_rank, chart_qid = positions[0]
                    tier, target = chart_target(best_rank)
                    state_key = f"{index}:{work_qid}"
                    old_state = chart_state.get(state_key) if isinstance(chart_state.get(state_key), dict) else {}
                    previous_target = number(old_state.get("targetMovePct"), 0)
                    delta = state_delta(previous_target, target)
                    next_chart_state[state_key] = {"bestRank": best_rank, "chartQid": chart_qid, "tier": tier, "targetMovePct": target, "updatedAt": iso(now)}
                    if abs(delta) >= 0.05:
                        pending.append(outcome_event(
                            record,
                            f"wikidata:music-chart:{person_qid}:{work_qid}:{tier}",
                            "music-chart-outcome",
                            f"Chart result: #{best_rank} — {chart_labels.get(chart_qid, 'record chart')}",
                            "Wikidata",
                            f"https://www.wikidata.org/wiki/{work_qid}",
                            delta,
                            now,
                            {"workQid": work_qid, "chartQid": chart_qid, "chartName": chart_labels.get(chart_qid, chart_qid), "chartRank": best_rank, "outcomeTier": tier},
                        ))

            if category == "Actor":
                ratio_info = best_box_office_ratio(work_entity)
                if ratio_info:
                    ratio, gross, cost, unit = ratio_info
                    target_info = actor_box_office_target(ratio, age_days)
                    if target_info:
                        tier, target = target_info
                        state_key = f"{index}:{work_qid}"
                        old_state = box_state.get(state_key) if isinstance(box_state.get(state_key), dict) else {}
                        previous_target = number(old_state.get("targetMovePct"), 0)
                        delta = state_delta(previous_target, target)
                        next_box_state[state_key] = {"ratio": round(ratio, 4), "tier": tier, "targetMovePct": target, "gross": gross, "cost": cost, "unit": unit, "updatedAt": iso(now)}
                        if abs(delta) >= 0.05:
                            pending.append(outcome_event(
                                record,
                                f"wikidata:box-office:{person_qid}:{work_qid}:{tier}",
                                "actor-box-office-outcome",
                                f"Box-office outcome: {ratio:.2f}× production cost",
                                "Wikidata",
                                f"https://www.wikidata.org/wiki/{work_qid}",
                                delta,
                                now,
                                {"workQid": work_qid, "boxOfficeToCostRatio": round(ratio, 4), "boxOffice": gross, "productionCost": cost, "currencyUnit": unit, "outcomeTier": tier},
                            ))

            attention_key = str(base_event.get("eventKey") or "")
            if title and attention_key and 7 <= age_days <= max(7, args.lookback_days) and attention_key not in attention_state and pageview_checks < max(0, args.max_pageview_checks):
                pageview_checks += 1
                start = when.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=21)
                end = when.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=6)
                try:
                    measured = attention_ratio(pageview_series(session, title, start, end, args.request_timeout), when)
                except Exception as exc:  # noqa: BLE001
                    measured = None
                    errors.append(f"Wikimedia pageviews {record.get('name')}: {type(exc).__name__}: {exc}")
                next_attention_state[attention_key] = {"checkedAt": iso(now), "title": title}
                if measured:
                    ratio, baseline_avg, outcome_avg = measured
                    target_info = attention_target(ratio)
                    next_attention_state[attention_key].update({"ratio": round(ratio, 4), "baselineDailyAverage": round(baseline_avg, 2), "postEventDailyAverage": round(outcome_avg, 2)})
                    if target_info:
                        tier, target = target_info
                        pending.append(outcome_event(
                            record,
                            f"wikimedia:attention:{person_qid}:{work_qid}:{tier}",
                            f"{category.lower()}-attention-outcome",
                            f"Audience attention {tier}: {ratio:.2f}× baseline",
                            "Wikimedia Analytics API",
                            f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                            target,
                            now,
                            {"workQid": work_qid, "attentionRatio": round(ratio, 4), "baselineDailyPageviews": round(baseline_avg, 2), "postEventDailyPageviews": round(outcome_avg, 2), "wikipediaTitle": title, "outcomeTier": tier, "baseEventKey": attention_key},
                        ))

        if pending:
            pending = pending[:max(0, event_budget)]
            events_by_index[index] = pending
            event_budget -= len(pending)

    changed_records = 0
    applied_events = 0
    largest: list[dict[str, Any]] = []
    for index, pending in events_by_index.items():
        updated, added = apply_outcome_events(records[index], pending)
        if not added:
            continue
        records[index] = updated
        changed_records += 1
        applied_events += added
        for event in updated.get("priceEvents", [])[-added:]:
            largest.append({"name": updated.get("name"), "category": updated.get("primaryCategory"), "event": event.get("name"), "eventType": event.get("eventType"), "movePct": event.get("movePct"), "priceAfter": event.get("priceAfter")})

    if errors and not args.allow_source_errors and applied_events == 0:
        raise RuntimeError("Outcome sources failed without usable events: " + "; ".join(errors[-8:]))

    args.catalog.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    manifest_out = {
        "version": MODEL_VERSION,
        "generatedAt": iso(now),
        "lastSuccessfulAt": iso(now),
        "previousSuccessfulAt": manifest.get("lastSuccessfulAt"),
        "candidateProfiles": len(candidate_records),
        "workEntitiesChecked": len(work_qids),
        "pageviewChecks": pageview_checks,
        "recordsChanged": changed_records,
        "eventsApplied": applied_events,
        "musicChartState": next_chart_state,
        "actorBoxOfficeState": next_box_state,
        "attentionState": next_attention_state,
        "sourceErrorCount": len(errors),
        "sourceErrors": errors[-100:],
        "largestMoves": sorted(largest, key=lambda item: abs(number(item.get("movePct"), 0)), reverse=True)[:50],
        "policy": {
            "maximumSingleOutcomeMovePct": MAX_OUTCOME_MOVE_PCT,
            "noOutcomeNoMove": True,
            "musicChartProperty": "P2291 with P1352 ranking qualifier",
            "actorBoxOfficeProperty": "P2142",
            "actorProductionCostProperty": "P2130",
            "audienceSignal": "English Wikipedia pageviews, first 7 days after event versus prior 21-day baseline",
        },
    }
    args.manifest.write_text(json.dumps(manifest_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Outcome refresh: {applied_events:,} events changed {changed_records:,} profiles; pageview checks: {pageview_checks:,}; source warnings: {len(errors):,}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
