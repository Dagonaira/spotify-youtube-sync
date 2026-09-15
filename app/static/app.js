const SERVICES = {
  spotify: {
    name: "Spotify",
    placeholder: "https://open.spotify.com/playlist/...",
    icon: (size) => `<svg width="${size}" height="${size}" viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg">
      <circle cx="14" cy="14" r="13" fill="#1DB954"/>
      <path d="M7.5 11.5c4-1.2 9-1 12.8 1.1" stroke="#fff" stroke-width="1.8" stroke-linecap="round" fill="none"/>
      <path d="M8 15c3.3-1 7.5-0.8 10.6 0.9" stroke="#fff" stroke-width="1.6" stroke-linecap="round" fill="none"/>
      <path d="M8.5 18.3c2.7-0.8 6-0.6 8.4 0.7" stroke="#fff" stroke-width="1.4" stroke-linecap="round" fill="none"/>
    </svg>`,
  },
  youtube: {
    name: "YouTube",
    placeholder: "https://www.youtube.com/playlist?list=...",
    icon: (size) => `<svg width="${Math.round(size * 1.17)}" height="${size}" viewBox="0 0 34 24" xmlns="http://www.w3.org/2000/svg">
      <rect x="0.5" y="0.5" width="33" height="23" rx="6" fill="#FF0000"/>
      <path d="M14 7.5L22 12L14 16.5V7.5Z" fill="#fff"/>
    </svg>`,
  },
};

const el = (id) => document.getElementById(id);

const state = {
  direction: "spotify_to_youtube",
  accounts: {
    spotify: { connected: false, account: null },
    youtube: { connected: false, account: null },
  },
  jobs: [],
  connecting: { spotify: false, youtube: false },
  activeListTab: {}, // jobId -> "added" | "unmatched" (absent = panel closed)
  listCache: {}, // "<jobId>:<kind>" -> array (undefined while loading)
};

const PLAY_ICON = `<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M3 2l7 4-7 4V2z" fill="currentColor"/></svg>`;
const SEARCH_ICON = `<svg width="11" height="11" viewBox="0 0 14 14" fill="none" stroke="currentColor"><circle cx="6" cy="6" r="4.3" stroke-width="1.4"/><path d="M9.3 9.3L12.5 12.5" stroke-width="1.4" stroke-linecap="round"/></svg>`;

function sourceService() {
  return state.direction === "spotify_to_youtube" ? "spotify" : "youtube";
}
function destService() {
  return state.direction === "spotify_to_youtube" ? "youtube" : "spotify";
}

function formatQuotaText(job) {
  const resumeAt = new Date(job.resume_at);
  const diffMs = Math.max(0, resumeAt - new Date());
  const diffMin = Math.round(diffMs / 60000);
  const hours = Math.floor(diffMin / 60);
  const mins = diffMin % 60;
  const timeStr = hours > 0 ? `${hours}h ${mins}m` : `${Math.max(mins, 1)}m`;
  const queued = Math.max(0, job.total - job.done);
  if (job.wait_reason === "quota") {
    const days = Math.max(1, Math.ceil(queued / 60));
    return `Daily YouTube limit reached. Resuming automatically in ${timeStr} — no action needed. About ${days} day${days === 1 ? "" : "s"} left at this pace.`;
  }
  const service = job.direction === "spotify_to_youtube" ? "YouTube" : "Spotify";
  const phrase = hours >= 1 ? `${service} is rate-limiting requests right now` : `${service} asked us to slow down for a moment`;
  return `${phrase}. Resuming automatically in ${timeStr} — no action needed.`;
}

function statusBadgeLabel(status) {
  return {
    queued: "Queued",
    running: "Running",
    paused: "Paused",
    waiting_quota: "Waiting",
    done: "Done",
    error: "Error",
  }[status] || status;
}

