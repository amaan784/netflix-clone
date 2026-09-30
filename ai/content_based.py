"""Content-based filtering: TF-IDF over each title's metadata blob, ranked by cosine similarity."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class ContentBasedModel:
    def __init__(self):
        self.vectorizer: TfidfVectorizer | None = None
        self.tfidf = None
        self.sim = None
        self.title_ids: list[int] = []
        self.idx_of: dict[int, int] = {}

    def fit(self, titles: pd.DataFrame):
        self.title_ids = titles["title_id"].tolist()
        self.idx_of = {tid: i for i, tid in enumerate(self.title_ids)}
        self.vectorizer = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2), min_df=1, max_features=5000)
        self.tfidf = self.vectorizer.fit_transform(titles["text_blob"])
        self.sim = cosine_similarity(self.tfidf, dense_output=True)
        return self

    def similar(self, title_id: int, k: int = 12, exclude: set[int] | None = None):
        """Return [(title_id, score)] most similar to the given title."""
        if title_id not in self.idx_of:
            return []
        i = self.idx_of[title_id]
        scores = self.sim[i]
        order = np.argsort(scores)[::-1]
        exclude = exclude or set()
        out = []
        for j in order:
            tid = self.title_ids[j]
            if tid == title_id or tid in exclude:
                continue
            out.append((tid, float(scores[j])))
            if len(out) >= k:
                break
        return out

    def profile_scores(self, liked_title_ids: list[int], weights=None) -> dict[int, float]:
        """Average the TF-IDF vectors of liked titles, then score every title against it."""
        idxs = [self.idx_of[t] for t in liked_title_ids if t in self.idx_of]
        if not idxs:
            return {}
        if weights is None:
            weights = np.ones(len(idxs))
        else:
            weights = np.asarray([weights[t] for t in liked_title_ids if t in self.idx_of])
        weights = weights / (weights.sum() or 1.0)

        profile_vec = self.tfidf[idxs].multiply(weights[:, None]).sum(axis=0)
        profile_vec = np.asarray(profile_vec)
        scores = cosine_similarity(profile_vec, self.tfidf).ravel()
        return {self.title_ids[j]: float(scores[j]) for j in range(len(self.title_ids))}
