"""Shared filesystem paths."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "netflix.db"
SCHEMA_PATH = ROOT / "data_engineering" / "schema.sql"
FRONTEND_DIR = ROOT / "frontend"
LOG_DIR = ROOT / "data" / "logs"
STREAM_DIR = ROOT / "data" / "streaming"
TMDB_DIR = ROOT / "data" / "tmdb"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
STREAM_DIR.mkdir(parents=True, exist_ok=True)