function statusLabel(status) {
  if (status === "added") return "added";
  if (status === "no_match") return "no match";
  return "not added";
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function renderActivityRow(r) {
  const iconSvg =
    r.status === "added"
      ? `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M5 8.2l2 2 4-4.4" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`
      : `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 5v3.5" stroke-width="1.8" stroke-linecap="round"/><circle cx="8" cy="11" r="0.9" fill="currentColor"/></svg>`;
  return `<div class="activity-row">
    <div class="activity-icon ${r.status}">${iconSvg}</div>
    <div class="activity-main">
      <span class="activity-track">${escapeHtml(r.title)}</span>
      ${r.subtitle ? `<span class="activity-artist">${escapeHtml(r.subtitle)}</span>` : ""}
    </div>
    <span class="activity-status">${statusLabel(r.status)}</span>
  </div>`;
}

async function selectTab(jobId, kind) {
  state.activeListTab[jobId] = kind;
  render();
  const key = `${jobId}:${kind}`;
  if (state.listCache[key] !== undefined) return; // already loaded - instant tab switch
  try {
    state.listCache[key] = await api(`/api/jobs/${jobId}/${kind}`, "GET");
  } catch (e) {
    state.listCache[key] = [];
  }
  render();
}

// Clicking a pill opens the shared panel on that tab; clicking the pill for
// the tab that's already showing closes the panel again.
function togglePanel(jobId, kind) {
  if (state.activeListTab[jobId] === kind) {
    delete state.activeListTab[jobId];
    render();
    return;
  }
  selectTab(jobId, kind);
}

function copyList(jobId, kind) {
  const list = state.listCache[`${jobId}:${kind}`] || [];
  const text = list.map((t) => (t.subtitle ? `${t.title} - ${t.subtitle}` : t.title)).join("\n");
  navigator.clipboard?.writeText(text).catch(() => {});
}

// A real full page (a YouTube video, a Spotify track, search results) can't
// be embedded as an iframe - both sites block being framed by another
// origin. A small, reused popup window is the closest thing to an in-app
// portal: opens once, and later clicks of the same kind navigate that same
// window instead of piling up tabs.
function openPopup(url, name) {
  if (!url) return;
  window.open(url, name, "width=440,height=760,noopener,noreferrer");
}

function sourceTrackUrl(job, sourceId) {
  if (!sourceId) return null;
  return job.direction === "spotify_to_youtube"
    ? `https://open.spotify.com/track/${sourceId}`
    : `https://www.youtube.com/watch?v=${sourceId}`;
}

function destTrackUrl(job, destId) {
  if (!destId) return null;
  if (job.direction === "spotify_to_youtube") return `https://www.youtube.com/watch?v=${destId}`;
  const trackId = destId.startsWith("spotify:track:") ? destId.slice("spotify:track:".length) : destId;
  return `https://open.spotify.com/track/${trackId}`;
}

function searchUrl(job, track) {
  const query = [track.title, track.subtitle].filter(Boolean).join(" ");
  return job.direction === "spotify_to_youtube"
    ? `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`
    : `https://open.spotify.com/search/${encodeURIComponent(query)}`;
}

function makeIconButton(iconSvg, title, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "track-action-btn";
  btn.title = title;
  btn.innerHTML = iconSvg;
  btn.onclick = onClick;
  return btn;
}

function buildTrackRow(job, kind, track, sourceService, destService) {
  const row = document.createElement("div");
  row.className = "track-row";

  const info = document.createElement("div");
  info.className = "track-row-info";
  info.innerHTML = `<span class="track-row-title">${escapeHtml(track.title || "")}</span>${
    track.subtitle ? `<span class="track-row-subtitle">${escapeHtml(track.subtitle)}</span>` : ""
  }`;
  row.appendChild(info);

  const actions = document.createElement("div");
  actions.className = "track-row-actions";
  if (kind === "unmatched") {
    const listenUrl = sourceTrackUrl(job, track.source_id);
    if (listenUrl) {
      actions.appendChild(makeIconButton(PLAY_ICON, `Listen on ${sourceService}`, () => openPopup(listenUrl, "crossplay-listen")));
    }
    actions.appendChild(
      makeIconButton(SEARCH_ICON, `Search on ${destService}`, () => openPopup(searchUrl(job, track), "crossplay-search"))
    );
  } else {
    const openUrl = destTrackUrl(job, track.dest_id);
    if (openUrl) {
      actions.appendChild(makeIconButton(PLAY_ICON, `Open on ${destService}`, () => openPopup(openUrl, "crossplay-listen")));
    }
  }
  row.appendChild(actions);
  return row;
}

function renderListPanel(node, job) {
  const panel = node.querySelector(".job-list-panel");
  const activeKind = state.activeListTab[job.id];

  const addedTab = panel.querySelector(".job-tab-added");
  const unmatchedTab = panel.querySelector(".job-tab-unmatched");
  addedTab.textContent = `Added (${job.added})`;
  unmatchedTab.textContent = `Not found (${job.not_found})`;
  addedTab.onclick = () => selectTab(job.id, "added");
  unmatchedTab.onclick = () => selectTab(job.id, "unmatched");

  if (!activeKind) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  addedTab.classList.toggle("active", activeKind === "added");
  unmatchedTab.classList.toggle("active", activeKind === "unmatched");

  const key = `${job.id}:${activeKind}`;
  const list = state.listCache[key];
  const listEl = panel.querySelector(".track-list");
  listEl.innerHTML = "";
  if (list === undefined) {
    listEl.innerHTML = `<div class="activity-empty">Loading…</div>`;
  } else if (list.length === 0) {
    listEl.innerHTML = `<div class="activity-empty">${activeKind === "unmatched" ? "Nothing unmatched." : "Nothing added yet."}</div>`;
  } else {
    const sourceService = job.direction === "spotify_to_youtube" ? "Spotify" : "YouTube";
    const destService = job.direction === "spotify_to_youtube" ? "YouTube" : "Spotify";
    for (const t of list) {
      listEl.appendChild(buildTrackRow(job, activeKind, t, sourceService, destService));
    }
  }
  panel.querySelector(".track-copy-btn").onclick = () => copyList(job.id, activeKind);
}

function configureJobActions(job, primaryBtn, removeBtn) {
  removeBtn.hidden = false;
  removeBtn.title = job.status === "running" ? "Cancel" : "Remove";
  removeBtn.onclick = () => removeJob(job.id, job.name, job.status === "running");

  primaryBtn.hidden = false;
  if (job.status === "running") {
    primaryBtn.textContent = "Pause";
    primaryBtn.onclick = () => pauseJob(job.id);
  } else if (job.status === "queued") {
    primaryBtn.textContent = "Start now";
    primaryBtn.onclick = () => resumeJob(job.id);
  } else if (job.status === "paused") {
    primaryBtn.textContent = "Resume";
    primaryBtn.onclick = () => resumeJob(job.id);
  } else if (job.status === "waiting_quota") {
    primaryBtn.textContent = "Resume now";
    primaryBtn.onclick = () => resumeJob(job.id);
  } else if (job.status === "error") {
    primaryBtn.textContent = "Retry";
    primaryBtn.onclick = () => resumeJob(job.id);
  } else {
    primaryBtn.hidden = true; // done
  }
}

function buildJobCard(job) {
  const tpl = el("job-card-template");
  const frag = tpl.content.cloneNode(true);
  const node = frag.querySelector(".job-card");
  fillJobCard(node, job);
  return node;
}

function fillJobCard(node, job) {
  const src = job.direction === "spotify_to_youtube" ? "spotify" : "youtube";
  const dst = job.direction === "spotify_to_youtube" ? "youtube" : "spotify";

  node.querySelector(".job-dir-icon").innerHTML =
    SERVICES[src].icon(18) +
    `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 6h8M7 3l3 3-3 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>` +
    SERVICES[dst].icon(18);

  node.querySelector(".job-name").textContent = job.name || "Untitled sync";
  node.querySelector(".job-sub").textContent = `${SERVICES[src].name} → ${SERVICES[dst].name}`;

  const badge = node.querySelector(".job-status-badge");
  badge.textContent = statusBadgeLabel(job.status);
  badge.className = `job-status-badge status-${job.status}`;

  // This node may be reused across polls (updated in place rather than
  // rebuilt) so every conditional section must be explicitly reset here,
  // not just switched on - otherwise a banner shown for a past status
  // (e.g. "error" before a successful retry) would never get hidden again.
  node.querySelector(".job-wait-banner").hidden = true;
  node.querySelector(".job-error-banner").hidden = true;
  node.querySelector(".job-done-banner").hidden = true;
  node.querySelector(".job-activity").hidden = true;

  const pct = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
  node.querySelector(".progress-fill").style.width = `${pct}%`;
  node.querySelector(".job-progress-count").textContent =
    job.total > 0 ? `${job.done} / ${job.total} tracks` : job.status === "queued" ? "Waiting to start…" : "Fetching tracks…";

  node.querySelector(".job-pill-queued").textContent = `${Math.max(0, job.total - job.done)} queued`;

  const addedPill = node.querySelector(".job-pill-added");
  addedPill.textContent = `${job.added} added`;
  addedPill.classList.toggle("pill-clickable", job.added > 0);
  addedPill.title = job.added > 0 ? "Click to see which ones" : "";
  addedPill.onclick = job.added > 0 ? () => togglePanel(job.id, "added") : null;

  const notfoundPill = node.querySelector(".job-pill-notfound");
  notfoundPill.textContent = `${job.not_found} not found`;
  notfoundPill.classList.toggle("pill-clickable", job.not_found > 0);
  notfoundPill.title = job.not_found > 0 ? "Click to see which ones" : "";
  notfoundPill.onclick = job.not_found > 0 ? () => togglePanel(job.id, "unmatched") : null;

  renderListPanel(node, job);

  if (job.status === "waiting_quota") {
    node.querySelector(".job-wait-banner").hidden = false;
    node.querySelector(".job-wait-text").textContent = formatQuotaText(job);
  }
  if (job.status === "error") {
    node.querySelector(".job-error-banner").hidden = false;
    node.querySelector(".job-error-text").textContent = job.error_message || "Something went wrong.";
  }
  if (job.status === "done") {
    node.querySelector(".job-done-banner").hidden = false;
    node.querySelector(".job-done-link").href = job.result_playlist_url || "#";
  }

  if (job.status === "running") {
    node.querySelector(".job-activity").hidden = false;
    const list = node.querySelector(".job-activity-list");
    list.innerHTML =
      job.recent.length === 0
        ? `<div class="activity-empty">Fetching tracks…</div>`
        : job.recent.map(renderActivityRow).join("");
    const queuedFooter = Math.max(0, job.total - job.done);
    node.querySelector(".job-activity-footer").textContent =
      queuedFooter > 0 ? `${queuedFooter} more tracks queued — will continue automatically` : "";
  }

  configureJobActions(job, node.querySelector(".job-primary-action"), node.querySelector(".job-remove-action"));
}

function render() {
  const src = sourceService();
  const dst = destService();

  el("source-icon").innerHTML = SERVICES[src].icon(34);
  el("source-name").textContent = SERVICES[src].name;
  el("dest-icon").innerHTML = SERVICES[dst].icon(34);
  el("dest-name").textContent = SERVICES[dst].name;

  for (const [slot, service] of [["source", src], ["dest", dst]]) {
    const acc = state.accounts[service];
    const statusEl = el(`${slot}-status`);
    statusEl.classList.toggle("connected", !!acc.connected);
    statusEl.querySelector(".status-text").textContent = acc.connected ? `Connected as ${acc.account}` : "Not connected";

    const btn = el(`${slot}-connect`);
    if (acc.connected) {
      btn.hidden = true;
    } else {
      btn.hidden = false;
      btn.disabled = state.connecting[service];
      btn.textContent = state.connecting[service] ? "Connecting…" : `Connect ${SERVICES[service].name}`;
    }
  }

  el("source-field-label").textContent = "Playlist link";
  el("source-input").placeholder = SERVICES[src].placeholder;
  el("liked-label").textContent = src === "spotify" ? "Use my Liked Songs instead" : "Use my Liked Videos instead";
  const liked = el("liked-checkbox").checked;
  el("source-input").disabled = liked;

  const anyRunning = state.jobs.some((j) => j.status === "running");
  el("start-btn").textContent = anyRunning ? "Add to queue" : "Start sync";

  el("queue-section").hidden = state.jobs.length === 0;
  el("queue-empty").hidden = state.jobs.length !== 0;
  renderJobList();
}

// Job cards are updated in place rather than torn down and rebuilt every
// poll (jobs refresh every 1s) - rebuilding wholesale used to reset the DOM
// out from under an in-progress scroll/touch gesture, causing the list to
// visibly jump. Existing nodes are reused (see fillJobCard); only genuinely
// new or removed jobs touch the DOM structure itself.
const jobNodesById = new Map();

function renderJobList() {
  const list = el("queue-list");
  const desiredIds = new Set(state.jobs.map((j) => j.id));

  for (const [id, node] of jobNodesById) {
    if (!desiredIds.has(id)) {
      node.remove();
      jobNodesById.delete(id);
    }
  }

  state.jobs.forEach((job, index) => {
    let node = jobNodesById.get(job.id);
    if (node) {
      fillJobCard(node, job);
    } else {
      node = buildJobCard(job);
      jobNodesById.set(job.id, node);
    }
    const nodeAtIndex = list.children[index];
    if (nodeAtIndex !== node) {
      list.insertBefore(node, nodeAtIndex || null);
    }
  });
}

function showFormError(msg) {
  const box = el("form-error");
  box.textContent = msg;
  box.hidden = false;
}
function clearFormError() {
  el("form-error").hidden = true;
}

async function api(path, method, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}));
    throw new Error(errBody.detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return null;
  return res.json();
}

