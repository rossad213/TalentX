-- TalentX production-oriented PostgreSQL starter schema.
-- The static GitHub Pages prototype does not use this database directly.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE talent_primary_category AS ENUM ('Athlete','Music','Actor','Creator');
CREATE TYPE talent_market_segment AS ENUM ('Current','Legacy','Under Review');

CREATE TABLE talent (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    public_slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    ticker TEXT NOT NULL UNIQUE,
    primary_category talent_primary_category NOT NULL,
    discipline TEXT NOT NULL,
    league_or_medium TEXT,
    team_or_platform TEXT,
    role_name TEXT,
    country_code TEXT,
    career_status TEXT NOT NULL,
    career_stage TEXT NOT NULL DEFAULT 'Stage under review',
    market_segment talent_market_segment NOT NULL DEFAULT 'Under Review',
    status_confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
    last_verified_at TIMESTAMPTZ,
    next_verification_at TIMESTAMPTZ,
    is_tradeable BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX talent_market_filter_idx
    ON talent (market_segment, primary_category, discipline, career_status, career_stage);

CREATE INDEX talent_name_search_idx
    ON talent USING gin (to_tsvector('simple', display_name || ' ' || coalesce(ticker,'')));

CREATE TABLE talent_alias (
    id BIGSERIAL PRIMARY KEY,
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'alternate_name',
    UNIQUE (talent_id, alias)
);

CREATE TABLE source_record (
    id BIGSERIAL PRIMARY KEY,
    talent_id UUID REFERENCES talent(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    provider_record_id TEXT NOT NULL,
    source_url TEXT,
    source_payload JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_record_id)
);

CREATE TABLE talent_status_history (
    id BIGSERIAL PRIMARY KEY,
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    previous_segment talent_market_segment,
    new_segment talent_market_segment NOT NULL,
    source_record_id BIGINT REFERENCES source_record(id),
    confidence NUMERIC(5,4) NOT NULL,
    effective_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    review_note TEXT
);

CREATE TABLE career_metric_snapshot (
    id BIGSERIAL PRIMARY KEY,
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    normalized_value NUMERIC(7,3) NOT NULL CHECK (normalized_value >= 0 AND normalized_value <= 100),
    raw_value NUMERIC,
    unit TEXT,
    source_record_id BIGINT REFERENCES source_record(id),
    measured_at TIMESTAMPTZ NOT NULL,
    confidence NUMERIC(5,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX career_metric_talent_time_idx
    ON career_metric_snapshot (talent_id, measured_at DESC);


-- One immutable IPO record per drafted rookie listing. Later market prices live in market_price.
CREATE TABLE rookie_ipo (
    talent_id UUID PRIMARY KEY REFERENCES talent(id) ON DELETE CASCADE,
    draft_league TEXT NOT NULL,
    draft_year INTEGER NOT NULL CHECK (draft_year >= 1900),
    overall_pick INTEGER NOT NULL CHECK (overall_pick > 0),
    round_number INTEGER CHECK (round_number > 0),
    position_name TEXT NOT NULL,
    draft_capital_score NUMERIC(7,3) NOT NULL CHECK (draft_capital_score BETWEEN 0 AND 100),
    pre_pro_performance_score NUMERIC(7,3) NOT NULL CHECK (pre_pro_performance_score BETWEEN 0 AND 100),
    opportunity_score NUMERIC(7,3) NOT NULL CHECK (opportunity_score BETWEEN 0 AND 100),
    position_value_score NUMERIC(7,3) NOT NULL CHECK (position_value_score BETWEEN 0 AND 100),
    development_score NUMERIC(7,3) NOT NULL CHECK (development_score BETWEEN 0 AND 100),
    availability_score NUMERIC(7,3) NOT NULL CHECK (availability_score BETWEEN 0 AND 100),
    audience_score NUMERIC(7,3) NOT NULL CHECK (audience_score BETWEEN 0 AND 100),
    model_score NUMERIC(7,3) NOT NULL CHECK (model_score BETWEEN 0 AND 100),
    opening_price NUMERIC(14,4) NOT NULL CHECK (opening_price > 0),
    confidence_low NUMERIC(14,4),
    confidence_high NUMERIC(14,4),
    model_version TEXT NOT NULL DEFAULT 'rookie-ipo-v1',
    priced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_record_id BIGINT REFERENCES source_record(id),
    CHECK (confidence_low IS NULL OR confidence_low > 0),
    CHECK (confidence_high IS NULL OR confidence_high >= opening_price)
);

-- Stores the scheduled fade from draft reputation to observed professional performance.
CREATE TABLE rookie_weight_schedule (
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    effective_at TIMESTAMPTZ NOT NULL,
    draft_capital_weight NUMERIC(6,5) NOT NULL CHECK (draft_capital_weight BETWEEN 0 AND 1),
    professional_performance_weight NUMERIC(6,5) NOT NULL CHECK (professional_performance_weight BETWEEN 0 AND 1),
    reason TEXT NOT NULL,
    PRIMARY KEY (talent_id, effective_at)
);

CREATE TABLE market_event (
    id BIGSERIAL PRIMARY KEY,
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    headline TEXT NOT NULL,
    event_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id BIGINT REFERENCES source_record(id),
    confidence NUMERIC(5,4) NOT NULL,
    max_price_impact_pct NUMERIC(7,4),
    effective_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE market_price (
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    priced_at TIMESTAMPTZ NOT NULL,
    model_type TEXT NOT NULL,
    fundamental_value NUMERIC(14,4) NOT NULL,
    market_price NUMERIC(14,4) NOT NULL CHECK (market_price > 0),
    confidence_low NUMERIC(14,4),
    confidence_high NUMERIC(14,4),
    demand_premium_pct NUMERIC(9,4) NOT NULL DEFAULT 0,
    momentum_pct NUMERIC(9,4) NOT NULL DEFAULT 0,
    PRIMARY KEY (talent_id, priced_at)
);

CREATE INDEX market_price_latest_idx
    ON market_price (talent_id, priced_at DESC);

CREATE TABLE app_user (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT UNIQUE,
    virtual_cash NUMERIC(14,2) NOT NULL DEFAULT 25000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE portfolio_position (
    user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    shares NUMERIC(18,6) NOT NULL DEFAULT 0 CHECK (shares >= 0),
    average_cost NUMERIC(14,4) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, talent_id)
);

CREATE TABLE trade_order (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('buy','sell')),
    shares NUMERIC(18,6) NOT NULL CHECK (shares > 0),
    execution_price NUMERIC(14,4) NOT NULL CHECK (execution_price > 0),
    status TEXT NOT NULL DEFAULT 'filled',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE watchlist_item (
    user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    talent_id UUID NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, talent_id)
);
