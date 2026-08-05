"""Creator category normalization adapter."""
class CreatorEnricher:
    name = "creator.generic"
    def supports(self, record):
        return str(record.get("primaryCategory") or "").lower() == "creator"
    def enrich(self, record):
        result = dict(record)
        result["situationEvidence"] = {
            **(result.get("situationEvidence") if isinstance(result.get("situationEvidence"), dict) else {}),
            "platform": result.get("teamOrPlatform"), "niche": result.get("discipline"),
            "postingActive": result.get("postingActive"), "brandSafety": result.get("brandSafety"),
        }
        return result
ENRICHER = CreatorEnricher()