async function pollJobs() {
  try {
    state.jobs = await api("/api/jobs", "GET");
    render();
  } catch (e) {
    /* transient - next poll will retry */
  }
}

async function pollAccounts() {
  try {
    state.accounts = await api("/api/accounts", "GET");
    render();
  } catch (e) {
    /* transient */
  }
}

async function connect(service) {
  state.connecting[service] = true;
  render();
  try {
    state.accounts[service] = await api(`/api/accounts/${service}/connect`, "POST");
    clearFormError();
  } catch (e) {
    showFormError(e.message);
  }
  state.connecting[service] = false;
  render();
}

async function pauseJob(jobId) {
  try {
    await api(`/api/jobs/${jobId}/pause`, "POST");
  } catch (e) {
    /* ignore */
  }
  pollJobs();
}

async function resumeJob(jobId) {
  try {
    await api(`/api/jobs/${jobId}/resume`, "POST");
  } catch (e) {
    /* ignore */
  }
  pollJobs();
}

async function removeJob(jobId, jobName, isRunning) {
  const label = jobName || "this sync";
  const message = isRunning
    ? `Cancel "${label}"? This stops it now and discards its progress permanently.`
    : `Remove "${label}"? This discards its progress permanently.`;
  if (!confirm(message)) return;
  try {
    await api(`/api/jobs/${jobId}`, "DELETE");
  } catch (e) {
    /* ignore */
  }
  pollJobs();
}

