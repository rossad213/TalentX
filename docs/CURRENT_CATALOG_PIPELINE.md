# TalentX Current Catalog Pipeline

## Current production state

The deployed Vercel beta reads the committed 200-profile catalog in `data/current_catalog.json`. Those profiles are curated prototype records, not automated roster-verified records.

## Experimental objective

The separate workflow `.github/workflows/build-current-catalog-and-pages.yml` experiments with building a larger point-in-time catalog from roster and professional-source endpoints. Its target is 10,000 source-backed Current records, but that target is a pipeline acceptance condition—not the live catalog size.

## Inclusion rule for automated records

An automated record should enter a generated batch only when:

1. The person appears in an approved current source response.
2. The source does not explicitly mark the person inactive.
3. A usable source identity and person name are present.
4. The record is not a duplicate.
5. Required source metadata and schema checks pass.

Each automated record must include `lastVerifiedAt`, `sourceUrl`, `sourceRecordId`, `sourceNamespace`, and `dataConfidence`. Point-in-time verification is not a promise that the person's status remains current indefinitely.

## Current source families under test

- ESPN team-roster endpoints for supported leagues
- NHL roster endpoints from `api-web.nhle.com`
- Curated and public professional-source experiments for non-athlete categories

Source licensing, reliability, identity matching, and commercial permissions must be resolved before production use.

## Workflow behavior

The experimental workflow runs weekly, can be started manually, and may run after relevant pipeline inputs change. It:

- builds and enriches a candidate catalog;
- validates count, uniqueness, required fields, and source metadata;
- fails when its acceptance thresholds are not met; and
- uploads successful output as a temporary GitHub Actions artifact.

The workflow does **not** automatically replace the catalog served by the Vercel beta.

## Market fields

Identity/status sources do not supply TalentX prices. Market price, Career Score, fundamental value, movement, volume, and charts remain simulated and must never be described as source-reported facts.

## Publication rule

A generated catalog should be published only after its source coverage, licensing, identity matches, category assignments, status freshness, and application performance have been reviewed. Publication should be a deliberate release step.

## Next production step

Move canonical records into PostgreSQL with separate identity, source-observation, status-history, pricing-event, and market-snapshot tables. Serve search and profiles through paginated APIs instead of shipping a very large JSON file to every visitor.
