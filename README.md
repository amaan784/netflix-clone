# NetClone v1: Data warehouse

The foundation. An ETL pipeline builds a star schema in SQLite from a curated
catalog of 61 titles and a seeded population of synthetic viewers, then runs
13 data-quality checks on the result. Nothing else exists yet; every later
stage reads the warehouse this one builds.

## Run

```powershell
pip install -r requirements.txt
python run.py
```

Writes `data/netflix.db` and prints the ETL log and the check results.
`python -m data_engineering.pipeline` does the same thing.

## Files

```
paths.py                 shared paths
data_engineering/
  catalog_seed.py        the title catalog (source data)
  generate_data.py       synthetic viewers and their watch/rating events
  schema.sql             star schema: dim_*, fact_*, bridge table
  pipeline.py            extract -> transform -> load -> validate
  data_quality.py        the 13 checks
  analytics.py           KPI queries, used later by the dashboard
```

## Schema

- Dimensions: `dim_title`, `dim_genre`, `dim_user`, `dim_profile`, `dim_date`
- Bridge: `bridge_title_genre` (titles to genres, many-to-many)
- Facts: `fact_watch_event`, `fact_rating`

The generator is seeded, so every run produces the same warehouse.
