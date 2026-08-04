# TalentX Catalog Expansion v2.1

This patch expands the source-linked catalog by **9,400 identities** before the existing 10,000-name automated athlete roster build runs.

## Requested additions

- Music: 5,000
- Actors: 300
- Creators: 100
- Baseball: 500
- Tennis: 400
- Golf: 300
- Motorsport: 300
- Combat sports: 300
- Cricket: 200
- Soccer: 2,000

Total: **9,400**

## Source and pricing policy

The builder discovers candidate identities through English Wikipedia categories and checks Wikidata entity records to confirm a human or musical-group identity and exclude known deceased or dissolved entities. This proves identity and broad profession only. It does not prove current performance, achievements, audience size, team, league, or active status.

Every generated listing is therefore marked provisional and remains under the limited-evidence price cap until a dedicated evidence pipeline enriches it.

## Repeat-run safety

The builder removes records produced by earlier Wikipedia/Wikidata expansion runs before rebuilding. This prevents weekly GitHub Actions runs from repeatedly appending thousands of duplicate expansion cohorts.

## Soccer source roots

- Association football players by nationality
- Men's association football players by nationality
- Women's association football players by nationality

The stored TalentX discipline label is `Soccer` for clarity in the United States-facing interface.
