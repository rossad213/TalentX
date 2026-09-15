#!/usr/bin/env python3
"""NFL opportunity protection for TalentX game-event pricing.

This layer is intentionally narrow. It does not reprice established NFL players
whose source-backed per-game expectations are already credible. It only protects
against the pathological case where a reserve player's tiny historical workload
becomes the denominator for a much larger one-game opportunity, making ordinary
injury-replacement production look like a historic breakout.

The layer also preserves the most recent *played* prior season when a player has
a zero-game season between meaningful seasons. That lets a returning player keep
a sensible pre-absence performance prior instead of being treated like a player
with no NFL history.
"""
from __future__ import annotations

import copy
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import hourly_price_refresh as refresh
import hourly_price_refresh_nfl as nfl

OPPORTUNITY_MODEL_VERSION = "1.0-nfl-role-adjusted-opportunity-floor"

# Conservative replacement/full-game production scores on TalentX's existing
# NFL signal scale. These are denominator floors only; they are not price caps.
# Established players with a higher personal expectation are completely
# unaffected. When actual production is below the role floor, the effective
# expectation is limited to actual production so expanded opportunity by itself
# cannot manufacture either a giant gain or an artificial punishment.
ROLE_REPLACEMENT_BASELINES = {
    "QB": 5.0,
    "RB": 2.0,
    "REC": 2.5,
    "DEF": 8.0,
    "ST": 2.5,
    "OL": 2.0,
}

_original_expected_baseline_stats = nfl.nfl_expected_baseline_stats
_original_game_evidence = nfl._nfl_game_evidence
_original_migrate_latest = nfl.migrate_latest_nfl_expectations
_installed = False


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return default
    return parsed


def role_group(record: dict[str, Any]) -> str:
    role = str(record.get("role") or "").lower().strip()
    if role == "qb" or "quarterback" in role:
        return "QB"
    if role in {"rb", "fb"} or "running back" in role or "fullback" in role:
        return "RB"
    if role in {"wr", "te"} or "wide receiver" in role or "receiver" in role or "tight end" in role:
        return "REC"
    if any(token in role for token in ("kicker", "punter", "long snapper")):
        return "ST"
    if any(token in role for token in ("offensive line", "offensive tackle", "offensive guard")) or role in {"ot", "og", "c"}:
        return "OL"
    return "DEF"


def replacement_baseline_score(record: dict[str, Any]) -> float:
    return ROLE_REPLACEMENT_BASELINES.get(role_group(record), ROLE_REPLACEMENT_BASELINES["DEF"])


def _played_prior_history(item: dict[str, Any]) -> dict[str, Any]:
    """Drop zero-game prior seasons while retaining the current season.

    A 17-game injury absence should not erase the last source-backed season that
    actually describes the player's established role. This is deliberately based
    on observed games rather than trying to infer the reason for every absence.
    """
    history = item.get("nflSeasonStats") if isinstance(item.get("nflSeasonStats"), dict) else None
    if not history:
        return item

    normalized: dict[int, dict[str, Any]] = {}
    for raw_year, stats in history.items():
        if not isinstance(stats, dict):
            continue
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            continue
        normalized[year] = stats
    if len(normalized) < 2:
        return item

    current_year = max(normalized)
    filtered: dict[int, dict[str, Any]] = {}
    for year, stats in normalized.items():
        games = nfl._game_count(stats)
        if year == current_year or (games is not None and games > 0):
            filtered[year] = stats
    if filtered.keys() == normalized.keys():
        return item

    result = dict(item)
    result["nflSeasonStats"] = filtered
    result["opportunityPriorSkippedZeroGameSeasons"] = sorted(set(normalized) - set(filtered))
    return result


def expected_baseline_stats(record: dict[str, Any], item: dict[str, Any]) -> dict[str, float]:
    """Use the normal NFL expectation model after skipping zero-game gap seasons."""
    return _original_expected_baseline_stats(record, _played_prior_history(item))


