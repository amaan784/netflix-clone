"""
Collaborative filtering over the profile x title affinity matrix.

Two signals: matrix factorization (TruncatedSVD) to predict affinity for unseen
titles, and item-item cosine similarity for "people who watched A also watched B".
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize


class CollaborativeModel:
    def __init__(self, n_factors: int = 24):
        self.n_factors = n_factors
        self.svd: TruncatedSVD | None = None
        self.user_factors = None        # profiles x k
        self.item_factors = None        # titles x k
        self.item_sim = None            # titles x titles (item-item cosine)
        self.profile_ids: list[int] = []
        self.title_ids: list[int] = []
        self.p_index: dict[int, int] = {}
        self.t_index: dict[int, int] = {}

    def fit(self, interactions: pd.DataFrame, title_ids: list[int]):
        self.profile_ids = sorted(interactions["profile_id"].unique().tolist())
        self.title_ids = list(title_ids)
        self.p_index = {p: i for i, p in enumerate(self.profile_ids)}
        self.t_index = {t: i for i, t in enumerate(self.title_ids)}

        rows = interactions["profile_id"].map(self.p_index).values
        cols = interactions["title_id"].map(self.t_index).values
        vals = interactions["affinity"].astype(float).values
        mask = ~np.isnan(cols.astype(float))
        R = sparse.csr_matrix(
            (vals[mask], (rows[mask], cols[mask].astype(int))),
            shape=(len(self.profile_ids), len(self.title_ids)))
        self.R = R

        # matrix factorization
        k = min(self.n_factors, max(2, min(R.shape) - 1))
        self.svd = TruncatedSVD(n_components=k, random_state=42)
        self.user_factors = self.svd.fit_transform(R)        # profiles x k
        self.item_factors = self.svd.components_.T           # titles x k

        # item-item similarity (cosine over normalised item columns)
        item_vectors = normalize(R.T.tocsr(), axis=1)        # titles x profiles
        self.item_sim = (item_vectors @ item_vectors.T)      # sparse titles x titles
        return self

    def predict_scores(self, profile_id: int) -> dict[int, float]:
        if profile_id not in self.p_index:
            return {}
        u = self.user_factors[self.p_index[profile_id]]
        preds = self.item_factors @ u                        # titles
        return {self.title_ids[j]: float(preds[j]) for j in range(len(self.title_ids))}

    # item-item: "people who watched these also watched..."
    def coviewed_scores(self, seed_title_ids: list[int]) -> dict[int, float]:
        idxs = [self.t_index[t] for t in seed_title_ids if t in self.t_index]
        if idxs is None or len(idxs) == 0 or self.item_sim is None:
            return {}
        agg = np.asarray(self.item_sim[idxs].sum(axis=0)).ravel()
        return {self.title_ids[j]: float(agg[j]) for j in range(len(self.title_ids))}

    def similar_items(self, title_id: int, k: int = 12):
        if title_id not in self.t_index or self.item_sim is None:
            return []
        i = self.t_index[title_id]
        row = np.asarray(self.item_sim[i].todense()).ravel()
        order = np.argsort(row)[::-1]
        out = []
        for j in order:
            tid = self.title_ids[j]
            if tid == title_id:
                continue
            out.append((tid, float(row[j])))
            if len(out) >= k:
                break
        return out

