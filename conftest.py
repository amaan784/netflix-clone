"""Pytest fixtures: ensure the warehouse exists and share a trained recommender."""
import pytest


@pytest.fixture(scope="session", autouse=True)
def ensure_db():
    from data_engineering.pipeline import ensure_warehouse
    ensure_warehouse()


@pytest.fixture(scope="session")
def demo_pid():
    """The demo account's first profile."""
    import sqlite3
    from paths import DB_PATH
    from data_engineering.generate_data import DEMO_EMAIL
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("""SELECT p.profile_id FROM dim_profile p JOIN dim_user u ON u.user_id = p.user_id
                               WHERE u.email = ? ORDER BY p.profile_id""", (DEMO_EMAIL,)).fetchone()[0]
    finally:
        conn.close()


@pytest.fixture(scope="session")
def svc():
    from ai.recommender import RecommenderService
    return RecommenderService().fit()
