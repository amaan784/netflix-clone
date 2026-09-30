"""
Natural-language catalog search.

Ranks titles by TF-IDF cosine similarity to a free-text query, with a light
parser that pulls out decade/content-type/genre and applies them as soft boosts.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class SemanticSearch:
    def __init__(self):
        self.vectorizer: TfidfVectorizer | None = None
        self.matrix = None
        self.titles: pd.DataFrame | None = None

    def fit(self, titles: pd.DataFrame):
        self.titles = titles.reset_index(drop=True)
        # Include the title itself (boosted) so exact-name queries rank well.
        corpus = (self.titles["title"].str.lower() + " ") * 3 + self.titles["text_blob"]
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        self.matrix = self.vectorizer.fit_transform(corpus)
        return self

    def parse_intent(self, query: str) -> dict:
        q = query.lower()
        intent = {"decade": None, "content_type": None, "genres": [], "min_year": None}

        # decade: "90s", "1990s", "from the 2000s"
        m = re.search(r"\b((?:19|20)?\d0)s\b", q)
        if m:
            dec = m.group(1)
            if len(dec) == 2:
                dec = ("19" if int(dec) >= 30 else "20") + dec
            intent["decade"] = dec + "s"

        if any(w in q for w in ["movie", "film", "films", "movies"]):
            intent["content_type"] = "movie"
        if any(w in q for w in ["series", "show", "shows", "tv", "season"]):
            intent["content_type"] = "series"

        known_genres = set(g for gl in self.titles["genres"] for g in gl)
        for g in known_genres:
            if g.lower() in q:
                intent["genres"].append(g)
        # a few common synonyms
        syn = {"scary": "Horror", "funny": "Comedy", "romantic": "Romance",
               "spooky": "Horror", "kids": "Family", "animated": "Animation"}
        for word, g in syn.items():
            if word in q and g not in intent["genres"]:
                intent["genres"].append(g)
        return intent

    def search(self, query: str, k: int = 20, apply_filters: bool = True):
        if not query or not query.strip():
            return []
        qv = self.vectorizer.transform([query.lower()])
        scores = cosine_similarity(qv, self.matrix).ravel().astype(float)

        intent = self.parse_intent(query) if apply_filters else {
            "decade": None, "content_type": None, "genres": []}

        # Soft boosts for matched structured intent.
        for i, row in self.titles.iterrows():
            if intent["content_type"] and row["content_type"] == intent["content_type"]:
                scores[i] += 0.10
            if intent["decade"] and row["decade"] == intent["decade"]:
                scores[i] += 0.10
            if intent["genres"]:
                overlap = len(set(intent["genres"]) & set(row["genres"]))
                scores[i] += 0.08 * overlap

        order = np.argsort(scores)[::-1]
        out = []
        for i in order:
            if scores[i] <= 0:
                continue
            out.append((int(self.titles.iloc[i]["title_id"]), float(scores[i])))
            if len(out) >= k:
                break
        return out
