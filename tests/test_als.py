import pandas as pd

from ai.collaborative import ALSModel


def test_als_ranks_liked_cluster_above_other():
    # profiles 1 & 2 like titles 10, 11; profile 3 likes title 20
    rows = [(1, 10, 3.0), (1, 11, 2.0), (2, 10, 2.5), (2, 11, 3.0), (3, 20, 3.0)]
    df = pd.DataFrame(rows, columns=["profile_id", "title_id", "affinity"])
    model = ALSModel(n_factors=4, n_iters=25, seed=1).fit(df, [10, 11, 20])
    scores = model.predict_scores(2)
    assert scores[10] > scores[20]
    assert scores[11] > scores[20]


def test_als_unknown_profile_returns_empty():
    df = pd.DataFrame([(1, 10, 1.0)], columns=["profile_id", "title_id", "affinity"])
    model = ALSModel(n_factors=2, n_iters=5).fit(df, [10, 11])
    assert model.predict_scores(999) == {}
