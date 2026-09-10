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
GRAND_SLAM_NAMES = {
    "australianopen",
    "frenchopen",
    "rolandgarros",
    "wimbledon",
    "usopen",
}
TENNIS_PRICING_POLICY_VERSION = "tennis-major-stage-upset-v2"
_BASE_APPLY_LIVE_MATCHES = base.apply_live_matches


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
    """Price verified Tennis results by stage, opponent quality and surprise.

    The model intentionally has no hard percentage ceiling. Routine results stay
    modest, while late-round Grand Slam wins and genuine ranking upsets receive
    progressively stronger moves. Established favorites receive a smaller loss
    penalty than the winner's breakthrough reward when an upset occurs.
    """
    del max_move_pct
    importance = base.round_importance(round_name)

    if winner:
        move = 0.06 + importance * 1.65

        # Grand Slam significance grows non-linearly by round: early major wins
        # matter, but quarterfinals, semifinals and finals should separate
        # meaningfully from ordinary tour results.
        if major:
            move = move * 1.40 + 0.20 + 8.0 * (importance ** 1.5)

        if sets_for or sets_against:
            if sets_against == 0:
                move += 0.04

        own_rank = base.player_rank(player_record)
        opponent_rank = base.player_rank(opponent_record)
        if own_rank and opponent_rank:
            # Beating an elite opponent is meaningful even when rankings are
            # close; a true upset then adds a separate surprise component.
            if opponent_rank <= 3:
                move += 0.90
            elif opponent_rank <= 10:
                move += 0.35

            if own_rank > opponent_rank:
                ranking_gap = (own_rank - opponent_rank) / max(float(opponent_rank), 3.0)
                move += 0.75 * math.log1p(ranking_gap)
                if opponent_rank <= 3:
                    move += 0.40
            elif own_rank < opponent_rank:
                move -= 0.02 * math.log1p((opponent_rank - own_rank) / 100.0)
    else:
        move = -(0.07 + importance * 0.22)

        if major:
            move *= 1.30

        if sets_for or sets_against:
            if sets_for == 0:
                move -= 0.03

        own_rank = base.player_rank(player_record)
        opponent_rank = base.player_rank(opponent_record)
        if own_rank and opponent_rank:
            if own_rank < opponent_rank:
                # An established favorite losing to a lower-ranked player should
                # register clearly, especially late in a major, but the downside
                # remains smaller than the challenger's breakthrough reward.
                ranking_gap = (opponent_rank - own_rank) / max(float(own_rank), 3.0)
                move -= 0.55 * math.log1p(ranking_gap)
                if own_rank <= 3:
                    move -= 0.70
                if major:
                    move -= 4.0 * (importance ** 1.5)
            elif own_rank > opponent_rank:
                move += 0.02 * math.log1p((own_rank - opponent_rank) / 100.0)

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


def infer_major(tournament_name: str, explicit_flag: Any = None) -> bool:
    """Recognize the four Grand Slams even when ESPN omits its major flag."""
    if explicit_flag is True:
        return True
    return base.norm(tournament_name) in GRAND_SLAM_NAMES


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
            "major": infer_major(tournament_name, tournament.get("major")),
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
    """Discover Tennis matches date-by-date across all and tour scoreboards.

    ESPN can expose only part of a Grand Slam draw on one scoreboard surface. We
    therefore merge tennis/all with ATP and WTA date-scoped boards and deduplicate
    by ESPN competition id. The all-board result wins when the same match appears
    more than once because its explicit draw metadata is the safest tour label.
    """
    client = http or base.session()
    matches_by_competition: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    cursor = start.date()
    final_date = end.date()

    while cursor <= final_date:
        date_text = cursor.strftime("%Y%m%d")
        all_url = ESPN_ALL_SCOREBOARD.format(date=date_text)
        try:
            payload = base.fetch_json(all_url, timeout, client)
            for match in flatten_scoreboard_results(payload, all_url):
                competition_id = str(match.get("competitionId") or match.get("matchKey") or "")
                if competition_id:
                    matches_by_competition[competition_id] = match
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"all scoreboard {date_text}: {type(exc).__name__}: {exc}")

        for tour in tours:
            tour_text = str(tour or "").lower()
            url = ESPN_TOUR_SCOREBOARD.format(tour=tour_text, date=date_text)
            try:
                payload = base.fetch_json(url, timeout, client)
                for match in flatten_scoreboard_results(payload, url, default_tour=tour_text):
                    competition_id = str(match.get("competitionId") or match.get("matchKey") or "")
                    if competition_id:
                        matches_by_competition.setdefault(competition_id, match)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{tour_text} scoreboard {date_text}: {type(exc).__name__}: {exc}")
        cursor += timedelta(days=1)

    return sorted(
        matches_by_competition.values(),
        key=lambda item: str(item.get("startedAt") or ""),
    ), warnings


