#!/usr/bin/env python3
"""Sync current NFL injury-report status into the TalentX Sports catalog.

This is an NFL-only context layer. It never changes talent/performance evidence
or another sport. The valuation layer decides how a verified current injury
changes availability/situation; this script only records the factual report.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

NFL_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
USER_AGENT = "TalentX-NFL-Injury-Sync/1.0 (+https://github.com/rossad213/TalentX)"
INJURY_EVIDENCE_VERSION = "1.0-espn-league-injury-report"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("description", "displayName", "shortDisplayName", "name", "status", "detail"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return ""


def injury_identity(item: dict[str, Any]) -> tuple[str, str]:
    athlete = item.get("athlete") if isinstance(item.get("athlete"), dict) else {}
    athlete_id = str(athlete.get("id") or item.get("athleteId") or "").strip()
    name = str(
        athlete.get("fullName")
        or athlete.get("displayName")
        or athlete.get("name")
        or item.get("athleteName")
        or ""
    ).strip()
    return athlete_id, name


def injury_status(item: dict[str, Any]) -> str:
    for key in ("status", "designation", "gameStatus", "injuryStatus"):
        value = _text(item.get(key))
        if value:
            return value
    details = item.get("details")
    if isinstance(details, dict):
        for key in ("status", "type", "detail"):
            value = _text(details.get(key))
            if value:
                return value
    return "Injury report"


def injury_type(item: dict[str, Any]) -> str:
    details = item.get("details")
    if isinstance(details, dict):
        for key in ("type", "injuryType", "location"):
            value = _text(details.get(key))
            if value:
                return value
    for key in ("type", "injuryType"):
        value = _text(item.get(key))
        if value and value.lower() not in {"out", "doubtful", "questionable", "probable"}:
            return value
    return ""


def parse_injury_report(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return current injuries keyed by ESPN athlete id and normalized name."""
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    team_groups = payload.get("injuries") if isinstance(payload.get("injuries"), list) else []
    for group in team_groups:
        if not isinstance(group, dict):
            continue
        team = group.get("team") if isinstance(group.get("team"), dict) else {}
        team_abbr = str(team.get("abbreviation") or team.get("shortDisplayName") or "").strip()
        injuries = group.get("injuries") if isinstance(group.get("injuries"), list) else []
        for item in injuries:
            if not isinstance(item, dict):
                continue
            athlete_id, name = injury_identity(item)
            if not athlete_id and not name:
                continue
            record = {
                "athleteId": athlete_id,
                "name": name,
                "status": injury_status(item),
                "type": injury_type(item),
                "team": team_abbr,
                "date": str(item.get("date") or item.get("updated") or "").strip() or None,
            }
            if athlete_id:
                by_id[athlete_id] = record
            if name:
                by_name[_norm(name)] = record
    return by_id, by_name


def apply_injuries(
    records: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    *,
    verified_at: str,
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    active = 0
    for raw in records:
        record = dict(raw)
        if (
            str(record.get("leagueOrMedium") or "").strip().upper() != "NFL"
            or str(record.get("sourceNamespace") or "").strip().lower() != "espn"
        ):
            output.append(record)
            continue

        source_id = str(record.get("sourceRecordId") or "").strip()
        match = by_id.get(source_id)
        if match is None:
            match = by_name.get(_norm(record.get("name")))

        record["nflInjuryEvidenceVersion"] = INJURY_EVIDENCE_VERSION
        record["nflInjuryVerifiedAt"] = verified_at
        record["nflInjurySource"] = NFL_INJURIES_URL
        if match is None:
            record["nflInjuryActive"] = False
            record["nflInjuryStatus"] = "Not listed"
            record.pop("nflInjuryType", None)
            record.pop("nflInjuryReportDate", None)
        else:
            active += 1
            record["nflInjuryActive"] = True
            record["nflInjuryStatus"] = str(match.get("status") or "Injury report")
            if match.get("type"):
                record["nflInjuryType"] = match["type"]
            else:
                record.pop("nflInjuryType", None)
            if match.get("date"):
                record["nflInjuryReportDate"] = match["date"]
            else:
                record.pop("nflInjuryReportDate", None)
        output.append(record)
    return output, active


def fetch_report(timeout: float) -> dict[str, Any]:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    response = session.get(NFL_INJURIES_URL, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("NFL injury endpoint did not return a JSON object")
    return payload


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = sorted({
        key for record in records for key, value in record.items()
        if not isinstance(value, (dict, list))
    })
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/current_catalog.json"))
    parser.add_argument("--request-timeout", type=float, default=12.0)
    args = parser.parse_args()

    if not args.catalog.exists():
        raise SystemExit(f"{args.catalog} does not exist")
    records = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise SystemExit(f"{args.catalog} must contain a JSON array")

    try:
        payload = fetch_report(args.request_timeout)
        by_id, by_name = parse_injury_report(payload)
    except Exception as exc:  # noqa: BLE001
        # A transient injury-feed outage must not erase the last verified status
        # or block all Sports pricing. Preserve the prior state and try next run.
        print(f"WARNING: NFL injury sync unavailable ({type(exc).__name__}: {exc}); prior injury state preserved.")
        return 0

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updated, active = apply_injuries(
        [dict(item) for item in records if isinstance(item, dict)],
        by_id,
        by_name,
        verified_at=stamp,
    )
    args.catalog.write_text(json.dumps(updated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.catalog.name == "current_catalog.json":
        _write_csv(args.catalog.with_suffix(".csv"), updated)
    print(f"Synced NFL injury report: {active:,} listed player(s); all non-NFL records unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
