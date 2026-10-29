-- Relocation Camper Watch schema. Run at every start (python -m rcw migrate); every statement is idempotent.
-- Data is never deleted (David 2026-10-29): listings that disappear become status 'gone' and move to History.

CREATE TABLE IF NOT EXISTS relocations (
    id              bigserial PRIMARY KEY,
    source          text NOT NULL,                 -- imoova | transfercar
    listing_id      text NOT NULL,                 -- the site's own id
    url             text NOT NULL,                 -- direct booking link
    vehicle_name    text,
    vehicle_type    text NOT NULL DEFAULT 'other', -- motorhome | campervan | other
    four_wd         boolean NOT NULL DEFAULT false,
    berths          int,                           -- NULL = not shown (passes the filter, shows as ****)
    origin          text,
    destination     text,
    pickup_from     date,
    pickup_to       date,
    days_included   int,
    extra_days      int,
    extra_day_price numeric(10, 2),
    inclusions      text,
    bond            numeric(10, 2),
    operator        text,
    description     text,
    matched         boolean NOT NULL DEFAULT false, -- passed every hard filter (re-checked each poll while open)
    boosted         boolean NOT NULL DEFAULT false, -- hit a keyword or inclusion boost
    fail_reasons    text,
    boost_reasons   text,
    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now(),
    matched_at      timestamptz,                   -- first time it passed the filters
    missed_polls    int NOT NULL DEFAULT 0,        -- consecutive successful polls it was missing from
    gone_at         timestamptz,
    status          text NOT NULL DEFAULT 'open',  -- open | gone
    digested_at     timestamptz,                   -- included in a daily digest
    alerted_at      timestamptz,                   -- instant alert sent (switched off while gathering data)
    dup_of          bigint REFERENCES relocations (id), -- same deal on the other site: listed under this row
    raw             jsonb NOT NULL DEFAULT '{}',
    UNIQUE (source, listing_id)
);
CREATE INDEX IF NOT EXISTS relocations_status_idx ON relocations (status, matched);
CREATE INDEX IF NOT EXISTS relocations_gone_idx ON relocations (gone_at);

-- 2026-10-29: km caps and trip stats (David: fuel allowance, time vs distance, daily hours, room to explore)
ALTER TABLE relocations ADD COLUMN IF NOT EXISTS km_allowed int;
ALTER TABLE relocations ADD COLUMN IF NOT EXISTS km_per_extra_day int;
ALTER TABLE relocations ADD COLUMN IF NOT EXISTS excess_km_rate numeric(6, 2);
ALTER TABLE relocations ADD COLUMN IF NOT EXISTS trip jsonb;   -- trips.stats(): pace, explore km, km cap, fuel

-- 2026-10-30: New Zealand. Prices are in the listing's own currency.
ALTER TABLE relocations ADD COLUMN IF NOT EXISTS region text NOT NULL DEFAULT 'AU';
ALTER TABLE relocations ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT 'AUD';

-- 2026-10-29: ways home (relocations into Hobart with the ferry included) get one instant Signal alert each
ALTER TABLE relocations ADD COLUMN IF NOT EXISTS home_alerted_at timestamptz;

-- 2026-10-29: route alerts (any start -> finish, saved from the Routes page) and the dashboard's on/off switches
CREATE TABLE IF NOT EXISTS watches (
    id            bigserial PRIMARY KEY,
    origin        text,                       -- NULL = anywhere
    destination   text,                       -- NULL = anywhere
    require_ferry boolean NOT NULL DEFAULT false,
    require_fuel  boolean NOT NULL DEFAULT false,
    matches_only  boolean NOT NULL DEFAULT false,   -- false = any vehicle
    alert         boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS watch_seen (
    watch_id       bigint NOT NULL REFERENCES watches (id) ON DELETE CASCADE,
    relocation_id  bigint NOT NULL REFERENCES relocations (id),
    seen_at        timestamptz NOT NULL,
    alerted        boolean NOT NULL,          -- false = baseline or the alert was switched off
    PRIMARY KEY (watch_id, relocation_id)
);
CREATE TABLE IF NOT EXISTS prefs (           -- switches set on the Alerts page; they override config.yaml
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Road distances from the routing service, kept for good (one request per route, ever).
CREATE TABLE IF NOT EXISTS road_routes (
    key          text PRIMARY KEY,        -- "lon,lat;lon,lat[;...]" rounded to 4 places
    drive_km     numeric,
    drive_hours  numeric,                 -- car time from the routing service
    ferry_km     numeric,
    ferry_hours  numeric,
    roads        text[],                  -- every road name / number on the route (unsealed check)
    error        text,                    -- the service found no route
    fetched_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS polls (
    id              bigserial PRIMARY KEY,
    source          text NOT NULL,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz NOT NULL DEFAULT now(),
    ok              boolean NOT NULL,
    skipped         text,
    listings_found  int,
    new_listings    int,
    new_matches     int,
    gone            int,
    alerts_sent     int,
    requests        int,
    error           text
);
CREATE INDEX IF NOT EXISTS polls_source_idx ON polls (source, started_at);

CREATE TABLE IF NOT EXISTS source_state (
    source               text PRIMARY KEY,
    last_success         timestamptz,
    last_failure         timestamptz,
    failing_since        timestamptz,     -- first failure since the last good poll
    consecutive_failures int NOT NULL DEFAULT 0,
    next_attempt_at      timestamptz,     -- backing off until then
    last_error           text,
    last_count           int,
    outage_alerted_at    timestamptz      -- the one Signal alert for this outage has gone
);

CREATE TABLE IF NOT EXISTS digests (
    id          bigserial PRIMARY KEY,
    for_date    date NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    dry_run     boolean NOT NULL,
    sent        boolean NOT NULL,
    new_matches int,
    starred     int,
    gone        int,
    text        text
);

CREATE TABLE IF NOT EXISTS messages (
    id          bigserial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    kind        text NOT NULL,             -- outage | instant
    dry_run     boolean NOT NULL,
    sent        boolean NOT NULL,
    text        text
);
