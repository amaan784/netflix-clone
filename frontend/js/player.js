// video player: the title's (or episode's) HLS stream with custom controls,
// seek-bar previews, subtitles, Skip Intro and Next Episode. Without any video
// set up (python -m media.ingest) the same controls drive a fast-forward simulation.
const UP_NEXT_SECONDS = 10;
const SIM_SECONDS = 20;  // a simulated playthrough takes this long

const ICONS = {
  play: '<svg viewBox="0 0 24 24"><path d="M7 4v16l13-8z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M6 4h4v16H6zM14 4h4v16h-4z"/></svg>',
  back10: '<svg viewBox="0 0 24 24"><path d="M12 4V1L7 5l5 4V6a7 7 0 1 1-7 7H3a9 9 0 1 0 9-9z"/><text x="12" y="16.5" text-anchor="middle" font-size="7" font-weight="700">10</text></svg>',
  fwd10: '<svg viewBox="0 0 24 24"><path d="M12 4V1l5 4-5 4V6a7 7 0 1 0 7 7h2a9 9 0 1 1-9-9z"/><text x="12" y="16.5" text-anchor="middle" font-size="7" font-weight="700">10</text></svg>',
  volume: '<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 4V5L7 9z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  muted: '<svg viewBox="0 0 24 24"><path d="M3 9v6h4l5 4V5L7 9z"/><path d="M16 9l6 6M22 9l-6 6" stroke="currentColor" stroke-width="2"/></svg>',
  cc: '<svg viewBox="0 0 24 24"><rect x="2" y="5" width="20" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><text x="12" y="15.5" text-anchor="middle" font-size="7.5" font-weight="700">CC</text></svg>',
  full: '<svg viewBox="0 0 24 24"><path d="M3 9V3h6M15 3h6v6M21 15v6h-6M9 21H3v-6" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  unfull: '<svg viewBox="0 0 24 24"><path d="M9 3v6H3M21 9h-6V3M15 21v-6h6M3 15h6v6" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M5 5v14l10-7zM16 5h3v14h-3z"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M15 4l-8 8 8 8" fill="none" stroke="currentColor" stroke-width="2.5"/></svg>',
};

const player = {
  item: null, info: null, episode: null, next: null,
  media: null, mode: null, hls: null,   // media is the <video>, or a SimClock
  watched: 0, lastTime: 0,               // real seconds played, not counting seeks
  thumbs: [], subLang: null,
  upNextTimer: null, upNextDismissed: false, idleTimer: null, dragging: false,
};

