#!/usr/bin/env python
"""Launcher: run the ETL pipeline to build the data warehouse."""
from data_engineering.pipeline import run_pipeline
from paths import DB_PATH

if __name__ == "__main__":
    run_pipeline()
    print("\nWarehouse built at:", DB_PATH)
