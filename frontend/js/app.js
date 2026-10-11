// front-end application logic
const state = {
  profile: null,
  homeStale: false,
  logos: new Set(),  // titles with a TMDB logo image
};

const $ = (sel) => document.querySelector(sel);
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
// for text that comes from outside the catalog (TMDB)
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// boot
async function init() {
  const art = await API.artwork().catch(() => ({}));
  state.logos = new Set(art.logos || []);
  API.artVersion = encodeURIComponent(art.version || "none");
  if (art.attribution) $("#art-credit").textContent = art.attribution;
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
  if (name !== "home") stopHeroPreview();
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
    <div class="hero-bg"><img src="${API.backdropUrl(item.title_id)}" alt=""></div>
    <div class="hero-content">
      <div class="hero-tag">${item.content_type === "series" ? "SERIES" : "FILM"}</div>
      <h1 class="hero-title">${titleArt(item)}</h1>
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
    </div>
    <button class="hero-mute hidden" title="Sound on/off"></button>`;
  hero.querySelector(".btn-play").addEventListener("click", () => openPlayer(item));
  hero.querySelector(".btn-info").addEventListener("click", () => openDetail(item.title_id));
  hero.querySelector(".hero-mute").addEventListener("click", toggleHeroSound);
  scheduleHeroPreview(item);
}

// a muted clip plays behind the banner a few seconds after it shows, then
// fades back to the still (Netflix does the same with trailers)
const preview = { item: null, hls: null, video: null, timer: null };

function scheduleHeroPreview(item) {
  stopHeroPreview();
  preview.item = item;
  preview.timer = setTimeout(() => startHeroPreview(item), 2500);
}

async function startHeroPreview(item) {
  const busy = !$("#player-modal").classList.contains("hidden") || !$("#detail-modal").classList.contains("hidden");
  if (preview.item !== item || busy || document.hidden || window.scrollY > 200
      || !(window.Hls && Hls.isSupported())) return;
  let info;
  try { info = await API.get(`/api/play/${item.title_id}?profile_id=${state.profile.profile_id}`); }
  catch (e) { return; }
  if (preview.item !== item || !info.available || info.placeholder) return;

  const v = document.createElement("video");
  v.className = "hero-video";
  v.muted = true;
  v.playsInline = true;
  const start = Math.min(info.duration * 0.3, (info.intro ? info.intro[1] : 0) + 45);  // past the titles
  const hls = new Hls({ startPosition: start, capLevelToPlayerSize: true, maxBufferLength: 20 });
  Object.assign(preview, { hls, video: v });
  hls.loadSource(info.src);
  hls.attachMedia(v);
  hls.on(Hls.Events.MANIFEST_PARSED, () => v.play().catch(stopHeroPreview));
  hls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) stopHeroPreview(); });
  v.addEventListener("playing", () => {
    $("#hero").classList.add("previewing");
    $(".hero-mute").classList.remove("hidden");
    drawHeroSound();
  }, { once: true });
  v.addEventListener("timeupdate", () => { if (v.currentTime > start + 60) stopHeroPreview(); });
  $("#hero .hero-bg").appendChild(v);
}

function stopHeroPreview() {
  clearTimeout(preview.timer);
  if (preview.hls) { preview.hls.destroy(); preview.hls = null; }
  if (preview.video) { preview.video.remove(); preview.video = null; }
  const banner = $("#hero");
  if (banner) banner.classList.remove("previewing");
  const mute = $(".hero-mute");
  if (mute) mute.classList.add("hidden");
}

function toggleHeroSound() {
  if (!preview.video) return;
  preview.video.muted = !preview.video.muted;
  drawHeroSound();
}

function drawHeroSound() {
  $(".hero-mute").innerHTML = preview.video && !preview.video.muted ? ICONS.volume : ICONS.muted;
}

function renderRow(row) {
  const r = el(`<section class="row"><h2 class="row-title">${row.title}</h2>
    <div class="row-track"></div></section>`);
  const track = r.querySelector(".row-track");
  const ranked = row.kind === "trending" || row.kind === "country";
  // Continue Watching cards pick up playback straight away
  row.items.forEach((item, i) => track.appendChild(
    card(item, { rank: ranked ? i + 1 : null, resume: row.kind === "continue" })));
  return r;
}

function card(item, opts = {}) {
  const meta = [item.episode_label, `${matchPct(item)}%`, item.release_year, item.genres[0]]
    .filter(Boolean).join(" · ");
  const c = el(`
    <div class="card">
      <img loading="lazy" src="${API.posterUrl(item.title_id)}" alt="${item.title}">
      ${opts.rank ? `<div class="card-rank">#${opts.rank}</div>` : ""}
      <div class="card-hover">
        <div class="ch-title">${item.title}</div>
        <div class="ch-meta">${meta}</div>
      </div>
      ${typeof item.progress === "number" ? `<div class="card-progress"><div class="card-progress-fill" style="width:${item.progress}%"></div></div>` : ""}
    </div>`);
  c.addEventListener("click", () => opts.resume ? openPlayer(item, item.episode_id) : openDetail(item.title_id));
  return c;
}

// detail modal
async function openDetail(titleId) {
  stopHeroPreview();
  const data = await API.title(titleId, state.profile.profile_id);
  const inList = data.in_list;
  const next = data.up_next;  // series only: the episode Play starts
  const card = $("#detail-card");
  card.innerHTML = `
    <div class="detail-hero">
      <div class="dh-bg"><img src="${API.backdropUrl(data.title_id, 900, 420)}"></div>
      <button class="detail-close">✕</button>
      <div class="detail-hero-content">
        <h2>${titleArt(data)}</h2>
        <div class="detail-actions">
          <button class="btn btn-play">▶ ${next ? `${next.resume ? "Resume" : "Play"} ${next.label}` : "Play"}</button>
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
    ${data.seasons ? `<div class="episodes" id="episodes"></div>` : ""}
    <div class="detail-section-title">More Like This</div>
    <div class="row-track" id="detail-mlt" style="padding-bottom:24px"></div>`;

  if (data.seasons) renderEpisodes(data);
  const mlt = card.querySelector("#detail-mlt");
  data.more_like_this.forEach((it) => mlt.appendChild(card2(it)));

  card.querySelector(".detail-close").addEventListener("click", closeDetail);
  card.querySelector(".btn-play").addEventListener("click", () => {
    closeDetail();
    openPlayer(data, next ? next.episode_id : null);
  });
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

// a series' episode list, one season at a time, opened on the season of the
// episode that's up next
function renderEpisodes(data) {
  const box = $("#episodes");
  let season = data.up_next ? data.up_next.season_number : data.seasons[0].season;
  box.innerHTML = `
    <div class="ep-head">
      <h3>Episodes</h3>
      ${data.seasons.length > 1
        ? `<select class="ep-season">${data.seasons.map((s) => `<option value="${s.season}">Season ${s.season}</option>`).join("")}</select>`
        : `<span class="ep-one">Season 1</span>`}
    </div>
    <div class="ep-list"></div>`;
  const draw = () => {
    const list = box.querySelector(".ep-list");
    list.innerHTML = "";
    for (const ep of data.seasons.find((s) => s.season === season).episodes) {
      const current = data.up_next && data.up_next.episode_id === ep.episode_id;
      const row = el(`
        <div class="ep ${current ? "current" : ""}">
          <div class="ep-num">${ep.episode_number}</div>
          <div class="ep-thumb">
            <img loading="lazy" src="${API.stillUrl(ep.episode_id)}" alt="">
            <span class="ep-play">▶</span>
            ${ep.progress != null ? `<div class="card-progress"><div class="card-progress-fill" style="width:${ep.progress}%"></div></div>` : ""}
          </div>
          <div class="ep-info">
            <div class="ep-top"><span class="ep-title">${esc(ep.episode_title)}</span><span class="ep-len">${ep.runtime_minutes}m</span></div>
            ${ep.synopsis ? `<p class="ep-syn">${esc(ep.synopsis)}</p>` : ""}
          </div>
        </div>`);
      row.addEventListener("click", () => { closeDetail(); openPlayer(data, ep.episode_id); });
      list.appendChild(row);
    }
  };
  const picker = box.querySelector(".ep-season");
  if (picker) {
    picker.value = season;
    picker.addEventListener("change", () => { season = Number(picker.value); draw(); });
  }
  draw();
}

// the player (openPlayer, closePlayer, ...) lives in player.js

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
  loadLakehouse();
  API.get("/api/eval?cached=true").then((r) => { if (r.models) renderEval(r); }).catch(() => {});
}

// offline evaluation: the model comparison and the ranker's learned weights
const FEATURE_NAMES = {
  content: "Content similarity", coview: "Watched together", svd: "Matrix factorisation (SVD)",
  als: "Matrix factorisation (ALS)", popularity: "Popularity", genre_match: "Genre share",
  genre_lift: "Genre vs other viewers", taste_fit: "Genre vs the catalog", type_match: "Films vs series habit",
  imdb: "IMDb score", age: "Title age", is_series: "Is a series", history: "History size",
  kids_profile: "Kids profile",
};
function renderEval(r) {
  const rows = Object.entries(r.models).sort((a, b) => b[1]["ndcg@10"] - a[1]["ndcg@10"]);
  const cols = [["ndcg@10", "NDCG"], ["precision@10", "Precision"], ["recall@10", "Recall"],
                ["hit_rate@10", "Hit rate"], ["coverage@10", "Coverage"]];
  $("#eval-table").innerHTML = `<table class="eval-table">
    <tr><th>Model</th>${cols.map(([, label]) => `<th>${label}</th>`).join("")}</tr>
    ${rows.map(([name, m]) => `<tr class="${name === "ranker" ? "ranker" : ""}"><th>${name}</th>
      ${cols.map(([key]) => `<td>${m[key].toFixed(3)}</td>`).join("")}</tr>`).join("")}
  </table>`;
  const weights = r.ranker_features || [];
  const max = Math.max(...weights.map((w) => Math.abs(w.weight)), 0.01);
  $("#eval-weights").innerHTML = weights.map((w) => `
    <div class="bar-row">
      <div class="bl wide">${FEATURE_NAMES[w.feature] || w.feature}</div>
      <div class="bt"><div class="bf ${w.weight < 0 ? "neg" : ""}" style="width:${(Math.abs(w.weight) / max) * 100}%"></div></div>
      <div class="bv">${w.weight > 0 ? "+" : ""}${w.weight.toFixed(2)}</div>
    </div>`).join("");
  const ranker = r.models.ranker, hybrid = r.models.hybrid;
  $("#eval-meta").textContent = `Last run ${String(r.ran_at || "").replace("T", " ")} on ${r.config.n_profiles_evaluated} profiles. `
    + (ranker && hybrid ? `Ranker NDCG@10 ${ranker["ndcg@10"].toFixed(3)} vs hybrid ${hybrid["ndcg@10"].toFixed(3)}.` : "");
}

// lakehouse: layer sizes from the last run, plus a few gold tables
async function loadLakehouse(run = false) {
  try {
    renderLakehouse(run ? await API.post("/api/lakehouse/run", {}) : await API.get("/api/lakehouse"));
  } catch (e) {
    $("#lake-meta").textContent = "The lakehouse isn't available right now.";
  }
}

function renderLakehouse(d) {
  const r = d.last_run;
  if (!r) {
    $("#lake-meta").textContent = d.hint || "The first lakehouse run is still going; try Run pipeline in a moment.";
    return;
  }
  $("#lake-layers").innerHTML = ["bronze", "silver", "gold"].map((layer) => {
    const s = r.layers[layer];
    return `<div class="kpi layer ${layer}"><div class="v">${s.rows.toLocaleString()}</div>
      <div class="l">${layer} rows · ${s.files} files · ${(s.bytes / 1e6).toFixed(2)} MB</div></div>`;
  }).join("");
  const landed = Object.values(r.stream_records_landed || {}).reduce((a, b) => a + b, 0);
  $("#lake-meta").textContent = `Last run ${r.ran_at.replace("T", " ")} took ${r.seconds}s: `
    + `${landed} new event${landed === 1 ? "" : "s"} from the stream, ${r.quarantined} quarantined in total.`;
  barChart("#lake-binge", d.binge_by_series.map((x) => [x.title, x.episodes_per_session]));
  barChart("#lake-views", d.top_views.map((x) => [x.title, x.views]));
  $("#lake-retention").innerHTML = retentionTable(d.cohort_retention);
  $("#lake-quarantine").innerHTML = d.quarantine.length
    ? d.quarantine.map((q) => `<div class="bar-row"><div class="bl wide">${esc(q.problem)} (${q.source.replace("_", " ")})</div>
        <div class="bv">${q.n}</div></div>`).join("")
    : `<div class="empty" style="padding:8px 0;font-size:14px">Nothing quarantined.</div>`;
}

function retentionTable(rows) {
  const cohorts = [...new Set(rows.map((r) => r.cohort))];
  const months = [...new Set(rows.map((r) => r.month_number))].sort((a, b) => a - b);
  const cell = (c, m) => {
    const r = rows.find((x) => x.cohort === c && x.month_number === m);
    return r ? `<td style="background:rgba(229,9,20,${(r.retention * 0.8).toFixed(2)})">${Math.round(r.retention * 100)}%</td>` : "<td></td>";
  };
  return `<table class="retention">
    <tr><th>First watched</th>${months.map((m) => `<th>+${m} mo</th>`).join("")}</tr>
    ${cohorts.map((c) => `<tr><th>${c}</th>${months.map((m) => cell(c, m)).join("")}</tr>`).join("")}
  </table>`;
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
function titleArt(item) {  // the title's logo when TMDB has one, otherwise its name
  return state.logos.has(item.title_id)
    ? `<img class="title-logo" src="${API.logoUrl(item.title_id)}" alt="${item.title}">`
    : item.title;
}
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
  $("#detail-modal").addEventListener("click", (e) => { if (e.target.id === "detail-modal") closeDetail(); });
  wirePlayer();
  $("#lake-run").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Running...";
    await loadLakehouse(true);
    btn.disabled = false;
    btn.textContent = "Run pipeline";
  });
  $("#eval-run").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Evaluating...";
    try { renderEval(await API.get("/api/eval?k=10")); }
    catch (err) { $("#eval-meta").textContent = "The evaluation failed; see the server log."; }
    btn.disabled = false;
    btn.textContent = "Run evaluation";
  });

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
    if (window.scrollY > 300) stopHeroPreview();  // it's scrolled out of sight
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopHeroPreview(); });
  document.addEventListener("keydown", (e) => {
    if (playerKeys(e)) return;
    if (e.key === "Escape") { closeDetail(); closePlayer(); closeAssistant(); }
  });
}

init();
