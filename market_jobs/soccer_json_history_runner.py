#!/usr/bin/env python3
"""Run Soccer history with Soccer-aware ESPN JSON parsing.

The shared TalentX ESPN parser is optimized for sports whose summary payloads
expose ``boxscore.players``. Soccer frequently places participation under
``rosters`` / ``lineups`` and also uses full-time status labels that differ from
other ESPN sports. This runner patches only the standalone Soccer history job so
verified finished matches, starters and used substitutes are not discarded.
"""
from __future__ import annotations

import math
import re
from typing import Any

import soccer_json_history as base

ESPN_CDN_LINEUPS = "https://cdn.espn.com/core/soccer/lineups?xhr=1&gameId={event_id}&league={league}"

STAT_ALIASES = {
    "minutes": "minutes", "min": "minutes", "mins": "minutes",
    "goals": "goals", "goal": "goals", "g": "goals", "gls": "goals",
    "assists": "assists", "assist": "assists", "a": "assists", "ast": "assists",
    "shots": "shots", "shot": "shots", "sh": "shots", "totalshots": "shots",
    "shotsontarget": "shotsOnTarget", "shotsongoal": "shotsOnTarget",
    "shotstarget": "shotsOnTarget", "sog": "shotsOnTarget", "st": "shotsOnTarget",
    "saves": "saves", "save": "saves", "sv": "saves",
    "yellowcards": "yellowCards", "yellowcard": "yellowCards", "yc": "yellowCards",
    "redcards": "redCards", "redcard": "redCards", "rc": "redCards",
}


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def soccer_completed_event(state: str) -> bool:
    """Accept ESPN Soccer's finished-match status vocabulary.

    Soccer feeds commonly use values such as STATUS_FULL_TIME / FULL_TIME in
    places where MLB/NBA use post/final. Treat only explicit final/full-time
    variants as complete; scheduled, postponed and abandoned matches remain out.
    """
    normalized = norm(state)
    if not normalized:
        return False
    if normalized in {
        "post", "final", "completed", "complete", "off", "closed", "official",
        "fulltime", "fulltimeresult", "statusfinal", "statusfulltime",
        "statusfulltimeresult", "afterextratime", "statusafterextratime",
        "afterpenalties", "statusafterpenalties",
    }:
        return True
    return normalized.startswith("statusfinal") or normalized.startswith("statusfulltime")


def numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "-", "—", "DNP", "DND", "N/A"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def team_id_from(node: dict[str, Any], fallback: str = "") -> str:
    team = node.get("team") if isinstance(node.get("team"), dict) else {}
    for value in (team.get("id"), node.get("teamId"), node.get("teamID")):
        text = str(value or "").strip()
        if text:
            return text
    return fallback


def normalized_stat_name(value: Any) -> str | None:
    return STAT_ALIASES.get(norm(value))


def add_stat(output: dict[str, float], name: Any, value: Any) -> None:
    target = normalized_stat_name(name)
    parsed = numeric(value)
    if target and parsed is not None:
        output[target] = parsed


def stats_from_entry(entry: dict[str, Any]) -> dict[str, float]:
    output: dict[str, float] = {}
    for key, value in entry.items():
        if normalized_stat_name(key):
            add_stat(output, key, value)

    labels = entry.get("labels") or entry.get("names") or entry.get("displayNames")
    raw_stats = entry.get("stats")
    if isinstance(labels, list) and isinstance(raw_stats, list) and raw_stats and not isinstance(raw_stats[0], dict):
        for label, value in zip(labels, raw_stats):
            add_stat(output, label, value)

    def consume(value: Any) -> None:
        if isinstance(value, dict):
            label = value.get("name") or value.get("abbreviation") or value.get("displayName") or value.get("label")
            raw = value.get("value")
            if raw is None:
                raw = value.get("displayValue")
            if raw is None:
                raw = value.get("stat")
            if label is not None and raw is not None:
                add_stat(output, label, raw)
            for key, nested in value.items():
                if normalized_stat_name(key) and not isinstance(nested, (dict, list)):
                    add_stat(output, key, nested)
        elif isinstance(value, list):
            for item in value:
                consume(item)

    consume(entry.get("statistics"))
    if isinstance(raw_stats, (dict, list)):
        consume(raw_stats)
    return output


