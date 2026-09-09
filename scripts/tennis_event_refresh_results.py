#!/usr/bin/env python3
"""Run Tennis event refresh with results-proportional, uncapped movement.

Live discovery is hardened for ESPN Tennis by reading date-scoped scoreboards,
accepting grouped/direct competition shapes, inferring ATP/WTA from draw metadata,
and preferring verified canonical TalentX identities over prototype duplicates.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Iterable

import tennis_event_refresh as base
from results_event_pricing import MODEL_VERSION as RESULTS_MODEL_VERSION

ESPN_ALL_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/tennis/all/scoreboard"
    "?limit=1000&dates={date}"
)
ESPN_TOUR_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard"
    "?limit=1000&dates={date}"
)


def tennis_match_move_results(
    *,
    winner: bool,
    round_name: str,
    major: bool,
    sets_for: int,
    sets_against: int,
    player_record=None,
    opponent_record=None,
    max_move_pct: float = 2.5,
) -> float:
    """Price a verified Tennis result without a fixed percentage ceiling."""
    del max_move_pct
    importance = base.round_importance(round_name)
    move = 0.06 + importance * 1.65 if winner else -(0.07 + importance * 0.22)

    if major:
        move *= 1.75 if winner else 1.30

    if sets_for or sets_against:
        if winner and sets_against == 0:
            move += 0.04
        elif not winner and sets_for == 0:
            move -= 0.03

    own_rank = base.player_rank(player_record)
    opponent_rank = base.player_rank(opponent_record)
    if own_rank and opponent_rank:
        if winner and own_rank > opponent_rank:
            move += 0.22 * math.log1p((own_rank - opponent_rank) / 20.0)
        elif not winner and own_rank < opponent_rank:
            move -= 0.18 * math.log1p((opponent_rank - own_rank) / 20.0)
        elif winner and own_rank < opponent_rank:
            move -= 0.02 * math.log1p((opponent_rank - own_rank) / 100.0)

    if abs(move) < 0.03:
        move = 0.03 if winner else -0.03
    return round(move, 3)


def verified_tennis_record_strength(record):
    """Prefer verified current roster identities over legacy/prototype duplicates."""
    source_namespace = str(record.get("sourceNamespace") or "").strip().lower()
    source_type = str(record.get("sourceType") or "").strip().lower()
    verification = str(record.get("verificationStatus") or "").strip().lower()
    record_id = str(record.get("id") or "")

    official_roster = int(
        source_namespace == "curated-individual-sport-roster"
        or source_type == "official-ranking-roster"
    )
    non_prototype = int("prototype" not in verification and "seed" not in verification)
    canonical_id = int(record_id.startswith("athlete-tennis-"))
    current_segment = int(str(record.get("marketSegment") or "").strip().lower() == "current")
    confidence = float(record.get("pricingConfidence") or record.get("dataConfidence") or 0)
    market_price = float(record.get("marketPrice") or 0)
    return (
        official_roster,
        non_prototype,
        canonical_id,
        current_segment,
        confidence,
        market_price,
        record_id,
    )


def _draw_text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values).lower().replace("_", " ").replace("-", " ")


def infer_tour(*values: Any, default: str = "") -> str:
    """Infer ATP/WTA from draw metadata; use endpoint tour only as fallback."""
    text = _draw_text(*values)
    if any(token in text for token in ("women's", "womens", "women singles", "female")):
        return "WTA"
    if any(token in text for token in ("men's", "mens", "men singles", "male")):
        return "ATP"
    fallback = str(default or "").strip().upper()
    return fallback if fallback in {"ATP", "WTA"} else ""


def _competition_candidates(payload: dict[str, Any]):
    """Yield competition, tournament, grouping slug, grouping display name."""
    for tournament in payload.get("events") or []:
        if not isinstance(tournament, dict):
            continue

        if isinstance(tournament.get("competitors"), list):
            yield tournament, tournament, "", ""

        for grouping_block in tournament.get("groupings") or []:
            if not isinstance(grouping_block, dict):
                continue
            grouping = grouping_block.get("grouping") if isinstance(grouping_block.get("grouping"), dict) else {}
            grouping_slug = str(grouping.get("slug") or grouping_block.get("slug") or "")
            grouping_name = str(
                grouping.get("displayName")
                or grouping.get("name")
                or grouping_block.get("displayName")
                or grouping_block.get("name")
                or ""
            )
            for competition in grouping_block.get("competitions") or []:
                if isinstance(competition, dict):
                    yield competition, tournament, grouping_slug, grouping_name

        for competition in tournament.get("competitions") or []:
            if isinstance(competition, dict):
                yield competition, tournament, "", ""


def flatten_scoreboard_results(
    payload: dict[str, Any],
    source_url: str = "",
    *,
    default_tour: str = "",
) -> list[dict[str, Any]]:
    """Flatten supported ESPN Tennis shapes into unique completed singles matches."""
    matches: dict[str, dict[str, Any]] = {}
    for competition, tournament, grouping_slug, grouping_name in _competition_candidates(payload):
        if not base.tennis_completed(competition):
            continue

        type_info = competition.get("type") if isinstance(competition.get("type"), dict) else {}
        type_slug = str(type_info.get("slug") or "")
        type_text = str(type_info.get("text") or type_info.get("displayName") or "")
        draw_text = _draw_text(grouping_slug, grouping_name, type_slug, type_text)
        if "double" in draw_text or "mixed" in draw_text:
            continue
        if not base.singles_competition(competition, grouping_slug):
            continue

        tour = infer_tour(grouping_slug, grouping_name, type_slug, type_text, default=default_tour)
        if not tour:
            continue

        competitors = [item for item in (competition.get("competitors") or []) if isinstance(item, dict)]
        if len(competitors) != 2:
            continue

        started = base.parse_datetime(
            competition.get("date")
            or competition.get("startDate")
            or tournament.get("date")
            or tournament.get("startDate")
        )
        competition_id = str(competition.get("id") or competition.get("uid") or "").strip()
        if not competition_id or started is None:
            continue

        parsed_competitors: list[dict[str, Any]] = []
        for competitor in competitors:
            name = base.competitor_name(competitor)
            if not name:
                continue
            athlete = competitor.get("athlete") if isinstance(competitor.get("athlete"), dict) else {}
            parsed_competitors.append({
                "name": name,
                "normalizedName": base.norm(name),
                "athleteId": str(competitor.get("id") or athlete.get("id") or "").strip(),
                "winner": competitor.get("winner") is True,
                "setsWon": base.sets_won(competitor),
                "linescores": [dict(item) for item in (competitor.get("linescores") or []) if isinstance(item, dict)],
            })
        if len(parsed_competitors) != 2:
            continue

        round_info = competition.get("round") if isinstance(competition.get("round"), dict) else {}
        round_name = str(
            round_info.get("displayName")
            or round_info.get("name")
            or competition.get("roundDisplayName")
            or "Match"
        )
        tournament_name = str(tournament.get("name") or tournament.get("shortName") or "Tennis event")
        key = f"{tour.lower()}:{competition_id}"
        matches[key] = {
            "matchKey": key,
            "competitionId": competition_id,
            "tournamentId": str(tournament.get("id") or ""),
            "tour": tour,
            "tournament": tournament_name,
            "round": round_name,
            "major": bool(tournament.get("major")),
            "startedAt": base.iso_utc(started),
            "sourceUrl": source_url,
            "competitors": parsed_competitors,
        }
    return sorted(matches.values(), key=lambda item: str(item.get("startedAt") or ""))


def discover_matches_results(
    start,
    end,
    *,
    timeout: float,
    tours: Iterable[str] = base.TOURS,
    http=None,
):
    """Discover Tennis matches date-by-date, preferring ESPN tennis/all."""
    client = http or base.session()
    matches: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    cursor = start.date()
    final_date = end.date()

    while cursor <= final_date:
        date_text = cursor.strftime("%Y%m%d")
        all_url = ESPN_ALL_SCOREBOARD.format(date=date_text)
        all_matches: list[dict[str, Any]] = []
        try:
            payload = base.fetch_json(all_url, timeout, client)
            all_matches = flatten_scoreboard_results(payload, all_url)
            for match in all_matches:
                matches[str(match.get("matchKey") or "")] = match
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"all scoreboard {date_text}: {type(exc).__name__}: {exc}")

        if not all_matches:
            for tour in tours:
                tour_text = str(tour or "").lower()
                url = ESPN_TOUR_SCOREBOARD.format(tour=tour_text, date=date_text)
                try:
                    payload = base.fetch_json(url, timeout, client)
                    for match in flatten_scoreboard_results(payload, url, default_tour=tour_text):
                        matches[str(match.get("matchKey") or "")] = match
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"{tour_text} scoreboard {date_text}: {type(exc).__name__}: {exc}")
        cursor += timedelta(days=1)

    clean = [match for key, match in matches.items() if key]
    return sorted(clean, key=lambda item: str(item.get("startedAt") or "")), warnings


base.tennis_match_move = tennis_match_move_results
base.record_strength = verified_tennis_record_strength
base.discover_matches = discover_matches_results
base.RESULTS_EVENT_PRICING_MODEL = RESULTS_MODEL_VERSION

if __name__ == "__main__":
    raise SystemExit(base.main())
