# NetClone v9: Recommendation quality

Three changes to what users see:

- Top Picks is re-ranked with maximal marginal relevance, giving up a little
  relevance for variety so the row isn't all one genre (`recommender.py`).
- A Continue Watching row lists titles that were started but not finished,
  with a progress bar (`store.py`, `main.py`, frontend).
- A Top 10 row ranks what viewers in the profile's country watch most
  (`analytics.py`, `store.py`, `main.py`).

## Run

```powershell
pip install -r requirements.txt
python run.py
```
