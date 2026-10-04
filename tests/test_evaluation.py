from ai import evaluation


def test_evaluation_runs_and_metrics_in_range():
    res = evaluation.run_evaluation(
        ks=(5, 10), models=("popularity", "content", "itemcf", "svd", "hybrid"))
    assert res["config"]["n_profiles_evaluated"] > 0
    for model, row in res["models"].items():
        for metric, val in row.items():
            assert 0.0 <= val <= 1.0, f"{model}.{metric}={val} out of range"


def test_hybrid_beats_popularity_baseline():
    res = evaluation.run_evaluation(
        ks=(5, 10), models=("popularity", "content", "itemcf", "svd", "hybrid"))
    assert res["models"]["hybrid"]["ndcg@10"] >= res["models"]["popularity"]["ndcg@10"]


def test_grid_search_returns_best_weights():
    gs = evaluation.grid_search_hybrid(metric="ndcg@10", step=0.5)  # coarse = fast
    assert set(gs["best_weights"]) == {"content", "coview", "svd", "pop"}
    assert abs(sum(gs["best_weights"].values()) - 1.0) < 1e-6
