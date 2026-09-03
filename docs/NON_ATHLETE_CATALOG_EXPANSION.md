# TalentX Non-Athlete Catalog Expansion v1

## What changed

TalentX now maintains 100 curated current listings in each non-athlete category:

- Music: 100
- Actors: 100
- Creators: 100

The athlete seed remains intact. The full generated current catalog is expected
to contain approximately 10,410 records after the 10,000-name live roster build.

## Why a curated roster is used

Team sports offer roster endpoints that can supply thousands of current names.
Music, acting, and creator careers do not have one equivalent public roster API.
The temporary solution is an explicit, reviewable roster stored in:

`data/non_athlete_roster.json`

The roster is broad-market and internationally diversified. Candidate selection
was informed by Spotify's 2025 music lists, IMDb's 2025 popular-star list, and
Forbes' 2025 creator coverage. Inclusion is not proof of a verified score.

## Pricing behavior

Each record includes an explicit `benchmarkRank`. The pricing model now reads
that field instead of silently depending on JSON array order. This protects
relative valuations when the seed is regenerated or sorted.

All non-athlete records remain labeled:

`Curated benchmark prior — profession evidence required`

The benchmark rank is temporary. It must be replaced by category evidence feeds
for streaming/catalog data, project and box-office data, or creator reach and
engagement data.

## Build order

The Pages workflow now runs:

1. `scripts/build_non_athlete_catalog.py`
2. `scripts/build_current_catalog.py`
3. athlete evidence enrichment
4. universal repricing
5. pricing and catalog validation
6. Pages deployment

## Adding or moving a name

Edit only `data/non_athlete_roster.json`.

- Add the profile metadata.
- Assign a unique benchmark rank in that category.
- Keep ranks consecutive from 1 through the category total.
- Run `python scripts/build_non_athlete_catalog.py`.
- Run the normal repricing and validation scripts.

The builder creates stable IDs and tickers, updates the taxonomy, rebuilds the
seed, and writes `data/non_athlete_manifest.json`.

## Validation safeguards

The validation suite now checks:

- at least 100 Music, Actor, and Creator records;
- unique benchmark ranks;
- benchmark fundamentals remain ordered by rank;
- explicit cross-category regression comparisons;
- all existing pricing-model checks and evidence caps.