// remembered between visits; storage can be off (private windows), so never rely on it
const prefs = {
  get(key, fallback) {
    try { const v = localStorage.getItem("player." + key); return v === null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  },
  set(key, value) { try { localStorage.setItem("player." + key, JSON.stringify(value)); } catch (e) { /* fine */ } },
};

// Stands in for <video> when there's no stream: plays the runtime in
// SIM_SECONDS, with the few media-element members the controls use.
class SimClock {
  constructor(duration) {
    this.duration = duration; this.t = 0; this.paused = true; this.ended = false;
    this.watched = 0; this.timer = null;
  }
  get currentTime() { return this.t; }
  set currentTime(v) {
    this.t = Math.max(0, Math.min(this.duration, v)); this.ended = false;
    if (this.onseeked) this.onseeked();
    if (this.ontimeupdate) this.ontimeupdate();
  }
  play() {
    if (this.paused) {
      this.paused = false;
      this.timer = setInterval(() => this.tick(), 250);
      if (this.onplay) this.onplay();
    }
    return Promise.resolve();
  }
  pause() {
    if (this.paused) return;
    this.paused = true; clearInterval(this.timer);
    if (this.onpause) this.onpause();
  }
  tick() {
    const step = Math.min(0.25 * this.duration / SIM_SECONDS, this.duration - this.t);
    this.t += step; this.watched += step;
    if (this.ontimeupdate) this.ontimeupdate();
    if (this.t >= this.duration) { this.pause(); this.ended = true; if (this.onended) this.onended(); }
  }
  stop() { clearInterval(this.timer); this.paused = true; }
}

async function openPlayer(item, episodeId = null) {
  if (typeof stopHeroPreview === "function") stopHeroPreview();
  if (player.item) await leavePlayback();  // moving to another episode: save this one first
  Object.assign(player, { item, info: null, episode: null, next: null, watched: 0, lastTime: 0,
                          thumbs: [], upNextDismissed: false });
  $("#player-title").textContent = item.title;
  $("#player-episode").textContent = "";
  $("#player-credit").textContent = "";
  $("#pc-time").textContent = "";
  $("#skip-intro").classList.add("hidden");
  hideUpNext();
  drawSeek(0);
  $("#seek-buffered").style.width = "0";
  $("#player-modal").classList.remove("hidden");
  wakeControls();

  let info = null;
  try {
    const ep = episodeId ? `&episode_id=${episodeId}` : "";
    info = await API.get(`/api/play/${item.title_id}?profile_id=${state.profile.profile_id}${ep}`);
  } catch (e) { /* no stream info: simulate */ }
  if (player.item !== item) return;  // closed (or moved on) while we waited

  player.info = info;
  player.episode = info && info.episode;
  player.next = info && info.next_episode;
  if (player.episode) $("#player-episode").textContent = `${player.episode.label}  ${player.episode.episode_title}`;
  $("#pc-next").classList.toggle("hidden", !player.next);

  const video = $("#player-video");
  const canPlay = (window.Hls && Hls.isSupported()) || video.canPlayType("application/vnd.apple.mpegurl");
  if (info && info.available && canPlay) startVideo(info);
  else startSimulated(item, info && info.hint);
}

function setMedia(media, mode) {
  Object.assign(player, { media, mode });
  $("#player-video").classList.toggle("hidden", mode !== "video");
  $("#sim-stage").classList.toggle("hidden", mode !== "sim");
  for (const sel of ["#pc-mute", "#pc-volume", "#player-quality"]) {
    $(sel).classList.toggle("hidden", mode !== "video");
  }
  media.ontimeupdate = onTime;
  media.onseeked = () => { player.lastTime = media.currentTime; };
  media.onplay = media.onpause = () => { $("#pc-play").innerHTML = media.paused ? ICONS.play : ICONS.pause; wakeControls(); };
  media.onended = onEnded;
}

function startVideo(info) {
  const v = $("#player-video");
  setMedia(v, "video");
  $("#player-credit").textContent = info.placeholder
    ? "Placeholder clip. Run python -m media.ingest to fetch the real films."
    : `Stand-in film: ${info.credit}`;
  v.volume = prefs.get("volume", 1);
  v.muted = prefs.get("muted", false);
  v.onvolumechange = drawVolume;
  v.onprogress = drawBuffered;
  drawVolume();
  addSubtitles(v, info.subtitles || []);
  loadThumbnails(info, info.thumbnails);

  const quality = $("#player-quality");
  quality.innerHTML = "";
  if (window.Hls && Hls.isSupported()) {
    // renderTextTracksNatively off: otherwise hls.js toggles our subtitle tracks as if they were its own
    const hls = new Hls({ startPosition: info.resume_at > 0 ? info.resume_at : -1,
                          renderTextTracksNatively: false });
    player.hls = hls;
    hls.loadSource(info.src);
    hls.attachMedia(v);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      quality.innerHTML = `<option value="-1">Auto</option>` +
        hls.levels.map((l, i) => `<option value="${i}">${levelName(l)}</option>`).join("");
      v.play().catch(() => {});  // autoplay may be blocked; the play button still works
    });
    hls.on(Hls.Events.LEVEL_SWITCHED, (_, d) => {
      quality.options[0].textContent = `Auto (${levelName(hls.levels[d.level])})`;
    });
    hls.on(Hls.Events.ERROR, (_, d) => {
      if (!d.fatal || player.hls !== hls) return;
      teardown();
      startSimulated(player.item, "Couldn't load the video stream.");
    });
    quality.onchange = () => { hls.currentLevel = Number(quality.value); };
  } else {
    // Safari plays HLS natively and picks the quality itself
    quality.classList.add("hidden");
    v.src = info.src;
    v.addEventListener("loadedmetadata", () => {
      if (info.resume_at > 0) v.currentTime = info.resume_at;
      v.play().catch(() => {});
    }, { once: true });
  }
  if (info.resume_at > 0) toast(`Resuming from ${clock(info.resume_at)}`);
}

function startSimulated(item, note) {
  const minutes = (player.episode ? player.episode.runtime_minutes : item.runtime_minutes) || 60;
  const sim = new SimClock(minutes * 60);
  setMedia(sim, "sim");
  $("#pc-cc").classList.add("hidden");
  $("#sim-title").textContent = player.episode ? `${item.title} · ${player.episode.label}` : item.title;
  $("#player-credit").textContent = note || "";
  sim.play();
}

