"use strict";
/* Studio UI. All text goes in through textContent / createTextNode, never as markup. */

const TOKEN = (document.querySelector('meta[name="studio-token"]') || {}).content || "";
const STEPS = [
  ["topics", "1  Topics"], ["script", "2  Script"], ["footage", "3  Footage"],
  ["render", "4  Voice and render"], ["review", "5  Review"], ["export", "6  Export"],
];
const HOOKS = ["question", "shocking_stat", "myth_bust", "comparison", "countdown"];

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (method !== "GET") {
    opts.headers["X-Studio-Token"] = TOKEN;
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body || {});
  }
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    let msg = (data && (data.violations ? data.violations.join("\n") : data.detail)) || res.statusText;
    if (typeof msg !== "string") msg = JSON.stringify(msg);
    throw new Error(msg);
  }
  return data;
}

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ home */

async function initHome() {
  async function load() {
    const rows = await api("GET", "/api/batches");
    const list = $("batch-list");
    list.replaceChildren();
    if (!rows.length) list.append(h("li", { class: "muted" }, "No batches yet."));
    for (const b of rows) {
      list.append(h("li", {},
        h("a", { href: "/batch/" + encodeURIComponent(b.id) }, b.title),
        h("span", { class: "muted" }, `${b.platform} - ${b.video_count} videos`)));
    }
  }
  $("new-batch").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("new-batch-error");
    err.hidden = true;
    try {
      const res = await api("POST", "/api/batches", { name: $("batch-name").value, platform: $("batch-platform").value });
      location.href = "/batch/" + encodeURIComponent(res.id);
    } catch (e) { err.textContent = e.message; err.hidden = false; }
  });
  await load();
}

/* ----------------------------------------------------------------- batch */

