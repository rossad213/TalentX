# TalentX Scaling Architecture

## Why the one-file prototype must change

A browser can load a few thousand profiles from JSON for a prototype. It should not download hundreds of thousands or one million complete profiles.

At production scale:

- the browser requests only the current page of results;
- filtering and sorting happen on the server;
- search uses a dedicated index;
- profile details load on demand;
- price history is stored separately from identity records;
- current status is reverified on a schedule.

## Recommended architecture

### Front end

- React or Next.js
- Deployed to Vercel
- Responsive web application
- Server-rendered or cached public market pages
- Authenticated portfolio and watchlist pages

### Application API

- REST or GraphQL API
- Cursor pagination
- Rate limiting
- Role-based administrative tools
- Event audit logs
- Data corrections and takedown workflow

### Database

PostgreSQL is appropriate for canonical identity, taxonomy, status, and market data.

Core tables:

- `talent`
- `talent_alias`
- `taxonomy_node`
- `talent_taxonomy`
- `source_record`
- `talent_status_history`
- `career_metric_snapshot`
- `market_price`
- `market_event`
- `user_account`
- `portfolio_position`
- `trade_order`
- `watchlist_item`

### Search

Use PostgreSQL full-text search for the first production version. Add Typesense, Meilisearch, OpenSearch, or Elasticsearch if search volume and filtering complexity justify it.

### Data ingestion

Each source connector should:

1. fetch source records;
2. preserve the original source ID;
3. normalize category and status fields;
4. match against the canonical person;
5. flag uncertain identity matches;
6. write timestamped metric snapshots;
7. create price events only after validation;
8. retain an audit trail.

### Images

Do not store millions of images in Git. Store licensed images or permitted remote references in object storage with CDN delivery.

### GitHub

Keep in GitHub:

- source code;
- schemas;
- migrations;
- small test fixtures;
- taxonomy definitions;
- documentation.

Do not keep in GitHub:

- one-million-person production exports;
- user portfolios;
- trade history;
- API secrets;
- licensed bulk datasets;
- large image libraries.

## Growth stages

### Stage 1: Prototype

- 200 current-first seed profiles
- 5,000 historical profiles
- static JSON
- browser-only virtual trading

### Stage 2: MVP

- 1,000–10,000 verified current profiles
- authentication
- PostgreSQL
- server-side search and filters
- daily or hourly source refresh
- administrator review queue

### Stage 3: Expansion

- 100,000+ profiles
- multiple source providers
- automated identity resolution
- event moderation
- scalable search index
- cached price-history service

### Stage 4: Million-profile catalog

- distributed ingestion jobs
- partitioned metric and price tables
- queue-based event processing
- source licensing and compliance operations
- dedicated data-quality team
- regional legal and publicity-rights controls

## Current-market eligibility

A production record should enter the Current market only when it includes:

- canonical person ID;
- primary category;
- category-specific subcategory;
- current career status;
- approved status source;
- source record ID;
- verification timestamp;
- confidence score;
- scheduled next verification date.
