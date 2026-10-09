import sqlite3

from paths import DB_PATH
from data_engineering import data_quality


def test_all_quality_checks_pass():
    conn = sqlite3.connect(DB_PATH)
    try:
        report = data_quality.run_all_checks(conn)
    finally:
        conn.close()
    failed = [c["name"] for c in report["checks"] if not c["passed"]]
    assert report["all_passed"], f"failed checks: {failed}"
    assert report["total"] >= 20
