"""Actor category normalization adapter."""
class ActorEnricher:
    name = "actor.generic"
    def supports(self, record):
        return str(record.get("primaryCategory") or "").lower() == "actor"
    def enrich(self, record):
        result = dict(record)
        result["situationEvidence"] = {
            **(result.get("situationEvidence") if isinstance(result.get("situationEvidence"), dict) else {}),
            "representationOrPlatform": result.get("teamOrPlatform"), "medium": result.get("leagueOrMedium"),
            "currentProject": result.get("currentProject"), "billing": result.get("billing"),
        }
        return result
ENRICHER = ActorEnricher()
