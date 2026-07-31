# Talent Exchange — 5,000-Person Prototype

A standalone, responsive virtual talent-market prototype. Open `index.html` directly in a modern browser; no server or installation is required.

## Catalog

- 1,500 athletes
- 2,000 actors
- 1,000 music figures
- 500 creators and creative personalities
- 5,000 globally unique normalized names

## Main features

- Full-catalog search and category filtering
- 50-row pagination for responsive rendering
- Individual profiles with source-snapshot fields
- Simulated prices, charts, career scores, volume, and daily changes
- Virtual buy/sell orders with limited local price impact
- Portfolio, watchlist, and transaction history
- Browser-local persistence with `localStorage`
- Data and pricing methodology page

## Important distinction

Names and selected biographical or credit fields come from the source snapshots identified on each record. Those fields may be historical or incomplete and are not guaranteed current. Every financial-style field—including price, percentage change, chart, score, volume, demand premium, and momentum—is simulated for product testing. No security, ownership interest, royalty, endorsement, or claim on a person's career is offered.

## Files

- `index.html`: complete standalone application with the catalog embedded
- `talent_catalog_5000.json`: complete machine-readable catalog
- `talent_catalog_5000.csv`: compact tabular catalog
- `build_catalog.py`: deterministic catalog generator
- `catalog_manifest.json`: counts and QA summary
- `SOURCES_AND_LIMITATIONS.md`: attribution and production limitations

## Rebuilding

The generator expects separately obtained source snapshots at the paths configured in `build_catalog.py`. Raw third-party source files are not bundled. Review each source's license and terms before rebuilding, distributing, or using the data commercially.
