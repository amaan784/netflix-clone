# NetClone v8: Offline evaluation

Adds a way to measure the recommender rather than eyeball it.
`ai/evaluation.py` holds out some positive interactions per profile, retrains
every model on the rest, and scores how well each one ranks the held-out
titles: Precision@K, Recall@K, NDCG@K, MAP@K, hit rate and catalog coverage.
Popularity, content-based, item-item, SVD, ALS and the hybrid all run on the
same split. A grid search over the blend weights is included.

This stage also adds an implicit-feedback ALS model (`ALSModel` in
`collaborative.py`) as a candidate. It scored slightly below SVD, so it's
benchmarked here but not used in the live blend.

## Run

```powershell
pip install -r requirements.txt
python -m ai.evaluation           # comparison table at K=10
python -m ai.evaluation --grid    # also search the blend weights
```

The running app serves the same numbers at `GET /api/eval`.
