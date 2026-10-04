MATURE = {"TV-MA", "R", "NC-17"}


def test_models_trained(svc):
    assert svc.ready
    assert svc.content.tfidf.shape[0] == len(svc.title_lookup)
    assert svc.collab.svd.n_components >= 2
    assert svc.collab.R.shape[1] == len(svc.title_lookup)


def test_personalization_differs(svc):
    demo = svc.frames["profiles"]
    demo = demo[demo["is_synthetic"] == 0]["profile_id"].tolist()
    lists = {pid: tuple(i["title"] for i in svc.recommend_for_profile(pid, 10)) for pid in demo}
    assert len({v for v in lists.values()}) > 1, "all profiles got identical recs"


def test_kids_profile_has_no_mature_titles(svc):
    kids = svc.frames["profiles"]
    kids = kids[kids["is_kids"] == 1]["profile_id"]
    if not len(kids):
        return
    pid = int(kids.iloc[0])
    recs = svc.recommend_for_profile(pid, 30)
    assert all(i["maturity_rating"] not in MATURE for i in recs)


def test_cold_start_falls_back(svc):
    recs = svc.recommend_for_profile(10**9, 5)
    assert len(recs) >= 1


def test_more_like_this(svc):
    tid = next(iter(svc.title_lookup))
    ml = svc.more_like_this(tid, 8)
    assert 1 <= len(ml) <= 8
    assert all(i["title_id"] != tid for i in ml)


def test_live_interaction_updates_state(svc):
    demo = int(svc.frames["profiles"].query("is_synthetic == 0")["profile_id"].iloc[0])
    tid = next(iter(svc.title_lookup))
    before = svc.watched.get(demo, {}).get(tid, 0.0)
    svc.add_interaction(demo, tid, completion=1.0, rating=5)
    assert svc.watched[demo][tid] > before
