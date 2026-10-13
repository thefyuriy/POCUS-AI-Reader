/* POCUS AI Reader front end. No build step and no dependencies.
   Rule: user-provided text (labels, notes, file names) is only ever inserted with
   textContent / createTextNode, never as HTML. */
"use strict";

const state = {
  info: null,
  archives: [],
  filters: { q: "", sort: "newest", views: new Set(), tag: "" },
  cleanup: null,
};

const appEl = document.getElementById("app");
const fileInput = document.getElementById("file-input");
const zipInput = document.getElementById("zip-input");

/* ------------------------------------------------------------------ helpers */

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k === "value") value = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  if (value !== undefined) el.value = value;
  return el;
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new Error("The app could not be reached. Close it and open it again.");
  }
  if (!res.ok) {
    let msg = `Something went wrong (${res.status}).`;
    try { msg = (await res.json()).detail || msg; } catch { /* keep default */ }
    throw new Error(msg);
  }
  return res.json();
}

function sendForm(method, url, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error((data && data.detail) || `Upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error("The app could not be reached. Close it and open it again."));
    xhr.send(form);
  });
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function formatDate(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

function normalizeTag(t) {
  return t.trim().replace(/^#+/, "").toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9_-]/g, "").slice(0, 40);
}

function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  wrapped.flush = (...args) => { clearTimeout(t); fn(...args); };
  return wrapped;
}

let toastTimer;
function toast(message, { error = false, action = null } = {}) {
  const el = document.getElementById("toast");
  el.className = error ? "toast error" : "toast";
  el.replaceChildren(h("span", null, message));
  if (action) el.append(h("button", { type: "button", onclick: () => { action.run(); el.hidden = true; } }, action.label));
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 9000 : 7000);
}

function openModal(content, { onClose } = {}) {
  const root = document.getElementById("modal-root");
  const previous = document.activeElement;
  const backdrop = h("div", { class: "backdrop" }, content);
  const close = () => {
    document.removeEventListener("keydown", onKey, true);
    backdrop.remove();
    if (previous && previous.focus) previous.focus();
    if (onClose) onClose();
  };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("keydown", onKey, true);
  content.setAttribute("role", "dialog");
  content.setAttribute("aria-modal", "true");
  content.classList.add("dialog");
  root.append(backdrop);
  const first = content.querySelector("input, select, textarea, button");
  if (first) first.focus();
  return { close };
}

function confirmDialog({ title, body, confirmLabel, danger = false }) {
  return new Promise((resolve) => {
    let modal;
    const done = (v) => { resolve(v); modal.close(); };  // resolve first: close() fires onClose, which resolves false
    const box = h("div", { "aria-label": title },
      h("h2", null, title),
      h("p", { class: "muted" }, body),
      h("div", { class: "buttons" },
        h("button", { class: "btn", type: "button", onclick: () => done(false) }, "Cancel"),
        h("button", { class: `btn ${danger ? "danger" : "primary"}`, type: "button", onclick: () => done(true) }, confirmLabel)));
    modal = openModal(box, { onClose: () => resolve(false) });
  });
}

/* ------------------------------------------------------------------- AI jobs */

const jobBar = document.getElementById("jobbar");
let pendingAnalyze = null;   // an Analyze the person asked for while PanEcho was still downloading
let jobTimer = null;
let dismissTimer = null;

function paintJob(job) {
  clearTimeout(dismissTimer);
  if (!job || (job.state !== "running" && job.state !== "queued" && job.dismissed)) { jobBar.hidden = true; return; }
  const active = job.state === "running" || job.state === "queued";
  const dismiss = () => { jobBar.hidden = true; if (state.lastJob) state.lastJob.dismissed = true; };
  const kids = [h("div", { class: "jobline" },
    h("strong", null, job.title),
    h("span", { class: "muted small" }, job.state === "failed" ? job.error : job.message))];
  if (active) {
    kids.push(h("progress", { max: "1", value: String(job.progress), "aria-label": job.title }));
    kids.push(h("button", { class: "btn small", type: "button", onclick: () => api("POST", `/api/jobs/${job.id}/cancel`).catch(() => {}) }, "Cancel"));
  } else {
    kids.push(h("button", { class: "btn small", type: "button", onclick: dismiss }, "Dismiss"));
    if (job.state === "done") dismissTimer = setTimeout(dismiss, 6000);
  }
  jobBar.className = `jobbar ${job.state}`;
  jobBar.replaceChildren(...kids);
  jobBar.hidden = false;
}

async function pollJob() {
  clearTimeout(jobTimer);
  let job;
  try { job = await api("GET", "/api/jobs/current"); } catch { jobTimer = setTimeout(pollJob, 2000); return; }
  const previous = state.lastJob;
  if (job && previous && previous.id === job.id && previous.dismissed) job.dismissed = true;
  state.lastJob = job;
  if (!job) { jobBar.hidden = true; return; }
  paintJob(job);
  if (job.state === "running" || job.state === "queued") { jobTimer = setTimeout(pollJob, 1000); return; }
  const finishedNow = !previous || previous.id !== job.id || previous.state === "running" || previous.state === "queued";
  if (!finishedNow) return;
  if (job.kind === "download" && job.state === "done" && pendingAnalyze) {
    const next = pendingAnalyze; pendingAnalyze = null;
    startAnalyze(next.archiveId, next.clipIds, true);
  } else if (job.kind === "analyze" && job.state === "done") {
    const aid = currentArchiveId();
    if (aid && aid === job.archive_id || /^#\/archive\/[0-9a-f]{32}\/clip\//.test(location.hash)) render();
  }
  if (job.state !== "done") pendingAnalyze = null;
}

async function startAnalyze(archiveId, clipIds, afterDownload = false) {
  try {
    const status = await api("GET", "/api/ai/status");
    state.ai = status;
    if (!status.installed) {
      if (afterDownload) throw new Error("PanEcho could not be installed.");
      const ok = await confirmDialog({
        title: "Download PanEcho?",
        body: `Analyze needs the PanEcho model (${status.download_mb} MB), a one-time download from its authors at Yale. ` +
          "After that, analysis runs on this Mac and your clips never leave it. " +
          `The model is licensed ${status.license}: for research and teaching only, not for clinical use.`,
        confirmLabel: `Download ${status.download_mb} MB`,
      });
      if (!ok) return;
      pendingAnalyze = { archiveId, clipIds };
      await api("POST", "/api/ai/download");
    } else {
      await api("POST", "/api/ai/analyze", { archive_id: archiveId, clip_ids: clipIds });
    }
    pollJob();
  } catch (e) {
    pendingAnalyze = null;
    toast(e.message, { error: true });
  }
}

/* ---------------------------------------------------------- PanEcho results */

const pct = (p) => `${Math.round(p * 100)}%`;
const niceClass = (c) => String(c).replace(/\|/g, " / ");

/* What the model "calls" for a category or yes/no task, and the probability it gives that answer. */
function taskCall(task, r) {
  if (!r || typeof r !== "object") return null;
  if (task.type === "binary_classification" && Number.isFinite(r.p)) {
    const present = r.p >= 0.5;
    return { answer: niceClass(present ? task.positive : task.negative), conf: present ? r.p : 1 - r.p, others: "" };
  }
  if (task.type === "multi-class_classification" && r.probs && typeof r.probs === "object") {
    const entries = Object.entries(r.probs).filter(([, v]) => Number.isFinite(v)).sort((a, b) => b[1] - a[1]);
    if (!entries.length) return null;
    return { answer: niceClass(entries[0][0]), conf: entries[0][1],
      others: entries.slice(1).map(([k, v]) => `${niceClass(k)} ${pct(v)}`).join(", ") };
  }
  return null;
}

function measurementText(task, r) {
  if (!r || !Number.isFinite(r.value)) return null;
  const units = task.units && task.units !== "N/A" ? ` ${task.units}` : "";
  return `${Math.abs(r.value) >= 100 ? r.value.toFixed(0) : r.value.toFixed(1)}${units}`;
}

function callTable(rows) {
  return h("table", { class: "results" }, h("tbody", null, rows.map((row) =>
    h("tr", null, h("th", { scope: "row" }, row.task.label),
      h("td", null, h("span", { class: "val" }, row.answer), h("span", { class: "conf" }, ` ${pct(row.conf)}`),
        row.others ? h("span", { class: "muted small" }, `  also: ${row.others}`) : null)))));
}

function panechoResults(tasks, data, heading) {
  const results = (data && data.tasks) || {};
  const calls = [];
  const measures = [];
  for (const task of tasks) {
    const r = results[task.name];
    if (!r) continue;
    if (task.type === "regression") {
      const text = measurementText(task, r);
      if (text) measures.push({ task, text });
    } else {
      const call = taskCall(task, r);
      if (call) calls.push({ task, ...call });
    }
  }
  if (!calls.length && !measures.length) return null;

  // Deciles of the probability the model gives its own top answer, highest first.
  const groups = [];
  for (let d = 9; d >= 0; d--) {
    const rows = calls.filter((c) => Math.min(9, Math.floor(c.conf * 10)) === d).sort((a, b) => b.conf - a.conf);
    if (rows.length) groups.push(h("div", { class: "decile" }, h("h4", null, `${d * 10}–${d * 10 + 10}% confident`), callTable(rows)));
  }
  return h("div", { class: "panecho" },
    h("h3", null, heading),
    h("p", { class: "muted small" }, "Findings are grouped by how confident the model is in its own answer: the probability it gives its top answer."),
    ...groups,
    measures.length ? h("div", { class: "decile" }, h("h4", null, "Measurements (the model gives no confidence for these)"),
      h("table", { class: "results" }, h("tbody", null, measures.map((m) =>
        h("tr", null, h("th", { scope: "row" }, m.task.label),
          h("td", null, h("span", { class: "val" }, m.text), m.task.caution ? h("div", { class: "caution small" }, m.task.caution) : null))))))) : null,
    h("p", { class: "muted small" }, "Model output for teaching and research, not a diagnosis. Check it against the clip yourself."),
    h("details", { class: "legend" }, h("summary", null, "What does “confident” mean here?"),
      h("p", { class: "muted small" }, "It is the model’s own probability for its top answer. It shows how sure the model is, not how often it is right, and it says nothing about how severe the finding is."),
      h("ul", { class: "muted small" },
        h("li", null, "Category results (for example RV size) split 100% across the possible answers; the top answer and its percentage are shown, with the others after “also”."),
        h("li", null, "Yes/no findings (for example RV systolic function) show the model’s call and its probability. If the model says 30% decreased, the call is “Normal” at 70%."),
        h("li", null, "Measurements (EF, TAPSE, RVSP…) are single estimated values with no error range. A percent sign there is part of the unit, for example EF in %."),
        h("li", null, "These probabilities were not calibrated on point-of-care clips, and neural-network probabilities are often over-confident."))));
}

async function aiTasks() {
  if (!state.ai) state.ai = await api("GET", "/api/ai/status");
  return state.ai.tasks;
}

/* ------------------------------------------------------------- components */

function tagEditor(initial, onChange, label = "Tags") {
  let tags = [...initial];
  const input = h("input", { type: "text", "aria-label": `Add a tag`, placeholder: "Add a tag, then press Enter" });
  const box = h("div", { class: "tags" });
  const paint = () => {
    box.replaceChildren(
      ...tags.map((t) => h("span", { class: "tag" }, `#${t}`,
        h("button", { type: "button", "aria-label": `Remove tag ${t}`, onclick: () => { tags = tags.filter((x) => x !== t); paint(); onChange(tags); } }, "×"))),
      input);
  };
  const commit = () => {
    const added = input.value.split(",").map(normalizeTag).filter(Boolean);
    input.value = "";
    const next = [...tags];
    for (const t of added) if (!next.includes(t)) next.push(t);
    if (next.length !== tags.length) { tags = next; paint(); onChange(tags); }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); }
    else if (e.key === "Backspace" && !input.value && tags.length) { tags = tags.slice(0, -1); paint(); onChange(tags); }
  });
  input.addEventListener("blur", commit);
  paint();
  return h("div", null, h("span", { class: "field-label" }, label), box);
}