function initBatch() {
  const batchId = decodeURIComponent(location.pathname.split("/").pop());
  const base = "/api/batches/" + encodeURIComponent(batchId);
  let data = null;
  let selected = null;
  let draft = [];
  let dirty = false;
  let lastSignature = "";

  const banner = (msg) => { const b = $("banner"); b.textContent = msg || ""; b.hidden = !msg; };
  const busy = () => !!(data && data.running);
  const canRun = (step) => data && data.steps[step] !== "locked" && !busy();

  async function action(fn) {
    banner("");
    try { await fn(); } catch (e) { banner(e.message); }
    await refresh(true);
  }
  const runStep = (step, params) => action(() => api("POST", `${base}/steps/${step}/run`, params || {}));
  const approve = (gate) => action(() => api("POST", `${base}/gates/${gate}/approve`, {}));
  const saveVideos = (stage) => action(async () => {
    await api("PUT", `${base}/spec?stage=${stage}`, { videos: draft });
    dirty = false;
  });

  function edit(obj, key, parse) {
    return (ev) => { obj[key] = parse ? parse(ev.target.value) : ev.target.value; dirty = true; };
  }

  function actionButton(label, onclick, enabled, cls) {
    return h("button", { class: cls || "", onclick, disabled: !enabled }, label);
  }

  /* ---- panels */
  function topicsPanel() {
    const approved = data.gates.idea === "approved";
    const theme = h("input", { value: "space facts", disabled: approved });
    const count = h("input", { type: "number", min: "1", max: "12", value: "5", style: "width:80px", disabled: approved });
    const out = [
      h("h2", {}, "Choose topics"),
      h("p", { class: "muted" }, "Ask Claude for topic ideas, edit them, then approve. Nothing is made until you approve."),
      h("div", { class: "row" },
        h("label", {}, "Theme", theme), h("label", {}, "How many", count),
        actionButton("Suggest topics", () => runStep("topics", { theme: theme.value, count: Number(count.value) }),
          canRun("topics") && !approved, "primary")),
    ];
    draft.forEach((v, i) => {
      const url = (v.topic_sources || [])[0] || "";
      out.push(h("div", { class: "video" },
        h("h4", {}, v.id),
        h("label", {}, "Topic", h("input", { value: v.topic || "", disabled: approved, oninput: edit(v, "topic") })),
        h("div", { class: "grid2" },
          h("label", {}, "Hook type", h("select", { disabled: approved, onchange: edit(v, "hook_type") },
            HOOKS.map((t) => h("option", { value: t, selected: t === v.hook_type }, t)))),
          h("label", {}, "Source page (URL)", h("input", { value: url, disabled: approved,
            oninput: (ev) => { v.topic_sources = [ev.target.value]; dirty = true; } }))),
        h("label", {}, "First spoken sentence", h("input", { value: v.hook_text || "", disabled: approved, oninput: edit(v, "hook_text") })),
        approved ? null : actionButton("Remove", () => { draft.splice(i, 1); dirty = true; renderPanel(true); }, true)));
    });
    if (!approved) {
      out.push(h("div", { class: "row" },
        actionButton("Add topic", () => {
          const n = Math.max(0, ...draft.map((v) => Number(String(v.id).replace(/\D/g, "")) || 0)) + 1;
          draft.push({ id: "v" + n, topic: "", hook_type: "question", hook_text: "", topic_sources: [""] });
          dirty = true; renderPanel(true);
        }, !busy()),
        actionButton("Save edits", () => saveVideos("idea"), !busy() && draft.length > 0)));
    }
    out.push(h("div", { class: "gate-box" },
      actionButton("Approve topics", () => approve("idea"), !busy() && !dirty && draft.length > 0 && !approved, "gate"),
      h("span", { class: "muted" }, approved ? "Approved." : dirty ? "Save your edits first." : "Your approval, not Claude's.")));
    return out;
  }

  function scriptPanel() {
    const out = [h("h2", {}, "Scripts"),
      h("p", { class: "muted" }, "Claude writes narration with a source for every claim. Read and edit before moving on."),
      actionButton("Write scripts", () => runStep("script"), canRun("script"), "primary")];
    draft.forEach((v) => {
      out.push(h("div", { class: "video" },
        h("h4", {}, `${v.id}: ${v.topic || ""}`),
        h("label", {}, "Narration", h("textarea", { oninput: edit(v, "narration") }, v.narration || "")),
        h("label", {}, "Title", h("input", { value: v.title || "", oninput: edit(v, "title") })),
        h("label", {}, "Description", h("textarea", { oninput: edit(v, "description") }, v.description || "")),
        h("label", {}, "Tags (comma separated)", h("input", { value: (v.tags || []).join(", "),
          oninput: edit(v, "tags", (s) => s.split(",").map((t) => t.trim()).filter(Boolean)) })),
        h("div", { class: "muted" }, "Claims: ", (v.claims || []).map((c) =>
          h("div", {}, c.text, " - ", (c.sources || []).map((s) => h("a", { href: s, target: "_blank", rel: "noopener" }, s)))))));
    });
    out.push(actionButton("Save edits", () => saveVideos("script"), canRun("script") && draft.length > 0 && dirty));
    return out;
  }

  function footagePanel() {
    const keys = Object.keys((data.spec.sources) || {});
    const out = [h("h2", {}, "Footage"),
      h("p", { class: "muted" }, "Claude finds free clips (size-capped), looks at contact sheets and proposes cuts. Adjust the cut times if a shot is wrong."),
      actionButton("Find footage", () => runStep("footage"), canRun("footage"), "primary")];
    keys.forEach((k) => out.push(h("div", {}, h("div", { class: "muted" }, `${k} (one frame per few seconds, left to right)`),
      h("img", { class: "sheet", src: `/media/${encodeURIComponent(batchId)}/review/sheet_${encodeURIComponent(k)}.png`,
        alt: "contact sheet " + k, onerror: (ev) => { ev.target.hidden = true; } }))));
    draft.forEach((v) => {
      v.cuts = v.cuts || [];
      const rows = v.cuts.map((c, i) => h("tr", {},
        h("td", {}, h("select", { onchange: edit(c, "source") }, keys.map((k) => h("option", { value: k, selected: k === c.source }, k)))),
        h("td", {}, h("input", { type: "number", step: "0.1", value: c.in, oninput: edit(c, "in", Number) })),
        h("td", {}, h("input", { type: "number", step: "0.1", value: c.dur, oninput: edit(c, "dur", Number) })),
        h("td", {}, actionButton("x", () => { v.cuts.splice(i, 1); dirty = true; renderPanel(true); }, true))));
      out.push(h("div", { class: "video" }, h("h4", {}, `${v.id}: ${v.topic || ""}`),
        v.redo_note ? h("div", { class: "muted" }, "Redo note: " + v.redo_note) : null,
        h("table", { class: "cuts" }, h("tr", {}, h("th", {}, "Source"), h("th", {}, "Start (s)"), h("th", {}, "Length (s)"), h("th", {})), rows),
        actionButton("Add cut", () => { if (keys.length) { v.cuts.push({ source: keys[0], in: 0, dur: 3 }); dirty = true; renderPanel(true); } }, keys.length > 0)));
    });
    out.push(actionButton("Save cuts", () => saveVideos("visuals"), canRun("footage") && draft.length > 0 && dirty));
    return out;
  }

  function renderPanelBody() {
    const out = [h("h2", {}, "Voice and render"),
      h("p", { class: "muted" }, "Narrates every video and renders 1080x1920 files. This takes a few minutes per video."),
      actionButton("Make videos", () => runStep("render"), canRun("render"), "primary")];
    (data.spec.videos || []).forEach((v) => out.push(h("div", {}, `${v.id}: ${v.topic || ""} - `,
      h("strong", {}, data.renders && data.renders[v.id] ? "rendered" : "not rendered"))));
    if (data.steps.render === "stale") out.push(h("p", { class: "error" }, "Something changed after the last render. Make the videos again."));
    return out;
  }

  function reviewPanel() {
    const out = [h("h2", {}, "Review"),
      h("p", { class: "muted" }, "Watch every video with sound. Redo any you do not like, then approve the batch."),
      actionButton("Prepare review", () => runStep("review"), canRun("review") || (data.steps.render === "done" && !busy()), "primary")];
    (data.spec.videos || []).forEach((v) => {
      if (!(data.renders && data.renders[v.id])) return;
      const note = h("input", { placeholder: "What is wrong with it?" });
      out.push(h("div", { class: "video" },
        h("h4", {}, `${v.id}: ${v.title || v.topic || ""}`),
        h("video", { controls: true, preload: "metadata", src: `/media/${encodeURIComponent(batchId)}/renders/${encodeURIComponent(v.id)}.mp4?v=${data.renders_stamp || ""}` }),
        h("div", { class: "row" }, note, actionButton("Redo this video", () => {
          if (confirm(`Redo ${v.id}? Its cuts and render are cleared.`)) action(() => api("POST", `${base}/videos/${encodeURIComponent(v.id)}/redo`, { note: note.value || "redo" }));
        }, !busy()))));
    });
    out.push(h("div", { class: "gate-box" },
      actionButton("Approve batch", () => approve("review_queue"), !busy() && data.steps.render === "done" && data.gates.review_queue === "awaiting", "gate"),
      h("span", { class: "muted" }, data.gates.review_queue === "approved" ? "Approved." : "Run Prepare review first. Your approval, not Claude's.")));
    return out;
  }

  function exportPanel() {
    return [h("h2", {}, "Export"),
      h("p", { class: "muted" }, "Writes each approved video with title, description and hashtag, private. Nothing is uploaded."),
      actionButton("Export (private)", () => runStep("export"), canRun("export"), "primary"),
      h("p", {}, "Folder: ", h("code", {}, `projects/${batchId}/export/`)),
      h("ul", { class: "list" }, (data.exports || []).map((e) => h("li", {}, e)))];
  }

  const PANELS = { topics: topicsPanel, script: scriptPanel, footage: footagePanel, render: renderPanelBody, review: reviewPanel, export: exportPanel };

  function renderRail() {
    const rail = $("rail");
    rail.replaceChildren(...STEPS.map(([key, label]) => h("button", {
      class: "step" + (key === selected ? " active" : ""), onclick: () => { selected = key; dirty = false; resetDraft(); renderPanel(true); renderRail(); } },
      h("span", {}, label), h("span", { class: "badge " + data.steps[key] }, data.steps[key]))));
  }

  function resetDraft() { draft = JSON.parse(JSON.stringify(data.spec.videos || [])); }

  function renderPanel(force) {
    if (!data) return;
    if (dirty && !force) return;
    $("panel").replaceChildren(...PANELS[selected]());
  }

  async function renderLog() {
    const job = (data.jobs || [])[0];
    $("job-status").textContent = job ? `${job.step} - ${job.status}` : "";
    if (!job) return;
    try {
      const res = await api("GET", `${base}/jobs/${encodeURIComponent(job.id)}/log?lines=80`);
      const pre = $("job-log");
      pre.textContent = res.log || "(no output yet)";
      pre.scrollTop = pre.scrollHeight;
    } catch (e) { /* log not written yet */ }
  }

  async function refresh(force) {
    data = await api("GET", base);
    $("batch-title").textContent = data.id;
    $("batch-platform").textContent = data.platform;
    if (!selected) {
      const next = STEPS.find(([k]) => data.steps[k] !== "done");
      selected = next ? next[0] : "export";
    }
    const signature = JSON.stringify([data.steps, data.gates, data.spec, data.running, (data.jobs || [])[0], data.renders, data.exports]);
    if (force || signature !== lastSignature) {
      lastSignature = signature;
      if (!dirty) resetDraft();
      renderRail();
      renderPanel(!!force && !dirty);
    }
    await renderLog();
  }

  const events = new EventSource(`${base}/events`);
  events.onmessage = () => refresh(false).catch((e) => banner(e.message));
  setInterval(() => { if (data && data.running) refresh(false).catch(() => {}); }, 4000);
  refresh(true).catch((e) => banner(e.message));
}

if (document.body.dataset.page === "home") initHome();
if (document.body.dataset.page === "batch") initBatch();
