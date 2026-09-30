"""
Natural-language assistant over the catalog.

Rule-based intent classification (recommend / similar / search / info / mood)
that routes to the recommender or search engine and composes a reply citing
real titles. No external LLM calls.
"""
from __future__ import annotations

import re

from ai.recommender import RecommenderService

MOOD_TO_GENRE = {
    "scary": "Horror", "spooky": "Horror", "creepy": "Horror",
    "funny": "Comedy", "laugh": "Comedy", "lighthearted": "Comedy",
    "romantic": "Romance", "romance": "Romance", "love": "Romance",
    "exciting": "Action", "action": "Action", "adrenaline": "Action",
    "mind-bending": "Sci-Fi", "thoughtful": "Drama", "sad": "Drama",
    "thrilling": "Thriller", "tense": "Thriller", "intense": "Thriller",
    "family": "Family", "kids": "Family", "magical": "Fantasy",
}

RECOMMEND_CUES = ["recommend", "suggest", "what should i watch", "what to watch",
                  "something to watch", "for me", "pick something", "surprise me"]
SIMILAR_CUES = ["like ", "similar to", "more like", "reminds me of"]
INFO_CUES = ["tell me about", "what is", "what's", "about the show", "about the movie",
             "plot of", "who is in", "cast of"]


class AIConcierge:
    def __init__(self, service: RecommenderService):
        self.svc = service

    def _find_title(self, query: str):
        """Best-effort match of a catalog title mentioned in the query."""
        q = query.lower()
        best, best_len = None, 0
        for rec in self.svc.title_lookup.values():
            name = rec["title"].lower()
            if name in q and len(name) > best_len:
                best, best_len = rec, len(name)
        if best:
            return best
        # fall back to the top semantic-search hit
        hits = self.svc.search(query, k=1)
        return hits[0] if hits else None

    def ask(self, profile_id: int, query: str) -> dict:
        q = (query or "").strip()
        if not q:
            return {"intent": "empty", "reply": "Ask me what you're in the mood for!",
                    "items": []}
        ql = q.lower()

        # "more like X"
        if any(c in ql for c in SIMILAR_CUES):
            title = self._find_title(q)
            if title:
                items = self.svc.more_like_this(title["title_id"], k=12)
                return {"intent": "similar", "anchor": title["title"],
                        "reply": f"If you liked **{title['title']}**, these have a similar feel.",
                        "items": items}

        # "tell me about X"
        if any(c in ql for c in INFO_CUES):
            title = self._find_title(q)
            if title:
                stats = self.svc.title_stats(title["title_id"])
                cast = ", ".join(title["cast"][:3])
                return {"intent": "info", "anchor": title["title"],
                        "reply": f"**{title['title']}** ({title['release_year']}, "
                                 f"{title['content_type']}, {title['maturity_rating']}): "
                                 f"{', '.join(title['genres'])}. {title['description']} "
                                 f"Starring {cast}. ⭐ {title['imdb_score']} · "
                                 f"{stats['viewers']} viewers here.",
                        "items": self.svc.more_like_this(title["title_id"], k=8)}

        # asked for recommendations
        if any(c in ql for c in RECOMMEND_CUES):
            # narrow by mood/genre if mentioned
            genre = self._detect_genre(ql)
            if genre:
                items = self.svc.by_genre(genre, k=12,
                                          allow=self.svc._allowed_for_profile(profile_id))
                reply = f"Some **{genre}** picks for you."
            else:
                items = self.svc.recommend_for_profile(profile_id, k=12)
                reply = "Picks based on your history and what similar viewers watch."
            return {"intent": "recommend", "reply": reply, "items": items}

        # short mood queries like "something funny"
        genre = self._detect_genre(ql)
        if genre and len(ql.split()) <= 6:
            items = self.svc.by_genre(genre, k=12,
                                      allow=self.svc._allowed_for_profile(profile_id))
            return {"intent": "mood", "reply": f"In the mood for **{genre}**? Try these.",
                    "items": items}

        # otherwise treat it as a search
        items = self.svc.search(q, k=18)
        if items:
            return {"intent": "search",
                    "reply": f"Here's what I found for '{q}'.",
                    "items": items}
        return {"intent": "search",
                "reply": f"Couldn't find anything for '{q}'. "
                         f"Try a genre, a mood, or a title you know.",
                "items": []}

    def _detect_genre(self, ql: str):
        for word, genre in MOOD_TO_GENRE.items():
            if re.search(rf"\b{re.escape(word)}\b", ql):
                return genre
        for g in self.svc.genres_list():
            if g.lower() in ql:
                return g
        return None