function mediaDuration() {
  const m = player.media;
  return (m && isFinite(m.duration) && m.duration) || (player.info && player.info.duration) || 0;
}

function onTime() {
  const m = player.media;
  if (!m) return;
  const dt = m.currentTime - player.lastTime;
  if (dt > 0 && dt < 2) player.watched += dt;  // bigger jumps are seeks
  player.lastTime = m.currentTime;
  if (!player.dragging) drawSeek(m.currentTime / (mediaDuration() || 1));
  $("#pc-time").textContent = `${clock(m.currentTime)} / ${clock(mediaDuration())}`;

  // Skip Intro while the opening titles run (episodes only, as on Netflix)
  const intro = player.episode && player.info && player.info.intro;
  $("#skip-intro").classList.toggle("hidden", !(intro && m.currentTime >= intro[0] && m.currentTime < intro[1]));
  // offer the next episode once the credits start
  const credits = player.info && player.info.credits;
  if (player.next && credits && m.currentTime >= credits && $("#up-next").classList.contains("hidden")
      && !player.upNextDismissed) showUpNext();
}

function onEnded() {
  if (player.next) { if ($("#up-next").classList.contains("hidden")) showUpNext(); }
  else closePlayer();
}

function togglePlay() {
  const m = player.media;
  if (!m) return;
  if (m.paused) m.play().catch(() => {}); else m.pause();
}

function skip(seconds) {
  const m = player.media;
  if (m) m.currentTime = Math.max(0, Math.min(mediaDuration(), m.currentTime + seconds));
}

// seek bar
function drawSeek(fraction) {
  const pct = Math.max(0, Math.min(1, fraction)) * 100 + "%";
  $("#seek-played").style.width = pct;
  $("#seek-knob").style.left = pct;
}

function drawBuffered() {
  const v = $("#player-video");
  const d = mediaDuration();
  if (!d || !v.buffered.length) return;
  $("#seek-buffered").style.width = (v.buffered.end(v.buffered.length - 1) / d) * 100 + "%";
}

function seekFraction(e) {
  const r = $("#seek").getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
}

function showPreview(e) {
  const t = seekFraction(e) * mediaDuration();
  const cue = player.thumbs.find((c) => t >= c.start && t < c.end);
  const thumb = $("#seek-thumb");
  thumb.classList.toggle("hidden", !cue);
  if (cue) {
    Object.assign(thumb.style, {
      backgroundImage: `url("${cue.url}")`, backgroundPosition: `-${cue.x}px -${cue.y}px`,
      width: cue.w + "px", height: cue.h + "px",
    });
  }
  $("#seek-time").textContent = clock(t);
  const box = $("#seek-preview");
  box.classList.remove("hidden");
  const width = $("#seek").getBoundingClientRect().width;
  const half = box.offsetWidth / 2;  // keep the preview inside the bar
  box.style.left = Math.min(width - half, Math.max(half, seekFraction(e) * width)) + "px";
}

async function loadThumbnails(info, url) {
  if (!url) return;
  try {
    const text = await (await fetch(url)).text();
    if (player.info !== info) return;  // moved on to another episode meanwhile
    const base = url.slice(0, url.lastIndexOf("/") + 1);
    player.thumbs = text.replace(/\r/g, "").split("\n\n").map((block) => {
      const m = block.match(/([\d:.]+) --> ([\d:.]+)\n(.+?)#xywh=(\d+),(\d+),(\d+),(\d+)/);
      return m && { start: seconds(m[1]), end: seconds(m[2]), url: base + m[3],
                    x: +m[4], y: +m[5], w: +m[6], h: +m[7] };
    }).filter(Boolean);
  } catch (e) { /* previews are a nice-to-have */ }
}

function seconds(stamp) {
  return stamp.split(":").reduce((total, part) => total * 60 + parseFloat(part), 0);
}

// subtitles: the browser parses the chosen track (in "hidden" mode) and we draw
// its lines in an overlay, so they sit above the controls and look the same everywhere
function addSubtitles(v, subs) {
  v.querySelectorAll("track").forEach((t) => t.remove());
  for (const s of subs) {
    const track = document.createElement("track");
    Object.assign(track, { kind: "subtitles", src: s.src, srclang: s.lang, label: s.label });
    v.appendChild(track);
  }
  $("#pc-cc").classList.toggle("hidden", !subs.length);
  const menu = $("#cc-menu");
  menu.innerHTML = `<button data-lang="">Off</button>` +
    subs.map((s) => `<button data-lang="${s.lang}">${s.label}</button>`).join("");
  menu.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    setSubtitles(b.dataset.lang || null, true);
    menu.classList.add("hidden");
  }));
  const remembered = prefs.get("subtitles", null);
  setSubtitles(subs.some((s) => s.lang === remembered) ? remembered : null);
}

