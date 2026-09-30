# NetClone v2: Recommender models

Adds `ai/`, the models that turn the warehouse into recommendations. They
train on the interaction data v1 writes, which takes a second or two.

## New in this stage

- `data_access.py` - loads the warehouse into pandas and computes an affinity
  score per (profile, title) from watch completion and ratings.
- `content_based.py` - TF-IDF over title metadata, compared with cosine
  similarity.
- `collaborative.py` - truncated SVD plus item-item cosine similarity.
- `semantic_search.py` - TF-IDF search that also picks up decade, movie/series
  and genre words in the query.
- `recommender.py` - blends the signals (0.40 content, 0.30 co-viewed, 0.20
  SVD, 0.10 popularity), falls back to trending for profiles with no history,
  and keeps mature titles away from kids profiles.

## Run

```powershell
pip install -r requirements.txt
python run.py
```

Builds the warehouse if it isn't there yet, then prints top picks for a demo
profile, titles similar to Breaking Bad, and a search result.
