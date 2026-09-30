#!/usr/bin/env python
"""Launcher: build the warehouse if needed, then demo the recommender."""
import sqlite3

from paths import DB_PATH


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


def main():
    ensure_db()
    from ai.recommender import RecommenderService

    svc = RecommenderService().fit()
    demo = svc.frames["profiles"].query("is_synthetic == 0")
    pid = int(demo.iloc[0]["profile_id"])
    name = demo.iloc[0]["profile_name"]

    print(f"\n=== Top picks for '{name}' ===")
    for i in svc.recommend_for_profile(pid, 8):
        print(f"  {i['title']:<28} score={i['score']:.3f}  {i['genres']}")

    bb = next(t for t in svc.title_lookup.values() if t["title"] == "Breaking Bad")
    print("\n=== More like Breaking Bad ===")
    print("  ", [t["title"] for t in svc.more_like_this(bb["title_id"], 6)])

    print("\n=== Search: 'scary supernatural small town' ===")
    print("  ", [t["title"] for t in svc.search("scary supernatural small town", 6)])


if __name__ == "__main__":
    main()
