// Thin fetch wrapper around the backend JSON API.
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${r.status} ${path}`);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${r.status} ${path}`);
    return r.json();
  },

  profiles: () => API.get("/api/profiles"),
  home: (pid) => API.get(`/api/home?profile_id=${pid}`),
  title: (id, pid) => API.get(`/api/title/${id}?profile_id=${pid}`),
  genre: (name, pid) => API.get(`/api/genre/${encodeURIComponent(name)}?profile_id=${pid}`),
  search: (q, pid) => API.get(`/api/search?q=${encodeURIComponent(q)}&profile_id=${pid}`),
  mylist: (pid) => API.get(`/api/mylist?profile_id=${pid}`),
  analytics: () => API.get("/api/analytics"),
  assistant: (pid, query) => API.post("/api/assistant", { profile_id: pid, query }),
  watch: (b) => API.post("/api/watch", b),
  rate: (b) => API.post("/api/rate", b),
  listAdd: (pid, tid) => API.post("/api/mylist/add", { profile_id: pid, title_id: tid }),
  listRemove: (pid, tid) => API.post("/api/mylist/remove", { profile_id: pid, title_id: tid }),
  artwork: () => API.get("/api/artwork"),

  // changes whenever new TMDB art is downloaded, so browsers don't keep old images
  artVersion: "none",
  posterUrl: (id, w = 300, h = 450) => `/api/poster/${id}?w=${w}&h=${h}&v=${API.artVersion}`,
  backdropUrl: (id, w = 1280, h = 720) => `/api/backdrop/${id}?w=${w}&h=${h}&v=${API.artVersion}`,
  logoUrl: (id) => `/api/logo/${id}?v=${API.artVersion}`,
  stillUrl: (episodeId) => `/api/still/${episodeId}?v=${API.artVersion}`,
};
