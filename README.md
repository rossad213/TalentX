# TalentX v3 — 10,000-Name Current-Roster Expansion

TalentX is a virtual market for following the career trajectories of athletes, music artists, actors, and creators.

This version adds a GitHub Actions data pipeline that builds **10,000 additional current-athlete profiles** from point-in-time team-roster endpoints before the site is deployed. It keeps the existing Current, Legacy, Under Review, retirement, and Rookie IPO systems.

## What the automated build does

- Reads the existing 200-person curated seed.
- Collects current athletes from team-roster endpoints for the NFL, NBA, WNBA, MLB, NHL, major soccer leagues, lower soccer divisions, and women's soccer leagues.
- Deduplicates people by source identity and normalized name/sport.
- Attaches sport, league, team, position, country when available, source URL, source record ID, last-verified timestamp, and confidence.
- Stops after **10,000 genuinely new roster-sourced names** have been collected.
- Fails instead of deploying a catalog that is below the requested minimum.
- Generates `data/current_catalog.json` and `data/current_catalog.csv`.
- Saves the generated JSON/CSV as a downloadable workflow artifact for 14 days.
- Deploys the generated site through GitHub Pages.
- Refreshes the roster snapshot daily.

## Important distinction

**Active status data** comes from point-in-time roster feeds during the GitHub Actions build. It can become outdated after the recorded timestamp and must be refreshed.

**TalentX market data remains simulated.** Prices, Career Scores, daily changes, demand premiums, momentum, charts, volume, portfolios, and trades are not real financial or career valuations.

## Publish this version

This package uses a custom GitHub Pages workflow. The repository's Pages source must be set to **GitHub Actions**, not `Deploy from a branch`.

1. Upload everything in this folder to the root of the TalentX repository, including the hidden `.github` folder.
2. In macOS Finder, press **Command + Shift + .** to show `.github` and `.nojekyll` before dragging files into GitHub.
3. In GitHub, open **Settings → Pages**.
4. Under **Build and deployment → Source**, choose **GitHub Actions**.
5. Open **Actions → Build current catalog and deploy TalentX**.
6. Run the workflow, or wait for the push-triggered run.
7. The workflow will only deploy after the 10,000-name catalog passes validation.

Detailed steps are in `GITHUB_UPDATE_INSTRUCTIONS.txt`.

## Local preview

The downloaded package includes the 200-person fallback catalog so it can be previewed without network access:

```bash
python3 -m http.server
```

Open `http://localhost:8000`.

The full 10,000-name expansion is generated on a network-enabled GitHub Actions runner.

## Repository layout

```text
TalentX/
├── .github/
│   └── workflows/
│       └── build-current-catalog-and-pages.yml
├── .nojekyll
├── index.html
├── requirements.txt
├── styles.css
├── app.js
├── data/
│   ├── current_seed.json
│   ├── current_catalog.json       # fallback locally; generated during deployment
│   ├── current_catalog.csv        # fallback locally; generated during deployment
│   ├── current_source_manifest.json
│   ├── legacy_catalog_v2.json
│   ├── taxonomy.json
│   └── catalog_manifest.json
├── scripts/
│   ├── build_current_catalog.py
│   ├── validate_current_catalog.py
│   ├── migrate_catalog.py
│   └── validate_catalog.py
├── docs/
│   ├── CURRENT_CATALOG_PIPELINE.md
│   ├── RETIREMENT_AND_STATUS_POLICY.md
│   ├── ROOKIE_IPO_POLICY.md
│   └── SCALING_ARCHITECTURE.md
└── database/
    └── schema.sql
```

## Career lifecycle rules

Retirement does not send a person's price to zero. TalentX changes an athlete from the Active Career Model to the Legacy Career Model after the retirement is verified.

Rookies can use the separate Rookie IPO model, with draft capital fading as professional evidence accumulates.

## Scaling

A 10,000-person generated JSON catalog is acceptable for this prototype. A catalog approaching hundreds of thousands or one million people should move to a real backend, server-side search, cursor pagination, source-history tables, and incremental ingestion rather than making every visitor download the entire database.
