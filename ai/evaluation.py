"""
Offline eval: hold out positives per profile, retrain everything on the rest,
then score how each model ranks the held-out items.

    python -m ai.evaluation [--grid]
"""
from __future__ import annotations

import argparse
import math
import sqlite3

import numpy as np
import pandas as pd

from paths import DB_PATH
from ai import data_access
from ai.content_based import ContentBasedModel
from ai.collaborative import CollaborativeModel, ALSModel

DEFAULT_KS = (5, 10, 20)
DEFAULT_MODELS = ("popularity", "content", "itemcf", "svd", "als", "hybrid")
DEFAULT_WEIGHTS = {"content": 0.40, "coview": 0.30, "svd": 0.20, "pop": 0.10}


# helpers
def _minmax(d: dict) -> dict:
    if not d:
        return {}
    vals = np.array(list(d.values()), dtype=float)
    lo, hi = vals.min(), vals.max()
    if hi - lo < 1e-9:
        return {k: 0.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


def _load_events_ratings():
    conn = sqlite3.connect(DB_PATH)
    try:
        events = pd.read_sql_query(
            "SELECT profile_id, title_id, watch_seconds, completion, completed, event_ts "
            "FROM fact_watch_event", conn)
        ratings = pd.read_sql_query(
            "SELECT profile_id, title_id, rating, rated_ts FROM fact_rating", conn)
    finally:
        conn.close()
    return events, ratings


def _train_popularity(train_events: pd.DataFrame) -> dict:
    if train_events.empty:
        return {}
    agg = train_events.groupby("title_id").agg(
        viewers=("profile_id", "nunique"),
        hours=("watch_seconds", lambda s: s.sum() / 3600.0)).reset_index()
    score = np.log1p(agg["viewers"]) * 0.6 + np.log1p(agg["hours"]) * 0.4
    return dict(zip(agg["title_id"], score))


# metrics (binary relevance)
def _precision(ranked, relevant, k):
    return len(set(ranked[:k]) & relevant) / k


def _recall(ranked, relevant, k):
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def _hit(ranked, relevant, k):
    return 1.0 if set(ranked[:k]) & relevant else 0.0


def _ndcg(ranked, relevant, k):
    dcg = sum(1.0 / math.log2(i + 2) for i, t in enumerate(ranked[:k]) if t in relevant)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / idcg if idcg > 0 else 0.0


def _ap(ranked, relevant, k):
    hits, score = 0, 0.0
    for i, t in enumerate(ranked[:k]):
        if t in relevant:
            hits += 1
            score += hits / (i + 1)
    denom = min(k, len(relevant))
    return score / denom if denom > 0 else 0.0


# split (leakage-free, at the raw-row level)
def _split(events, ratings, strategy, max_holdout, holdout_frac, min_train_items, seed):
    inter = data_access.build_interactions(events, ratings)
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


# fit train models -> per-profile score functions
def _build_scorers(train_events, train_ratings, titles, title_ids, train_liked, models, seed):
    train_inter = data_access.build_interactions(train_events, train_ratings)
    aff = {}
    for r in train_inter[train_inter["affinity"] > 0].itertuples():
        aff.setdefault(int(r.profile_id), {})[int(r.title_id)] = float(r.affinity)

    scorers = {}
    pop = _train_popularity(train_events)
    if "popularity" in models:
        scorers["popularity"] = lambda pid: dict(pop)
    if "content" in models or "hybrid" in models:
        cb = ContentBasedModel().fit(titles)
        scorers["content"] = lambda pid: cb.profile_scores(
            list(aff.get(pid, {})), weights=aff.get(pid, {})) if aff.get(pid) else {}
    if {"itemcf", "svd", "hybrid"} & set(models):
        cf = CollaborativeModel().fit(train_inter, title_ids)
        scorers["itemcf"] = lambda pid: cf.coviewed_scores(list(aff.get(pid, {})))
        scorers["svd"] = lambda pid: cf.predict_scores(pid)
    if "als" in models:
        als = ALSModel().fit(train_inter, title_ids)
        scorers["als"] = lambda pid: als.predict_scores(pid)
    return scorers, pop


def _rank(scores: dict, exclude: set, title_ids: list) -> list:
    if not scores:
        return [t for t in title_ids if t not in exclude]
    items = [(t, s) for t, s in scores.items() if t not in exclude]
    items.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in items]


