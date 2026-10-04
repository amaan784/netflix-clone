"""Pytest fixtures: ensure the warehouse exists and share a trained recommender."""
import sqlite3

import pytest

from paths import DB_PATH


@pytest.fixture(scope="session", autouse=True)
def ensure_db():
    need = True
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            need = conn.execute("SELECT COUNT(*) FROM dim_title").fetchone()[0] == 0
            conn.close()
        except sqlite3.Error:
            need = True
    if need:
        from data_engineering.pipeline import run_pipeline
        run_pipeline()


@pytest.fixture(scope="session")
def svc():
    from ai.recommender import RecommenderService
    return RecommenderService().fit()