function frameBox(archiveId, clip, cornerLeft, cornerRight) {
  const hasImage = clip && (clip.has_thumb || clip.kind === "still");
  return h("div", { class: "frame" },
    hasImage
      ? h("img", { src: `/thumb/${archiveId}/${clip.id}`, alt: "", loading: "lazy" })
      : h("div", { class: "none" }, "No preview"),
    h("div", { class: "corner left" }, cornerLeft),
    h("div", { class: "corner right" }, cornerRight));
}

function annotation(text, warn = false) { return h("span", { class: warn ? "ann warn" : "ann" }, text); }

function archiveTile(a) {
  const left = a.views.map((v) => annotation(v));
  if (a.unassigned) left.push(annotation("no view", true));
  const right = [annotation(plural(a.clip_count, "clip", "clips"))];
  if (a.still_count) right.push(annotation(plural(a.still_count, "still", "stills")));
  const thumbClip = a.thumb_clip ? { id: a.thumb_clip, has_thumb: true, kind: "clip" } : null;
  return h("a", { class: "tile", href: `#/archive/${a.id}` },
    frameBox(a.id, thumbClip, left, right),
    h("div", { class: "caption" },
      h("div", { class: "name" }, a.label),
      h("div", { class: "line" }, `Created ${formatDate(a.created)}`),
      a.tags.length ? h("div", { class: "line" }, a.tags.slice(0, 6).map((t) => `#${t}`).join("  ")) : null,
      h("div", { class: "line" }, { analyzed: "PanEcho analyzed", partial: "PanEcho run on some clips" }[a.ai_status] || "AI not run yet")));
}

