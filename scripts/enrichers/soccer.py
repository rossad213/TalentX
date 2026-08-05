"""Soccer-specific normalization and career-stage classification."""
from __future__ import annotations
from typing import Any


def _number(value: Any) -> float:
    try: return max(0.0, float(value))
    except (TypeError, ValueError): return 0.0


def _stage(record: dict[str, Any]) -> str:
    age = _number(record.get("age"))
    years = _number(record.get("yearsActive") or record.get("experienceYears"))
    appearances = _number(record.get("professionalGames") or record.get("careerAppearances") or record.get("appearances"))
    debut_year = _number(record.get("professionalDebutYear") or record.get("debutYear"))
    if not years and debut_year:
        from datetime import datetime, timezone
        years = max(0, datetime.now(timezone.utc).year - debut_year)
    if years >= 15 or appearances >= 500 or age >= 35: return "Veteran"
    if years >= 9 or appearances >= 300: return "Established"
    if years >= 5 or appearances >= 140 or (age and 24 <= age <= 30): return "Prime"
    if years >= 2 or appearances >= 45 or (age and 20 <= age < 24): return "Emerging"
    if years > 0 or appearances > 0 or (age and age < 20): return "Early Career"
    return "Stage under review"


class SoccerEnricher:
    name = "athlete.soccer"
    def supports(self, record: dict[str, Any]) -> bool:
        return str(record.get("primaryCategory") or "").lower() == "athlete" and str(record.get("discipline") or "").lower() in {"soccer", "football"}
    def enrich(self, record: dict[str, Any]) -> dict[str, Any]:
        result = dict(record)
        result["careerStage"] = _stage(result)
        result["situationEvidence"] = {
            **(result.get("situationEvidence") if isinstance(result.get("situationEvidence"), dict) else {}),
            "club": result.get("teamOrPlatform"),
            "league": result.get("leagueOrMedium"),
            "position": result.get("role"),
            "starter": result.get("starter"),
        }
        result["stageEvidence"] = {
            "age": result.get("age"),
            "yearsActive": result.get("yearsActive", result.get("experienceYears")),
            "appearances": result.get("professionalGames", result.get("careerAppearances", result.get("appearances"))),
            "debutYear": result.get("professionalDebutYear", result.get("debutYear")),
        }
        return result

ENRICHER = SoccerEnricher()
