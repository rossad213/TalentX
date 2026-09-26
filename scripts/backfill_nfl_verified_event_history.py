#!/usr/bin/env python3
"""Backfill verified NFL game events with point-in-time pricing evidence.

This is intentionally NFL-specific. Historical games are discovered from ESPN
completed-game scoreboards and player box scores, then replayed chronologically
with the same NFL expectation, opportunity-protection, and result-move functions
used by the live Sports refresh.

The critical rule is *no look-ahead*: when pricing a historical game, the current
season baseline contains only games that occurred before that event. Completed
prior seasons may be used, but later games from the same season are excluded.

Backfilled chart prices are modeled replay prices, not claims that TalentX
actually traded at those prices in the past. The replay is anchored backward to
the earliest recorded live event when one exists, otherwise to today's unchanged
market price. Every chart movePct is recomputed from its rounded priceBefore and
priceAfter so the percentage shown for an event exactly matches the chart points.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import hourly_price_refresh as refresh
import hourly_price_refresh_nfl as nfl
import hourly_price_refresh_nfl_rb as nfl_rb

BACKFILL_VERSION = "1.1-nfl-complete-point-in-time-chart-replay"
BACKFILL_MODEL = (
    f"{BACKFILL_VERSION}+{nfl.NFL_EXPECTATION_MODEL_VERSION}"
)
HISTORY_SOURCE = "verified-nfl-event-replay"
OLD_HISTORY_SOURCES = {HISTORY_SOURCE, "verified-historical-game-backfill"}
MAX_PRICE_EVENTS = 2500
MAX_HISTORY_POINTS = 5000
NFL_GAME_MONTHS = {1, 2, 7, 8, 9, 10, 11, 12}


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


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def positive_price(value: Any) -> float | None:
    parsed = finite(value)
    return parsed if parsed is not None and parsed > 0 else None


def event_key(event: dict[str, Any]) -> str:
    return str(event.get("eventKey") or event.get("eventId") or "").strip()


def event_started(event: dict[str, Any]) -> datetime | None:
    return parse_time(event.get("startedAt") or event.get("time") or event.get("date"))


def event_season(event: dict[str, Any]) -> int | None:
    started = event_started(event)
    if started is None:
        return None
    return started.year if started.month >= 7 else started.year - 1


def is_nfl_espn(record: dict[str, Any]) -> bool:
    return (
        str(record.get("primaryCategory") or "") == "Athlete"
        and str(record.get("leagueOrMedium") or "").strip().upper() == "NFL"
        and str(record.get("sourceNamespace") or "").strip().lower() == "espn"
        and bool(str(record.get("sourceRecordId") or "").strip())
    )


def needs_backfill(
    record: dict[str, Any],
    *,
    force: bool = False,
    requested_days: int | None = None,
) -> bool:
    if not is_nfl_espn(record):
        return False
    if force or str(record.get("nflHistoricalBackfillVersion") or "") != BACKFILL_VERSION:
        return True
    if requested_days is None:
        return False
    completed_days = int(finite(record.get("nflHistoricalBackfillDays")) or 0)
    return completed_days < max(1, int(requested_days))


def _completed_state(event: dict[str, Any]) -> bool:
    status = event.get("status") if isinstance(event.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    state = str(status_type.get("state") or status_type.get("name") or "")
    return refresh.completed_event(state)


def _scoreboard_dates(now: datetime, days: int) -> list[datetime]:
    first = (now - timedelta(days=max(1, days))).date()
    last = now.date()
    count = (last - first).days
    output: list[datetime] = []
    for offset in range(count + 1):
        day = first + timedelta(days=offset)
        if day.month in NFL_GAME_MONTHS:
            output.append(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
    return output


def discover_nfl_player_events(
    target_player_ids: set[str],
    *,
    now: datetime,
    days: int,
    timeout: float,
    workers: int,
) -> tuple[dict[str, list[dict[str, Any]]], int, list[str]]:
    """Discover completed NFL games and verified ESPN player box scores quickly."""
    cutoff = now - timedelta(days=max(1, days))
    dates = _scoreboard_dates(now, days)
    warnings: list[str] = []
    events: dict[str, dict[str, Any]] = {}

    def fetch_scoreboard(day: datetime) -> tuple[list[dict[str, Any]], str | None]:
        date = day.strftime("%Y%m%d")
        url = refresh.ESPN_SCOREBOARD.format(sport="football", league="nfl", date=date)
        try:
            payload = refresh.fetch_json(url, timeout)
        except Exception as exc:  # noqa: BLE001
            return [], f"scoreboard nfl/{date}: {type(exc).__name__}"
        rows = payload.get("events") if isinstance(payload, dict) else []
        return [row for row in rows or [] if isinstance(row, dict)], None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_scoreboard, day): day for day in dates}
        for future in as_completed(futures):
            rows, warning = future.result()
            if warning:
                warnings.append(warning)
            for event in rows:
                event_id = str(event.get("id") or "").strip()
                started = refresh.parse_datetime(event.get("date"))
                if (
                    not event_id
                    or started is None
                    or started < cutoff
                    or started > now + timedelta(hours=1)
                    or not _completed_state(event)
                ):
                    continue
                key = refresh.event_key("ESPN", event_id)
                if key in events:
                    continue
                winning_team_ids: list[str] = []
                for competition in event.get("competitions") or []:
                    if not isinstance(competition, dict):
                        continue
                    for competitor in competition.get("competitors") or []:
                        if not isinstance(competitor, dict):
                            continue
                        team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
                        team_id = str(team.get("id") or "")
                        if team_id and competitor.get("winner") is True:
                            winning_team_ids.append(team_id)
                season = event.get("season") if isinstance(event.get("season"), dict) else {}
                events[key] = {
                    "eventKey": key,
                    "eventId": event_id,
                    "provider": "ESPN",
                    "league": "nfl",
                    "sport": "football",
                    "name": str(event.get("name") or event.get("shortName") or event_id),
                    "startedAt": refresh.iso_utc(started),
                    "winningTeamIds": winning_team_ids,
                    "seasonType": season.get("type"),
                }

    athlete_events: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def fetch_summary(info: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str | None]:
        url = refresh.ESPN_SUMMARY.format(
            sport="football",
            league="nfl",
            event_id=str(info["eventId"]),
        )
        try:
            payload = refresh.fetch_json(url, timeout)
            stats = refresh.extract_espn_game_stats(
                payload,
                set(str(value) for value in info.get("winningTeamIds") or []),
            )
            return stats, None
        except Exception as exc:  # noqa: BLE001
            return {}, f"summary nfl/{info['eventId']}: {type(exc).__name__}"

    event_values = list(events.values())
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_summary, info): info for info in event_values}
        for future in as_completed(futures):
            info = futures[future]
            stats_by_player, warning = future.result()
            if warning:
                warnings.append(warning)
            for athlete_id, performance in stats_by_player.items():
                athlete_id = str(athlete_id)
                if athlete_id not in target_player_ids:
                    continue
                athlete_events[athlete_id].append(
                    {
                        "eventKey": info["eventKey"],
                        "eventId": info["eventId"],
                        "provider": "ESPN",
                        "league": "nfl",
                        "sport": "football",
                        "name": info["name"],
                        "startedAt": info["startedAt"],
                        "seasonType": info.get("seasonType"),
                        "stats": performance.get("stats") if isinstance(performance.get("stats"), dict) else {},
                        "teamWon": performance.get("teamWon"),
                        "verifiedSource": "ESPN completed-game scoreboard + player box score",
                    }
                )

    for values in athlete_events.values():
        values.sort(key=lambda event: str(event.get("startedAt") or ""))
    return dict(athlete_events), len(events), warnings


def normalized_game_stats(event: dict[str, Any]) -> dict[str, float]:
    stats = event.get("stats") if isinstance(event.get("stats"), dict) else {}
    output: dict[str, float] = {}
    for key, value in stats.items():
        parsed = refresh.numeric_box_value(value)
        normalized = refresh.norm_key(key)
        if parsed is not None and normalized:
            output[normalized] = parsed
    if "yardsperpassattempt" not in output and "battingaverage" in output:
        output["yardsperpassattempt"] = output["battingaverage"]
    if "passerrating" in output:
        output["qbrating"] = output["passerrating"]
    return output


def _rate_weight(stats: dict[str, float], key: str) -> float:
    key = refresh.norm_key(key)
    if any(token in key for token in ("passerrating", "qbrating", "completionpct", "completionpercentage", "yardsperpassattempt")):
        return max(1.0, float(refresh.stat_value(stats, "passingAttempts", "passAttempts", "attempts") or 1.0))
    if any(token in key for token in ("yardsperrushattempt", "yardspercarry", "rushingaverage")):
        return max(1.0, float(refresh.stat_value(stats, "rushingAttempts", "rushAttempts", "carries") or 1.0))
    if any(token in key for token in ("yardsperreception", "catchpct", "catchpercentage")):
        return max(1.0, float(refresh.stat_value(stats, "receivingTargets", "targets", "receptions") or 1.0))
    return 1.0


def aggregate_prior_games(events: list[dict[str, Any]]) -> dict[str, float]:
    """Build cumulative season stats from only the games already played."""
    totals: dict[str, float] = defaultdict(float)
    rate_numerator: dict[str, float] = defaultdict(float)
    rate_denominator: dict[str, float] = defaultdict(float)
    games = 0
    for event in events:
        stats = normalized_game_stats(event)
        if not stats:
            continue
        games += 1
        for key, value in stats.items():
            if nfl._is_rate_stat(key):
                weight = _rate_weight(stats, key)
                rate_numerator[key] += float(value) * weight
                rate_denominator[key] += weight
            else:
                totals[key] += float(value)
    output = dict(totals)
    for key, numerator in rate_numerator.items():
        denominator = rate_denominator.get(key, 0.0)
        if denominator > 0:
            output[key] = numerator / denominator
    if games:
        output["gamesplayed"] = float(games)
    return output


def _base_history(item: dict[str, Any]) -> dict[int, dict[str, float]]:
    raw = item.get("nflSeasonStats") if isinstance(item.get("nflSeasonStats"), dict) else {}
    output: dict[int, dict[str, float]] = {}
    for raw_year, stats in raw.items():
        if not isinstance(stats, dict):
            continue
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            continue
        output[year] = dict(stats)
    return output


def point_in_time_item(
    record: dict[str, Any],
    base_item: dict[str, Any],
    prior_same_season_events: list[dict[str, Any]],
    season: int,
) -> tuple[dict[str, Any], int]:
    """Return evidence containing nothing from later games in the event season."""
    source_history = _base_history(base_item)
    history = {
        year: dict(stats)
        for year, stats in source_history.items()
        if year < season
    }
    current = aggregate_prior_games(prior_same_season_events)
    if current:
        history[season] = current

    prior_games = 0
    for year, stats in history.items():
        if year == season:
            continue
        count = nfl._game_count(stats)
        if count is not None:
            prior_games += int(round(count))
    prior_games += len([event for event in prior_same_season_events if normalized_game_stats(event)])

    item = dict(base_item)
    item["nflSeasonStats"] = history
    item["professionalGames"] = prior_games
    item["career"] = {}
    item["recent"] = current if current else {}

    # Build a safe pre-game signal bundle as a fallback. Do not carry the
    # current/future season aggregate from the original evidence item.
    baseline = nfl.nfl_expected_baseline_stats(record, item)
    item["recent"] = baseline
    item["signals"] = refresh.signal_bundle(record, baseline, {}, 0.0) if baseline else {}
    item["historicalExpectationAsOfSeason"] = season
    item["historicalExpectationPriorGames"] = len(prior_same_season_events)
    return item, prior_games


def evaluate_player_events(
    record: dict[str, Any],
    base_item: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate each historical game using only evidence available before it."""
    prior_by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
    output: list[dict[str, Any]] = []

    for event in sorted(events, key=lambda value: str(value.get("startedAt") or "")):
        season = event_season(event)
        if season is None:
            continue
        prior_events = prior_by_season[season]
        item, prior_games = point_in_time_item(record, base_item, prior_events, season)
        point_record = dict(record)
        point_record["professionalGames"] = prior_games

        move_pct, evidence = nfl.nfl_results_based_game_event_move(
            point_record,
            item,
            event,
            float("inf"),
        )

        # The game becomes evidence for the next game even when this particular
        # event could not be priced (for example, a rookie's first regular game).
        prior_events.append(event)

        if not evidence.get("comparable"):
            continue
        move = finite(move_pct)
        if move is None or move <= -100.0:
            continue
        output.append(
            {
                **event,
                **evidence,
                "eventKey": event_key(event),
                "eventId": str(event.get("eventId") or event_key(event)),
                "eventType": "game",
                "verified": True,
                "historicalBackfill": True,
                "backfillModel": BACKFILL_MODEL,
                "historicalExpectationMode": "point-in-time-pre-game",
                "historicalExpectationPriorGames": len(prior_events) - 1,
                "modelMovePct": round(move, 3),
                "priceBasis": "modeled historical replay; verified game/box score; no look-ahead",
            }
        )
    return output


