#!/usr/bin/env python3
"""Ingest fresh public Billboard chart movement into TalentX Music pricing.

TalentX's existing non-athlete outcome refresh can price verified chart outcomes,
but Wikidata often receives chart claims well after the chart itself is published.
This refresh reads Billboard Canada chart pages (published under license from
Billboard), matches artist credits conservatively to Music profiles, and emits
normal ``music-chart-outcome`` price events through the existing outcome engine.

Design rules:
* only visible/public chart rows are used;
* movement is based on this-week versus last-week rank (or New/Re-Entry status);
* no movement means no price event;
* one strongest chart signal is applied per artist per chart week to avoid
  stacking several songs/charts into an exaggerated move;
* existing Wikidata chart events near the same date/rank suppress duplicates;
* events remain durable in ``priceEvents``, so reruns cannot price twice.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from non_athlete_event_refresh import parse_time, utc_now
from non_athlete_outcome_refresh import apply_outcome_events, chart_target, outcome_event

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "current_catalog.json"
USER_AGENT = "TalentX-Billboard-Chart-Refresh/1.0 (+https://github.com/rossad213/TalentX)"

CHARTS = (
    {
        "slug": "billboard-global-200",
        "name": "Billboard Global 200",
        "url": "https://ca.billboard.com/charts/billboard-global-200",
    },
    {
        "slug": "billboard-global-excl-us",
        "name": "Billboard Global Excl. U.S.",
        "url": "https://ca.billboard.com/charts/billboard-global-excl-us",
    },
    {
        "slug": "hot-100",
        "name": "Billboard Hot 100",
        "url": "https://ca.billboard.com/charts/hot-100",
    },
    {
        "slug": "billboard-200",
        "name": "Billboard 200",
        "url": "https://ca.billboard.com/charts/billboard-200",
    },
)

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
DATE_RE = re.compile(rf"\b(?:Week\s+of\s+)?({MONTHS})\s+(\d{{1,2}}),\s+(20\d{{2}})\b", re.I)
RANK_TOKEN_RE = re.compile(r"^(?:#\s*)?(\d{1,3})$")
NEW_TOKENS = {"new", "re-entry", "reentry", "re entry", "-", "—"}
CREDIT_SPLIT_RE = re.compile(
    r"\s+(?:feat(?:uring)?\.?|ft\.?|with|x|and|&)\s+|\s*,\s*|\s*\+\s*",
    re.I,
)


class VisibleChartParser(HTMLParser):
    """Collect visible tokens while preserving h2/h3 heading boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.tokens: list[tuple[str, str]] = []
        self.heading_tag = ""
        self.heading_parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.stack.append(tag)
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if not self.skip_depth and tag in {"h2", "h3"} and not self.heading_tag:
            self.heading_tag = tag
            self.heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.heading_tag == tag:
            text = collapse_space(" ".join(self.heading_parts))
            if text:
                self.tokens.append((tag, text))
            self.heading_tag = ""
            self.heading_parts = []
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = collapse_space(data)
        if not text:
            return
        if self.heading_tag:
            self.heading_parts.append(text)
        else:
            self.tokens.append(("text", text))


def collapse_space(value: Any) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(str(value or ""))).strip()