async function deleteClip(aid, clip) {
  if (!(await confirmDialog({ title: "Delete this clip?", body: `“${clip.filename}” will be removed from this computer. This can't be undone.`, confirmLabel: "Delete clip", danger: true }))) return false;
  try {
    await api("DELETE", `/api/archives/${aid}/clips/${clip.id}`);
    toast("Clip deleted.");
    return true;
  } catch (e) { toast(e.message, { error: true }); return false; }
}

function clipTile(archive, clip) {
  const left = [clip.view ? annotation(clip.view) : annotation("no view", true)];
  const right = clip.kind === "still" ? [annotation("still")] : [];
  return h("div", { class: "tilewrap" },
    h("a", { class: "tile", href: `#/archive/${archive.id}/clip/${clip.id}` },
      frameBox(archive.id, clip, left, right),
      h("div", { class: "caption" },
        h("div", { class: "name" }, clip.filename),
        clip.tags.length ? h("div", { class: "line" }, clip.tags.map((t) => `#${t}`).join("  ")) : null)),
    h("button", { class: "btn quiet small danger", type: "button", "aria-label": `Delete ${clip.filename}`,
      onclick: async () => { if (await deleteClip(archive.id, clip)) render(); } }, "Delete clip"));
}

/* -------------------------------------------------------- thumbnails (client) */