function setSubtitles(lang, chosen = false) {
  for (const t of $("#player-video").textTracks) {
    t.mode = t.language === lang ? "hidden" : "disabled";
    t.oncuechange = t.language === lang ? () => drawCues(t) : null;
  }
  drawCues(null);
  player.subLang = lang;
  if (chosen) prefs.set("subtitles", lang);
  if (lang) prefs.set("lastSubtitles", lang);
  $("#pc-cc").classList.toggle("on", !!lang);
  $("#cc-menu").querySelectorAll("button").forEach((b) => b.classList.toggle("active", (b.dataset.lang || null) === lang));
}

function drawCues(track) {
  const box = $("#subtitles");
  box.innerHTML = "";
  for (const cue of (track && track.activeCues) || []) {
    const line = document.createElement("div");
    line.appendChild(cue.getCueAsHTML());  // a safe fragment: text plus <i>/<b> at most
    box.appendChild(line);
  }
}

function toggleSubtitles() {
  if ($("#pc-cc").classList.contains("hidden")) return;
  const tracks = [...$("#player-video").textTracks];
  const last = prefs.get("lastSubtitles", null);
  const fallback = tracks.some((t) => t.language === last) ? last : tracks[0].language;
  setSubtitles(player.subLang ? null : fallback, true);
}

// volume, fullscreen
function drawVolume() {
  const v = $("#player-video");
  const silent = v.muted || v.volume === 0;
  $("#pc-mute").innerHTML = silent ? ICONS.muted : ICONS.volume;
  $("#pc-volume").value = silent ? 0 : v.volume;
  prefs.set("volume", v.volume);
  prefs.set("muted", v.muted);
}

function toggleMute() {
  const v = $("#player-video");
  if (player.mode !== "video") return;
  if (v.muted || v.volume === 0) { v.muted = false; if (v.volume === 0) v.volume = 0.5; }
  else v.muted = true;
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else if ($("#player").requestFullscreen) $("#player").requestFullscreen().catch(() => {});
}

// Next Episode
function showUpNext() {
  const n = player.next;
  const box = $("#up-next");
  box.innerHTML = `
    <div class="un-label">Next Episode</div>
    <div class="un-title">${n.label} · ${esc(n.episode_title)}</div>
    <div class="un-btns">
      <button class="un-play"><span class="un-fill"></span><span class="un-text">${ICONS.play} Next Episode</span></button>
      <button class="un-stay">Watch Credits</button>
    </div>`;
  box.classList.remove("hidden");
  box.querySelector(".un-play").addEventListener("click", playNext);
  box.querySelector(".un-stay").addEventListener("click", () => { player.upNextDismissed = true; hideUpNext(); });
  // fills up over the countdown, then the next episode starts by itself
  const fill = box.querySelector(".un-fill");
  fill.style.transition = `width ${UP_NEXT_SECONDS}s linear`;
  requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = "100%"; }));
  player.upNextTimer = setTimeout(playNext, UP_NEXT_SECONDS * 1000);
}

function hideUpNext() {
  clearTimeout(player.upNextTimer);
  $("#up-next").classList.add("hidden");
}

function playNext() {
  hideUpNext();
  if (player.item && player.next) openPlayer(player.item, player.next.episode_id);
}

// controls fade out while playing and the mouse is still
function wakeControls() {
  const p = $("#player");
  p.classList.remove("idle");
  clearTimeout(player.idleTimer);
  player.idleTimer = setTimeout(() => {
    if (player.media && !player.media.paused && $("#cc-menu").classList.contains("hidden")) {
      p.classList.add("idle");
    }
  }, 3000);
}

// stop the current stream and report what was watched; the modal stays open
async function leavePlayback() {
  const { item, episode, media, mode, info } = player;
  if (!item || !media) return false;
  hideUpNext();
  // reaching the end credits counts as finishing, as it does on Netflix
  const finished = media.ended || (info && info.credits && media.currentTime >= info.credits);
  const completion = finished ? 1 : Math.min(1, media.currentTime / (mediaDuration() || 1));
  const secondsWatched = Math.round(mode === "sim" ? media.watched : player.watched);
  teardown();
  if (completion <= 0.01 && secondsWatched < 5) return false;  // opened and closed straight away
  try {
    await API.watch({
      profile_id: state.profile.profile_id,
      title_id: item.title_id,
      episode_id: episode ? episode.episode_id : null,
      watch_seconds: secondsWatched,
      completion,
      device: "Web",
    });
    state.homeStale = true;
    return true;
  } catch (e) {
    return false;
  }
}

