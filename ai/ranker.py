"""
Learning-to-rank: a trained model that re-orders the hybrid's top candidates.

The hybrid blends its signals with weights picked by hand (and checked with a
grid search). The ranker learns how to combine them, plus a few facts about
the profile and the title, from what profiles actually went on to watch. To
train it, each profile's latest titles are held out as the answers, the base
models are fitted on everything before them, and the ranker learns which of
the hybrid's candidates turned out to be the ones watched.

It's pairwise (the RankNet idea with a linear scorer): it sees pairs from the
same profile, one title that was watched and one that wasn't, and learns
weights that score the watched one higher. Gradient-boosted trees were tried
first; on this data they overfit and did clearly worse than the hybrid. The
pairwise model comes out level with the hybrid (within the evaluation's noise),
having learned its blend from data rather than having it picked by hand, and
its weights are easy to read: the Analytics page shows them.

Serving is two-stage: the hybrid shortlists CANDIDATES titles, the ranker
orders them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from ai import data_access
from ai.collaborative import ALSModel, CollaborativeModel
from ai.content_based import ContentBasedModel

CANDIDATES = 40
FEATURES = ["content", "coview", "svd", "als", "popularity", "hybrid", "hybrid_rank",
            "genre_match", "genre_lift", "taste_fit", "type_match", "imdb", "age", "is_series", "history",
            "kids_profile"]
WEIGHTS = {"content": 0.40, "coview": 0.30, "svd": 0.20, "pop": 0.10}
# The hybrid's own score and its rank are computed but not used: for a linear
# model they only restate the parts, and the duplicates confused the fit.
USED = [f for f in FEATURES if f not in ("hybrid", "hybrid_rank")]


def minmax(d: dict) -> dict:
    if not d:
        return {}
    vals = np.array(list(d.values()), dtype=float)
    lo, hi = vals.min(), vals.max()
    if hi - lo < 1e-9:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


class Signals:
    """Everything the features are made from, for one slice of history: the
    fitted base models, popularity, what each profile liked, and title facts."""

    def __init__(self, titles: pd.DataFrame, content, collab, als, popularity: dict,
                 liked: dict, kids: set, weights: dict | None = None):
        self.content, self.collab, self.als = content, collab, als
        self.popularity = minmax(popularity)
        self.liked = liked          # profile -> {title: affinity}; the service passes its live copy
        self.kids = kids
        self.weights = weights or WEIGHTS
        self.title_ids = titles["title_id"].tolist()
        self.genres = dict(zip(titles["title_id"], titles["genres"]))
        self.series = dict(zip(titles["title_id"], titles["content_type"] == "series"))
        self.imdb = dict(zip(titles["title_id"], titles["imdb_score"]))
        self.year = dict(zip(titles["title_id"], titles["release_year"]))
        self.newest = max(self.year.values())
        # how often each genre shows up in everyone's history, to compare a profile against
        counts = {}
        for prof in liked.values():
            for t, w in prof.items():
                for g in self.genres.get(t, []) if w > 0 else []:
                    counts[g] = counts.get(g, 0.0) + w
        total = sum(counts.values()) or 1.0
        self.genre_base = {g: c / total for g, c in counts.items()}
        # and how much of the catalog each genre is
        in_catalog = {}
        for gs in self.genres.values():
            for g in gs:
                in_catalog[g] = in_catalog.get(g, 0) + 1
        self.genre_catalog = {g: n / len(self.genres) for g, n in in_catalog.items()}

    @classmethod
    def fit(cls, titles, events, ratings, kids, als=True):
        """Fit fresh base models on the given events and ratings."""
        inter = data_access.build_interactions(events, ratings)
        title_ids = titles["title_id"].tolist()
        liked = {}
        for r in inter[inter["affinity"] > 0].itertuples():
            liked.setdefault(int(r.profile_id), {})[int(r.title_id)] = float(r.affinity)
        pop = data_access.popularity_scores(events)
        return cls(titles, ContentBasedModel().fit(titles), CollaborativeModel().fit(inter, title_ids),
                   ALSModel().fit(inter, title_ids) if als else None,
                   dict(zip(pop["title_id"], pop["score"])), liked, kids)

    def hybrid(self, pid: int) -> tuple[dict, dict]:
        """The hybrid's blended score per title, and the normalised parts it's made of."""
        liked = {t: w for t, w in self.liked.get(pid, {}).items() if w > 0}
        parts = {
            "content": minmax(self.content.profile_scores(list(liked), weights=liked)) if liked else {},
            "coview": minmax(self.collab.coviewed_scores(list(liked))),
            "svd": minmax(self.collab.predict_scores(pid)),
            "als": minmax(self.als.predict_scores(pid)) if self.als else {},
            "popularity": self.popularity,
        }
        w = self.weights
        blend = {t: w["content"] * parts["content"].get(t, 0) + w["coview"] * parts["coview"].get(t, 0)
                 + w["svd"] * parts["svd"].get(t, 0) + w["pop"] * parts["popularity"].get(t, 0)
                 for t in self.title_ids}
        return blend, parts

    def candidates(self, pid: int, allow=None, k: int = CANDIDATES):
        """The hybrid's top-k unwatched titles and their feature rows."""
        liked = {t: w for t, w in self.liked.get(pid, {}).items() if w > 0}
        blend, parts = self.hybrid(pid)
        pool = [t for t in sorted(blend, key=blend.get, reverse=True)
                if t not in liked and (allow is None or allow(t))][:k]

        # the profile's taste: affinity share per genre (and how that compares with
        # everyone else's), and films vs series
        total = sum(liked.values()) or 1.0
        genre_share, series_share = {}, 0.0
        for t, w in liked.items():
            for g in self.genres.get(t, []):
                genre_share[g] = genre_share.get(g, 0.0) + w / total
            series_share += w / total if self.series.get(t) else 0.0
        mass = sum(genre_share.values()) or 1.0

        rows = []
        for rank, t in enumerate(pool, start=1):
            gs = [genre_share.get(g, 0.0) for g in self.genres.get(t, [])]
            lift = [genre_share.get(g, 0.0) / mass / self.genre_base[g]
                    for g in self.genres.get(t, []) if self.genre_base.get(g)]
            fit = [genre_share.get(g, 0.0) / mass / self.genre_catalog[g] for g in self.genres.get(t, [])]
            rows.append([
                parts["content"].get(t, 0.0), parts["coview"].get(t, 0.0), parts["svd"].get(t, 0.0),
                parts["als"].get(t, 0.0), parts["popularity"].get(t, 0.0), blend[t], rank,
                float(np.mean(gs)) if gs else 0.0, float(np.mean(lift)) if lift else 0.0,
                float(np.mean(fit)) if fit else 0.0,
                series_share if self.series.get(t) else 1.0 - series_share,
                self.imdb.get(t, 0.0), self.newest - self.year.get(t, self.newest),
                float(self.series.get(t, False)), np.log1p(len(liked)), float(pid in self.kids),
            ])
        return pool, np.array(rows, dtype=float).reshape(-1, len(FEATURES))


