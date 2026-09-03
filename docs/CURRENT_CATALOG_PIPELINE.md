# TalentX Current Catalog Pipeline

## Objective

Build a point-in-time Current market containing the existing curated seed plus 10,000 additional active athletes, without filling the catalog with historical names or silently accepting an incomplete build.

## Inclusion rule

A new athlete enters this automated batch only when:

1. The athlete appears in a current team-roster response during the build.
2. The source does not explicitly mark the athlete inactive.
3. A usable source identity and person name are present.
4. The athlete is not a duplicate within the same sport.
5. The generated catalog passes minimum-count, source-metadata, unique-ID, unique-ticker, and schema checks.

This is point-in-time automated verification, not a promise that the person remains active indefinitely. Every generated record includes `lastVerifiedAt`, `sourceUrl`, `sourceRecordId`, and `dataConfidence`.

## Current source families

- ESPN current team-roster endpoints for the NFL, NBA, WNBA, MLB, and configured soccer leagues.
- NHL current roster endpoints from `api-web.nhle.com`.

The source manifest records which leagues returned data and which endpoints failed. A failed source does not cause historical names to be substituted. The build continues through other current sources, but deployment fails if the requested minimum cannot be met.

## Catalog output

- `data/current_catalog.json`: application data used by TalentX.
- `data/current_catalog.csv`: reviewable export containing identity, category, team, role, status, source, confidence, and simulated market fields.
- `data/current_source_manifest.json`: source-by-source build results and errors.
- `data/catalog_manifest.json`: total counts and category/league summaries.

## Market fields

The roster source establishes identity and point-in-time active status only. TalentX generates deterministic simulated values for:

- Market price
- Career Score
- Fundamental value
- Daily change
- Demand premium
- Momentum
- Trading volume
- Chart history
- Active and legacy model factors

These fields must never be described as source-reported facts.

## Refresh behavior

The GitHub workflow runs:

- On a push to `main`
- Manually through `workflow_dispatch`
- Daily on a scheduled cron

The generated catalog is deployed as a GitHub Pages artifact. It is not committed back into the repository, so the workflow does not require repository write permission.

## Failure behavior

The workflow refuses to deploy when:

- Fewer than 10,000 automated roster-sourced records are produced.
- IDs or tickers are duplicated.
- Required profile fields are missing.
- Current catalog records are not marked Active and Current.
- Automated records lack source URLs, source IDs, or verification timestamps.
- CSV, JSON, and manifest counts disagree.

## Next production step

At larger scale, source records should be stored in PostgreSQL with separate identity, status-history, source-observation, pricing-event, and market-snapshot tables. A search service should return paginated results instead of shipping a full catalog to every browser.
