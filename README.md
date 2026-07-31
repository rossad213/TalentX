# TalentX v2.1 — Current-First Market + Rookie IPO

TalentX is a virtual market for following the career trajectories of athletes, music artists, actors, and creators.

This version changes the catalog from a historical 5,000-person snapshot into a lifecycle-aware structure and adds an explainable Rookie IPO pricing system:

- **Current market:** 200 current-first prototype seed profiles across sports and entertainment
- **Legacy market:** 2,636 historical profiles
- **Under Review:** 2,315 profiles that are excluded from the Current market until status is verified
- **Total included:** 5,151 unique profiles
- **Rookie IPO:** draft-position and position-aware opening-price calculator with draft-weight decay

## Run the preview

GitHub Pages:

1. Upload the contents of this folder to the root of the TalentX repository.
2. Keep `index.html` and `.nojekyll` at the repository root.
3. In GitHub, open **Settings → Pages**.
4. Publish from `main` and `/ (root)`.

Local preview:

```bash
python3 -m http.server
```

Then open `http://localhost:8000`.

Do not double-click `index.html`; browsers may block the JSON files when loaded from `file://`.

## Important data notice

The 200 current-seed names are real people selected for product prototyping, but the preview is **not connected to live rosters, rankings, music charts, project feeds, or creator-platform APIs**. Each Current listing is visibly labeled as requiring a live production source.

The historical catalog is never presented as verified current data. It is routed to either:

- `Legacy`
- `Under Review`

All prices, career scores, daily changes, charts, volumes, portfolios, and transactions are simulated.

## Main files

```text
TalentX/
├── .nojekyll
├── index.html
├── styles.css
├── app.js
├── data/
│   ├── current_seed.json
│   ├── legacy_catalog_v2.json
│   ├── taxonomy.json
│   └── catalog_manifest.json
├── database/
│   └── schema.sql
├── docs/
│   ├── RETIREMENT_AND_STATUS_POLICY.md
│   ├── ROOKIE_IPO_POLICY.md
│   └── SCALING_ARCHITECTURE.md
└── scripts/
    ├── migrate_catalog.py
    └── validate_catalog.py
```

## Retirement rule

Retirement does not send a person's price to zero. TalentX changes the asset from the **Active Career Model** to the **Legacy Career Model** after verifying the retirement event.

See `docs/RETIREMENT_AND_STATUS_POLICY.md`.

## Scaling rule

The static JSON structure is suitable for product prototyping only. A catalog with hundreds of thousands or millions of people must use a backend database, server-side search and filtering, cursor pagination, and scheduled data-ingestion jobs.

See `docs/SCALING_ARCHITECTURE.md`.


## Rookie IPO rule

Drafted athletes can be listed using a separate Rookie IPO model before enough professional performance exists. The calculator is available in **Data & Rules**. The production schema stores the immutable IPO inputs and a separate schedule that fades draft capital from 35% at listing to no more than 3% after year two.

See `docs/ROOKIE_IPO_POLICY.md`.
