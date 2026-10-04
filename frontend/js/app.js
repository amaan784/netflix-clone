// front-end application logic
const state = {
  profile: null,
  homeStale: false,
  player: { item: null, pct: 0, timer: null, paused: false },
};

const $ = (sel) => document.querySelector(sel);
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };

// boot
async function init() {
  const profiles = await API.profiles();
  const list = $("#profile-list");
  list.innerHTML = "";
  profiles.forEach((p) => {
    const tile = el(`
      <div class="profile-avatar ${p.is_kids ? "kids" : ""}">
        <div class="avatar-tile" style="background:hsl(${p.avatar_hue},55%,40%)">${p.name[0]}</div>
        <span>${p.name}</span>
      </div>`);
    tile.addEventListener("click", () => selectProfile(p));
    list.appendChild(tile);
  });
  wireGlobalUI();
}

function selectProfile(p) {
  state.profile = p;
  $("#profile-gate").classList.add("hidden");
  $("#app").classList.remove("hidden");
  const chip = $("#profile-chip");
  chip.textContent = p.name[0];
  chip.style.background = `hsl(${p.avatar_hue},55%,40%)`;
  showView("home");
  loadHome();
}

// views
function showView(name) {
  ["home", "search", "grid", "analytics"].forEach((v) =>
    $(`#${v}-view`).classList.toggle("hidden", v !== name));
  document.querySelectorAll(".nav-links a").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === name));
  window.scrollTo(0, 0);
}

async function loadHome() {
  const data = await API.home(state.profile.profile_id);
  const rows = data.rows;
  // hero is the first personalized pick, falling back to trending
  const heroSource = rows.find((r) => r.kind === "personalized") || rows[0];
  if (heroSource && heroSource.items.length) renderHero(heroSource.items[0]);

  const wrap = $("#rows");
  wrap.innerHTML = "";
  rows.forEach((row) => wrap.appendChild(renderRow(row)));
  state.homeStale = false;
}

function renderHero(item) {
  const match = matchPct(item);
  const hero = $("#hero");
  hero.innerHTML = `
    <div class="hero-bg"><img src="${API.posterUrl(item.title_id, 1200, 700)}" alt=""></div>
    <div class="hero-content">
      <div class="hero-tag">${item.content_type === "series" ? "SERIES" : "FILM"}</div>
      <h1 class="hero-title">${item.title}</h1>
      <div class="hero-meta">
        <span class="match">${match}% Match</span>
        <span>${item.release_year}</span>
        <span class="tag">${item.maturity_rating}</span>
        <span>${item.genres.slice(0, 3).join(" • ")}</span>
      </div>
      <p class="hero-desc">${item.description}</p>
      <div class="hero-btns">
        <button class="btn btn-play">▶ Play</button>
        <button class="btn btn-info">ⓘ More Info</button>
      </div>
    </div>`;
  hero.querySelector(".btn-play").addEventListener("click", () => openPlayer(item));
  hero.querySelector(".btn-info").addEventListener("click", () => openDetail(item.title_id));
}

function renderRow(row) {
  const r = el(`<section class="row"><h2 class="row-title">${row.title}</h2>
    <div class="row-track"></div></section>`);
  const track = r.querySelector(".row-track");
  const ranked = row.kind === "trending" || row.kind === "country";
  row.items.forEach((item, i) => track.appendChild(card(item, { rank: ranked ? i + 1 : null })));
  return r;
}