function makeThumb(file) {
  return new Promise((resolve) => {
    if (!file.type.startsWith("video/") && !/\.(mp4|m4v|mov|webm)$/i.test(file.name)) return resolve(null);
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.playsInline = true;
    let finished = false;
    const finish = (blob) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      URL.revokeObjectURL(url);
      resolve(blob);
    };
    const timer = setTimeout(() => finish(null), 10000);
    video.addEventListener("error", () => finish(null));
    video.addEventListener("loadeddata", () => {
      try { video.currentTime = Math.max(0.01, Math.min(0.1, (video.duration || 0) / 2)); } catch { finish(null); }
    });
    video.addEventListener("seeked", () => {
      const width = 480;
      const ratio = video.videoWidth ? video.videoHeight / video.videoWidth : 0.75;
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = Math.max(1, Math.round(width * ratio));
      try {
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => finish(blob), "image/jpeg", 0.82);
      } catch { finish(null); }
    });
    video.src = url;
  });
}

async function uploadThumbnails(archiveId, clipIds, blobs, onStep) {
  for (let i = 0; i < clipIds.length; i++) {
    if (blobs[i]) {
      try {
        await fetch(`/api/archives/${archiveId}/clips/${clipIds[i]}/thumbnail`, {
          method: "PUT", headers: { "Content-Type": "image/jpeg" }, body: blobs[i],
        });
      } catch { /* a missing preview is not worth failing the upload */ }
    }
    if (onStep) onStep(i + 1, clipIds.length);
  }
}

/* ------------------------------------------------------------ upload dialog */

function isSupported(file) {
  const ext = "." + file.name.split(".").pop().toLowerCase();
  return !file.name.startsWith(".") && state.info.extensions.includes(ext);
}

async function openUploadDialog(rawFiles, targetArchive) {
  const files = rawFiles.filter(isSupported);
  const skipped = rawFiles.length - files.length;
  if (!files.length) {
    toast("No supported files found. Use MP4, M4V, MOV, AVI, WEBM, JPG, PNG or GIF.", { error: true });
    return;
  }
  if (skipped) toast(`Skipped ${plural(skipped, "file", "files")} that are not MP4, MOV, AVI, WEBM or image files.`);

  let guesses = files.map(() => "");
  try { guesses = (await api("POST", "/api/guess-views", { names: files.map((f) => f.name) })).views; } catch { /* manual tags still work */ }

  const rows = files.map((file, i) => ({ file, view: guesses[i], blob: null, thumbEl: h("div", { class: "thumb" }) }));
  const stamp = new Date().toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  const labelInput = h("input", { type: "text", value: `Scans ${stamp}`, "aria-label": "Archive name" });
  const confirmBox = h("input", { type: "checkbox", id: "deid-confirm" });
  const submit = h("button", { class: "btn primary", type: "button", disabled: true }, "");
  const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
  const list = h("div", { class: "filelist" });
  const status = h("p", { class: "muted small", role: "status" });
  const bar = h("progress", { max: "1", value: "0", hidden: true });
  let busy = false;
  let modal;

  const refresh = () => {
    submit.textContent = `Upload ${plural(rows.length, "file", "files")}`;
    submit.disabled = busy || !confirmBox.checked || rows.length === 0;
  };

  const paintRows = () => {
    list.replaceChildren(...rows.map((row) => {
      const select = h("select", { "aria-label": `View for ${row.file.name}`, onchange: (e) => { row.view = e.target.value; } },
        h("option", { value: "" }, "No view yet"),
        ...state.info.views.map((v) => h("option", { value: v }, v)));
      select.value = row.view || "";
      return h("div", { class: "filerow" },
        row.thumbEl,
        h("div", { class: "fname" }, row.file.name, h("div", { class: "muted small" }, formatBytes(row.file.size))),
        select,
        h("button", { class: "btn quiet small", type: "button", "aria-label": `Remove ${row.file.name}`,
          onclick: () => { rows.splice(rows.indexOf(row), 1); paintRows(); refresh(); } }, "Remove"));
    }));
  };

  confirmBox.addEventListener("change", refresh);
  cancel.addEventListener("click", () => { if (!busy) modal.close(); });

  const body = h("div", { "aria-label": "Add files" },
    h("h2", null, targetArchive ? `Add files to ${targetArchive.label}` : "New archive"),
    targetArchive ? null : h("div", null, h("span", { class: "field-label" }, "Archive name"), labelInput),
    h("div", { class: "field-label" }, "Check each clip's view. Echo analysis relies on it."),
    list,
    h("div", { class: "reminder" },
      "Remove patient names, record numbers, birth dates and accession numbers from every clip first. " +
      "This app can't detect text burned into the image.",
      h("label", { for: "deid-confirm" }, confirmBox, "These files are de-identified.")),
    bar, status,
    h("div", { class: "buttons" }, cancel, submit));

  modal = openModal(body);
  paintRows();
  refresh();

  // Previews are made one at a time so a big batch doesn't freeze the window.
  (async () => {
    for (const row of rows) {
      if (row.file.type.startsWith("image/")) {
        row.thumbEl.replaceChildren(h("img", { alt: "", src: URL.createObjectURL(row.file) }));
        continue;
      }
      row.blob = await makeThumb(row.file);
      if (row.blob) row.thumbEl.replaceChildren(h("img", { alt: "", src: URL.createObjectURL(row.blob) }));
    }
  })();

  submit.addEventListener("click", async () => {
    busy = true; refresh();
    cancel.disabled = true;
    bar.hidden = false;
    status.textContent = "Uploading…";
    const form = new FormData();
    if (!targetArchive) form.append("label", labelInput.value);
    form.append("confirm_deidentified", "true");
    for (const row of rows) { form.append("files", row.file, row.file.name); form.append("views", row.view || ""); }
    try {
      const url = targetArchive ? `/api/archives/${targetArchive.id}/clips` : "/api/archives";
      const result = await sendForm("POST", url, form, (p) => { bar.value = p; status.textContent = p >= 1 ? "Processing… AVI files are converted to MP4, which can take a minute." : `Uploading… ${Math.round(p * 100)}%`; });
      status.textContent = "Creating previews…";
      bar.removeAttribute("value");
      await uploadThumbnails(result.id, result.new_clip_ids, rows.map((r) => r.blob));
      modal.close();
      toast(`Added ${plural(rows.length, "file", "files")}.`);
      if (location.hash === `#/archive/${result.id}`) render(); else location.hash = `#/archive/${result.id}`;
    } catch (err) {
      busy = false; cancel.disabled = false; bar.hidden = true;
      status.textContent = "";
      refresh();
      toast(err.message, { error: true });
    }
  });
}