def ascii_words(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("’", "'")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def slugify(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ascii_words(value)).strip("-")[:90]


def rank_token(value: Any) -> int | None:
    match = RANK_TOKEN_RE.fullmatch(collapse_space(value))
    if not match:
        return None
    rank = int(match.group(1))
    return rank if 1 <= rank <= 200 else None


def chart_date_from_text(text: str, fallback: datetime | None = None) -> datetime:
    matches: list[datetime] = []
    for month, day, year in DATE_RE.findall(text):
        try:
            matches.append(datetime.strptime(f"{month} {day} {year}", "%B %d %Y").replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    if matches:
        return max(matches)
    base = fallback or utc_now()
    return base.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def parse_billboard_chart(html: str, fallback_date: datetime | None = None) -> tuple[datetime, list[dict[str, Any]]]:
    parser = VisibleChartParser()
    parser.feed(html)
    tokens = parser.tokens
    chart_date = chart_date_from_text("\n".join(text for _, text in tokens), fallback_date)
    rows: list[dict[str, Any]] = []

    for index, (kind, title) in enumerate(tokens):
        if kind != "h2":
            continue

        artist_index: int | None = None
        for cursor in range(index + 1, min(len(tokens), index + 18)):
            next_kind, _ = tokens[cursor]
            if next_kind == "h2":
                break
            if next_kind == "h3":
                artist_index = cursor
                break
        if artist_index is None:
            continue

        current_rank: int | None = None
        for cursor in range(index - 1, max(-1, index - 12), -1):
            previous_kind, previous_text = tokens[cursor]
            if previous_kind in {"h2", "h3"}:
                break
            parsed_rank = rank_token(previous_text)
            if parsed_rank is not None:
                current_rank = parsed_rank
                break
        if current_rank is None:
            continue

        artist_credit = tokens[artist_index][1]
        last_week: int | None = None
        status = "ranked"
        for cursor in range(artist_index + 1, min(len(tokens), artist_index + 14)):
            next_kind, next_text = tokens[cursor]
            if next_kind == "h2":
                break
            lowered = collapse_space(next_text).casefold()
            if lowered in NEW_TOKENS:
                status = "new" if lowered == "new" else "re-entry"
                break
            parsed_rank = rank_token(next_text)
            if parsed_rank is not None:
                last_week = parsed_rank
                break

        rows.append(
            {
                "rank": current_rank,
                "lastWeekRank": last_week,
                "status": status,
                "title": collapse_space(title),
                "artistCredit": collapse_space(artist_credit),
            }
        )

    unique: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["rank"]), ascii_words(row["title"]), ascii_words(row["artistCredit"]))
        unique[key] = row
    return chart_date, sorted(unique.values(), key=lambda item: int(item["rank"]))


def artist_credit_parts(credit: str) -> set[str]:
    normalized_full = ascii_words(credit)
    parts = {ascii_words(part) for part in CREDIT_SPLIT_RE.split(credit) if ascii_words(part)}
    if normalized_full:
        parts.add(normalized_full)
    return parts


def artist_credit_matches(profile_name: str, credit: str) -> bool:
    profile = ascii_words(profile_name)
    if not profile:
        return False
    full_credit = ascii_words(credit)
    if profile == full_credit:
        return True
    return profile in artist_credit_parts(credit)


def movement_target(current_rank: int, last_week_rank: int | None, status: str = "ranked") -> float:
    """Return uncapped directional percentage target from verified rank movement."""
    _, current_tier = chart_target(current_rank)
    if last_week_rank is None:
        if status not in {"new", "re-entry"}:
            return 0.0
        novelty = 0.08 * math.log2(1 + max(1, 101 - min(current_rank, 100)))
        return round(current_tier + novelty, 3)

    if current_rank == last_week_rank:
        return 0.0

    _, prior_tier = chart_target(last_week_rank)
    delta = last_week_rank - current_rank
    direction = 1.0 if delta > 0 else -1.0
    magnitude = abs(current_tier - prior_tier) + 0.10 * math.log2(1 + abs(delta))

    thresholds = (40, 10, 5, 1)
    for threshold in thresholds:
        crossed_up = current_rank <= threshold < last_week_rank
        crossed_down = last_week_rank <= threshold < current_rank
        if crossed_up or crossed_down:
            magnitude += 0.08 if threshold == 40 else 0.12 if threshold == 10 else 0.15 if threshold == 5 else 0.20

    return round(direction * magnitude, 3)


def equivalent_recent_chart_event(record: dict[str, Any], rank: int, chart_date: datetime) -> bool:
    for event in record.get("priceEvents", []) if isinstance(record.get("priceEvents"), list) else []:
        if not isinstance(event, dict) or str(event.get("eventType") or "") != "music-chart-outcome":
            continue
        if int(event.get("chartRank") or 0) != rank:
            continue
        event_at = parse_time(event.get("startedAt"))
        if event_at is not None and abs((event_at.date() - chart_date.date()).days) <= 6:
            return True
    return False