def _sync_history_to_events(record: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """Keep already-published chart points aligned when an event is repriced."""
    event_map = {
        str(event.get("eventKey") or event.get("eventId") or ""): event
        for event in events
        if str(event.get("eventKey") or event.get("eventId") or "")
    }
    history = [dict(item) for item in record.get("priceHistory", []) if isinstance(item, dict)]
    for item in history:
        event = event_map.get(str(item.get("eventId") or ""))
        if not event:
            continue
        phase = str(item.get("phase") or "")
        if phase == "open" and float(event.get("priceBefore") or 0) > 0:
            item["price"] = round(float(event["priceBefore"]), 2)
        elif phase == "close" and float(event.get("priceAfter") or 0) > 0:
            item["price"] = round(float(event["priceAfter"]), 2)
    record["priceHistory"] = history


def apply_live_matches_results(
    records: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    *,
    max_move_pct: float,
):
    """Apply new matches and migrate recent legacy Tennis events exactly once.

    Existing event keys remain deduplicated. If a recent verified live Tennis
    event was priced before this policy version, replace its old movement factor
    rather than adding another event, then scale subsequent event price points so
    the chart and current market price remain internally consistent.
    """
    updated, touched, added = _BASE_APPLY_LIVE_MATCHES(
        records,
        matches,
        max_move_pct=max_move_pct,
    )
    rows = base.matched_competitors(updated, matches)
    grouped: dict[int, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]]] = {}
    for index, match, competitor, opponent_record in rows:
        grouped.setdefault(index, []).append((match, competitor, opponent_record))

    repriced_records = 0
    for index, entries in grouped.items():
        result = dict(updated[index])
        events = [dict(event) for event in result.get("priceEvents", []) if isinstance(event, dict)]
        if not events:
            continue
        events.sort(key=lambda event: str(event.get("startedAt") or ""))
        event_indexes = {
            str(event.get("eventKey") or event.get("eventId") or ""): position
            for position, event in enumerate(events)
            if str(event.get("eventKey") or event.get("eventId") or "")
        }
        current_price = max(0.01, float(result.get("marketPrice") or 0.01))
        repriced = False

        for match, competitor, opponent_record in sorted(entries, key=lambda item: str(item[0].get("startedAt") or "")):
            candidate = base.event_for_record(
                result,
                match,
                competitor,
                opponent_record,
                historical_backfill=False,
                max_move_pct=max_move_pct,
            )
            key = str(candidate.get("eventKey") or "")
            position = event_indexes.get(key)
            if position is None:
                continue
            existing = events[position]
            if existing.get("historicalBackfill") is True:
                continue
            if str(existing.get("pricingPolicyVersion") or "") == TENNIS_PRICING_POLICY_VERSION:
                continue

            old_move = float(existing.get("movePct") or 0.0)
            new_move = float(candidate.get("movePct") or 0.0)
            old_factor = 1.0 + old_move / 100.0
            new_factor = 1.0 + new_move / 100.0
            if old_factor <= 0 or new_factor <= 0:
                continue

            before = float(existing.get("priceBefore") or 0.0)
            after = float(existing.get("priceAfter") or 0.0)
            if before > 0 and after > 0:
                new_after = round(before * new_factor, 2)
                scale = new_after / after if after > 0 else new_factor / old_factor
                existing["priceAfter"] = new_after
                existing["movePct"] = round((new_after / before - 1.0) * 100.0, 3)
            else:
                scale = new_factor / old_factor
                existing["movePct"] = round(new_move, 3)

            existing["pricingPolicyVersion"] = TENNIS_PRICING_POLICY_VERSION
            existing["pricingBasis"] = "verified-tennis-result-stage-elite-opponent-surprise"
            existing["reason"] = candidate.get("reason")

            if abs(scale - 1.0) > 1e-9:
                for later in events[position + 1:]:
                    if float(later.get("priceBefore") or 0) > 0:
                        later["priceBefore"] = round(float(later["priceBefore"]) * scale, 2)
                    if float(later.get("priceAfter") or 0) > 0:
                        later["priceAfter"] = round(float(later["priceAfter"]) * scale, 2)
                current_price *= scale
                repriced = True

        # Stamp brand-new correctly priced events so they are not reconsidered.
        for event in events:
            if str(event.get("sport") or "").lower() == "tennis" and event.get("historicalBackfill") is not True:
                event.setdefault("pricingPolicyVersion", TENNIS_PRICING_POLICY_VERSION)

        if repriced:
            repriced_records += 1
            latest = max(events, key=lambda event: str(event.get("startedAt") or ""))
            result["marketPrice"] = round(current_price, 2)
            if float(latest.get("priceBefore") or 0) > 0:
                result["previousMarketPrice"] = round(float(latest["priceBefore"]), 2)
            if str(latest.get("sport") or "").lower() == "tennis":
                latest_move = round(float(latest.get("movePct") or 0), 3)
                result["hourlyChangePct"] = latest_move
                result["dailyChange"] = latest_move
                result["lastGameMovePct"] = latest_move
                result["lastGameStats"] = dict(latest.get("stats") or {})
                result["lastPriceEventAt"] = latest.get("startedAt")
                result["lastPriceEvent"] = latest.get("name")
                result["lastPriceEventId"] = latest.get("eventKey")
                result["priceExplanation"] = latest.get("reason")
            _sync_history_to_events(result, events)

        result["priceEvents"] = events
        result["tennisPricingPolicyVersion"] = TENNIS_PRICING_POLICY_VERSION
        updated[index] = result

    return updated, touched + repriced_records, added


base.tennis_match_move = tennis_match_move_results
base.record_strength = verified_tennis_record_strength
base.discover_matches = discover_matches_results
base.apply_live_matches = apply_live_matches_results
base.RESULTS_EVENT_PRICING_MODEL = RESULTS_MODEL_VERSION

if __name__ == "__main__":
    raise SystemExit(base.main())
