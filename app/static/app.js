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
};

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
  return `${service} asked us to slow down for a moment. Resuming automatically in ${timeStr} — no action needed.`;
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

function renderJobCard(job) {
  const tpl = el("job-card-template");
  const node = tpl.content.cloneNode(true);

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
  badge.classList.add(`status-${job.status}`);

  const pct = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
  node.querySelector(".progress-fill").style.width = `${pct}%`;
  node.querySelector(".job-progress-count").textContent =
    job.total > 0 ? `${job.done} / ${job.total} tracks` : job.status === "queued" ? "Waiting to start…" : "Fetching tracks…";

  node.querySelector(".job-pill-added").textContent = `${job.added} added`;
  node.querySelector(".job-pill-notfound").textContent = `${job.not_found} not found`;
  node.querySelector(".job-pill-queued").textContent = `${Math.max(0, job.total - job.done)} queued`;

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

  return node;
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
  el("liked-row").hidden = src !== "spotify";
  const liked = src === "spotify" && el("liked-checkbox").checked;
  el("source-input").disabled = liked;

  const anyRunning = state.jobs.some((j) => j.status === "running");
  el("start-btn").textContent = anyRunning ? "Add to queue" : "Start sync";

  el("queue-section").hidden = state.jobs.length === 0;
  el("queue-empty").hidden = state.jobs.length !== 0;
  const list = el("queue-list");
  list.innerHTML = "";
  for (const job of state.jobs) {
    list.appendChild(renderJobCard(job));
  }
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
  const src = sourceService();
  const liked = src === "spotify" && el("liked-checkbox").checked;
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