/* ---------------------------------------------------------- drag and drop */

async function walkEntry(entry, out) {
  if (entry.isFile) {
    out.push(await new Promise((resolve, reject) => entry.file(resolve, reject)));
  } else if (entry.isDirectory) {
    const reader = entry.createReader();
    let batch;
    do {
      batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
      for (const child of batch) await walkEntry(child, out);
    } while (batch.length);
  }
}

async function collectDropped(dt) {
  // Entries must be read synchronously, before the first await.
  const entries = [...(dt.items || [])].map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean);
  const plain = [...(dt.files || [])];
  if (!entries.length) return plain;
  const out = [];
  for (const entry of entries) await walkEntry(entry, out);
  return out;
}

function currentArchiveId() {
  const m = location.hash.match(/^#\/archive\/([0-9a-f]{32})\/?$/);
  return m ? m[1] : null;
}

async function startUpload(files) {
  if (!files.length) return;
  const aid = currentArchiveId();
  let target = null;
  if (aid) { try { target = await api("GET", `/api/archives/${aid}`); } catch { target = null; } }
  openUploadDialog(files, target);
}

function installDropTarget() {
  const veil = document.getElementById("veil");
  let depth = 0;
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { depth++; veil.hidden = false; } });
  window.addEventListener("dragleave", (e) => { if (hasFiles(e) && --depth <= 0) { depth = 0; veil.hidden = true; } });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0; veil.hidden = true;
    startUpload(await collectDropped(e.dataTransfer));
  });
}

function chooseFiles() { fileInput.value = ""; fileInput.click(); }

/* ---------------------------------------------------------------- library */

function filteredArchives() {
  const f = state.filters;
  const q = f.q.trim().toLowerCase();
  const out = state.archives.filter((a) =>
    (!q || a.search.includes(q)) &&
    [...f.views].every((v) => a.views.includes(v)) &&
    (!f.tag || a.tags.includes(f.tag)));
  const sorters = {
    newest: (a, b) => b.created.localeCompare(a.created),
    oldest: (a, b) => a.created.localeCompare(b.created),
    name: (a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }),
    clips: (a, b) => b.clip_count - a.clip_count,
  };
  return out.sort(sorters[f.sort] || sorters.newest);
}

