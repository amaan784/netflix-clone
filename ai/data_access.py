"""Load the SQLite data into pandas frames and build the feature columns the models use."""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd

from paths import DB_PATH


def _connect():
    return sqlite3.connect(DB_PATH)


def load_frames() -> dict:
    """Load everything the recommender needs in one shot."""
    conn = _connect()
    try:
        titles = pd.read_sql_query("SELECT * FROM dim_title", conn)
        genres = pd.read_sql_query("""
            SELECT b.title_id, g.genre_name
            FROM bridge_title_genre b JOIN dim_genre g ON b.genre_id = g.genre_id
        """, conn)
        events = pd.read_sql_query("""
            SELECT profile_id, title_id, episode_id, watch_seconds, completion, completed, event_ts
            FROM fact_watch_event
        """, conn)
        ratings = pd.read_sql_query(
            "SELECT profile_id, title_id, rating FROM fact_rating", conn)
        profiles = pd.read_sql_query("""
            SELECT p.profile_id, p.profile_name, p.is_kids, p.age_group,
                   p.avatar_hue, u.is_synthetic, u.user_id
            FROM dim_profile p JOIN dim_user u ON p.user_id = u.user_id
        """, conn)
    finally:
        conn.close()

    # genre lists per title
    genre_map = genres.groupby("title_id")["genre_name"].apply(list).to_dict()
    titles["genres"] = titles["title_id"].map(lambda t: genre_map.get(t, []))
    titles["cast_list"] = titles["cast_list"].apply(_loads)
    titles["keywords"] = titles["keywords"].apply(_loads)

    # combined text blob for content-based TF-IDF
    titles["text_blob"] = titles.apply(_build_text_blob, axis=1)

    return {
        "titles": titles,
        "genres": genres,
        "events": events,
        "ratings": ratings,
        "profiles": profiles,
    }


# An episode is a fraction of a show: six watched to the end count like one
# film, so a long binge doesn't drown out the films a profile chose.
EPISODES_PER_FILM = 6


def film_units(events: pd.DataFrame) -> pd.Series:
    """How much of a film each event amounts to: its completion for a film,
    a sixth of that for an episode."""
    units = events["completion"]
    if "episode_id" in events:
        units = units.where(events["episode_id"].isna(), units / EPISODES_PER_FILM)
    return units


def popularity_scores(events: pd.DataFrame) -> pd.DataFrame:
    """Per-title popularity: reach (unique viewers) blended with views counted in
    film-sized units. Like Netflix's own "views" (hours / runtime), this stops
    long shows winning on raw watch hours alone."""
    agg = (events.assign(units=film_units(events)).groupby("title_id")
           .agg(viewers=("profile_id", "nunique"), views=("units", "sum")).reset_index())
    agg["score"] = np.log1p(agg["viewers"]) * 0.6 + np.log1p(agg["views"]) * 0.4
    return agg


def profile_is_kids(profile_id: int) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT is_kids FROM dim_profile WHERE profile_id = ?", (int(profile_id),)).fetchone()
    finally:
        conn.close()
    return bool(row and row[0])


def build_interactions(events: pd.DataFrame, ratings: pd.DataFrame) -> pd.DataFrame:
    """Collapse events and ratings into one affinity score per (profile_id, title_id)."""
    # watch signal: accumulate completion across sessions (rewatch => stronger)
    watch = (events.assign(units=film_units(events)).groupby(["profile_id", "title_id"])["units"]
             .sum().clip(upper=3.0).reset_index(name="watch_signal"))

    # rating signal: centre on 3 -> [-2, +2]
    rate = (ratings.groupby(["profile_id", "title_id"])["rating"]
            .mean().reset_index(name="avg_rating"))
    rate["rating_signal"] = rate["avg_rating"] - 3.0

    merged = pd.merge(watch, rate[["profile_id", "title_id", "rating_signal"]],
                      on=["profile_id", "title_id"], how="outer").fillna(0.0)
    merged["affinity"] = (merged["watch_signal"] + 0.6 * merged["rating_signal"]).clip(lower=0.0)
    return merged


def holdout_split(events, ratings, strategy="last_n", max_holdout=5, holdout_frac=0.2,
                  min_train_items=3, seed=42):
    """Hold out each profile's latest (or random) positives as the answers to
    predict. Returns the events and ratings with those (profile, title) pairs
    removed, so nothing about the answers leaks into training, plus the held-out
    titles and the remaining liked titles per profile."""
    inter = build_interactions(events, ratings)
    pos = inter[inter["affinity"] > 0][["profile_id", "title_id"]].copy()
    recency = events.groupby(["profile_id", "title_id"])["event_ts"].max() \
        .reset_index(name="ts")
    pos = pos.merge(recency, on=["profile_id", "title_id"], how="left")
    pos["ts"] = pos["ts"].fillna("1970-01-01")

    held, train_liked = {}, {}
    for pid, grp in pos.groupby("profile_id"):
        tids = grp["title_id"].tolist()
        n_pos = len(tids)
        n_hold = max(1, min(max_holdout, int(round(n_pos * holdout_frac))))
        if n_pos - n_hold < min_train_items:
            n_hold = n_pos - min_train_items
        if n_hold < 1:
            # too few positives to hold any out: keep all as train context, don't test
            train_liked[pid] = set(tids)
            continue
        if strategy == "last_n":
            ordered = grp.sort_values("ts")["title_id"].tolist()
            hold = set(ordered[-n_hold:])
        else:
            rng = np.random.default_rng(seed + int(pid))
            arr = np.array(tids)
            rng.shuffle(arr)
            hold = set(arr[:n_hold].tolist())
        held[pid] = hold
        train_liked[pid] = set(t for t in tids if t not in hold)

    held_pairs = {(pid, t) for pid, hs in held.items() for t in hs}

    def keep_train(df):
        flags = [(p, t) not in held_pairs for p, t in zip(df["profile_id"], df["title_id"])]
        return df[pd.Series(flags, index=df.index)]

    return keep_train(events), keep_train(ratings), held, train_liked


def _loads(v):
    try:
        return json.loads(v) if isinstance(v, str) else (v or [])
    except (json.JSONDecodeError, TypeError):
        return []


def _build_text_blob(row) -> str:
    """Repeat genres/keywords so they weigh more in the TF-IDF blob."""
    parts = []
    parts += row["genres"] * 3
    parts += row["keywords"] * 2
    parts.append(row["description"] or "")
    parts.append(row["director"] or "")
    parts += list(row["cast_list"])
    parts.append(row["decade"] or "")
    parts.append(row["content_type"] or "")
    return " ".join(str(p) for p in parts).lower()