def _hybrid_scores(pid, scorers, weights):
    c = _minmax(scorers["content"](pid)) if "content" in scorers else {}
    v = _minmax(scorers["itemcf"](pid)) if "itemcf" in scorers else {}
    s = _minmax(scorers["svd"](pid)) if "svd" in scorers else {}
    p = _minmax(scorers.get("popularity", lambda _: {})(pid))
    keys = set(c) | set(v) | set(s) | set(p)
    return {t: weights["content"] * c.get(t, 0) + weights["coview"] * v.get(t, 0)
            + weights["svd"] * s.get(t, 0) + weights["pop"] * p.get(t, 0) for t in keys}


# public API
def run_evaluation(*, ks=DEFAULT_KS, strategy="last_n", max_holdout=5, holdout_frac=0.2,
                   min_train_items=3, models=DEFAULT_MODELS, hybrid_weights=None,
                   include_kids=True, seed=42, frames=None) -> dict:
    frames = frames or data_access.load_frames()
    titles = frames["titles"]
    title_ids = titles["title_id"].tolist()
    weights = hybrid_weights or DEFAULT_WEIGHTS

    events, ratings = _load_events_ratings()
    train_events, train_ratings, held, train_liked = _split(
        events, ratings, strategy, max_holdout, holdout_frac, min_train_items, seed)

    if not include_kids:
        kids = set(frames["profiles"].loc[frames["profiles"]["is_kids"] == 1, "profile_id"])
        held = {p: h for p, h in held.items() if p not in kids}

    scorers, _ = _build_scorers(train_events, train_ratings, titles, title_ids,
                                train_liked, models, seed)

    results = {m: {f"{name}@{k}": [] for k in ks
                   for name in ("precision", "recall", "ndcg", "map", "hit_rate")}
               for m in models}
    coverage = {m: {k: set() for k in ks} for m in models}

    for pid, relevant in held.items():
        if not relevant:
            continue
        exclude = train_liked.get(pid, set())
        for m in models:
            scores = _hybrid_scores(pid, scorers, weights) if m == "hybrid" else scorers[m](pid)
            ranked = _rank(scores, exclude, title_ids)
            for k in ks:
                results[m][f"precision@{k}"].append(_precision(ranked, relevant, k))
                results[m][f"recall@{k}"].append(_recall(ranked, relevant, k))
                results[m][f"ndcg@{k}"].append(_ndcg(ranked, relevant, k))
                results[m][f"map@{k}"].append(_ap(ranked, relevant, k))
                results[m][f"hit_rate@{k}"].append(_hit(ranked, relevant, k))
                coverage[m][k].update(ranked[:k])

    n_eval = sum(1 for r in held.values() if r)
    models_out = {}
    for m in models:
        row = {metric: round(float(np.mean(vals)), 4) if vals else 0.0
               for metric, vals in results[m].items()}
        for k in ks:
            row[f"coverage@{k}"] = round(len(coverage[m][k]) / max(len(title_ids), 1), 4)
        models_out[m] = row

    primary = ks[1] if len(ks) > 1 else ks[0]
    best = max(models_out, key=lambda m: models_out[m].get(f"ndcg@{primary}", 0))
    return {
        "config": {"strategy": strategy, "ks": list(ks), "n_profiles_evaluated": n_eval,
                   "n_titles": len(title_ids), "include_kids": include_kids,
                   "hybrid_weights": weights},
        "models": models_out,
        "best_by_ndcg": {"k": primary, "model": best},
    }


