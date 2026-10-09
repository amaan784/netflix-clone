-- Star schema for the Netflix clone (SQLite). Dimension tables plus watch/rating
-- fact tables.

PRAGMA foreign_keys = ON;

-- dimensions

CREATE TABLE IF NOT EXISTS dim_title (
    title_id        INTEGER PRIMARY KEY,
    title           TEXT    NOT NULL,
    content_type    TEXT    NOT NULL CHECK (content_type IN ('movie', 'series')),
    release_year    INTEGER NOT NULL,
    maturity_rating TEXT,
    runtime_minutes INTEGER,
    description     TEXT,
    director        TEXT,
    cast_list       TEXT,            -- JSON array
    keywords        TEXT,            -- JSON array
    country         TEXT,
    language        TEXT,
    imdb_score      REAL,
    added_date      TEXT,            -- ISO date the title was added to the catalog
    poster_hue      INTEGER,         -- colour for the generated poster
    decade          TEXT             -- e.g. "2010s"
);

CREATE TABLE IF NOT EXISTS dim_genre (
    genre_id   INTEGER PRIMARY KEY,
    genre_name TEXT NOT NULL UNIQUE
);

-- A series' episodes. Movies have none.
CREATE TABLE IF NOT EXISTS dim_episode (
    episode_id      INTEGER PRIMARY KEY,
    title_id        INTEGER NOT NULL REFERENCES dim_title(title_id),
    season_number   INTEGER NOT NULL,
    episode_number  INTEGER NOT NULL,
    episode_title   TEXT    NOT NULL,
    runtime_minutes INTEGER NOT NULL,
    synopsis        TEXT,             -- filled in from TMDB when its data has been fetched
    UNIQUE (title_id, season_number, episode_number)
);

-- Many-to-many bridge between titles and genres.
CREATE TABLE IF NOT EXISTS bridge_title_genre (
    title_id INTEGER NOT NULL REFERENCES dim_title(title_id),
    genre_id INTEGER NOT NULL REFERENCES dim_genre(genre_id),
    PRIMARY KEY (title_id, genre_id)
);

CREATE TABLE IF NOT EXISTS dim_user (
    user_id     INTEGER PRIMARY KEY,
    full_name   TEXT,
    email       TEXT,
    country     TEXT,
    plan        TEXT CHECK (plan IN ('Basic', 'Standard', 'Premium')),
    signup_date TEXT,
    is_synthetic INTEGER NOT NULL DEFAULT 1   -- 1 = generated history, 0 = real app user
);

CREATE TABLE IF NOT EXISTS dim_profile (
    profile_id   INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES dim_user(user_id),
    profile_name TEXT NOT NULL,
    is_kids      INTEGER NOT NULL DEFAULT 0,
    avatar_hue   INTEGER,
    age_group    TEXT      -- 'kids' | 'teen' | 'adult'
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_id  INTEGER PRIMARY KEY,   -- yyyymmdd
    cal_date TEXT NOT NULL,
    year     INTEGER,
    month    INTEGER,
    day      INTEGER,
    weekday  TEXT,
    is_weekend INTEGER
);

-- facts

CREATE TABLE IF NOT EXISTS fact_watch_event (
    event_id      INTEGER PRIMARY KEY,
    profile_id    INTEGER NOT NULL REFERENCES dim_profile(profile_id),
    title_id      INTEGER NOT NULL REFERENCES dim_title(title_id),
    episode_id    INTEGER REFERENCES dim_episode(episode_id),  -- NULL for movies
    date_id       INTEGER NOT NULL REFERENCES dim_date(date_id),
    watch_seconds INTEGER NOT NULL,        -- additive measure
    completion    REAL    NOT NULL,        -- fraction 0..1 of the movie or episode watched
    completed     INTEGER NOT NULL,        -- 1 if completion >= 0.9
    device        TEXT,                    -- TV | Mobile | Web | Tablet
    event_ts      TEXT NOT NULL,
    origin        TEXT NOT NULL DEFAULT 'generated'  -- 'generated' history, or 'app' (live, via the stream)
);

CREATE TABLE IF NOT EXISTS fact_rating (
    rating_id  INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES dim_profile(profile_id),
    title_id   INTEGER NOT NULL REFERENCES dim_title(title_id),
    date_id    INTEGER NOT NULL REFERENCES dim_date(date_id),
    rating     INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    rated_ts   TEXT NOT NULL,
    origin     TEXT NOT NULL DEFAULT 'generated'
);

-- Operational table used by the application (user's saved list).
CREATE TABLE IF NOT EXISTS my_list (
    profile_id INTEGER NOT NULL REFERENCES dim_profile(profile_id),
    title_id   INTEGER NOT NULL REFERENCES dim_title(title_id),
    added_ts   TEXT NOT NULL,
    PRIMARY KEY (profile_id, title_id)
);

-- indexes
CREATE INDEX IF NOT EXISTS ix_watch_profile ON fact_watch_event(profile_id);
CREATE INDEX IF NOT EXISTS ix_watch_title   ON fact_watch_event(title_id);
CREATE INDEX IF NOT EXISTS ix_watch_date    ON fact_watch_event(date_id);
CREATE INDEX IF NOT EXISTS ix_watch_episode ON fact_watch_event(episode_id);
CREATE INDEX IF NOT EXISTS ix_episode_title ON dim_episode(title_id);
CREATE INDEX IF NOT EXISTS ix_rating_profile ON fact_rating(profile_id);
CREATE INDEX IF NOT EXISTS ix_rating_title   ON fact_rating(title_id);
CREATE INDEX IF NOT EXISTS ix_btg_genre      ON bridge_title_genre(genre_id);