function teardown() {
  const m = player.media;
  if (!m) return;
  player.media = null;
  if (player.mode === "video") {
    m.pause();
    if (player.hls) { player.hls.destroy(); player.hls = null; }
    m.removeAttribute("src");
    m.querySelectorAll("track").forEach((t) => t.remove());
    drawCues(null);
    m.load();
  } else {
    m.stop();
  }
}

async function closePlayer() {
  const item = player.item;
  if (!item) return;  // Escape lands here even when nothing is playing
  $("#player-modal").classList.add("hidden");
  $("#cc-menu").classList.add("hidden");
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  const saving = leavePlayback();
  player.item = null;
  if (await saving) toast(`Saved your progress on "${item.title}". Recommendations updated.`);
}

// keyboard shortcuts while the player is open; returns true if it used the key
function playerKeys(e) {
  if ($("#player-modal").classList.contains("hidden") || !player.media) return false;
  if (e.target.matches && e.target.matches("input, select")) return false;
  const actions = {
    " ": togglePlay, k: togglePlay, ArrowLeft: () => skip(-10), ArrowRight: () => skip(10),
    f: toggleFullscreen, m: toggleMute, c: toggleSubtitles, n: () => player.next && playNext(),
  };
  const action = actions[e.key];
  if (!action) return false;
  e.preventDefault();
  action();
  wakeControls();
  return true;
}

function levelName(level) {
  const m = String(level.url || "").match(/(\d+p)\//);
  return m ? m[1] : `${level.height}p`;
}

function clock(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function wirePlayer() {
  $("#player-back").innerHTML = ICONS.back;
  $("#pc-play").innerHTML = ICONS.play;
  $("#pc-back10").innerHTML = ICONS.back10;
  $("#pc-fwd10").innerHTML = ICONS.fwd10;
  $("#pc-mute").innerHTML = ICONS.volume;
  $("#pc-cc").innerHTML = ICONS.cc;
  $("#pc-full").innerHTML = ICONS.full;
  $("#pc-next").innerHTML = `${ICONS.next}<span>Next Episode</span>`;

  $("#player-back").addEventListener("click", closePlayer);
  $("#pc-play").addEventListener("click", togglePlay);
  $("#pc-back10").addEventListener("click", () => skip(-10));
  $("#pc-fwd10").addEventListener("click", () => skip(10));
  $("#pc-mute").addEventListener("click", toggleMute);
  $("#pc-volume").addEventListener("input", (e) => {
    const v = $("#player-video");
    v.volume = Number(e.target.value);
    v.muted = v.volume === 0;
  });
  $("#pc-next").addEventListener("click", playNext);
  $("#pc-cc").addEventListener("click", () => $("#cc-menu").classList.toggle("hidden"));
  $("#pc-full").addEventListener("click", toggleFullscreen);
  $("#skip-intro").addEventListener("click", () => {
    const intro = player.info && player.info.intro;
    if (player.media && intro) player.media.currentTime = intro[1];
  });
  for (const sel of ["#player-video", "#sim-stage"]) {
    $(sel).addEventListener("click", togglePlay);
    $(sel).addEventListener("dblclick", toggleFullscreen);
  }
  $("#player").addEventListener("mousemove", wakeControls);
  document.addEventListener("fullscreenchange", () => {
    $("#pc-full").innerHTML = document.fullscreenElement ? ICONS.unfull : ICONS.full;
  });

  const seek = $("#seek");
  seek.addEventListener("pointerdown", (e) => {
    if (!player.media) return;
    player.dragging = true;
    seek.setPointerCapture(e.pointerId);
    drawSeek(seekFraction(e));
  });
  seek.addEventListener("pointermove", (e) => {
    showPreview(e);
    if (player.dragging) drawSeek(seekFraction(e));
  });
  seek.addEventListener("pointerup", (e) => {
    if (!player.dragging) return;
    player.dragging = false;
    if (player.media) player.media.currentTime = seekFraction(e) * mediaDuration();
  });
  seek.addEventListener("pointerleave", () => $("#seek-preview").classList.add("hidden"));
}