def _is_old_backfill_event(event: dict[str, Any]) -> bool:
    return bool(event.get("historicalBackfill")) or str(event.get("backfillModel") or "").startswith("historical-game-events-")


def existing_live_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("priceEvents") if isinstance(record.get("priceEvents"), list) else []
    return [
        dict(event)
        for event in raw
        if isinstance(event, dict) and not _is_old_backfill_event(event)
    ]


def _earliest_live_anchor(events: list[dict[str, Any]]) -> tuple[datetime, float] | None:
    candidates: list[tuple[datetime, float]] = []
    for event in events:
        started = event_started(event)
        before = positive_price(event.get("priceBefore"))
        if started is not None and before is not None:
            candidates.append((started, before))
    return min(candidates, key=lambda item: item[0]) if candidates else None


def reconstruct_backfill_chain(
    record: dict[str, Any],
    generated: list[dict[str, Any]],
    live_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Replay only the historical section before the first recorded live event."""
    live_keys = {event_key(event) for event in live_events if event_key(event)}
    anchor = _earliest_live_anchor(live_events)
    if anchor is not None:
        anchor_time, anchor_price = anchor
        candidates = [
            dict(event)
            for event in generated
            if event_key(event) not in live_keys
            and event_started(event) is not None
            and event_started(event) < anchor_time
        ]
        anchor_type = "earliest-recorded-live-event"
    else:
        anchor_price = positive_price(record.get("marketPrice")) or 1.0
        candidates = [dict(event) for event in generated if event_key(event) not in live_keys]
        anchor_type = "current-market-price"

    after = round(float(anchor_price), 2)
    rebuilt: list[dict[str, Any]] = []
    for event in reversed(sorted(candidates, key=lambda value: str(value.get("startedAt") or ""))):
        model_move = finite(event.get("modelMovePct"))
        if model_move is None or model_move <= -100.0:
            continue
        denominator = 1.0 + model_move / 100.0
        if denominator <= 0:
            continue
        before = max(0.01, round(after / denominator, 2))
        after_rounded = max(0.01, round(after, 2))
        actual_move = round((after_rounded / before - 1.0) * 100.0, 3)
        replay = dict(event)
        replay["priceBefore"] = before
        replay["priceAfter"] = after_rounded
        replay["movePct"] = actual_move
        replay["chartMovePct"] = actual_move
        replay["chartCorrelationVerified"] = True
        rebuilt.append(replay)
        after = before

    rebuilt.reverse()
    return rebuilt, anchor_type


def reconstruct_full_chart_chain(
    record: dict[str, Any],
    generated: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Build one complete modeled NFL chart chain ending at today's live price.

    Unlike the older pre-live-only replay, this deliberately includes verified
    games before, between, and after recorded live events. That lets history
    recover a missing recent game (for example a game that was once priced live
    but later disappeared from the durable event ledger) without changing today's
    marketPrice. The chart prices are modeled replay prices; live priceEvents
    remain authoritative for the actual recorded market ledger.
    """
    current_price = positive_price(record.get("marketPrice")) or 1.0
    by_key: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for event in generated:
        if not isinstance(event, dict):
            continue
        key = event_key(event)
        if key:
            by_key[key] = dict(event)
        else:
            anonymous.append(dict(event))
    candidates = [*anonymous, *by_key.values()]
    candidates = [
        event for event in candidates
        if event_started(event) is not None
        and finite(event.get("modelMovePct")) is not None
        and float(event.get("modelMovePct")) > -100.0
    ]
    candidates.sort(key=lambda value: str(value.get("startedAt") or ""))

    after = round(float(current_price), 2)
    rebuilt: list[dict[str, Any]] = []
    for event in reversed(candidates):
        model_move = finite(event.get("modelMovePct"))
        if model_move is None or model_move <= -100.0:
            continue
        denominator = 1.0 + model_move / 100.0
        if denominator <= 0:
            continue
        before = max(0.01, round(after / denominator, 2))
        after_rounded = max(0.01, round(after, 2))
        actual_move = round((after_rounded / before - 1.0) * 100.0, 3)
        replay = dict(event)
        replay["priceBefore"] = before
        replay["priceAfter"] = after_rounded
        replay["movePct"] = actual_move
        replay["chartMovePct"] = actual_move
        replay["chartCorrelationVerified"] = True
        replay["historicalBackfill"] = True
        replay["verified"] = True
        replay["priceBasis"] = (
            "modeled historical replay anchored to current TalentX market price; "
            "verified game/box score; point-in-time pre-game expectation; no look-ahead"
        )
        rebuilt.append(replay)
        after = before

    rebuilt.reverse()
    return rebuilt, "current-market-price-full-replay"


def replay_history_points(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for event in events:
        started = event_started(event)
        key = event_key(event)
        before = positive_price(event.get("priceBefore"))
        after = positive_price(event.get("priceAfter"))
        if started is None or not key or before is None or after is None:
            continue
        move = round((after / before - 1.0) * 100.0, 3)
        common = {
            "eventId": key,
            "label": str(event.get("name") or "Verified NFL game"),
            "historyType": "verified-event-replay",
            "eventType": "game",
            "provider": "ESPN",
            "source": HISTORY_SOURCE,
            "priceBasis": str(event.get("priceBasis") or ""),
            "movePct": move,
            "modelMovePct": event.get("modelMovePct"),
            "performanceDeltaPct": event.get("performanceDeltaPct"),
        }
        points.append({**common, "time": iso(started - timedelta(seconds=1)), "price": round(before, 2), "phase": "open"})
        points.append({**common, "time": iso(started), "price": round(after, 2), "phase": "close"})
    return points


def merge_history(
    record: dict[str, Any],
    replay_events: list[dict[str, Any]],
    live_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    existing = record.get("priceHistory") if isinstance(record.get("priceHistory"), list) else []
    live_price_map: dict[str, tuple[float, float]] = {}
    for event in live_events or []:
        key = event_key(event)
        before = positive_price(event.get("priceBefore"))
        after = positive_price(event.get("priceAfter"))
        if key and before is not None and after is not None:
            live_price_map[key] = (round(before, 2), round(after, 2))

    preserved: list[dict[str, Any]] = []
    for item in existing:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")
        history_type = str(item.get("historyType") or "")
        if source in OLD_HISTORY_SOURCES or history_type == "verified-event-replay":
            continue
        point = dict(item)
        point_id = str(point.get("eventId") or point.get("eventKey") or "")
        prices = live_price_map.get(point_id)
        if prices is not None:
            phase = str(point.get("phase") or "").lower()
            if phase == "open":
                point["price"] = prices[0]
            elif phase == "close":
                point["price"] = prices[1]
        preserved.append(point)

    combined = preserved + replay_history_points(replay_events)
    combined.sort(key=lambda item: str(item.get("time") or ""))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in combined:
        key = (
            str(item.get("time") or ""),
            str(item.get("eventId") or ""),
            str(item.get("phase") or ""),
            str(item.get("source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[-MAX_HISTORY_POINTS:]


def apply_backfill(
    record: dict[str, Any],
    generated: list[dict[str, Any]],
    *,
    completed_at: str,
    days: int,
) -> tuple[dict[str, Any], int]:
    """Attach replay events/history without changing today's live market state."""
    result = dict(record)
    market_fields = {
        key: result.get(key)
        for key in (
            "marketPrice",
            "previousMarketPrice",
            "fairValue",
            "fundamentalValue",
            "dailyChange",
            "hourlyChangePct",
            "lastPriceEventId",
            "lastPriceEventAt",
            "lastPriceEvent",
            "lastGameMovePct",
        )
    }

    live = existing_live_events(result)
    replay, anchor_type = reconstruct_full_chart_chain(result, generated)
    live_keys = {event_key(event) for event in live if event_key(event)}
    missing_replay = [
        dict(event)
        for event in replay
        if event_key(event) and event_key(event) not in live_keys
    ]
    combined_events = [*missing_replay, *live]
    combined_events.sort(key=lambda event: str(event.get("startedAt") or ""))
    result["priceEvents"] = combined_events[-MAX_PRICE_EVENTS:]
    result["priceHistory"] = merge_history(result, replay, live)
    if result["priceHistory"]:
        result["priceHistoryStatus"] = "source-backed-full-point-in-time-nfl-replay"
        result["priceHistoryDisclosure"] = (
            "NFL game facts and dates are source-backed. Chart prices are modeled "
            "point-in-time responses to verified games, anchored to today's unchanged "
            "TalentX market price; they are not claims of historical trades."
        )

    result["nflHistoricalBackfillVersion"] = BACKFILL_VERSION
    result["nflHistoricalBackfilledAt"] = completed_at
    result["nflHistoricalBackfillDays"] = int(days)
    result["nflHistoricalBackfillEventCount"] = len(replay)
    result["nflHistoricalBackfillImportedEventCount"] = len(missing_replay)
    result["nflHistoricalBackfillChartPointCount"] = len(replay) * 2
    result["nflHistoricalBackfillAnchor"] = anchor_type
    result["nflHistoricalBackfillModel"] = BACKFILL_MODEL
    if replay:
        result["nflHistoricalBackfillFirstEventAt"] = replay[0].get("startedAt")
        result["nflHistoricalBackfillLastEventAt"] = replay[-1].get("startedAt")

    # Strong invariant: this operation is chart/history enrichment only.
    for key, value in market_fields.items():
        if key in record:
            result[key] = value
        else:
            result.pop(key, None)
    return result, len(missing_replay)


def load_catalog(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"{path} must contain a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--days", type=int, default=400)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--max-athletes", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    records = load_catalog(args.catalog)
    target_indexes = [
        index for index, record in enumerate(records)
        if needs_backfill(record, force=args.force, requested_days=args.days)
    ]
    if not target_indexes:
        print(f"NFL verified history backfill {BACKFILL_VERSION} already complete; no work required.")
        return 0

    # Install the exact NFL production/expectation/opportunity hooks used by the
    # live Sports refresh before either evidence collection or historical moves.
    nfl_rb.install_rb_receiving_td_credit()

    target_records = [records[index] for index in target_indexes]
    if len(target_records) > args.max_athletes:
        target_records = sorted(
            target_records,
            key=lambda record: float(record.get("marketPrice") or 0.0),
            reverse=True,
        )[: args.max_athletes]
        target_ids = {str(record.get("id") or "") for record in target_records}
        target_indexes = [
            index for index in target_indexes
            if str(records[index].get("id") or "") in target_ids
        ]

    source_ids = {
        str(records[index].get("sourceRecordId") or "").strip()
        for index in target_indexes
        if str(records[index].get("sourceRecordId") or "").strip()
    }
    now = datetime.now(timezone.utc)
    athlete_events, discovered_games, warnings = discover_nfl_player_events(
        source_ids,
        now=now,
        days=args.days,
        timeout=args.request_timeout,
        workers=args.workers,
    )
    participants = {
        str(records[index].get("sourceRecordId") or "").strip()
        for index in target_indexes
        if str(records[index].get("sourceRecordId") or "").strip() in athlete_events
    }
    print(
        f"NFL historical discovery: {discovered_games:,} completed games; "
        f"{len(participants):,} target players with verified box scores; warnings={len(warnings):,}."
    )

    evidence: dict[int, dict[str, Any]] = {}
    evidence_indexes = [
        index for index in target_indexes
        if str(records[index].get("sourceRecordId") or "").strip() in participants
    ]
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(refresh.fetch_hourly_evidence, records[index], args.request_timeout): index
            for index in evidence_indexes
        }
        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            try:
                evidence[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                evidence[index] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            completed += 1
            if completed % 200 == 0 or completed == len(futures):
                usable = sum(1 for item in evidence.values() if item.get("ok"))
                print(f"NFL historical evidence: {completed:,}/{len(futures):,}; usable={usable:,}.", flush=True)

    completed_at = iso(now)
    output = list(records)
    players_completed = 0
    players_deferred = 0
    replay_events = 0
    chart_points = 0

    for index in target_indexes:
        record = records[index]
        athlete_id = str(record.get("sourceRecordId") or "").strip()
        events = athlete_events.get(athlete_id, [])
        if not events:
            updated, added = apply_backfill(record, [], completed_at=completed_at, days=args.days)
            output[index] = updated
            players_completed += 1
            continue

        item = evidence.get(index, {})
        if not item.get("ok"):
            deferred = dict(record)
            deferred["nflHistoricalBackfillWarning"] = str(item.get("reason") or "historical evidence unavailable")
            deferred["nflHistoricalBackfillCheckedAt"] = completed_at
            output[index] = deferred
            players_deferred += 1
            continue

        generated = evaluate_player_events(record, item, events)
        updated, added = apply_backfill(
            record,
            generated,
            completed_at=completed_at,
            days=args.days,
        )
        updated.pop("nflHistoricalBackfillWarning", None)
        output[index] = updated
        players_completed += 1
        replay_events += added
        chart_points += added * 2

    args.catalog.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"NFL verified history backfill complete: players={players_completed:,}; "
        f"deferred={players_deferred:,}; replay events={replay_events:,}; "
        f"chart points={chart_points:,}; current market prices unchanged."
    )
    if warnings:
        print("Sample discovery warnings:")
        for warning in warnings[:20]:
            print(f"  {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