async function renderLibrary() {
  state.archives = await api("GET", "/api/archives");
  const f = state.filters;
  const allTags = [...new Set(state.archives.flatMap((a) => a.tags))].sort();
  if (f.tag && !allTags.includes(f.tag)) f.tag = "";

  if (!state.archives.length) {
    appEl.replaceChildren(
      h("div", { class: "empty" },
        h("h1", null, "Library"),
        h("h2", null, "No archives yet"),
        h("p", null, "Drop MP4 files or a folder anywhere in this window, or choose files. Each upload becomes an archive you can tag, annotate and review."),
        h("button", { class: "btn primary", type: "button", onclick: chooseFiles }, "Choose files")));
    return;
  }

  const count = h("span", { class: "muted" });
  const sheet = h("div", { class: "sheet" });
  const none = h("p", { class: "muted" }, "No archives match these filters.");
  const paint = () => {
    const shown = filteredArchives();
    count.textContent = shown.length === state.archives.length
      ? plural(shown.length, "archive", "archives")
      : `${shown.length} of ${plural(state.archives.length, "archive", "archives")}`;
    sheet.replaceChildren(...shown.map(archiveTile));
    none.hidden = shown.length > 0;
  };

  const search = h("input", { type: "search", class: "grow", placeholder: "Search names, notes and tags", "aria-label": "Search archives", value: f.q,
    oninput: (e) => { f.q = e.target.value; paint(); } });
  const sort = h("select", { "aria-label": "Sort archives", onchange: (e) => { f.sort = e.target.value; paint(); } },
    h("option", { value: "newest" }, "Newest first"), h("option", { value: "oldest" }, "Oldest first"),
    h("option", { value: "name" }, "Name"), h("option", { value: "clips" }, "Most clips"));
  sort.value = f.sort;
  const tagSelect = h("select", { "aria-label": "Filter by tag", onchange: (e) => { f.tag = e.target.value; paint(); } },
    h("option", { value: "" }, "All tags"), ...allTags.map((t) => h("option", { value: t }, `#${t}`)));
  tagSelect.value = f.tag;
  const chips = h("div", { class: "viewchips", role: "group", "aria-label": "Filter by view" },
    state.info.views.map((v) => h("button", { class: "chip", type: "button", "aria-pressed": f.views.has(v) ? "true" : "false",
      onclick: (e) => { f.views.has(v) ? f.views.delete(v) : f.views.add(v); e.currentTarget.setAttribute("aria-pressed", f.views.has(v)); paint(); } }, v)));

  appEl.replaceChildren(
    h("div", { class: "page-head" }, h("h1", null, "Library"), count),
    h("div", { class: "toolbar" }, search, sort, tagSelect, chips, h("span", { class: "spacer" }),
      h("button", { class: "btn primary", type: "button", onclick: chooseFiles }, "New archive")),
    sheet, none);
  paint();
}

/* ---------------------------------------------------------------- archive */

async function exportArchives(ids) {
  try {
    const out = await api("POST", "/api/export", ids ? { ids } : {});
    toast(`Saved ${out.filename} in ${out.folder}.`, state.info.can_reveal
      ? { action: { label: "Show in Finder", run: () => api("POST", "/api/reveal", { target: "export", filename: out.filename }).catch((e) => toast(e.message, { error: true })) } }
      : {});
  } catch (e) { toast(e.message, { error: true }); }
}

async function renderArchive(aid) {
  const a = await api("GET", `/api/archives/${aid}`);
  const clips = a.clips.filter((c) => c.kind === "clip").length;
  const stills = a.clips.length - clips;
  const saved = h("span", { class: "saved", role: "status" });
  const flash = (text) => { saved.textContent = text; };
  const save = async (patch) => {
    flash("Saving…");
    try { await api("PATCH", `/api/archives/${aid}`, patch); flash("Saved"); } catch (e) { flash(""); toast(e.message, { error: true }); }
  };

  const title = h("input", { class: "title-input", type: "text", value: a.label, "aria-label": "Archive name", maxlength: "200",
    onchange: (e) => { if (e.target.value.trim()) save({ label: e.target.value }); else e.target.value = a.label; } });
  const notes = h("textarea", { "aria-label": "Archive notes", placeholder: "Teaching notes, history, what to look for…", value: a.notes });
  const saveNotes = debounce((v) => save({ notes: v }), 700);
  notes.addEventListener("input", (e) => saveNotes(e.target.value));
  notes.addEventListener("blur", (e) => saveNotes.flush(e.target.value));

  const sheet = h("div", { class: "sheet" }, a.clips.map((c) => clipTile(a, c)));
  const study = a.panecho_study
    ? panechoResults(await aiTasks(), a.panecho_study, `PanEcho: whole archive (average of ${plural(a.panecho_study.clips, "clip", "clips")})`)
    : null;
  appEl.replaceChildren(
    h("div", { class: "navrow" }, h("a", { class: "btn", href: "#/" }, "← All exams")),
    title,
    h("p", { class: "muted" }, `Created ${formatDate(a.created)}. ${plural(clips, "clip", "clips")}${stills ? `, ${plural(stills, "still", "stills")}` : ""}. `, saved),
    h("div", { class: "meta-row" },
      h("div", null, h("span", { class: "field-label" }, "Notes"), notes),
      tagEditor(a.tags, (tags) => save({ tags }))),
    h("div", { class: "actions" },
      h("button", { class: "btn primary", type: "button", onclick: chooseFiles }, "Add files"),
      h("button", { class: "btn primary", type: "button", onclick: () => startAnalyze(aid, null) }, "Analyze archive"),
      h("button", { class: "btn", type: "button", onclick: () => exportArchives([aid]) }, "Export this archive"),
      h("button", { class: "btn danger", type: "button", onclick: async () => {
        if (!(await confirmDialog({ title: "Delete this archive?", body: `“${a.label}” and all ${a.clips.length} of its files will be removed from this computer. This can't be undone.`, confirmLabel: "Delete archive", danger: true }))) return;
        try { await api("DELETE", `/api/archives/${aid}`); toast("Archive deleted."); location.hash = "#/"; } catch (e) { toast(e.message, { error: true }); }
      } }, "Delete archive")),
    sheet,
    ...(study ? [study] : []));   // replaceChildren turns null into the text "null"
}