function card(item, opts = {}) {
  const c = el(`
    <div class="card">
      <img loading="lazy" src="${API.posterUrl(item.title_id)}" alt="${item.title}">
      ${opts.rank ? `<div class="card-rank">#${opts.rank}</div>` : ""}
      <div class="card-hover">
        <div class="ch-title">${item.title}</div>
        <div class="ch-meta">${matchPct(item)}% · ${item.release_year} · ${item.genres[0] || ""}</div>
      </div>
      ${typeof item.progress === "number" ? `<div class="card-progress"><div class="card-progress-fill" style="width:${item.progress}%"></div></div>` : ""}
    </div>`);
  c.addEventListener("click", () => openDetail(item.title_id));
  return c;
}

// detail modal
async function openDetail(titleId) {
  const data = await API.title(titleId, state.profile.profile_id);
  const inList = data.in_list;
  const card = $("#detail-card");
  card.innerHTML = `
    <div class="detail-hero">
      <div class="dh-bg"><img src="${API.posterUrl(data.title_id, 900, 420)}"></div>
      <button class="detail-close">✕</button>
      <div class="detail-hero-content">
        <h2>${data.title}</h2>
        <div class="detail-actions">
          <button class="btn btn-play">▶ Play</button>
          <button class="icon-btn js-list ${inList ? "active" : ""}" title="My List">${inList ? "✓" : "+"}</button>
          <button class="icon-btn js-up" title="I like this">👍</button>
          <button class="icon-btn js-love" title="Love this">❤</button>
          <button class="icon-btn js-down" title="Not for me">👎</button>
        </div>
      </div>
    </div>
    <div class="detail-body">
      <div>
        <div class="detail-meta-row">
          <span class="match">${matchPct(data)}% Match</span>
          <span>${data.release_year}</span>
          <span class="tag">${data.maturity_rating}</span>
          <span>${formatRuntime(data)}</span>
          <span class="tag">⭐ ${data.imdb_score}</span>
        </div>
        <p class="detail-desc">${data.description}</p>
        <div class="detail-stats">
          <div class="stat"><div class="num">${data.stats.viewers}</div><div class="lbl">Viewers</div></div>
          <div class="stat"><div class="num">${data.stats.hours}</div><div class="lbl">Watch hours</div></div>
          <div class="stat"><div class="num">${data.stats.avg_completion}%</div><div class="lbl">Avg completion</div></div>
        </div>
      </div>
      <div class="detail-side">
        <div><b>Cast:</b> ${data.cast.join(", ")}</div>
        <div><b>Director:</b> ${data.director}</div>
        <div><b>Genres:</b> ${data.genres.join(", ")}</div>
        <div><b>Language:</b> ${data.language}</div>
        <div><b>Country:</b> ${data.country}</div>
      </div>
    </div>
    <div class="detail-section-title">More Like This</div>
    <div class="row-track" id="detail-mlt" style="padding-bottom:24px"></div>`;

  const mlt = card.querySelector("#detail-mlt");
  data.more_like_this.forEach((it) => mlt.appendChild(card2(it)));

  card.querySelector(".detail-close").addEventListener("click", closeDetail);
  card.querySelector(".btn-play").addEventListener("click", () => { closeDetail(); openPlayer(data); });
  card.querySelector(".js-list").addEventListener("click", (e) => toggleList(data.title_id, e.currentTarget));
  card.querySelector(".js-up").addEventListener("click", () => rate(data.title_id, 4));
  card.querySelector(".js-love").addEventListener("click", () => rate(data.title_id, 5));
  card.querySelector(".js-down").addEventListener("click", () => rate(data.title_id, 2));

  $("#detail-modal").classList.remove("hidden");
}
function card2(item) {  // card for in-modal rows
  const c = card(item);
  return c;
}
function closeDetail() { $("#detail-modal").classList.add("hidden"); }

// player: mock playback that emits a real watch event
function openPlayer(item) {
  state.player = { item, pct: 0, timer: null, paused: false };
  $("#player-title").textContent = item.title;
  $("#player-bar").style.width = "0%";
  $("#player-time").textContent = "0%";
  $("#player-playpause").textContent = "⏸";
  $("#player-modal").classList.remove("hidden");
  // fast-forwarded simulated playthrough, ticks every 280ms
  state.player.timer = setInterval(() => {
    if (state.player.paused) return;
    state.player.pct = Math.min(100, state.player.pct + 1.6);
    $("#player-bar").style.width = state.player.pct + "%";
    $("#player-time").textContent = Math.round(state.player.pct) + "%";
    if (state.player.pct >= 100) finishPlayer();
  }, 280);
}
function togglePause() {
  state.player.paused = !state.player.paused;
  $("#player-playpause").textContent = state.player.paused ? "▶" : "⏸";
}
async function finishPlayer() { await closePlayer(); }
async function closePlayer() {
  const p = state.player;
  if (p.timer) clearInterval(p.timer);
  $("#player-modal").classList.add("hidden");
  if (p.item && p.pct > 1) {
    const completion = p.pct / 100;
    const runtime = (p.item.runtime_minutes || 60) * 60;
    try {
      await API.watch({
        profile_id: state.profile.profile_id,
        title_id: p.item.title_id,
        watch_seconds: Math.round(runtime * completion),
        completion,
        device: "Web",
      });
      state.homeStale = true;
      toast(`Saved your progress on "${p.item.title}". Recommendations updated.`);
    } catch (e) { /* ignore */ }
  }
  state.player = { item: null, pct: 0, timer: null, paused: false };
}

// my list + ratings
async function toggleList(titleId, btn) {
  const active = btn.classList.contains("active");
  if (active) { await API.listRemove(state.profile.profile_id, titleId); btn.classList.remove("active"); btn.textContent = "+"; toast("Removed from My List"); }
  else { await API.listAdd(state.profile.profile_id, titleId); btn.classList.add("active"); btn.textContent = "✓"; toast("Added to My List"); }
  state.homeStale = true;
}
async function rate(titleId, rating) {
  await API.rate({ profile_id: state.profile.profile_id, title_id: titleId, rating });
  state.homeStale = true;
  toast(rating >= 5 ? "❤ Loved it!" : rating >= 4 ? "👍 Glad you liked it" : "👎 Noted, we'll show less like this");
}

// search
let searchTimer = null;
function onSearchInput(e) {
  const q = e.target.value.trim();
  clearTimeout(searchTimer);
  if (!q) { if (!$("#home-view").classList.contains("hidden")) return; showView("home"); maybeReloadHome(); return; }
  searchTimer = setTimeout(async () => {
    const data = await API.search(q, state.profile.profile_id);
    $("#search-title").textContent = `Results for "${q}"`;
    renderGridInto("#search-results", data.items);
    showView("search");
  }, 250);
}

// grid views: movies / series / mylist / genre
async function showCatalog(type, title) {
  const data = await API.get(`/api/catalog?profile_id=${state.profile.profile_id}${type ? "&type=" + type : ""}`);
  $("#grid-title").textContent = title;
  renderGridInto("#grid-results", data.items);
  showView("grid");
}
async function showMyList() {
  const data = await API.mylist(state.profile.profile_id);
  $("#grid-title").textContent = "My List";
  renderGridInto("#grid-results", data.items, "Your list is empty. Add titles with the + button.");
  showView("grid");
}
function renderGridInto(sel, items, emptyMsg = "No titles found.") {
  const g = $(sel);
  g.innerHTML = "";
  if (!items || !items.length) { g.innerHTML = `<div class="empty">${emptyMsg}</div>`; return; }
  items.forEach((it) => g.appendChild(card(it)));
}

// ai assistant
function openAssistant() {
  $("#assistant-panel").classList.remove("hidden");
  if (!$("#assistant-log").dataset.greeted) {
    botMessage(`Hi ${state.profile.name}! I'm your AI concierge. Ask me for recommendations, similar titles, or describe a mood.`, []);
    $("#assistant-log").dataset.greeted = "1";
    const sug = ["Recommend something for me", "Something funny", "More like Inception", "Scary shows from the 80s"];
    const box = $("#assistant-suggest");
    box.innerHTML = "";
    sug.forEach((s) => { const c = el(`<button class="chip">${s}</button>`); c.onclick = () => sendAssistant(s); box.appendChild(c); });
  }
}
function closeAssistant() { $("#assistant-panel").classList.add("hidden"); }

