"""
Synthetic interaction-data generator for the extract stage.

Simulates a population of viewers with latent genre preferences and generates
watch/rating events from them. A fixed RNG seed keeps runs reproducible.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from data_engineering.catalog_seed import CATALOG

SEED = 42

PLANS = ["Basic", "Standard", "Premium"]
DEVICES = ["TV", "Mobile", "Web", "Tablet"]
COUNTRIES = ["USA", "UK", "India", "Brazil", "Germany", "Japan", "South Korea",
             "Mexico", "France", "Canada", "Australia", "Spain"]

FIRST_NAMES = ["Alex", "Maria", "Liam", "Sofia", "Noah", "Emma", "Arjun", "Yuki",
               "Carlos", "Ingrid", "Chen", "Fatima", "Diego", "Hana", "Omar",
               "Priya", "Lucas", "Nina", "Ravi", "Elena", "Tom", "Aisha"]
LAST_NAMES = ["Smith", "Garcia", "Kim", "Patel", "Müller", "Silva", "Tanaka",
              "Johnson", "Rossi", "Nguyen", "Lopez", "Khan", "Brown", "Wang"]

PROFILE_NAMES = ["Me", "Family", "Kids", "Weekend", "Partner", "Roomie"]

# demo profiles the running app uses (is_synthetic=0), seeded with light history
DEMO_PROFILES = [
    {"name": "Aman", "is_kids": 0, "age_group": "adult",
     "favs": ["Sci-Fi", "Thriller", "Crime"]},
    {"name": "Movie Night", "is_kids": 0, "age_group": "adult",
     "favs": ["Action", "Comedy", "Adventure"]},
    {"name": "Chill", "is_kids": 0, "age_group": "teen",
     "favs": ["Drama", "Romance", "Mystery"]},
    {"name": "Kids", "is_kids": 1, "age_group": "kids",
     "favs": ["Animation", "Family", "Adventure"]},
]


def _build_genre_universe(catalog):
    genres = sorted({g for t in catalog for g in t["genres"]})
    return genres


def generate_population(n_users: int = 280):
    """Create synthetic users + profiles, each with a latent genre-affinity vector."""
    rng = random.Random(SEED)
    genres = _build_genre_universe(CATALOG)

    users = []
    profiles = []
    profile_affinities = {}  # profile_id -> {genre: weight}
    uid = 1
    pid = 1

    for _ in range(n_users):
        fn = rng.choice(FIRST_NAMES)
        ln = rng.choice(LAST_NAMES)
        signup = date(2019, 1, 1) + timedelta(days=rng.randint(0, 365 * 5))
        users.append({
            "user_id": uid,
            "full_name": f"{fn} {ln}",
            "email": f"{fn.lower()}.{ln.lower()}{uid}@example.com",
            "country": rng.choice(COUNTRIES),
            "plan": rng.choices(PLANS, weights=[3, 5, 4])[0],
            "signup_date": signup.isoformat(),
            "is_synthetic": 1,
        })

        # Each user has 1-3 profiles.
        for p in range(rng.randint(1, 3)):
            # Give each profile a few "favourite" genres with high weight.
            favs = rng.sample(genres, k=rng.randint(2, 4))
            aff = {g: rng.uniform(0.05, 0.2) for g in genres}
            for g in favs:
                aff[g] = rng.uniform(0.6, 1.0)
            is_kids = 1 if (p > 0 and rng.random() < 0.25) else 0
            age_group = "kids" if is_kids else rng.choice(["teen", "adult", "adult"])
            profiles.append({
                "profile_id": pid,
                "user_id": uid,
                "profile_name": rng.choice(PROFILE_NAMES) if p else "Me",
                "is_kids": is_kids,
                "avatar_hue": rng.randint(0, 360),
                "age_group": age_group,
            })
            profile_affinities[pid] = (aff, is_kids)
            pid += 1
        uid += 1

    # interactive demo account (real, not synthetic)
    demo_uid = uid
    users.append({
        "user_id": demo_uid,
        "full_name": "Demo Account",
        "email": "demo@netflixclone.local",
        "country": "USA",
        "plan": "Premium",
        "signup_date": "2024-01-01",
        "is_synthetic": 0,
    })
    for d in DEMO_PROFILES:
        aff = {g: rng.uniform(0.05, 0.15) for g in genres}
        for g in d["favs"]:
            aff[g] = rng.uniform(0.7, 1.0)
        profiles.append({
            "profile_id": pid,
            "user_id": demo_uid,
            "profile_name": d["name"],
            "is_kids": d["is_kids"],
            "avatar_hue": rng.randint(0, 360),
            "age_group": d["age_group"],
        })
        profile_affinities[pid] = (aff, d["is_kids"])
        pid += 1

    return users, profiles, profile_affinities


def generate_episodes(title_index):
    """One row per episode of every series, numbered within its season. The
    names are placeholders ("Episode 4") until TMDB data replaces them."""
    rng = random.Random(SEED + 2)
    episodes = []
    for idx, title in enumerate(CATALOG):
        for season, count in enumerate(title.get("seasons", []), start=1):
            for number in range(1, count + 1):
                stretch = 1.15 if number == count else rng.uniform(0.85, 1.1)  # finales run long
                episodes.append({
                    "episode_id": len(episodes) + 1,
                    "title_id": title_index[idx],
                    "season_number": season,
                    "episode_number": number,
                    "episode_title": f"Episode {number}",
                    "runtime_minutes": max(5, round(title["runtime"] * stretch)),
                    "synopsis": None,
                })
    return episodes


def _title_score_for_profile(title, affinity, is_kids):
    """How appealing is a title to a profile, given its genre affinities?"""
    if is_kids and title["maturity"] in ("TV-MA", "R"):
        return 0.0  # kids profiles don't watch mature content
    genre_pull = sum(affinity.get(g, 0.0) for g in title["genres"]) / max(len(title["genres"]), 1)
    quality_pull = (title["imdb"] - 5.0) / 5.0  # popular titles get watched more
    return max(genre_pull * 0.75 + quality_pull * 0.25, 0.0)


def generate_events(profiles, profile_affinities, title_index, episodes=()):
    """
    Generate watch + rating fact rows.

    `title_index` maps the catalog list position -> assigned title_id so the
    events reference the same surrogate keys the loader uses. A film session is
    one watch event; a series session is a binge of consecutive episodes, one
    event each, picking up where the profile left off.
    """
    rng = random.Random(SEED + 1)
    # episode details draw from their own generator, so the main sequence (and
    # with it every film event and rating) is the same as before episodes existed
    ep_rng = random.Random(SEED + 3)
    by_title = {}
    for ep in episodes:
        by_title.setdefault(ep["title_id"], []).append(ep)
    watch_events = []
    ratings = []
    rating_id = 1

    today = date(2024, 6, 1)

    for prof in profiles:
        pid = prof["profile_id"]
        affinity, is_kids = profile_affinities[pid]

        # Compute per-title appeal and turn it into sampling weights.
        scored = []
        for idx, title in enumerate(CATALOG):
            s = _title_score_for_profile(title, affinity, is_kids)
            if s > 0:
                scored.append((idx, s))
        if not scored:
            continue

        idxs = [i for i, _ in scored]
        weights = [w for _, w in scored]

        # How many distinct titles this profile engages with.
        n_titles = min(len(idxs), rng.randint(6, 28))
        chosen = _weighted_sample_without_replacement(rng, idxs, weights, n_titles)

        for idx in chosen:
            title = CATALOG[idx]
            title_id = title_index[idx]
            appeal = dict(scored)[idx]

            # Watch sessions: higher appeal -> higher completion & repeat views.
            n_sessions = 1 + (1 if rng.random() < appeal * 0.5 else 0)
            sessions = []
            for _ in range(n_sessions):
                completion = min(1.0, max(0.05, rng.gauss(0.55 + appeal * 0.4, 0.25)))
                n_episodes = 1 if title["type"] == "movie" else rng.randint(2, 10)
                ev_date = today - timedelta(days=rng.randint(0, 540))
                device = rng.choices(DEVICES, weights=[5, 4, 2, 2])[0]
                sessions.append((_ts(ev_date, rng), completion, n_episodes, device))

            if title["type"] == "movie":
                for ts, completion, _, device in sessions:
                    watch_events.append(_watch_event(
                        len(watch_events) + 1, pid, title_id, None,
                        int(title["runtime"] * 60 * completion), completion, device, ts))
            else:
                _binge(watch_events, pid, title_id, by_title[title_id], sessions, ep_rng)

            # Ratings: profiles rate ~45% of what they watch; rating tracks appeal.
            if rng.random() < 0.45:
                base = 3.0 + appeal * 2.0
                rating = int(round(min(5, max(1, rng.gauss(base, 0.7)))))
                r_date = today - timedelta(days=rng.randint(0, 540))
                ratings.append({
                    "rating_id": rating_id,
                    "profile_id": pid,
                    "title_id": title_id,
                    "rating": rating,
                    "rated_ts": _ts(r_date, rng),
                })
                rating_id += 1

    return watch_events, ratings


def _binge(watch_events, pid, title_id, eps, sessions, rng):
    """A profile's sessions with one series, oldest first. Each watches its
    episodes in order; stopping partway through one means the next session
    starts that episode again."""
    position = 0
    for ts, completion, n_episodes, device in sorted(sessions):
        start = datetime.fromisoformat(ts)
        for k in range(n_episodes):
            if position >= len(eps):
                return  # caught up with every episode that's out
            ep = eps[position]
            watched = completion if k == n_episodes - 1 else 1.0
            watch_events.append(_watch_event(
                len(watch_events) + 1, pid, title_id, ep["episode_id"],
                int(ep["runtime_minutes"] * 60 * watched), watched, device,
                start.isoformat(timespec="seconds")))
            start += timedelta(minutes=ep["runtime_minutes"] * watched + rng.randint(0, 3))
            if watched >= 0.9:
                position += 1


def _watch_event(event_id, pid, title_id, episode_id, seconds, completion, device, ts):
    return {
        "event_id": event_id, "profile_id": pid, "title_id": title_id, "episode_id": episode_id,
        "watch_seconds": seconds, "completion": round(completion, 3),
        "completed": 1 if completion >= 0.9 else 0, "device": device, "event_ts": ts,
    }


def _weighted_sample_without_replacement(rng, items, weights, k):
    """Efraimidis-Spirakis weighted sampling without replacement."""
    keyed = []
    for item, w in zip(items, weights):
        if w <= 0:
            continue
        u = rng.random()
        key = u ** (1.0 / w)
        keyed.append((key, item))
    keyed.sort(reverse=True)
    return [item for _, item in keyed[:k]]


def _ts(d: date, rng) -> str:
    h = rng.randint(6, 23)
    m = rng.randint(0, 59)
    s = rng.randint(0, 59)
    return f"{d.isoformat()}T{h:02d}:{m:02d}:{s:02d}"
