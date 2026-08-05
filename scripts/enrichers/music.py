"""Music category normalization adapter."""
class MusicEnricher:
    name = "music.generic"
    def supports(self, record):
        return str(record.get("primaryCategory") or "").lower() in {"music", "musician"}
    def enrich(self, record):
        result = dict(record)
        result["situationEvidence"] = {
            **(result.get("situationEvidence") if isinstance(result.get("situationEvidence"), dict) else {}),
            "labelOrPlatform": result.get("teamOrPlatform"), "genre": result.get("discipline"),
            "currentRelease": result.get("currentProject"), "touring": result.get("touring"),
        }
        return result
ENRICHER = MusicEnricher()
