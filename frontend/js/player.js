// video player: plays the title's HLS stream, or falls back to simulated playback
// when no video has been set up yet (python -m media.ingest)
const player = {
  item: null, mode: null, hls: null, duration: 0,
  watched: 0, lastTime: 0,            // real seconds played, not counting seeks
  pct: 0, timer: null, paused: false,  // simulated mode
};

async function openPlayer(item) {
  Object.assign(player, { item, mode: null, watched: 0, lastTime: 0, pct: 0, paused: false });
  $("#player-title").textContent = item.title;
  $("#player-credit").textContent = "";
  $("#player-time").textContent = "";
  $("#player-modal").classList.remove("hidden");

  let info = null;
  try {
    info = await API.get(`/api/play/${item.title_id}?profile_id=${state.profile.profile_id}`);
  } catch (e) { /* no stream info, simulate instead */ }
  if (player.item !== item) return;  // closed while we were waiting

  const video = $("#player-video");
  const canPlay = (window.Hls && Hls.isSupported()) || video.canPlayType("application/vnd.apple.mpegurl");
  if (info && info.available && canPlay) startVideo(info);
  else startSimulated(info && info.hint);
}

function setMode(mode) {
  player.mode = mode;
  const video = mode === "video";
  $("#player-video").classList.toggle("hidden", !video);
  $("#player-quality").classList.toggle("hidden", !video);
  for (const sel of [".player-glow", "#player-title", ".player-badge", "#player-playpause", ".player-progress"]) {
    $(sel).classList.toggle("hidden", video);
  }
}

function startVideo(info) {
  setMode("video");
  player.duration = info.duration;
  $("#player-credit").textContent = info.placeholder
    ? "Placeholder clip. Run python -m media.ingest to fetch the real films."
    : `Stand-in film: ${info.credit}`;

  const v = $("#player-video");
  v.ontimeupdate = () => {
    const dt = v.currentTime - player.lastTime;
    if (dt > 0 && dt < 2) player.watched += dt;  // bigger jumps are seeks
    player.lastTime = v.currentTime;
    $("#player-time").textContent = `${clock(v.currentTime)} / ${clock(v.duration || player.duration)}`;
  };
  v.onseeked = () => { player.lastTime = v.currentTime; };
  v.onended = () => closePlayer();

  const quality = $("#player-quality");
  quality.innerHTML = "";
  if (window.Hls && Hls.isSupported()) {
    const hls = new Hls({ startPosition: info.resume_at > 0 ? info.resume_at : -1 });
    player.hls = hls;
    hls.loadSource(info.src);
    hls.attachMedia(v);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      quality.innerHTML = `<option value="-1">Auto</option>` +
        hls.levels.map((l, i) => `<option value="${i}">${levelName(l)}</option>`).join("");
      v.play().catch(() => {});  // autoplay may be blocked; the controls still work
    });
    hls.on(Hls.Events.LEVEL_SWITCHED, (_, d) => {
      quality.options[0].textContent = `Auto (${levelName(hls.levels[d.level])})`;
    });
    hls.on(Hls.Events.ERROR, (_, d) => {
      if (!d.fatal) return;
      hls.destroy();
      player.hls = null;
      startSimulated("Couldn't load the video stream.");
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

function startSimulated(note) {
  setMode("simulated");
  $("#player-credit").textContent = note || "";
  $("#player-bar").style.width = "0%";
  $("#player-time").textContent = "0%";
  $("#player-playpause").textContent = "⏸";
  // fast-forwarded playthrough, ticks every 280ms
  player.timer = setInterval(() => {
    if (player.paused) return;
    player.pct = Math.min(100, player.pct + 1.6);
    $("#player-bar").style.width = player.pct + "%";
    $("#player-time").textContent = Math.round(player.pct) + "%";
    if (player.pct >= 100) closePlayer();
  }, 280);
}

function togglePause() {
  player.paused = !player.paused;
  $("#player-playpause").textContent = player.paused ? "▶" : "⏸";
}

async function closePlayer() {
  const item = player.item;
  if (!item) return;  // Escape lands here even when nothing is playing
  player.item = null;

  let completion, seconds;
  if (player.mode === "video") {
    const v = $("#player-video");
    const duration = v.duration || player.duration || 1;
    completion = v.ended ? 1 : Math.min(1, v.currentTime / duration);
    seconds = Math.round(player.watched);
    v.pause();
    if (player.hls) { player.hls.destroy(); player.hls = null; }
    v.removeAttribute("src");
    v.load();
  } else {
    clearInterval(player.timer);
    completion = player.pct / 100;
    seconds = Math.round((item.runtime_minutes || 60) * 60 * completion);
  }
  $("#player-modal").classList.add("hidden");
  if (completion <= 0.01 && seconds < 5) return;  // opened and closed straight away

  try {
    await API.watch({
      profile_id: state.profile.profile_id,
      title_id: item.title_id,
      watch_seconds: seconds,
      completion,
      device: "Web",
    });
    state.homeStale = true;
    toast(`Saved your progress on "${item.title}". Recommendations updated.`);
  } catch (e) { /* ignore */ }
}

function levelName(level) {
  const m = String(level.url || "").match(/(\d+p)\//);
  return m ? m[1] : `${level.height}p`;
}

function clock(s) {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
