"""
Recommendation service used by the backend.

Owns the trained models (content-based, collaborative, semantic search),
popularity stats, and per-profile interaction state. recommend_for_profile is a
hybrid ranker blending content (0.40), co-viewed (0.30), SVD (0.20) and
popularity (0.10), with cold-start fallback to trending and kids/maturity filtering.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ai import data_access
from ai.content_based import ContentBasedModel
from ai.collaborative import CollaborativeModel
from ai.semantic_search import SemanticSearch

MATURE_RATINGS = {"TV-MA", "R", "NC-17"}


def _minmax(d: dict) -> dict:
    if not d:
        return {}
    vals = np.array(list(d.values()), dtype=float)
    lo, hi = vals.min(), vals.max()
    if hi - lo < 1e-9:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


class RecommenderService:
    def __init__(self):
        self.frames = None
        self.titles = None
        self.title_lookup: dict[int, dict] = {}
        self.interactions = None
        self.content = ContentBasedModel()
        self.collab = CollaborativeModel()
        self.search_engine = SemanticSearch()
        self.popularity: dict[int, float] = {}
        self.watched: dict[int, dict[int, float]] = {}
        self.ready = False

    # training / loading
    def fit(self):
        self.frames = data_access.load_frames()
        self.titles = self.frames["titles"]
        self.title_lookup = {int(r.title_id): self._title_record(r)
                             for r in self.titles.itertuples()}

        self.interactions = data_access.build_interactions(
            self.frames["events"], self.frames["ratings"])

        title_ids = self.titles["title_id"].tolist()
        self.content.fit(self.titles)
        self.collab.fit(self.interactions, title_ids)
        self.search_engine.fit(self.titles)

        self._compute_popularity()
        self._build_watched_state()
        self.ready = True
        return self

    def refresh(self):
        """Reload from the database and retrain to pick up new events."""
        return self.fit()

    def _compute_popularity(self):
        ev = self.frames["events"]
        agg = ev.groupby("title_id").agg(
            views=("title_id", "size"),
            viewers=("profile_id", "nunique"),
            hours=("watch_seconds", lambda s: s.sum() / 3600.0),
            avg_completion=("completion", "mean")).reset_index()
        # popularity = blend of reach and engagement
        score = (np.log1p(agg["viewers"]) * 0.6 + np.log1p(agg["hours"]) * 0.4)
        self.popularity = dict(zip(agg["title_id"], score))
        self._pop_stats = agg.set_index("title_id").to_dict("index")

    def _build_watched_state(self):
        self.watched = {}
        for r in self.interactions.itertuples():
            self.watched.setdefault(int(r.profile_id), {})[int(r.title_id)] = float(r.affinity)

    # live interaction updates, no full retrain needed
    def add_interaction(self, profile_id: int, title_id: int,
                        completion: float = 0.0, rating: int | None = None):
        prof = self.watched.setdefault(int(profile_id), {})
        delta = float(min(max(completion, 0.0), 1.0))
        if rating is not None:
            delta += 0.6 * (rating - 3.0)
        prof[int(title_id)] = max(0.0, prof.get(int(title_id), 0.0) + delta)

    # serving helpers
    def _title_record(self, r) -> dict:
        return {
            "title_id": int(r.title_id),
            "title": r.title,
            "content_type": r.content_type,
            "release_year": int(r.release_year),
            "maturity_rating": r.maturity_rating,
            "runtime_minutes": int(r.runtime_minutes),
            "description": r.description,
            "director": r.director,
            "cast": r.cast_list,
            "keywords": r.keywords,
            "country": r.country,
            "language": r.language,
            "imdb_score": float(r.imdb_score),
            "genres": r.genres,
            "decade": r.decade,
            "poster_hue": int(r.poster_hue),
        }

    def get_title(self, title_id: int) -> dict | None:
        return self.title_lookup.get(int(title_id))

    def hydrate(self, scored, reason: str | None = None):
        """Turn [(title_id, score)] into serving dicts."""
        out = []
        for tid, score in scored:
            rec = self.title_lookup.get(int(tid))
            if rec:
                item = dict(rec)
                item["score"] = round(float(score), 4)
                if reason:
                    item["reason"] = reason
                out.append(item)
        return out

    def all_titles(self):
        return [dict(v) for v in self.title_lookup.values()]

    def genres_list(self):
        gs = sorted({g for v in self.title_lookup.values() for g in v["genres"]})
        return gs

    def _allowed_for_profile(self, profile_id: int):
        """Return a maturity filter predicate based on the profile's age group."""
        prof = self.frames["profiles"]
        row = prof[prof["profile_id"] == int(profile_id)]
        is_kids = bool(row["is_kids"].iloc[0]) if len(row) else False
        if is_kids:
            return lambda rec: rec["maturity_rating"] not in MATURE_RATINGS
        return lambda rec: True

    # rows / rails
    def trending(self, k=20, allow=None):
        scored = sorted(self.popularity.items(), key=lambda x: x[1], reverse=True)
        items = self.hydrate(scored)
        if allow:
            items = [i for i in items if allow(i)]
        return items[:k]

    def top_rated(self, k=20, allow=None):
        scored = sorted(((tid, rec["imdb_score"]) for tid, rec in self.title_lookup.items()),
                        key=lambda x: x[1], reverse=True)
        items = self.hydrate(scored)
        if allow:
            items = [i for i in items if allow(i)]
        return items[:k]

    def new_releases(self, k=20, allow=None):
        scored = sorted(((tid, rec["release_year"]) for tid, rec in self.title_lookup.items()),
                        key=lambda x: x[1], reverse=True)
        items = self.hydrate(scored)
        if allow:
            items = [i for i in items if allow(i)]
        return items[:k]

    def by_genre(self, genre: str, k=20, allow=None):
        cands = [(tid, rec["imdb_score"]) for tid, rec in self.title_lookup.items()
                 if genre in rec["genres"]]
        cands.sort(key=lambda x: (self.popularity.get(x[0], 0), x[1]), reverse=True)
        items = self.hydrate(cands)
        if allow:
            items = [i for i in items if allow(i)]
        return items[:k]

    def more_like_this(self, title_id: int, k=12):
        seed = self.title_lookup.get(int(title_id))
        c = dict(self.content.similar(title_id, k=k * 2))
        cf = dict(self.collab.similar_items(title_id, k=k * 2))
        c_n, cf_n = _minmax(c), _minmax(cf)
        keys = set(c_n) | set(cf_n)
        blended = {t: 0.6 * c_n.get(t, 0) + 0.4 * cf_n.get(t, 0) for t in keys}
        scored = sorted(blended.items(), key=lambda x: x[1], reverse=True)[:k]
        return self.hydrate(scored, reason=f"More like {seed['title']}" if seed else None)

    # hybrid personalised ranker
    def recommend_for_profile(self, profile_id: int, k=20, exclude_watched=True):
        profile_id = int(profile_id)
        allow = self._allowed_for_profile(profile_id)
        watched = self.watched.get(profile_id, {})
        liked = {t: w for t, w in watched.items() if w > 0}

        # Cold start: no history -> trending.
        if not liked:
            return self.trending(k=k, allow=allow)

        liked_ids = list(liked.keys())
        content_scores = self.content.profile_scores(liked_ids, weights=liked)
        svd_scores = self.collab.predict_scores(profile_id)
        coview_scores = self.collab.coviewed_scores(liked_ids)
        pop_scores = self.popularity

        c_n = _minmax(content_scores)
        s_n = _minmax(svd_scores)
        v_n = _minmax(coview_scores)
        p_n = _minmax(pop_scores)

        keys = set(self.title_lookup.keys())
        blended = {}
        for t in keys:
            blended[t] = (0.40 * c_n.get(t, 0.0)
                          + 0.30 * v_n.get(t, 0.0)
                          + 0.20 * s_n.get(t, 0.0)
                          + 0.10 * p_n.get(t, 0.0))

        if exclude_watched:
            for t in liked_ids:
                blended.pop(t, None)

        scored = sorted(blended.items(), key=lambda x: x[1], reverse=True)
        scored = [(t, s) for t, s in scored if allow(self.title_lookup[t])]
        scored = self._mmr_rerank(scored, k)
        return self.hydrate(scored, reason="Top pick for you")

    def _mmr_rerank(self, scored, k, lam=0.75):
        """Maximal Marginal Relevance: trade relevance against similarity to
        already-picked titles so the list isn't dominated by one genre."""
        sim = self.content.sim
        idx = self.content.idx_of
        cand = scored[:max(k * 3, 30)]
        if sim is None or not cand:
            return scored[:k]
        vals = [s for _, s in cand]
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1.0
        rel = {t: (s - lo) / rng for t, s in cand}
        raw = dict(cand)
        pool = [t for t, _ in cand]
        picked, picked_idx = [], []
        while pool and len(picked) < k:
            best, best_val = None, -1e18
            for t in pool:
                ti = idx.get(t)
                div = max((sim[ti, j] for j in picked_idx), default=0.0) if ti is not None else 0.0
                val = lam * rel[t] - (1 - lam) * div
                if val > best_val:
                    best, best_val = t, val
            picked.append(best)
            bi = idx.get(best)
            if bi is not None:
                picked_idx.append(bi)
            pool.remove(best)
        return [(t, raw[t]) for t in picked]

    def because_you_watched(self, profile_id: int, max_rows=3, k=12):
        """Build 'Because you watched X' rails from the profile's top titles."""
        profile_id = int(profile_id)
        watched = self.watched.get(profile_id, {})
        if not watched:
            return []
        top_seeds = sorted(watched.items(), key=lambda x: x[1], reverse=True)[:max_rows]
        allow = self._allowed_for_profile(profile_id)
        rows = []
        for seed_id, _ in top_seeds:
            seed = self.title_lookup.get(seed_id)
            if not seed:
                continue
            items = [i for i in self.more_like_this(seed_id, k=k * 2)
                     if allow(i) and i["title_id"] not in watched][:k]
            if items:
                rows.append({"seed": seed["title"], "seed_id": seed_id, "items": items})
        return rows

    def search(self, query: str, k=24):
        scored = self.search_engine.search(query, k=k)
        return self.hydrate(scored)

    # home page assembly
    def home_rows(self, profile_id: int):
        allow = self._allowed_for_profile(profile_id)
        rows = [
            {"title": "Top Picks for You", "kind": "personalized",
             "items": self.recommend_for_profile(profile_id, k=18)},
            {"title": "Trending Now", "kind": "trending",
             "items": self.trending(k=18, allow=allow)},
            {"title": "Critically Acclaimed", "kind": "top_rated",
             "items": self.top_rated(k=18, allow=allow)},
            {"title": "New Releases", "kind": "new",
             "items": self.new_releases(k=18, allow=allow)},
        ]
        for byw in self.because_you_watched(profile_id, max_rows=2):
            rows.append({"title": f"Because you watched {byw['seed']}",
                         "kind": "because", "items": byw["items"]})
        # a couple of genre rails based on the profile's top genres
        for g in self._top_genres_for(profile_id, n=3):
            rows.append({"title": f"{g}", "kind": "genre",
                         "items": self.by_genre(g, k=18, allow=allow)})
        return [r for r in rows if r["items"]]

    def _top_genres_for(self, profile_id: int, n=3):
        watched = self.watched.get(int(profile_id), {})
        if not watched:
            return self.genres_list()[:n]
        counts = {}
        for tid, w in watched.items():
            rec = self.title_lookup.get(tid)
            if not rec:
                continue
            for g in rec["genres"]:
                counts[g] = counts.get(g, 0) + w
        return [g for g, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]]

    def title_stats(self, title_id: int):
        st = getattr(self, "_pop_stats", {}).get(int(title_id))
        if not st:
            return {"views": 0, "viewers": 0, "hours": 0.0, "avg_completion": 0.0}
        return {"views": int(st["views"]), "viewers": int(st["viewers"]),
                "hours": round(float(st["hours"]), 1),
                "avg_completion": round(float(st["avg_completion"]) * 100, 1)}


# Module-level singleton used by the backend.
_service: RecommenderService | None = None


def get_service() -> RecommenderService:
    global _service
    if _service is None:
        _service = RecommenderService().fit()
    return _service