async function startSync() {
  const liked = el("liked-checkbox").checked;
  const source = liked ? "liked" : el("source-input").value.trim();
  const name = el("name-input").value.trim();

  if (!name) return showFormError("Enter a name for the new playlist.");
  if (!liked && !source) return showFormError("Enter a playlist link.");
  clearFormError();

  try {
    await api("/api/jobs", "POST", { direction: state.direction, source, name });
    el("source-input").value = "";
    el("name-input").value = "";
    el("liked-checkbox").checked = false;
    pollJobs();
  } catch (e) {
    showFormError(e.message);
  }
}

el("swap-btn").addEventListener("click", () => {
  state.direction = state.direction === "spotify_to_youtube" ? "youtube_to_spotify" : "spotify_to_youtube";
  el("liked-checkbox").checked = false;
  clearFormError();
  render();
});

el("liked-checkbox").addEventListener("change", render);
el("start-btn").addEventListener("click", startSync);
el("source-connect").addEventListener("click", () => connect(sourceService()));
el("dest-connect").addEventListener("click", () => connect(destService()));
el("report-bug-btn").addEventListener("click", () => api("/api/support/report-bug", "POST").catch(() => {}));
el("open-log-btn").addEventListener("click", () => api("/api/support/open-log-folder", "POST").catch(() => {}));

render();
pollJobs();
pollAccounts();
setInterval(pollJobs, 1000);
setInterval(pollAccounts, 15000);
