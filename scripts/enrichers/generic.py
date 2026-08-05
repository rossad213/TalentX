"""Fallback adapter for future TalentX categories."""
class GenericEnricher:
    name = "generic"
    def supports(self, record): return True
    def enrich(self, record): return dict(record)
ENRICHER = GenericEnricher()
