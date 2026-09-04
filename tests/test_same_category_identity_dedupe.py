import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from same_category_identity_dedupe import dedupe_same_category_identities


def test_curated_music_record_wins_over_discovered_duplicate():
    records = [
        {
            "id": "cur-rosal-a",
            "name": "Rosalía",
            "primaryCategory": "Music",
            "sourceNamespace": "curated-non-athlete-roster",
            "dataConfidence": 0.95,
            "pricingConfidence": 0.9,
            "ticker": "ROIA",
        },
        {
            "id": "cur-rosalia-music-q123",
            "name": "Rosalia",
            "primaryCategory": "Music",
            "sourceNamespace": "wikidata-music-strict",
            "sourceRecordId": "Q123",
            "dataConfidence": 0.8,
            "pricingConfidence": 0.68,
            "ticker": "ROSA",
        },
    ]

    deduped, repairs = dedupe_same_category_identities(records)

    assert len(deduped) == 1
    assert deduped[0]["id"] == "cur-rosal-a"
    assert deduped[0]["name"] == "Rosalía"
    assert repairs[0]["suppressedId"] == "cur-rosalia-music-q123"


def test_same_name_in_different_categories_is_not_collapsed_here():
    records = [
        {"id": "a", "name": "Jordan Lee", "primaryCategory": "Actor"},
        {"id": "m", "name": "Jordan Lee", "primaryCategory": "Music"},
    ]

    deduped, repairs = dedupe_same_category_identities(records)

    assert len(deduped) == 2
    assert repairs == []


def test_unverified_same_name_homonyms_are_preserved():
    records = [
        {
            "id": "one",
            "name": "Jordan Lee",
            "primaryCategory": "Music",
            "sourceNamespace": "wikidata-non-athlete",
            "sourceRecordId": "Q111",
            "dataConfidence": 0.72,
        },
        {
            "id": "two",
            "name": "Jordan Lee",
            "primaryCategory": "Music",
            "sourceNamespace": "wikidata-music-strict",
            "sourceRecordId": "Q222",
            "dataConfidence": 0.88,
        },
    ]

    deduped, repairs = dedupe_same_category_identities(records)

    assert len(deduped) == 2
    assert repairs == []


def test_shared_source_identity_is_collapsed():
    records = [
        {
            "id": "one",
            "name": "Example Artist",
            "primaryCategory": "Music",
            "sourceNamespace": "wikidata-non-athlete",
            "sourceRecordId": "Q333",
            "dataConfidence": 0.72,
            "pricingConfidence": 0.60,
        },
        {
            "id": "two",
            "name": "Example Artist",
            "primaryCategory": "Music",
            "sourceNamespace": "wikidata-music-strict",
            "sourceRecordId": "Q333",
            "dataConfidence": 0.88,
            "pricingConfidence": 0.68,
        },
    ]

    deduped, repairs = dedupe_same_category_identities(records)

    assert len(deduped) == 1
    assert deduped[0]["id"] == "two"
    assert len(repairs) == 1
