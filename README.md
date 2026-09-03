# TalentX beta

TalentX is a virtual market for following athletes, musicians, actors, and creators. All prices, scores, movements, charts, portfolios, and trades are simulated; no real money changes hands.

## Current live catalog

The deployed beta currently uses `data/current_catalog.json`, which contains **200 curated Current profiles**:

- 110 athletes
- 40 music profiles
- 30 actors
- 20 creators

The repository also contains 4,951 Legacy or Under Review reference profiles, for 5,151 records across all catalog files. Those historical/reference records are not presented as verified current listings.

## Catalog confidence and limitations

The 200 Current profiles are curated prototype records. They have complete required identity and market fields, but they are **not connected to automated live career-status feeds**:

- Curated Current profiles: 200
- Automated roster-verified Current profiles: 0
- Data-confidence value: 0.70 for every Current profile
- Missing required identity/market fields: 0
- Duplicate names, IDs, or tickers: 0
- JSON and CSV Current record counts: 200 each

Names, teams, roles, and career statuses can become outdated. Production use requires approved sources, timestamps, source IDs, correction workflows, and recurring verification.

## Experimental expansion pipeline

The workflow in `.github/workflows/build-current-catalog-and-pages.yml` is an experimental catalog builder. It targets a larger source-backed catalog, runs weekly or manually, validates its output, and uploads a temporary workflow artifact. Its generated records are not the catalog currently served by the Vercel beta unless they are separately reviewed and published.

## Application data flow

- `data/current_catalog.json`: 200-profile Current beta catalog used by the application
- `data/current_catalog.csv`: reviewable export matching the Current JSON catalog
- `data/catalog_manifest.json`: authoritative counts and QA status for the deployed catalog files
- `data/current_source_manifest.json`: source and verification status for the Current catalog
- `data/legacy_catalog_v2.json`: Legacy and Under Review reference profiles
- `data/taxonomy.json`: categories, disciplines, filters, and career-status vocabulary

## Career lifecycle rules

Retirement does not send a person's simulated price to zero. A verified retirement should move the profile from the Active Career Model to the Legacy Career Model.

Rookies can use the separate Rookie IPO model, with draft capital fading as professional evidence accumulates.

## Production direction

Before materially expanding the catalog, TalentX should move canonical identity, status history, source observations, pricing events, accounts, portfolios, and trades to a database-backed service with server-side search and pagination. See `docs/SCALING_ARCHITECTURE.md`.
