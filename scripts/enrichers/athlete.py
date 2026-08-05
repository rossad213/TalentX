"""Fallback athlete adapter for sports without a dedicated discipline adapter."""
from __future__ import annotations

class AthleteEnricher:
    name = "athlete.generic"
    def supports(self, record):
        return str(record.get("primaryCategory") or "").lower() == "athlete"
    def enrich(self, record):
        result = dict(record)
        result["situationEvidence"] = {
            **(result.get("situationEvidence") if isinstance(result.get("situationEvidence"), dict) else {}),
            "team": result.get("teamOrPlatform"), "league": result.get("leagueOrMedium"),
            "role": result.get("role"), "starter": result.get("starter"),
        }
        return result

ENRICHER = AthleteEnricher()
