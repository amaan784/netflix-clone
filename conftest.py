"""Pytest fixtures: ensure the warehouse exists and share a trained recommender."""
import pytest


@pytest.fixture(scope="session", autouse=True)
def ensure_db():
    from data_engineering.pipeline import ensure_warehouse
    ensure_warehouse()


@pytest.fixture(scope="session")
def svc():
    from ai.recommender import RecommenderService
    return RecommenderService().fit()
