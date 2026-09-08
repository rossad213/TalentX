#!/usr/bin/env python3
"""Direct public outcome signals for TalentX Actor listings.

This adapter supplements the slower Wikidata outcome path with two measurable,
public sources:

* The Numbers current domestic weekend box-office chart.
* Netflix Tudum's official all-weeks-global.tsv weekly Top 10 dataset.

Only titles already attached to an Actor through a verified ``actor-release``
price event are eligible.  Title matching is deliberately exact after simple
normalization.  The adapter keeps provider/title state and applies only the
change in the verified outcome target so a title is not repriced from scratch
every run.  No fixed percentage movement ceiling is used.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from statistics import median
from typing import Any

import requests

from non_athlete_event_refresh import iso, number, parse_time, utc_now
from non_athlete_outcome_refresh import apply_outcome_events

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
DEFAULT_MANIFEST = ROOT / "data" / "actor_direct_outcome_manifest.json"
DEFAULT_OUTCOME_MANIFEST = ROOT / "data" / "actor_outcome_manifest.json"
THE_NUMBERS_URL = "https://www.the-numbers.com/weekend-box-office-chart"
NETFLIX_GLOBAL_URL = "https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv"
MODEL_VERSION = "actor-direct-outcomes-v1"
USER_AGENT = "TalentX-Actor-Direct-Outcomes/1.0 (+https://github.com/rossad213/TalentX)"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def normalize_title(value: Any) -> str:
    text = html.unescape(str(value or "")).lower().replace("&", " and ")
    text = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def release_title(event: dict[str, Any]) -> str:
    explicit = str(event.get("workTitle") or event.get("title") or "").strip()
    if explicit:
        return explicit
    name = str(event.get("name") or "").strip()
    if ":" in name:
        return name.split(":", 1)[1].strip()
    return name


def recent_actor_releases(record: dict[str, Any], now: datetime, lookback_days: int) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=max(1, lookback_days))
    output: list[dict[str, Any]] = []
    for raw in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(raw, dict) or str(raw.get("eventType") or "") != "actor-release":
            continue
        when = parse_time(raw.get("startedAt"))
        title = release_title(raw)
        if when is None or when < cutoff or when > now or not normalize_title(title):
            continue
        output.append({**raw, "_when": when, "_title": title, "_norm": normalize_title(title)})
    return output


def strip_tags(value: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", "", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_money(value: Any) -> float:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    return max(0.0, number(text, 0.0))


def parse_percent(value: Any) -> float | None:
    text = str(value or "").strip().replace("%", "")
    if not text or text.lower() in {"new", "-", "—", "n/a"}:
        return None
    parsed = number(text, float("nan"))
    return parsed if math.isfinite(parsed) else None


def parse_the_numbers(html_text: str) -> tuple[datetime | None, list[dict[str, Any]]]:
    date_match = re.search(
        r"Weekend\s+Domestic\s+Box\s+Office\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        strip_tags(html_text),
        flags=re.I,
    )
    chart_date: datetime | None = None
    if date_match:
        try:
            chart_date = datetime.strptime(date_match.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            chart_date = None

    rows: list[dict[str, Any]] = []
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html_text, flags=re.I | re.S):
        cells = [strip_tags(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)]
        if len(cells) < 4:
            continue
        rank_match = re.fullmatch(r"\s*(\d+)\s*", cells[0])
        if not rank_match:
            continue
        rank = int(rank_match.group(1))
        title = cells[2] if len(cells) > 2 else ""
        gross = parse_money(cells[3] if len(cells) > 3 else "")
        weekly_change = parse_percent(cells[4] if len(cells) > 4 else "")
        total_gross = parse_money(cells[7] if len(cells) > 7 else "")
        if title and gross > 0:
            rows.append(
                {
                    "rank": rank,
                    "title": title,
                    "gross": gross,
                    "weeklyChangePct": weekly_change,
                    "totalGross": total_gross,
                    "new": str(cells[1] if len(cells) > 1 else "").strip().lower() in {"new", "(new)"},
                }
            )
    rows.sort(key=lambda item: int(item["rank"]))
    return chart_date, rows


def box_office_target(row: dict[str, Any], rows: list[dict[str, Any]]) -> float:
    """Continuous box-office outcome target versus the current market field."""
    ranked = [item for item in rows if int(item.get("rank") or 999) <= 20 and number(item.get("gross"), 0) > 0]
    reference = median([number(item.get("gross"), 0) for item in ranked]) if ranked else number(row.get("gross"), 1)
    rank = max(1, int(number(row.get("rank"), 20)))
    rank_component = 0.90 * math.log2(21.0 / min(21.0, rank + 1.0)) / math.log2(21.0 / 2.0)
    gross_ratio = max(0.01, number(row.get("gross"), 0.01) / max(1.0, reference))
    gross_component = 0.45 * math.log2(gross_ratio)
    change = row.get("weeklyChangePct")
    hold_component = 0.0 if change is None else 0.008 * (number(change) + 45.0)
    return rank_component + gross_component + hold_component


def parse_netflix_global(tsv_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(StringIO(tsv_text), delimiter="\t")
    for raw in reader:
        week = str(raw.get("week") or "").strip()
        title = str(raw.get("show_title") or "").strip()
        category = str(raw.get("category") or "").strip()
        if not week or not title or not category:
            continue
        try:
            when = datetime.strptime(week, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        rows.append(
            {
                "week": when,
                "category": category,
                "rank": max(1, int(number(raw.get("weekly_rank"), 10))),
                "title": title,
                "seasonTitle": str(raw.get("season_title") or "").strip(),
                "hoursViewed": max(0.0, number(raw.get("weekly_hours_viewed"), 0.0)),
                "views": max(0.0, number(raw.get("weekly_views"), 0.0)),
                "weeksInTop10": max(1, int(number(raw.get("cumulative_weeks_in_top_10"), 1))),
            }
        )
    return rows


def latest_netflix_weeks(rows: list[dict[str, Any]], now: datetime) -> tuple[datetime | None, list[dict[str, Any]], list[dict[str, Any]]]:
    weeks = sorted({row["week"] for row in rows if isinstance(row.get("week"), datetime) and row["week"] <= now})
    if not weeks:
        return None, [], []
    latest = weeks[-1]
    previous = weeks[-2] if len(weeks) > 1 else None
    return latest, [row for row in rows if row.get("week") == latest], [row for row in rows if previous and row.get("week") == previous]


def netflix_target(row: dict[str, Any], current_rows: list[dict[str, Any]], previous_row: dict[str, Any] | None) -> float:
    category_rows = [item for item in current_rows if str(item.get("category")) == str(row.get("category"))]
    view_values = [number(item.get("views"), 0) for item in category_rows if number(item.get("views"), 0) > 0]
    reference = median(view_values) if view_values else number(row.get("views"), 0)
    rank = max(1, int(number(row.get("rank"), 10)))
    rank_component = 0.80 * math.log2(11.0 / min(11.0, rank + 1.0)) / math.log2(11.0 / 2.0)
    views = number(row.get("views"), 0)
    scale_component = 0.0
    if views > 0 and reference > 0:
        scale_component = 0.55 * math.log2(max(0.01, views / reference))
    momentum_component = 0.0
    if previous_row and views > 0 and number(previous_row.get("views"), 0) > 0:
        momentum_component = 0.45 * math.log2(max(0.01, views / number(previous_row.get("views"), 1)))
    persistence = 0.05 * math.log1p(max(0, int(number(row.get("weeksInTop10"), 1)) - 1))
    return rank_component + scale_component + momentum_component + persistence


def outcome_event(
    record: dict[str, Any],
    release: dict[str, Any],
    provider_key: str,
    provider: str,
    source_url: str,
    when: datetime,
    target_delta: float,
    name: str,
    event_type: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    work_qid = str(release.get("workQid") or release.get("eventId") or "")
    record_key = str(record.get("id") or normalize_title(record.get("name")))
    title_key = normalize_title(release.get("_title"))
    event_key = f"{provider_key}:{record_key}:{work_qid or title_key}:{when.date().isoformat()}"
    return {
        "eventKey": event_key,
        "eventId": event_key,
        "eventType": event_type,
        "provider": provider,
        "sourceUrl": source_url,
        "name": name,
        "startedAt": iso(when),
        "targetOutcomeMovePct": round(target_delta, 4),
        "artist": record.get("name"),
        "workQid": work_qid,
        "workTitle": release.get("_title"),
        **details,
    }


def existing_wikidata_box_office(record: dict[str, Any], work_qid: str) -> bool:
    if not work_qid:
        return False
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict):
            continue
        if str(event.get("eventType") or "") != "actor-box-office-outcome":
            continue
        if str(event.get("workQid") or "") == work_qid and str(event.get("provider") or "") == "Wikidata":
            return True
    return False


def direct_explanation(event: dict[str, Any], price: float) -> dict[str, Any]:
    move = number(event.get("movePct"), 0)
    provider = str(event.get("provider") or "")
    if provider == "The Numbers":
        summary = [
            f"Verified domestic weekend gross: ${number(event.get('weekendGross'), 0):,.0f} at #{int(number(event.get('boxOfficeRank'), 0))}.",
            "The move compares rank, gross scale, and week-over-week hold with the current theatrical field.",
        ]
        headline = "Direct theatrical box-office outcome"
    else:
        views = number(event.get("weeklyViews"), 0)
        summary = [
            f"Verified Netflix global Top 10 rank: #{int(number(event.get('streamingRank'), 0))}" + (f" with {views:,.0f} weekly views." if views > 0 else "."),
            "The move compares rank, audience scale, momentum, and persistence with the current Netflix Top 10 field.",
        ]
        headline = "Direct Netflix streaming outcome"
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
        "pricingMode": "Verified direct Actor outcome; event market price preserved",
        "source": provider,
        "sourceUrl": event.get("sourceUrl"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--outcome-manifest", type=Path, default=DEFAULT_OUTCOME_MANIFEST)
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--max-events", type=int, default=500)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    payload = load_json(args.catalog, [])
    if not isinstance(payload, list) or not payload:
        raise SystemExit(f"{args.catalog} must contain a non-empty array")
    records = [dict(item) for item in payload if isinstance(item, dict)]
    manifest = load_json(args.manifest, {})
    manifest = manifest if isinstance(manifest, dict) else {}
    state = manifest.get("state") if isinstance(manifest.get("state"), dict) else {}
    state = {str(key): dict(value) for key, value in state.items() if isinstance(value, dict)}
    now = utc_now()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,text/tab-separated-values,*/*"})
    errors: list[str] = []

    box_date: datetime | None = None
    box_rows: list[dict[str, Any]] = []
    try:
        response = session.get(THE_NUMBERS_URL, timeout=args.request_timeout)
        response.raise_for_status()
        box_date, box_rows = parse_the_numbers(response.text)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"The Numbers: {type(exc).__name__}: {exc}")

    netflix_latest: datetime | None = None
    netflix_rows: list[dict[str, Any]] = []
    netflix_previous: list[dict[str, Any]] = []
    try:
        response = session.get(NETFLIX_GLOBAL_URL, timeout=args.request_timeout)
        response.raise_for_status()
        netflix_latest, netflix_rows, netflix_previous = latest_netflix_weeks(parse_netflix_global(response.text), now)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Netflix Top 10: {type(exc).__name__}: {exc}")

    box_by_title = {normalize_title(row.get("title")): row for row in box_rows}
    netflix_by_title = {normalize_title(row.get("title")): row for row in netflix_rows}
    netflix_prev_by_title = {normalize_title(row.get("title")): row for row in netflix_previous}

    event_budget = max(0, args.max_events)
    changed = 0
    applied = 0
    largest: list[dict[str, Any]] = []
    next_state = dict(state)

    outcome_manifest = load_json(args.outcome_manifest, {})
    outcome_manifest = outcome_manifest if isinstance(outcome_manifest, dict) else {}
    actor_box_state = outcome_manifest.get("actorBoxOfficeState") if isinstance(outcome_manifest.get("actorBoxOfficeState"), dict) else {}
    actor_box_state = dict(actor_box_state)

    for index, record in enumerate(records):
        if event_budget <= 0 or str(record.get("primaryCategory") or "") != "Actor":
            continue
        releases = recent_actor_releases(record, now, args.lookback_days)
        if not releases:
            continue
        candidates: list[tuple[float, dict[str, Any], str, float]] = []

        for release in releases:
            key = release["_norm"]
            work_qid = str(release.get("workQid") or release.get("eventId") or "")
            if box_date and key in box_by_title and not existing_wikidata_box_office(record, work_qid):
                row = box_by_title[key]
                target = box_office_target(row, box_rows)
                state_key = f"numbers:{record.get('id')}:{work_qid or key}"
                previous_target = number(state.get(state_key, {}).get("targetMovePct"), 0) if isinstance(state.get(state_key), dict) else 0
                delta = target - previous_target
                next_state[state_key] = {
                    "targetMovePct": round(target, 6),
                    "rank": row.get("rank"),
                    "weekendGross": row.get("gross"),
                    "week": box_date.date().isoformat(),
                    "updatedAt": iso(now),
                }
                if abs(delta) >= 0.08:
                    event = outcome_event(
                        record,
                        release,
                        "the-numbers",
                        "The Numbers",
                        THE_NUMBERS_URL,
                        box_date,
                        delta,
                        f"Domestic box office: #{row['rank']} — {row['title']}",
                        "actor-box-office-outcome",
                        {
                            "boxOfficeRank": row["rank"],
                            "weekendGross": round(number(row.get("gross"), 0), 2),
                            "domesticTotalGross": round(number(row.get("totalGross"), 0), 2),
                            "weeklyChangePct": row.get("weeklyChangePct"),
                            "directOutcomeTargetPct": round(target, 4),
                        },
                    )
                    candidates.append((abs(delta), event, state_key, target))
                    if work_qid:
                        actor_box_state[f"{index}:{work_qid}"] = {
                            "targetMovePct": round(target, 6),
                            "provider": "The Numbers direct baseline",
                            "updatedAt": iso(now),
                        }

            if netflix_latest and key in netflix_by_title:
                row = netflix_by_title[key]
                previous_row = netflix_prev_by_title.get(key)
                target = netflix_target(row, netflix_rows, previous_row)
                state_key = f"netflix:{record.get('id')}:{work_qid or key}"
                previous_target = number(state.get(state_key, {}).get("targetMovePct"), 0) if isinstance(state.get(state_key), dict) else 0
                delta = target - previous_target
                next_state[state_key] = {
                    "targetMovePct": round(target, 6),
                    "rank": row.get("rank"),
                    "weeklyViews": row.get("views"),
                    "week": netflix_latest.date().isoformat(),
                    "updatedAt": iso(now),
                }
                if abs(delta) >= 0.08:
                    event = outcome_event(
                        record,
                        release,
                        "netflix-top10",
                        "Netflix Tudum Top 10",
                        NETFLIX_GLOBAL_URL,
                        netflix_latest,
                        delta,
                        f"Netflix global Top 10: #{row['rank']} — {row['title']}",
                        "actor-streaming-outcome",
                        {
                            "streamingRank": row["rank"],
                            "weeklyViews": round(number(row.get("views"), 0), 2),
                            "weeklyHoursViewed": round(number(row.get("hoursViewed"), 0), 2),
                            "cumulativeWeeksInTop10": row.get("weeksInTop10"),
                            "netflixCategory": row.get("category"),
                            "directOutcomeTargetPct": round(target, 4),
                        },
                    )
                    candidates.append((abs(delta), event, state_key, target))

        if not candidates:
            continue
        # Conservative: only the strongest verified direct outcome per Actor per run.
        candidates.sort(key=lambda item: item[0], reverse=True)
        pending = [candidates[0][1]]
        updated, added = apply_outcome_events(record, pending)
        if not added:
            continue
        latest_event = updated.get("priceEvents", [])[-1]
        updated["priceExplanation"] = direct_explanation(latest_event, number(updated.get("marketPrice"), 0))
        records[index] = updated
        changed += 1
        applied += added
        event_budget -= added
        largest.append(
            {
                "name": updated.get("name"),
                "event": latest_event.get("name"),
                "provider": latest_event.get("provider"),
                "movePct": latest_event.get("movePct"),
                "priceAfter": latest_event.get("priceAfter"),
            }
        )

    if errors and not args.allow_source_errors and applied == 0:
        raise RuntimeError("Direct Actor outcome sources failed without usable events: " + "; ".join(errors[-6:]))

    args.catalog.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(
            {
                "version": MODEL_VERSION,
                "generatedAt": iso(now),
                "state": next_state,
                "theNumbersChartDate": box_date.date().isoformat() if box_date else None,
                "theNumbersRows": len(box_rows),
                "netflixWeek": netflix_latest.date().isoformat() if netflix_latest else None,
                "netflixRows": len(netflix_rows),
                "recordsChanged": changed,
                "eventsApplied": applied,
                "sourceErrors": errors[-50:],
                "largestMoves": sorted(largest, key=lambda item: abs(number(item.get("movePct"), 0)), reverse=True)[:50],
                "policy": {
                    "noVerifiedOutcomeNoMove": True,
                    "maximumSingleOutcomeMovePct": None,
                    "titleMatching": "exact after conservative normalization",
                    "perActorPerRun": "strongest direct outcome only",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if actor_box_state:
        outcome_manifest["actorBoxOfficeState"] = actor_box_state
        args.outcome_manifest.write_text(json.dumps(outcome_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"Direct Actor outcomes: {applied:,} events changed {changed:,} profiles; "
        f"The Numbers rows {len(box_rows):,}; Netflix rows {len(netflix_rows):,}; warnings {len(errors):,}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