def protect_game_evidence(
    record: dict[str, Any],
    item: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Protect tiny reserve baselines without changing normal established players."""
    evidence = dict(_original_game_evidence(record, _played_prior_history(item), event))
    if not evidence.get("comparable"):
        return evidence

    actual = _finite(evidence.get("actualPerformanceScore"), 0.0) or 0.0
    personal_expected = _finite(evidence.get("expectedPerformanceScore"), 0.0) or 0.0
    role_floor = replacement_baseline_score(record)

    # Surgical guard: if the existing personal expectation is already credible
    # for the role, leave every field and every price move untouched.
    if actual <= 0 or personal_expected <= 0 or personal_expected >= role_floor:
        evidence["opportunityFloorApplied"] = False
        evidence["opportunityModelVersion"] = OPPORTUNITY_MODEL_VERSION
        return evidence

    effective_expected = max(personal_expected, min(role_floor, actual))
    if effective_expected <= 0:
        return evidence

    production_delta = (actual / effective_expected - 1.0) * 100.0

    # A tiny historical workload also makes its efficiency comparison unstable.
    # When the floor activates, price the game from production relative to the
    # role-adjusted denominator and keep the old efficiency figure informational.
    evidence.update({
        "personalExpectedPerformanceScore": round(personal_expected, 3),
        "expectedPerformanceScore": round(effective_expected, 3),
        "replacementBaselineScore": round(role_floor, 3),
        "productionDeltaPct": round(production_delta, 2),
        "performanceDeltaPct": round(production_delta, 2),
        "opportunityFloorApplied": True,
        "opportunityModelVersion": OPPORTUNITY_MODEL_VERSION,
        "expectationSource": (
            f"{evidence.get('expectationSource') or 'NFL source-backed per-game history'} "
            "+ TalentX role-adjusted opportunity floor"
        ),
    })
    return evidence


def _parse_time(value: Any) -> datetime | None:
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


def _needs_opportunity_repair(record: dict[str, Any], *, now: datetime) -> bool:
    if str(record.get("leagueOrMedium") or "") != "NFL":
        return False
    selected = nfl._latest_nfl_game_event(record)
    if selected is None:
        return False
    _, event = selected
    event_time = _parse_time(event.get("startedAt"))
    if event_time is None:
        return False
    cutoff = now - timedelta(days=nfl.NFL_MIGRATION_LOOKBACK_DAYS)
    if event_time < cutoff:
        return False
    event_key = str(event.get("eventKey") or event.get("eventId") or "").strip()
    if not event_key or str(record.get("lastPriceEventId") or "") != event_key:
        return False

    expected = _finite(event.get("expectedPerformanceScore"))
    if expected is not None and expected > 0 and expected < replacement_baseline_score(record):
        return True

    # Some compacted events retain the excessive delta but not the detailed
    # expected score. Restrict that fallback to low-sample players so a genuine
    # huge game by an established star is not swept into this repair.
    delta = abs(_finite(record.get("lastGamePerformanceDeltaPct"), 0.0) or 0.0)
    games = max(0.0, _finite(record.get("professionalGames"), 0.0) or 0.0)
    return delta >= 400.0 and games < 80.0


def migrate_latest_opportunity_events(
    catalog_path: Path = Path("data/current_catalog.json"),
    *,
    timeout: float = 10.0,
    now: datetime | None = None,
) -> int:
    """Reprice only recent NFL events inflated by a tiny denominator.

    The existing migration is run on a temporary catalog containing only affected
    records, then those records are merged back. Normal NFL listings whose prices
    already look right are never passed through the migration.
    """
    if not catalog_path.exists():
        return 0
    current = now or datetime.now(timezone.utc)
    records = nfl.load_records(catalog_path)
    flagged = [copy.deepcopy(record) for record in records if _needs_opportunity_repair(record, now=current)]
    if not flagged:
        return 0

    # Force only flagged records through the existing one-time migration.
    for record in flagged:
        record["nflExpectationModelVersion"] = f"pre-{OPPORTUNITY_MODEL_VERSION}"

    with tempfile.TemporaryDirectory(prefix="talentx-nfl-opportunity-") as temp_dir:
        temp_path = Path(temp_dir) / "current_catalog.json"
        nfl.write_records(temp_path, flagged)
        changed = _original_migrate_latest(temp_path, timeout=timeout, now=current)
        if not changed:
            return 0
        migrated = nfl.load_records(temp_path)

    by_id = {str(record.get("id") or ""): record for record in migrated if record.get("id")}
    merged = 0
    for index, record in enumerate(records):
        record_id = str(record.get("id") or "")
        replacement = by_id.get(record_id)
        if replacement is None:
            continue
        replacement["opportunityModelVersion"] = OPPORTUNITY_MODEL_VERSION
        records[index] = replacement
        merged += 1

    if merged:
        nfl.write_records(catalog_path, records)
        print(f"Repaired {merged:,} NFL latest-game event(s) with role-adjusted opportunity protection.")
    return merged


def install_opportunity_protection() -> None:
    """Install the narrow NFL opportunity wrappers exactly once."""
    global _installed
    if _installed:
        return
    nfl.nfl_expected_baseline_stats = expected_baseline_stats
    nfl._nfl_game_evidence = protect_game_evidence
    nfl.migrate_latest_nfl_expectations = migrate_latest_opportunity_events
    _installed = True