def build_music_index(records: list[dict[str, Any]]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for position, record in enumerate(records):
        if str(record.get("primaryCategory") or record.get("category") or "").casefold() != "music":
            continue
        key = ascii_words(record.get("name"))
        if key:
            index.setdefault(key, []).append(position)
    return index


def candidate_positions(index: dict[str, list[int]], credit: str) -> set[int]:
    output: set[int] = set()
    for part in artist_credit_parts(credit):
        output.update(index.get(part, []))
    return output


def candidate_event(
    record: dict[str, Any],
    row: dict[str, Any],
    chart: dict[str, str],
    chart_date: datetime,
) -> dict[str, Any] | None:
    current_rank = int(row["rank"])
    last_week_rank = row.get("lastWeekRank")
    last_rank = int(last_week_rank) if isinstance(last_week_rank, int) else None
    target = movement_target(current_rank, last_rank, str(row.get("status") or "ranked"))
    if abs(target) < 0.01:
        return None
    if equivalent_recent_chart_event(record, current_rank, chart_date):
        return None

    direction = "rose" if target > 0 else "fell"
    prior_label = f"#{last_rank}" if last_rank is not None else str(row.get("status") or "new").title()
    name = f"{chart['name']}: {row['title']} {direction} {prior_label} → #{current_rank}"
    event_key = ":".join(
        [
            "billboard",
            chart["slug"],
            chart_date.date().isoformat(),
            slugify(record.get("id") or record.get("name")),
            slugify(row.get("title")),
            str(current_rank),
        ]
    )
    return outcome_event(
        record,
        event_key,
        "music-chart-outcome",
        name,
        chart["name"],
        chart["url"],
        target,
        chart_date,
        {
            "chartProvider": "Billboard",
            "chartName": chart["name"],
            "chartSlug": chart["slug"],
            "chartDate": chart_date.date().isoformat(),
            "chartRank": current_rank,
            "previousChartRank": last_rank,
            "chartStatus": row.get("status"),
            "releaseTitle": row.get("title"),
            "artistCredit": row.get("artistCredit"),
            "outcomeEvidence": "direct-public-chart",
        },
    )


def refresh_catalog(
    records: list[dict[str, Any]],
    chart_payloads: list[tuple[dict[str, str], datetime, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], int, int]:
    music_index = build_music_index(records)
    strongest: dict[tuple[int, str], dict[str, Any]] = {}
    matched_rows = 0

    for chart, chart_date, rows in chart_payloads:
        for row in rows:
            positions = candidate_positions(music_index, str(row.get("artistCredit") or ""))
            if not positions:
                continue
            for position in positions:
                record = records[position]
                if not artist_credit_matches(str(record.get("name") or ""), str(row.get("artistCredit") or "")):
                    continue
                event = candidate_event(record, row, chart, chart_date)
                if event is None:
                    continue
                matched_rows += 1
                key = (position, chart_date.date().isoformat())
                prior = strongest.get(key)
                if prior is None or abs(float(event.get("targetOutcomeMovePct") or 0)) > abs(float(prior.get("targetOutcomeMovePct") or 0)):
                    strongest[key] = event

    changed_records = 0
    applied_events = 0
    updated = list(records)
    by_position: dict[int, list[dict[str, Any]]] = {}
    for (position, _), event in strongest.items():
        by_position.setdefault(position, []).append(event)

    for position, events in by_position.items():
        next_record, count = apply_outcome_events(updated[position], events)
        if count:
            updated[position] = next_record
            changed_records += 1
            applied_events += count

    return updated, changed_records, applied_events


def fetch_chart(session: Any, chart: dict[str, str], timeout: float) -> tuple[datetime, list[dict[str, Any]]]:
    response = session.get(chart["url"], timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    response.raise_for_status()
    return parse_billboard_chart(response.text, utc_now())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--request-timeout", type=float, default=18.0)
    parser.add_argument("--allow-source-errors", action="store_true")
    args = parser.parse_args()

    try:
        records = json.loads(args.catalog.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Billboard refresh: unable to read catalog: {exc}")
        return 1
    if not isinstance(records, list):
        print("Billboard refresh: catalog must be a JSON array")
        return 1

    import requests

    session = requests.Session()
    chart_payloads: list[tuple[dict[str, str], datetime, list[dict[str, Any]]]] = []
    errors: list[str] = []
    for chart in CHARTS:
        try:
            chart_date, rows = fetch_chart(session, chart, args.request_timeout)
            if not rows:
                raise ValueError("no public chart rows parsed")
            chart_payloads.append((chart, chart_date, rows))
            print(f"Billboard refresh: {chart['name']} {chart_date.date()} rows={len(rows)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{chart['name']}: {type(exc).__name__}: {exc}")

    if errors:
        for error in errors:
            print(f"Billboard refresh source warning: {error}")
        if not args.allow_source_errors and not chart_payloads:
            return 1

    updated, changed_records, applied_events = refresh_catalog(records, chart_payloads)
    if applied_events:
        args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        "Billboard refresh complete: "
        f"charts={len(chart_payloads)} changed_records={changed_records} applied_events={applied_events}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