async function sendAssistant(text) {
  const input = $("#assistant-input");
  const q = text || input.value.trim();
  if (!q) return;
  input.value = "";
  userMessage(q);
  const thinking = botMessage("...", []);
  try {
    const res = await API.assistant(state.profile.profile_id, q);
    thinking.remove();
    botMessage(res.reply, res.items || []);
  } catch (e) {
    thinking.textContent = "Sorry, something went wrong.";
  }
}
function userMessage(text) {
  const m = el(`<div class="msg user"></div>`); m.textContent = text;
  $("#assistant-log").appendChild(m); scrollLog();
}
function botMessage(markup, items) {
  const m = el(`<div class="msg bot">${mdBold(markup)}</div>`);
  $("#assistant-log").appendChild(m);
  if (items && items.length) {
    const cards = el(`<div class="msg-cards"></div>`);
    items.slice(0, 12).forEach((it) => {
      const mc = el(`<div class="mc"><img src="${API.posterUrl(it.title_id, 184, 276)}"><span>${it.title}</span></div>`);
      mc.onclick = () => openDetail(it.title_id);
      cards.appendChild(mc);
    });
    $("#assistant-log").appendChild(cards);
  }
  scrollLog();
  return m;
}
function scrollLog() { const l = $("#assistant-log"); l.scrollTop = l.scrollHeight; }
function mdBold(s) { return (s || "").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>"); }

// analytics
async function loadAnalytics() {
  const d = await API.analytics();
  const k = d.kpis;
  $("#kpi-cards").innerHTML = [
    ["Catalog titles", k.total_titles],
    ["Profiles", k.total_profiles],
    ["Watch events", k.total_watch_events.toLocaleString()],
    ["Total watch hours", k.total_watch_hours.toLocaleString()],
    ["Avg rating", k.avg_rating + " ★"],
    ["Avg completion", k.avg_completion_pct + "%"],
  ].map(([l, v]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  barChart("#chart-top", d.top_titles.map(r => [r.title, r.watch_hours]));
  barChart("#chart-genre", d.watch_hours_by_genre.map(r => [r.genre_name, r.watch_hours]));
  barChart("#chart-completion", d.completion_by_genre.map(r => [r.genre_name, r.avg_completion_pct]), "%");
  barChart("#chart-device", d.watch_hours_by_device.map(r => [r.device, r.watch_hours]));
  barChart("#chart-rating", d.rating_distribution.map(r => [r.rating + " ★", r.n]));
  barChart("#chart-daily", d.daily_engagement.slice(-14).map(r => [r.cal_date.slice(5), r.active_profiles]));

  loadStreaming();
}

async function loadStreaming() {
  try {
    const st = await API.get("/api/streaming/stats");
    $("#streaming-kpis").innerHTML = [
      ["Backend", st.backend === "kafka" ? "Apache Kafka" : "Log broker"],
      ["Events processed", st.processed],
      ["Watch events", st.watch_events],
      ["Rating events", st.rating_events],
      ["Consumer lag", st.lag],
      ["Events / min", st.events_per_min],
      ["Consumer group", st.consumer_group],
    ].map(([l, v]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
    barChart("#stream-topics", st.broker.topics.map(t => [`${t.topic} (${t.partitions}p)`, t.total_messages]));

    const tr = await API.get("/api/streaming/trending?k=8");
    const tops = tr.items;
    $("#stream-trending").innerHTML = tops.length
      ? (() => { const max = tops[0].score || 1; return tops.map((it, i) => `
          <div class="bar-row">
            <div class="bl">#${i + 1} ${it.title}</div>
            <div class="bt"><div class="bf" style="width:${(it.score / max) * 100}%"></div></div>
            <div class="bv">${it.score.toFixed(1)}</div>
          </div>`).join(""); })()
      : '<div class="empty" style="padding:8px 0;font-size:14px">No live events yet. Play or rate something, then reopen Analytics.</div>';
  } catch (e) { /* streaming optional */ }
}
function barChart(sel, rows, suffix = "") {
  const max = Math.max(...rows.map(r => r[1]), 1);
  $(sel).innerHTML = rows.map(([label, val]) => `
    <div class="bar-row">
      <div class="bl">${label}</div>
      <div class="bt"><div class="bf" style="width:${(val / max) * 100}%"></div></div>
      <div class="bv">${val}${suffix}</div>
    </div>`).join("");
}

// helpers + global wiring
function matchPct(item) {
  if (typeof item.score === "number" && item.score > 0) return Math.min(99, Math.round(60 + item.score * 39));
  return Math.min(99, Math.round((item.imdb_score || 7) * 9.5));
}
function formatRuntime(d) {
  if (d.content_type === "series") return `${d.runtime_minutes}m / ep`;
  const h = Math.floor(d.runtime_minutes / 60), m = d.runtime_minutes % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}
function maybeReloadHome() { if (state.homeStale) loadHome(); }

function wireGlobalUI() {
  $("#search-input").addEventListener("input", onSearchInput);
  $("#assistant-btn").addEventListener("click", openAssistant);
  $("#assistant-close").addEventListener("click", closeAssistant);
  $("#assistant-form").addEventListener("submit", (e) => { e.preventDefault(); sendAssistant(); });
  $("#player-close").addEventListener("click", closePlayer);
  $("#player-playpause").addEventListener("click", togglePause);
  $("#detail-modal").addEventListener("click", (e) => { if (e.target.id === "detail-modal") closeDetail(); });

  document.querySelectorAll("[data-nav]").forEach((a) => a.addEventListener("click", () => {
    const v = a.dataset.nav;
    if (v === "home") { showView("home"); maybeReloadHome(); }
    else if (v === "movies") showCatalog("movie", "Movies");
    else if (v === "series") showCatalog("series", "Series");
    else if (v === "mylist") showMyList();
    else if (v === "analytics") { showView("analytics"); loadAnalytics(); }
  }));

  window.addEventListener("scroll", () => {
    $("#navbar").classList.toggle("solid", window.scrollY > 60);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeDetail(); closePlayer(); closeAssistant(); }
  });
}

init();