def grid_search_hybrid(*, metric="ndcg@10", step=0.1, strategy="last_n",
                       include_kids=True, seed=42, frames=None) -> dict:
    """Sweep the hybrid weight simplex (summing to 1) and report the best combo."""
    frames = frames or data_access.load_frames()
    titles = frames["titles"]
    title_ids = titles["title_id"].tolist()
    k = int(metric.split("@")[1])

    events, ratings = _load_events_ratings()
    train_events, train_ratings, held, train_liked = _split(
        events, ratings, strategy, 5, 0.2, 3, seed)
    if not include_kids:
        kids = set(frames["profiles"].loc[frames["profiles"]["is_kids"] == 1, "profile_id"])
        held = {p: h for p, h in held.items() if p not in kids}
    scorers, _ = _build_scorers(train_events, train_ratings, titles, title_ids,
                                train_liked, DEFAULT_MODELS, seed)

    # precompute normalized component scores once per profile
    comp = {}
    for pid in held:
        comp[pid] = {
            "content": _minmax(scorers["content"](pid)),
            "coview": _minmax(scorers["itemcf"](pid)),
            "svd": _minmax(scorers["svd"](pid)),
            "pop": _minmax(scorers["popularity"](pid)),
        }

    steps = [round(i * step, 4) for i in range(int(1 / step) + 1)]
    leaderboard = []
    for wc in steps:
        for wv in steps:
            for ws in steps:
                wp = round(1.0 - wc - wv - ws, 4)
                if wp < -1e-9 or wp > 1 + 1e-9:
                    continue
                w = {"content": wc, "coview": wv, "svd": ws, "pop": wp}
                vals = []
                for pid, relevant in held.items():
                    if not relevant:
                        continue
                    c = comp[pid]
                    keys = set(c["content"]) | set(c["coview"]) | set(c["svd"]) | set(c["pop"])
                    blended = {t: wc * c["content"].get(t, 0) + wv * c["coview"].get(t, 0)
                               + ws * c["svd"].get(t, 0) + wp * c["pop"].get(t, 0) for t in keys}
                    ranked = _rank(blended, train_liked.get(pid, set()), title_ids)
                    vals.append(_ndcg(ranked, relevant, k))
                leaderboard.append((w, round(float(np.mean(vals)), 4) if vals else 0.0))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    return {"metric": metric, "best_weights": leaderboard[0][0],
            "best_score": leaderboard[0][1],
            "default_weights": DEFAULT_WEIGHTS,
            "leaderboard": [{"weights": w, metric: s} for w, s in leaderboard[:8]]}


def _print_table(res: dict):
    cfg = res["config"]
    k = res["best_by_ndcg"]["k"]
    cols = [f"precision@{k}", f"recall@{k}", f"ndcg@{k}", f"map@{k}",
            f"hit_rate@{k}", f"coverage@{k}"]
    print(f"\nOffline evaluation  (strategy={cfg['strategy']}, "
          f"profiles={cfg['n_profiles_evaluated']}, titles={cfg['n_titles']}, K={k})")
    print("-" * 96)
    print(f"{'model':<12}" + "".join(f"{c.split('@')[0]:>14}" for c in cols))
    print("-" * 96)
    ranked = sorted(res["models"].items(), key=lambda x: x[1][f"ndcg@{k}"], reverse=True)
    for name, row in ranked:
        print(f"{name:<12}" + "".join(f"{row[c]:>14.4f}" for c in cols))
    print("-" * 96)
    print(f"best by ndcg@{k}: {res['best_by_ndcg']['model']}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--strategy", choices=["last_n", "random"], default="last_n")
    ap.add_argument("--no-kids", action="store_true")
    ap.add_argument("--grid", action="store_true")
    args = ap.parse_args()

    # a fresh checkout has no database yet; the API builds it on startup, the CLI has to here
    from data_engineering.pipeline import ensure_warehouse
    ensure_warehouse()
    result = run_evaluation(ks=(5, args.k, 20), strategy=args.strategy,
                            include_kids=not args.no_kids)
    _print_table(result)
    if args.grid:
        gs = grid_search_hybrid(metric=f"ndcg@{args.k}", strategy=args.strategy,
                                include_kids=not args.no_kids)
        print("Grid search (hybrid weights):")
        print(f"  default {gs['default_weights']} ")
        print(f"  best    {gs['best_weights']}  {gs['metric']}={gs['best_score']}")
