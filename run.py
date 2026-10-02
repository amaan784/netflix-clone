#!/usr/bin/env python
"""
Launcher for the Netflix clone.

  python run.py            # build DB if needed, then start the web server
  python run.py --rebuild  # force-rebuild the data warehouse, then start
  python run.py --etl      # run the ETL pipeline only (no server)
  python run.py --kafka    # use the Kafka backend at localhost:9092
                           # (run `docker compose up -d` first)
"""
import os
import sys

from paths import DB_PATH


def main():
    args = set(sys.argv[1:])

    if "--kafka" in args and not os.environ.get("KAFKA_BOOTSTRAP"):
        os.environ["KAFKA_BOOTSTRAP"] = "localhost:9092"

    if "--rebuild" in args or "--etl" in args:
        from data_engineering.pipeline import run_pipeline
        run_pipeline()
        if "--etl" in args:
            print("\nETL complete. Database at:", DB_PATH)
            return

    import uvicorn
    print("=" * 60)
    print(" NetClone is starting...  ->  http://127.0.0.1:8000")
    print("=" * 60)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
