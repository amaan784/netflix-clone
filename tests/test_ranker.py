"""The learned ranker: training, ranking the hybrid's shortlist, and its place in the evaluation."""
import numpy as np

from ai import evaluation, ranker
from ai.recommender import MATURE_RATINGS


def test_ranker_is_trained_with_readable_weights(svc):
    r = svc.ranker
    assert r.ready and r.trained_on["pairs"] > 1000
    assert len(r.weights) == len(r.features) == len(ranker.USED) and np.all(np.isfinite(r.weights))
    assert {w["feature"] for w in r.importance} == set(ranker.USED)


def test_rank_orders_unwatched_candidates(svc):
    pid = int(svc.frames["profiles"].query("is_synthetic == 0")["profile_id"].iloc[0])
    ranked = svc.ranker.rank(svc.signals, pid)
    scores = [s for _, s in ranked]
    assert len(ranked) == ranker.CANDIDATES and scores == sorted(scores, reverse=True)
    liked = {t for t, w in svc.watched[pid].items() if w > 0}
    assert not liked & {t for t, _ in ranked}


def test_rank_respects_the_kids_filter(svc):
    # the demo Kids profile (some synthetic ones have already seen every kid-safe title)
    kid = int(svc.frames["profiles"].query("is_kids == 1 and is_synthetic == 0")["profile_id"].iloc[0])
    safe = lambda t: svc.title_lookup[t]["maturity_rating"] not in MATURE_RATINGS
    ranked = svc.ranker.rank(svc.signals, kid, allow=safe)
    assert ranked and all(safe(t) for t, _ in ranked)


def test_candidate_features_line_up():
    import pandas as pd
    titles = pd.DataFrame({
        "title_id": [1, 2, 3], "title": ["A", "B", "C"], "content_type": ["movie", "series", "movie"],
        "genres": [["Drama"], ["Drama", "Comedy"], ["Horror"]], "imdb_score": [7.0, 8.0, 6.0],
        "release_year": [2020, 2021, 2019], "text_blob": ["drama", "drama comedy", "horror"],
        "keywords": [[], [], []], "cast_list": [[], [], []], "description": ["", "", ""],
        "director": ["", "", ""], "decade": ["2020s", "2020s", "2010s"]})
    events = pd.DataFrame({"profile_id": [9, 9, 8], "title_id": [1, 3, 2], "episode_id": [None, None, 5],
                           "completion": [1.0, 0.8, 1.0], "watch_seconds": [600, 500, 400],
                           "event_ts": ["2024-01-01", "2024-01-02", "2024-01-03"]})
    ratings = pd.DataFrame({"profile_id": pd.Series(dtype=int), "title_id": pd.Series(dtype=int),
                            "rating": pd.Series(dtype=float)})
    signals = ranker.Signals.fit(titles, events, ratings, kids=set(), als=False)
    pool, rows = signals.candidates(9)
    assert pool == [2] and rows.shape == (1, len(ranker.FEATURES))
    genre_match = rows[0][ranker.FEATURES.index("genre_match")]
    assert 0 < genre_match < 1  # half of profile 9's history is drama


def test_evaluation_compares_ranker_with_hybrid():
    res = evaluation.run_evaluation(ks=(5, 10), models=("popularity", "hybrid", "ranker"))
    ranker_row, hybrid_row = res["models"]["ranker"], res["models"]["hybrid"]
    assert all(0.0 <= v <= 1.0 for v in ranker_row.values())
    # it should at least clearly beat popularity, and land near the hybrid
    assert ranker_row["ndcg@10"] > res["models"]["popularity"]["ndcg@10"]
    assert abs(ranker_row["ndcg@10"] - hybrid_row["ndcg@10"]) < 0.03
    assert res["ranker_features"]