def entry_participated(entry: dict[str, Any], stats: dict[str, float], in_lineup: bool) -> bool:
    if entry.get("didNotPlay") is True or entry.get("dnp") is True:
        return False
    if entry.get("starter") is True or entry.get("subbedIn") is True or entry.get("substituteUsed") is True:
        return True
    for key in ("played", "appeared", "enteredGame"):
        if entry.get(key) is True:
            return True
    if (stats.get("minutes") or 0) > 0:
        return True
    if any(abs(stats.get(key, 0)) > 0 for key in ("goals", "assists", "shots", "shotsOnTarget", "saves", "yellowCards", "redCards")):
        return True
    status = " ".join(str(entry.get(key) or "") for key in ("status", "type", "role", "lineupType")).lower()
    if in_lineup and any(token in status for token in ("starter", "starting", "subbed in", "substitute used")):
        return True
    return False


def extract_soccer_lineup_stats(payload: dict[str, Any], winning_team_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Extract verified Soccer participants from roster/lineup-shaped ESPN JSON."""
    found: dict[str, dict[str, Any]] = {}
    lineup_keys = {"roster", "rosters", "lineup", "lineups", "starters", "substitutes", "players", "athletes"}

    def merge(athlete_id: str, team_id: str, stats: dict[str, float]) -> None:
        item = found.setdefault(
            athlete_id,
            {"stats": {}, "teamId": team_id, "teamWon": team_id in winning_team_ids if team_id else None},
        )
        if team_id and not item.get("teamId"):
            item["teamId"] = team_id
            item["teamWon"] = team_id in winning_team_ids
        item["stats"].update(stats)
        item["stats"]["appearances"] = 1.0
        item["stats"]["gamesPlayed"] = 1.0

    def walk(node: Any, team_id: str = "", in_lineup: bool = False) -> None:
        if isinstance(node, dict):
            local_team = team_id_from(node, team_id)
            athlete = node.get("athlete") if isinstance(node.get("athlete"), dict) else None
            player = node.get("player") if isinstance(node.get("player"), dict) else None
            person = athlete or player
            athlete_id = ""
            if person:
                athlete_id = str(person.get("id") or person.get("uid") or "").strip()
            if not athlete_id:
                athlete_id = str(node.get("athleteId") or node.get("playerId") or "").strip()
            if athlete_id:
                stats = stats_from_entry(node)
                if entry_participated(node, stats, in_lineup):
                    merge(athlete_id, local_team, stats)

            for key, value in node.items():
                child_lineup = in_lineup or norm(key) in lineup_keys
                walk(value, local_team, child_lineup)
        elif isinstance(node, list):
            for value in node:
                walk(value, team_id, in_lineup)

    walk(payload)
    return found


def merge_player_maps(target: dict[str, dict[str, Any]], incoming: dict[str, dict[str, Any]]) -> None:
    for athlete_id, item in incoming.items():
        if athlete_id not in target:
            target[athlete_id] = {"stats": {}, "teamId": item.get("teamId", ""), "teamWon": item.get("teamWon")}
        current = target[athlete_id]
        if item.get("teamId") and not current.get("teamId"):
            current["teamId"] = item.get("teamId")
            current["teamWon"] = item.get("teamWon")
        current.setdefault("stats", {}).update(item.get("stats") or {})


def fetch_match_stats(info: dict[str, Any], timeout: float) -> tuple[dict[str, dict[str, Any]], str | None]:
    winners = {str(value) for value in info.get("winningTeamIds") or []}
    sources = [
        base.ESPN_SUMMARY.format(sport="soccer", league=info["league"], event_id=info["eventId"]),
        ESPN_CDN_LINEUPS.format(league=info["league"], event_id=info["eventId"]),
        base.ESPN_CDN_BOXSCORE.format(league=info["league"], event_id=info["eventId"]),
        base.ESPN_CDN_GAME.format(league=info["league"], event_id=info["eventId"]),
    ]
    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for source in sources:
        try:
            payload = base.unwrap_gamepackage(base.fetch_json(source, timeout))
            merge_player_maps(merged, base.extract_espn_game_stats(payload, winners))
            merge_player_maps(merged, extract_soccer_lineup_stats(payload, winners))
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)
    if merged:
        return merged, None
    return {}, f"no Soccer player JSON for {info['league']}/{info['eventId']} ({'/'.join(errors) or 'empty payloads'})"


# Patch only this standalone history process. Live Sports game pricing keeps its
# existing parser until this Soccer-specific behavior proves healthy.
base.completed_event = soccer_completed_event
base.fetch_match_stats = fetch_match_stats


if __name__ == "__main__":
    raise SystemExit(base.main())