def training_pairs(titles, events, ratings, kids, seed=42, negatives=10, rounds=1):
    """Hold out each profile's latest titles, fit the base models on the rest,
    and pair every held-out title that made the candidate list with up to
    `negatives` candidates that weren't watched. With rounds > 1 it steps back
    in time and does it again on what's left, for more (older) examples.
    Returns feature differences (watched minus not watched) and the number of
    profile examples used."""
    rng = np.random.default_rng(seed)
    diffs, examples = [], 0
    for _ in range(rounds):
        events, ratings, held, _ = data_access.holdout_split(
            events, ratings, "last_n", max_holdout=3, holdout_frac=0.2, min_train_items=3, seed=seed)
        if not held:
            break
        signals = Signals.fit(titles, events, ratings, kids)
        for pid, answers in held.items():
            pool, rows = signals.candidates(pid)
            pos = [i for i, t in enumerate(pool) if t in answers]
            neg = [i for i, t in enumerate(pool) if t not in answers]
            for p in pos:
                for n in rng.choice(neg, size=min(negatives, len(neg)), replace=False):
                    diffs.append(rows[p] - rows[n])
        examples += len(held)
    return np.array(diffs).reshape(-1, len(FEATURES)), examples


class Ranker:
    def __init__(self, seed: int = 42, C: float = 0.1, features=None, rounds: int = 1, negatives: int = 10):
        self.seed, self.C, self.rounds, self.negatives = seed, C, rounds, negatives
        self.features = list(features or USED)
        self.cols = [FEATURES.index(f) for f in self.features]
        self.weights = None       # one per feature, in raw feature units
        self.importance: list[dict] = []
        self.trained_on = {}
        self.ready = False

    def fit(self, titles, events, ratings, kids):
        diffs, n_profiles = training_pairs(titles, events, ratings, kids, self.seed,
                                           self.negatives, self.rounds)
        diffs = diffs[:, self.cols]
        if len(diffs) < 50:  # too little history to learn anything; the hybrid will do
            return self
        # each pair both ways round, so the classes balance and no intercept is needed
        X = np.vstack([diffs, -diffs])
        y = np.r_[np.ones(len(diffs)), np.zeros(len(diffs))]
        scale = X.std(axis=0)
        scale[scale == 0] = 1.0
        model = LogisticRegression(C=self.C, fit_intercept=False, max_iter=5000).fit(X / scale, y)
        self.weights = model.coef_[0] / scale
        self.ready = True
        # standardised weights: how much one standard deviation of a feature moves a title
        self.importance = sorted(({"feature": f, "weight": round(float(w), 3)}
                                  for f, w in zip(self.features, model.coef_[0])),
                                 key=lambda r: abs(r["weight"]), reverse=True)
        self.trained_on = {"profiles": n_profiles, "pairs": len(diffs)}
        return self

    def rank(self, signals: Signals, pid: int, allow=None, k: int = CANDIDATES) -> list[tuple[int, float]]:
        """[(title_id, score)] for the hybrid's candidates, best first. Scores are
        squashed to 0..1 (higher is better; they're for ordering, not probabilities)."""
        pool, rows = signals.candidates(pid, allow, k)
        if not pool:
            return []
        raw = rows[:, self.cols] @ self.weights
        scores = 1.0 / (1.0 + np.exp(-(raw - raw.mean())))
        return sorted(zip(pool, scores.tolist()), key=lambda x: x[1], reverse=True)