/* ----------------------------------------------------------------- viewer */

async function renderViewer(aid, cid) {
  const a = await api("GET", `/api/archives/${aid}`);
  const index = a.clips.findIndex((c) => c.id === cid);
  if (index < 0) throw new Error("That clip is no longer in this archive.");
  const clip = a.clips[index];
  const prev = a.clips[index - 1];
  const next = a.clips[index + 1];
  const isVideo = clip.kind === "clip";
  const saved = h("span", { class: "saved", role: "status" });
  const save = async (patch) => {
    saved.textContent = "Saving…";
    try { await api("PATCH", `/api/archives/${aid}/clips/${cid}`, patch); saved.textContent = "Saved"; }
    catch (e) { saved.textContent = ""; toast(e.message, { error: true }); }
  };

  let stage;
  const cleanups = [];
  if (isVideo) {
    let fps = 30;
    const video = h("video", { src: `/media/${aid}/${cid}`, controls: true, loop: true, muted: true, playsinline: true, preload: "auto", "aria-label": clip.filename });
    video.muted = true;
    const readout = h("div", { class: "readout", "aria-hidden": "true" }, "0.00 s  frame 0");
    const paint = () => { readout.textContent = `${video.currentTime.toFixed(2)} s  frame ${Math.round(video.currentTime * fps)}`; };
    video.addEventListener("timeupdate", paint);
    video.addEventListener("seeked", paint);
    let alive = true;
    cleanups.push(() => { alive = false; video.pause(); });
    if ("requestVideoFrameCallback" in video) {
      const tick = () => { if (!alive) return; paint(); video.requestVideoFrameCallback(tick); };
      video.requestVideoFrameCallback(tick);
    }
    const step = (dir) => {
      video.pause();
      const frame = Math.round(video.currentTime * fps) + dir;
      video.currentTime = Math.max(0, Math.min(video.duration || 0, (frame + 0.5) / fps));
    };
    const fpsInput = h("input", { type: "number", min: "1", max: "240", value: "30", "aria-label": "Frames per second",
      onchange: (e) => { fps = Math.max(1, Math.min(240, Number(e.target.value) || 30)); e.target.value = fps; paint(); } });
    const speed = h("select", { "aria-label": "Playback speed", onchange: (e) => { video.playbackRate = Number(e.target.value); } },
      ...[0.25, 0.5, 1, 2].map((s) => h("option", { value: String(s) }, `${s}×`)));
    speed.value = "1";
    const loop = h("input", { type: "checkbox", checked: true, onchange: (e) => { video.loop = e.target.checked; } });
    const toggle = () => (video.paused ? video.play() : video.pause());
    const onKey = (e) => {
      if (e.target.closest("input, textarea, select")) return;
      if (e.key === " ") { e.preventDefault(); toggle(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
    };
    document.addEventListener("keydown", onKey);
    cleanups.push(() => document.removeEventListener("keydown", onKey));
    stage = h("div", null,
      h("div", { class: "stage" }, video, readout),
      h("div", { class: "transport" },
        h("button", { class: "btn small", type: "button", onclick: toggle }, "Play or pause"),
        h("button", { class: "btn small", type: "button", onclick: () => step(-1), "aria-label": "Back one frame" }, "Frame back"),
        h("button", { class: "btn small", type: "button", onclick: () => step(1), "aria-label": "Forward one frame" }, "Frame forward"),
        h("label", null, "Speed", speed),
        h("label", null, "Frame rate", fpsInput),
        h("label", null, loop, "Loop")),
      h("p", { class: "hint" }, "Space plays or pauses. Left and right arrows step one frame. Set the frame rate to match the scanner so frame numbers are right."));
  } else {
    stage = h("div", null, h("div", { class: "stage" }, h("img", { src: `/media/${aid}/${cid}`, alt: clip.filename })));
  }
  state.cleanup = () => cleanups.forEach((fn) => fn());

  const viewSelect = h("select", { "aria-label": "View", onchange: (e) => save({ view: e.target.value }) },
    h("option", { value: "" }, "No view yet"), ...state.info.views.map((v) => h("option", { value: v }, v)));
  viewSelect.value = clip.view || "";
  const notes = h("textarea", { "aria-label": "Clip notes", placeholder: "Findings, teaching points, questions for the learner…", value: clip.notes });
  const saveNotes = debounce((v) => save({ notes: v }), 700);
  notes.addEventListener("input", (e) => saveNotes(e.target.value));
  notes.addEventListener("blur", (e) => saveNotes.flush(e.target.value));

  const panechoClip = clip.results && clip.results.panecho;
  const panechoView = panechoClip ? panechoResults(await aiTasks(), panechoClip, `PanEcho, analyzed ${formatDate(panechoClip.analyzed)}`) : null;

  const side = h("div", { class: "side" },
    h("div", { class: "side-head" },
      h("span", { class: "name" }, clip.filename),
      h("span", null,
        prev ? h("a", { class: "btn quiet small", href: `#/archive/${aid}/clip/${prev.id}` }, "Previous") : null,
        next ? h("a", { class: "btn quiet small", href: `#/archive/${aid}/clip/${next.id}` }, "Next") : null)),
    h("div", null, h("span", { class: "field-label" }, "View"), viewSelect),
    tagEditor(clip.tags, (tags) => save({ tags })),
    h("div", null, h("span", { class: "field-label" }, "Notes"), notes, saved),
    h("div", null, h("button", { class: "btn danger small", type: "button", onclick: async () => {
      if (!(await deleteClip(aid, clip))) return;
      const after = next || prev;
      location.hash = after ? `#/archive/${aid}/clip/${after.id}` : `#/archive/${aid}`;
    } }, "Delete this clip")),
    h("div", { class: "ai" },
      h("h2", null, "AI interpretation"),
      clip.kind === "clip"
        ? h("button", { class: "btn primary", type: "button", onclick: () => startAnalyze(aid, [cid]) },
          panechoClip ? "Analyze this clip again" : "Analyze this clip")
        : h("p", { class: "muted small" }, "Still images are not analyzed."),
      panechoView || h("p", { class: "muted small" }, "Nothing has been analyzed yet. AI only runs when you click Analyze."),
      h("div", { class: "model" }, h("h3", null, "EchoPrime"),
        h("p", null, "A second opinion from a different model, with its own view check. Planned after PanEcho.")),
      h("p", { class: "caveat" }, "Both models were trained on consultative echocardiograms. Their accuracy on point-of-care clips has not been validated, so treat results as a teaching aid.")));

  appEl.replaceChildren(
    h("div", { class: "navrow" },
      h("a", { class: "btn", href: `#/archive/${aid}` }, "← Back to this exam"),
      h("a", { class: "btn", href: "#/" }, "All exams")),
    h("div", { class: "viewer" }, stage, side));
}

/* ----------------------------------------------------------------- router */

function renderError(err) {
  appEl.replaceChildren(h("div", { class: "empty" },
    h("h2", null, "That page can't be shown"),
    h("p", null, err.message),
    h("a", { class: "btn primary", href: "#/" }, "All exams")));
}

async function render() {
  if (state.cleanup) { state.cleanup(); state.cleanup = null; }
  const parts = (location.hash || "#/").slice(1).split("/").filter(Boolean);
  try {
    if (parts[0] === "archive" && parts[1] && parts[2] === "clip" && parts[3]) await renderViewer(parts[1], parts[3]);
    else if (parts[0] === "archive" && parts[1]) await renderArchive(parts[1]);
    else await renderLibrary();
  } catch (err) {
    renderError(err);
  }
  window.scrollTo(0, 0);
}

/* -------------------------------------------------------------- top bar */

function installTopbar() {
  document.getElementById("btn-export").addEventListener("click", () => exportArchives(null));
  document.getElementById("btn-import").addEventListener("click", () => { zipInput.value = ""; zipInput.click(); });
  document.getElementById("btn-folder").addEventListener("click", async () => {
    try {
      const r = await api("POST", "/api/reveal", { target: "library" });
      if (!r.revealed) toast(`Your library is stored at ${r.path}`);
    } catch (e) { toast(e.message, { error: true }); }
  });
  zipInput.addEventListener("change", async () => {
    const file = zipInput.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file, file.name);
    toast("Importing…");
    try {
      const r = await sendForm("POST", "/api/import", form);
      toast(`Imported ${plural(r.archives.length, "archive", "archives")} with ${plural(r.clips, "file", "files")}.`);
      location.hash = "#/";
      render();
    } catch (e) { toast(e.message, { error: true }); }
  });
  fileInput.addEventListener("change", () => startUpload([...fileInput.files]));
}

async function boot() {
  try {
    state.info = await api("GET", "/api/info");
  } catch (err) {
    renderError(err);
    return;
  }
  installTopbar();
  installDropTarget();
  pollJob();   // pick up a job that is already running (for example after a reload)
  window.addEventListener("hashchange", render);
  render();
}

boot();
