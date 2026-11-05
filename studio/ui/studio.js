"use strict";
/* Studio UI. All text goes in through textContent / createTextNode, never as markup. */

const TOKEN = (document.querySelector('meta[name="studio-token"]') || {}).content || "";
const STEPS = [
  ["topics", "1  Topics"], ["script", "2  Script"], ["footage", "3  Footage"],
  ["render", "4  Voice and render"], ["review", "5  Review"], ["export", "6  Export"],
];
const STEP_LABEL = Object.fromEntries(STEPS.map(([k, l]) => [k, l.replace(/^\d+\s+/, "")]));
const HOOKS = ["question", "shocking_stat", "myth_bust", "comparison", "countdown"];
const WORDS_PER_SECOND = 2.9;
const SEG_COLORS = 6;

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
const isHttp = (s) => /^https?:\/\//i.test(String(s));

function elapsed(iso) {
  const s = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

function estimateSeconds(text) {
  const words = (String(text || "").trim().match(/\S+/g) || []).length;
  return { words, seconds: Math.round(words / WORDS_PER_SECOND) };
}

function toast(msg, kind) {
  const box = $("toasts");
  if (!box) return;
  const el = h("div", { class: "toast " + (kind || "") }, msg);
  box.append(el);
  setTimeout(() => el.remove(), 6500);
}

/* ------------------------------------------------------------------ home */

async function initHome() {
  async function load() {
    const rows = await api("GET", "/api/batches");
    const list = $("batch-list");
    list.replaceChildren();
    if (!rows.length) list.append(h("li", { class: "muted" }, "No batches yet - create one above."));
    for (const b of rows) {
      const done = b.current_step === "done";
      const pct = Math.round((b.steps_done / b.steps_total) * 100);
      list.append(h("li", {},
        h("div", {},
          h("a", { href: "/batch/" + encodeURIComponent(b.id) }, b.title),
          h("div", { class: "muted small" }, `${b.platform} - ${b.video_count} videos`)),
        h("div", { class: "row" },
          h("span", { class: "progress", title: `${b.steps_done} of ${b.steps_total} steps done` }, h("span", { style: `width:${pct}%` })),
          h("span", { class: "chip" }, done ? "Done" : "Next: " + (STEP_LABEL[b.current_step] || b.current_step)))));
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
  let prevJob = null;
  let retryFn = null;
  let settingsSig = "";
  const lastParams = {};

  const busy = () => !!(data && data.running);
  const canRun = (step) => data && data.steps[step] !== "locked" && !busy();

  function banner(msg, retry) {
    $("banner-text").textContent = msg || "";
    $("banner").hidden = !msg;
    retryFn = retry || null;
    $("banner-retry").hidden = !retry;
  }
  $("banner-close").addEventListener("click", () => banner(""));
  $("banner-retry").addEventListener("click", () => { const fn = retryFn; banner(""); if (fn) fn(); });
  window.addEventListener("beforeunload", (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ""; } });

  async function action(fn, retry) {
    banner("");
    try { await fn(); } catch (e) { banner(e.message, retry); settingsSig = ""; }
    await refresh(true);
  }
  const runStep = (step, params) => {
    lastParams[step] = params || {};
    return action(() => api("POST", `${base}/steps/${step}/run`, params || {}), () => runStep(step, params));
  };
  const approve = (gate) => action(() => api("POST", `${base}/gates/${gate}/approve`, {}), () => approve(gate));
  const updateSettings = (patch) => action(() => api("PUT", `${base}/settings`, patch));
  const researchTopics = (ids) => {
    if (!ids.length) return;
    // typed topics are saved as a draft first, then the AI finds sources, hook type and first sentence
    return action(async () => {
      await api("PUT", `${base}/spec?stage=${topicSaveStage()}`, { videos: draft });
      dirty = false;
      const theme = lastParams.topics && lastParams.topics.theme ? lastParams.topics.theme : "space facts";
      await api("POST", `${base}/steps/topics/run`, { research: ids, theme });
    }, () => researchTopics(ids));
  };
  function renderSettings() {
    const s = data.settings, ready = data.providers || {};
    const sig = JSON.stringify([s, ready, data.voice, data.voices]);
    if (sig === settingsSig) return;
    settingsSig = sig;
    const voices = data.voices || {};
    const current = data.voice || { engine: "piper" };
    const currentId = current.engine === "kokoro" ? "kokoro:" + (current.voice || "af_heart") : "piper:" + (current.model || "en_US-lessac-medium");
    const voiceOption = (engine, v) => h("option", { value: `${engine}:${v.id}`, selected: `${engine}:${v.id}` === currentId }, v.label);
    const pickVoice = h("select", { onchange: (ev) => {
      const [engine, id] = ev.target.value.split(":");
      action(async () => {
        await api("PUT", `${base}/voice`, engine === "kokoro" ? { engine, voice: id } : { engine, model: id });
        toast("Voice changed - the videos are voiced again. If the new voice makes a video longer than its footage, Studio lengthens the last shot or tells you to redo the footage.", "ok");
      });
    } },
    ["piper", "kokoro"].map((engine) => h("optgroup", { label: engine === "piper" ? "Piper (original)" : voices[engine] && voices[engine].ready ? "Kokoro (newer, more natural)" : "Kokoro (not set up)" },
      ((voices[engine] || {}).voices || []).map((v) => voiceOption(engine, v)))));
    const player = h("audio", { controls: true, hidden: true });
    const previewBtn = h("button", { onclick: async () => {
      previewBtn.disabled = true;
      try {
        const [engine, id] = pickVoice.value.split(":");
        const res = await api("POST", `${base}/voice/preview`, engine === "kokoro" ? { engine, voice: id } : { engine, model: id });
        player.src = res.url + "?t=" + Date.now();
        player.hidden = false;
        player.play().catch(() => {});
      } catch (e) { banner(e.message); }
      previewBtn.disabled = false;
    } }, "Preview voice");
    const label = (p) => (p === "claude" ? "Claude" : "Codex") + (ready[p] ? "" : " (not installed)");
    const select = h("select", { onchange: (ev) => updateSettings({ provider: ev.target.value }) },
      ["claude", "codex"].map((p) => h("option", { value: p, selected: p === s.provider, disabled: !ready[p] }, label(p))));
    const auto = h("input", { type: "checkbox", checked: s.autopilot,
      onchange: (ev) => updateSettings({ autopilot: ev.target.checked }) });
    $("settings").replaceChildren(
      h("label", { class: "inline" }, "AI for this batch ", select),
      h("label", { class: "inline" }, "Voice ", pickVoice), previewBtn, player,
      h("label", { class: "inline" }, auto, " Keep going automatically after the scripts (footage, voice and render). I still approve the topics and the final batch myself."));
  }
  const saveVideos = (stage) => action(async () => {
    await api("PUT", `${base}/spec?stage=${stage}`, { videos: draft });
    dirty = false;
    toast("Edits saved.", "ok");
  });

  function select(step) {
    if (dirty && !confirm("You have unsaved edits. Leave this step without saving?")) return;
    selected = step;
    dirty = false;
    resetDraft();
    renderPanel(true);
    renderRail();
    nextBanner();
  }

  function reasonFor(step, extra) {
    if (busy()) return "A job is running - wait for it to finish.";
    if (step && data.steps[step] === "locked") return "Locked until the previous step is done.";
    return extra || "";
  }

  function actionButton(label, onclick, enabled, cls, why) {
    return h("button", { class: cls || "", onclick, disabled: !enabled, title: enabled ? null : (why || null) }, label);
  }

  const edit = (obj, key, parse, after) => (ev) => {
    obj[key] = parse ? parse(ev.target.value) : ev.target.value;
    dirty = true;
    if (after) after();
  };

  /* ---- topics */
  /* fatal = cannot be saved; missing = saved as a draft, researched before approval */
  const topicIssues = (v) => {
    const fatal = [], missing = [];
    if (!String(v.topic || "").trim()) fatal.push("Type the topic.");
    if (!HOOKS.includes(v.hook_type)) missing.push("hook type");
    if (!String(v.hook_text || "").trim()) missing.push("first spoken sentence");
    if (!isHttp((v.topic_sources || [])[0])) missing.push("source page");
    return { fatal, missing };
  };
  const validateTopic = (v) => { const i = topicIssues(v); return i.fatal; };
  const incompleteTopics = () => draft.filter((v) => topicIssues(v).missing.length > 0 && topicIssues(v).fatal.length === 0);
  const topicSaveStage = () => (draft.some((v) => topicIssues(v).missing.length) ? "draft" : "idea");

  function topicsPanel() {
    const approved = data.gates.idea === "approved";
    const theme = h("input", { value: lastParams.topics ? lastParams.topics.theme : "space facts", disabled: approved || busy() });
    const count = h("input", { type: "number", min: "1", max: "12", value: lastParams.topics ? lastParams.topics.count : "5", style: "width:80px", disabled: approved || busy() });
    const checks = [];
    let saveBtn = null;
    let researchAllBtn = null;
    let approveBtn = null;
    const refreshChecks = () => {
      let bad = false;
      checks.forEach((c) => {
        const issues = topicIssues(c.v);
        bad = bad || issues.fatal.length > 0;
        c.node.textContent = issues.fatal.join(" ");
        c.note.textContent = issues.missing.length && !issues.fatal.length ? "Not researched yet - still needs: " + issues.missing.join(", ") + "." : "";
        c.chip.textContent = c.v.hook_type || "no hook yet";
        c.chip.className = "chip " + (c.v.hook_type || "");
        const typed = !!String(c.v.topic || "").trim();
        c.research.disabled = busy() || !typed;
        c.research.title = busy() ? "A job is running - wait for it to finish." : typed ? "" : "Type the topic first.";
      });
      if (researchAllBtn) researchAllBtn.disabled = busy() || incompleteTopics().length === 0;
      if (approveBtn) approveBtn.disabled = busy() || dirty || draft.length === 0 || approved || incompleteTopics().length > 0 || bad;
      if (saveBtn) {
        saveBtn.disabled = busy() || bad || draft.length === 0;
        saveBtn.title = bad ? "Fix the highlighted topics first." : "";
      }
    };
    const out = [
      h("h2", {}, "Choose topics"),
      h("p", { class: "muted" }, "Ask Claude for topic ideas, edit them, then approve. Nothing is made until you approve."),
      h("div", { class: "row" },
        h("label", {}, "Theme", theme), h("label", {}, "How many", count),
        actionButton("Suggest topics", () => {
          if (draft.length && !confirm(`Replace the ${draft.length} topic(s) you have now with new suggestions?`)) return;
          runStep("topics", { theme: theme.value, count: Number(count.value), ...(draft.length ? { replace: true } : {}) });
        },
          canRun("topics") && !approved, "primary", approved ? "Topics are already approved." : reasonFor("topics"))),
    ];
    draft.forEach((v, i) => {
      const url = (v.topic_sources || [])[0] || "";
      const chip = h("span", { class: "chip " + v.hook_type }, v.hook_type);
      const node = h("div", { class: "error small" });
      const note = h("div", { class: "muted small" });
      const research = actionButton("Research this topic", () => researchTopics([v.id]), !busy() && !!String(v.topic || "").trim(), "primary",
        busy() ? reasonFor(null) : "Type the topic first.");
      checks.push({ v, node, note, chip, research });
      out.push(h("div", { class: "video" },
        h("h4", {}, v.id, chip),
        h("label", {}, "Topic", h("input", { value: v.topic || "", disabled: approved || busy(), oninput: edit(v, "topic", null, refreshChecks) })),
        h("div", { class: "grid2" },
          h("label", {}, "Hook type", h("select", { disabled: approved || busy(), onchange: edit(v, "hook_type", null, refreshChecks) },
            [h("option", { value: "", selected: !v.hook_type }, "(research will choose)")].concat(
              HOOKS.map((t) => h("option", { value: t, selected: t === v.hook_type }, t))))),
          h("label", {}, "Source page (link)", h("input", { value: url, disabled: approved || busy(),
            oninput: (ev) => { v.topic_sources = [ev.target.value]; dirty = true; refreshChecks(); } }))),
        h("label", {}, "First spoken sentence", h("input", { value: v.hook_text || "", disabled: approved || busy(), oninput: edit(v, "hook_text", null, refreshChecks) })),
        node, note,
        approved ? null : h("div", { class: "row" },
          research,
          actionButton("Remove", () => { draft.splice(i, 1); dirty = true; renderPanel(true); }, true))));
    });
    if (!approved) {
      saveBtn = actionButton("Save edits", () => saveVideos(topicSaveStage()), !busy() && draft.length > 0, "", reasonFor(null));
      researchAllBtn = actionButton("Research all incomplete", () => researchTopics(incompleteTopics().map((v) => v.id)),
        !busy() && incompleteTopics().length > 0, "", busy() ? reasonFor(null) : "Every topic already has sources.");
      out.push(h("div", { class: "row" },
        actionButton("Add topic", () => {
          const n = Math.max(0, ...draft.map((v) => Number(String(v.id).replace(/\D/g, "")) || 0)) + 1;
          draft.push({ id: "v" + n, topic: "", hook_type: "", hook_text: "", topic_sources: [] });
          dirty = true; renderPanel(true);
        }, !busy(), "", reasonFor(null)),
        researchAllBtn,
        saveBtn));
    }
    approveBtn = actionButton("Approve topics", () => approve("idea"), !busy() && !dirty && draft.length > 0 && !approved && incompleteTopics().length === 0, "gate",
        approved ? "Already approved." : dirty ? "Save your edits first." : !draft.length ? "Suggest or add topics first."
          : incompleteTopics().length ? "Research the topics that still need sources first." : reasonFor(null));
    out.push(h("div", { class: "gate-box" }, approveBtn,
      h("span", { class: "muted" }, approved ? "Approved - the scripts are written automatically." : dirty ? "Save your edits first."
        : "Your approval, not Claude's. Approving starts the scripts by itself.")));
    setTimeout(refreshChecks, 0);
    return out;
  }

  /* ---- script */
  function counterLine(getText, describe) {
    const el = h("div", { class: "counter" });
    const update = () => { const r = describe(getText()); el.textContent = r.text; el.className = "counter " + (r.level || ""); };
    update();
    return { el, update };
  }

  const VERDICT_LABEL = { supported: "supported", partly: "partly supported", unsupported: "NOT supported", unverifiable: "could not be checked" };

  function factNote() {
    const f = data.fact_check || {};
    if (f.error) return `The last fact check failed: ${f.error}`;
    if (!f.checked) return "No fact check yet - it runs by itself after the scripts are written.";
    const who = f.same_ai ? `${f.checker} (the same AI that wrote them - a different one would be a stronger check)` : f.checker;
    return `Checked by ${who}. The AI reads the cited pages; you decide what to keep.`;
  }

  function claimRows(v) {
    const f = ((data.fact_check || {}).videos || {})[v.id];
    const rows = [h("div", { class: "muted small" }, "Claims and fact check:")];
    if (f && f.stale) rows.push(h("div", { class: "counter warn" }, "The script changed after the last fact check - run it again."));
    (v.claims || []).forEach((c, i) => {
      const verdict = f && !f.stale ? (f.claims || []).find((x) => x.index === i) : null;
      rows.push(h("div", { class: "claim" },
        verdict ? h("span", { class: "verdict " + verdict.verdict }, VERDICT_LABEL[verdict.verdict] || verdict.verdict) : null,
        " ", c.text, " - ",
        (c.sources || []).map((s) => (isHttp(s) ? h("a", { href: s, target: "_blank", rel: "noopener" }, s) : h("span", {}, s))),
        verdict && verdict.note ? h("div", { class: "muted small" }, verdict.note + (verdict.quote ? ` - "${verdict.quote}"` : "")) : null));
    });
    if (f && !f.stale && (f.issues || []).length) {
      rows.push(h("div", { class: "counter warn" }, "The narration says things no claim covers: " + f.issues.join(" | ")));
    }
    return h("div", { class: "factbox" }, rows);
  }

  function scriptPanel() {
    const limits = data.limits || { max_description: 2000, max_hashtags: 30 };
    const out = [h("h2", {}, "Scripts"),
      h("p", { class: "muted" }, "Claude writes narration with a source for every claim. Read and edit before moving on."),
      actionButton("Write scripts", () => runStep("script"), canRun("script"), "primary", reasonFor("script"))];
    draft.forEach((v) => {
      const nar = counterLine(() => v.narration, (t) => {
        const { words, seconds } = estimateSeconds(t);
        const level = seconds < 18 || seconds > 55 ? "warn" : "";
        return { text: `${words} words - about ${seconds} s (aim for 18 to 55 s)`, level };
      });
      const title = counterLine(() => v.title, (t) => ({ text: `${String(t || "").length} / 100 characters`, level: String(t || "").length > 100 ? "over" : "" }));
      const desc = counterLine(() => v.description, (t) => {
        const len = String(t || "").length;
        const tags = ((String(t || "").match(/#\w+/g) || []).length) + 1;
        const over = len > limits.max_description || tags > limits.max_hashtags;
        return { text: `${len} / ${limits.max_description} characters, ${tags} / ${limits.max_hashtags} hashtags (one is added at export)`, level: over ? "over" : "" };
      });
      out.push(h("div", { class: "video" },
        h("h4", {}, `${v.id}: ${v.topic || ""}`),
        h("label", {}, "Narration", h("textarea", { oninput: edit(v, "narration", null, nar.update) }, v.narration || "")), nar.el,
        h("label", {}, "Title", h("input", { value: v.title || "", oninput: edit(v, "title", null, title.update) })), title.el,
        h("label", {}, "Description", h("textarea", { oninput: edit(v, "description", null, desc.update) }, v.description || "")), desc.el,
        h("label", {}, "Tags (comma separated)", h("input", { value: (v.tags || []).join(", "),
          oninput: edit(v, "tags", (s) => s.split(",").map((t) => t.trim()).filter(Boolean)) })),
        claimRows(v)));
    });
    out.push(h("div", { class: "row" },
      actionButton("Fact-check again", () => runStep("script", { factcheck: true }), canRun("script") && draft.length > 0 && !dirty, "",
        dirty ? "Save your edits first." : reasonFor("script")),
      h("span", { class: "muted small" }, factNote())));
    out.push(actionButton("Save edits", () => saveVideos("script"), canRun("script") && draft.length > 0 && dirty, "",
      dirty ? reasonFor("script") : "Nothing to save yet."));
    return out;
  }

  /* ---- footage */
  function cutProblems(videos, info) {
    const problems = {};
    const bySource = {};
    videos.forEach((v, vi) => (v.cuts || []).forEach((c, ci) => {
      const start = Number(c.in), len = Number(c.dur);
      const id = `${vi}:${ci}`;
      if (!(start >= 0) || !(len > 0)) { problems[id] = "Needs a start of 0 or more and a length above 0."; return; }
      const meta = info[c.source];
      if (meta && start + len > meta.duration + 0.05) problems[id] = `Runs past the end of the clip (${meta.duration.toFixed(1)} s long).`;
      (bySource[c.source] = bySource[c.source] || []).push({ id, start, end: start + len, who: v.id });
    }));
    Object.values(bySource).forEach((segs) => {
      segs.sort((a, b) => a.start - b.start);
      for (let i = 1; i < segs.length; i++) {
        if (segs[i].start < segs[i - 1].end) {
          problems[segs[i].id] = problems[segs[i].id] || `Overlaps a cut of ${segs[i - 1].who} on the same clip.`;
          problems[segs[i - 1].id] = problems[segs[i - 1].id] || `Overlaps a cut of ${segs[i].who} on the same clip.`;
        }
      }
    });
    return problems;
  }

  function usageBar(key, videos, info, problems) {
    const meta = info[key];
    let total = meta ? meta.duration : 0;
    const segs = [];
    videos.forEach((v, vi) => (v.cuts || []).forEach((c, ci) => {
      if (c.source !== key) return;
      segs.push({ vi, ci, start: Number(c.in) || 0, len: Number(c.dur) || 0, who: v.id });
      if (!meta) total = Math.max(total, (Number(c.in) || 0) + (Number(c.dur) || 0));
    }));
    total = total || 1;
    const bar = h("div", { class: "usage", title: meta ? `${meta.duration.toFixed(0)} s clip` : "clip length unknown" });
    segs.forEach((s) => bar.append(h("div", {
      class: `seg seg${s.vi % SEG_COLORS}` + (problems[`${s.vi}:${s.ci}`] ? " bad" : ""),
      style: `left:${(s.start / total) * 100}%;width:${Math.max(0.4, (s.len / total) * 100)}%`,
      title: `${s.who}: ${s.start.toFixed(1)} to ${(s.start + s.len).toFixed(1)} s`,
    })));
    return bar;
  }

  function footagePanel() {
    const keys = Object.keys((data.spec.sources) || {});
    const info = data.source_info || {};
    const rows = {};
    const hosts = {};
    let saveBtn = null;
    const refreshViz = () => {
      const problems = cutProblems(draft, info);
      keys.forEach((k) => hosts[k] && hosts[k].replaceChildren(usageBar(k, draft, info, problems)));
      Object.entries(rows).forEach(([id, tr]) => { tr.className = problems[id] ? "bad" : ""; tr.title = problems[id] || ""; });
      if (saveBtn) {
        saveBtn.disabled = busy() || !dirty || Object.keys(problems).length > 0;
        saveBtn.title = Object.keys(problems).length ? "Fix the highlighted cuts first." : "";
      }
    };
    const out = [h("h2", {}, "Footage"),
      h("p", { class: "muted" }, "Claude finds free clips (size-capped), looks at contact sheets and proposes cuts. Adjust the cut times if a shot is wrong. Coloured bars show which seconds of each clip are used; red means a problem."),
      actionButton("Find footage", () => runStep("footage"), canRun("footage"), "primary", reasonFor("footage"))];
    out.push(h("div", { class: "legend" }, draft.map((v, vi) => h("span", {}, h("i", { class: `seg${vi % SEG_COLORS}` }), v.id))));
    keys.forEach((k) => {
      const meta = info[k];
      const step = meta ? meta.sheet_step : 4;
      const label = h("span", { class: "sheet-time", hidden: true });
      const img = h("img", { class: "sheet", src: `/media/${encodeURIComponent(batchId)}/review/sheet_${encodeURIComponent(k)}.png`,
        alt: "contact sheet " + k, onerror: (ev) => { ev.target.hidden = true; },
        onmousemove: (ev) => {
          const r = ev.target.getBoundingClientRect();
          const col = Math.min(5, Math.floor(((ev.clientX - r.left) / r.width) * 6));
          const row = Math.min(4, Math.floor(((ev.clientY - r.top) / r.height) * 5));
          label.hidden = false;
          label.textContent = `about ${(row * 6 + col) * step} s`;
        },
        onmouseleave: () => { label.hidden = true; } });
      const host = h("div", {});
      hosts[k] = host;
      out.push(h("div", {}, h("div", { class: "muted" }, `${k}${meta ? ` - ${meta.duration.toFixed(0)} s long, one frame every ${step} s (hover the sheet for the time)` : ""}`),
        h("div", { class: "sheet-wrap" }, img, label), host));
    });
    draft.forEach((v, vi) => {
      v.cuts = v.cuts || [];
      const trs = v.cuts.map((c, ci) => {
        const tr = h("tr", {},
          h("td", {}, h("select", { onchange: edit(c, "source", null, refreshViz) }, keys.map((k) => h("option", { value: k, selected: k === c.source }, k)))),
          h("td", {}, h("input", { type: "number", step: "0.1", value: c.in, oninput: edit(c, "in", Number, refreshViz) })),
          h("td", {}, h("input", { type: "number", step: "0.1", value: c.dur, oninput: edit(c, "dur", Number, refreshViz) })),
          h("td", {}, actionButton("x", () => { v.cuts.splice(ci, 1); dirty = true; renderPanel(true); }, true)));
        rows[`${vi}:${ci}`] = tr;
        return tr;
      });
      const total = v.cuts.reduce((a, c) => a + (Number(c.dur) || 0), 0);
      const narration = estimateSeconds(v.narration).seconds;
      out.push(h("div", { class: "video" }, h("h4", {}, `${v.id}: ${v.topic || ""}`),
        v.redo_note ? h("div", { class: "muted" }, "Redo note: " + v.redo_note) : null,
        h("div", { class: "muted small" }, `${total.toFixed(1)} s of footage` + (narration ? `, narration about ${narration} s` : "")),
        h("table", { class: "cuts" }, h("tr", {}, h("th", {}, "Source"), h("th", {}, "Start (s)"), h("th", {}, "Length (s)"), h("th", {})), trs),
        actionButton("Add cut", () => { if (keys.length) { v.cuts.push({ source: keys[0], in: 0, dur: 3 }); dirty = true; renderPanel(true); } }, keys.length > 0)));
    });
    saveBtn = actionButton("Save cuts", () => saveVideos("visuals"), canRun("footage") && draft.length > 0 && dirty, "", dirty ? reasonFor("footage") : "Nothing to save yet.");
    out.push(saveBtn);
    setTimeout(refreshViz, 0);
    return out;
  }

  /* ---- render, review, export */
  function renderPanelBody() {
    const vids = data.spec.videos || [];
    const made = vids.filter((v) => data.renders && data.renders[v.id]).length;
    const out = [h("h2", {}, "Voice and render"),
      h("p", { class: "muted" }, "Narrates every video and renders 1080x1920 files, only the ones that changed. This takes a few minutes per video."),
      actionButton("Make videos", () => runStep("render"), canRun("render"), "primary", reasonFor("render")),
      h("p", {}, h("span", { class: "progress" }, h("span", { style: `width:${vids.length ? (made / vids.length) * 100 : 0}%` })), `  ${made} of ${vids.length} videos rendered`)];
    vids.forEach((v) => out.push(h("div", {}, `${v.id}: ${v.topic || ""} - `,
      h("strong", {}, data.renders && data.renders[v.id] ? "rendered" : "not rendered"))));
    if (data.steps.render === "stale") out.push(h("p", { class: "error" }, "Something changed after the last render. Make the videos again."));
    return out;
  }

  function reviewPanel() {
    const out = [h("h2", {}, "Review"),
      h("p", { class: "muted" }, "Watch every video with sound. Redo any you do not like, then approve the batch."),
      actionButton("Prepare review", () => runStep("review"), canRun("review") || (data.steps.render === "done" && !busy()), "primary",
        reasonFor("review"))];
    (data.spec.videos || []).forEach((v) => {
      if (!(data.renders && data.renders[v.id])) return;
      const note = h("input", { placeholder: "What is wrong with it?" });
      out.push(h("div", { class: "video" },
        h("h4", {}, `${v.id}: ${v.title || v.topic || ""}`),
        h("video", { controls: true, preload: "metadata", src: `/media/${encodeURIComponent(batchId)}/renders/${encodeURIComponent(v.id)}.mp4?v=${data.renders_stamp || ""}` }),
        h("div", { class: "row" }, note, actionButton("Redo this video", () => {
          if (confirm(`Redo ${v.id}? Its cuts and render are cleared.`)) action(() => api("POST", `${base}/videos/${encodeURIComponent(v.id)}/redo`, { note: note.value || "redo" }));
        }, !busy(), "", reasonFor(null)))));
    });
    const ready = !busy() && data.steps.render === "done" && data.gates.review_queue === "awaiting";
    out.push(h("div", { class: "gate-box" },
      actionButton("Approve batch", () => approve("review_queue"), ready, "gate",
        busy() ? reasonFor(null) : data.steps.render !== "done" ? "Make the videos first." : "Run Prepare review first."),
      h("span", { class: "muted" }, data.gates.review_queue === "approved" ? "Approved." : "Run Prepare review first. Your approval, not Claude's.")));
    return out;
  }

  function exportPanel() {
    return [h("h2", {}, "Export"),
      h("p", { class: "muted" }, "Writes each approved video with title, description and hashtag, private. Nothing is uploaded."),
      actionButton("Export (private)", () => runStep("export"), canRun("export"), "primary", reasonFor("export")),
      h("p", {}, "Folder: ", h("code", {}, `projects/${batchId}/export/`)),
      h("ul", { class: "list" }, (data.exports || []).map((e) => h("li", {}, e)))];
  }

  const PANELS = { topics: topicsPanel, script: scriptPanel, footage: footagePanel, render: renderPanelBody, review: reviewPanel, export: exportPanel };

  /* ---- chrome: rail, next-step banner, title, log */
  function renderRail() {
    $("rail").replaceChildren(...STEPS.map(([key, label]) => h("button", {
      class: "step" + (key === selected ? " active" : ""), onclick: () => select(key) },
      h("span", {}, label),
      h("span", { class: "badge " + data.steps[key] }, data.steps[key] === "running" ? h("span", { class: "spinner", style: "margin:0 4px 0 0" }) : null, data.steps[key]))));
  }

  function nextBanner() {
    const n = data.next;
    const box = $("next");
    box.className = "next " + n.kind;
    let text = n.text;
    if (n.kind === "running" && data.running && data.running.step === "render") {
      const vids = data.spec.videos || [];
      text += ` (${vids.filter((v) => data.renders && data.renders[v.id]).length} of ${vids.length} videos rendered so far)`;
    }
    const parts = [h("div", {}, n.kind === "running" ? h("span", { class: "spinner" }) : null, h("strong", {}, text))];
    if (n.step !== selected) parts.push(actionButton("Go to " + STEP_LABEL[n.step], () => select(n.step), true, "primary"));
    box.replaceChildren(...parts);
  }

  function updateTitle() {
    const k = data.next.kind;
    document.title = (k === "running" ? "(working) " : k === "failed" ? "(failed) " : k === "done" ? "(done) " : "") + data.id + " - Studio";
  }

  function resetDraft() { draft = JSON.parse(JSON.stringify(data.spec.videos || [])); }

  function renderPanel(force) {
    if (!data) return;
    if (dirty && !force) return;
    $("panel").replaceChildren(...PANELS[selected]());
  }

  function retryJob(job) {
    const argv = job.argv || [];
    const i = argv.indexOf("--approve");
    const research = /^<topics:research:(.+)>$/.exec(argv[0] || "");
    if (i >= 0) approve(argv[i + 1]);
    else if (research) researchTopics(research[1].split(","));
    else if (job.step === "topics" && !lastParams.topics) banner("Open the Topics step and use Suggest topics or Research again - Studio no longer remembers what that run was for.");
    else runStep(job.step, lastParams[job.step]);
  }

  async function renderLog() {
    const job = (data.jobs || [])[0];
    $("job-status").textContent = job ? `${STEP_LABEL[job.step] || job.step} - ${job.status}` : "";
    const retry = $("job-retry");
    retry.hidden = !(job && job.status === "failed" && !busy());
    retry.textContent = job ? "Retry " + (STEP_LABEL[job.step] || job.step).toLowerCase() : "Retry";
    retry.onclick = () => retryJob(job);
    tickElapsed();
    if (!job) return;
    try {
      const res = await api("GET", `${base}/jobs/${encodeURIComponent(job.id)}/log?lines=80`);
      const pre = $("job-log");
      pre.textContent = res.log || "(no output yet)";
      pre.scrollTop = pre.scrollHeight;
    } catch (e) { /* log not written yet */ }
  }

  function tickElapsed() {
    $("job-elapsed").textContent = data && data.running ? `running for ${elapsed(data.running.started)}` : "";
  }

  function announceFinished() {
    const job = (data.jobs || [])[0] || null;
    if (prevJob && job && prevJob.id === job.id && prevJob.status === "running" && job.status !== "running") {
      toast(`${STEP_LABEL[job.step] || job.step} ${job.status === "ok" ? "finished." : "failed - see the log."}`, job.status === "ok" ? "ok" : "bad");
    }
    prevJob = job ? { id: job.id, status: job.status } : null;
  }

  async function refresh(force) {
    data = await api("GET", base);
    $("batch-title").textContent = data.id;
    $("batch-platform").textContent = data.platform;
    if (!selected) selected = data.next.kind === "done" ? "export" : data.next.step;
    announceFinished();
    updateTitle();
    renderSettings();
    if (data.code_stale && $("banner").hidden) {
      banner("Studio was updated after this copy started, so this page may be running old code. Close the Studio window and start it again (Studio.bat).");
    }
    const signature = JSON.stringify([data.steps, data.gates, data.spec, data.running, (data.jobs || [])[0], data.renders, data.exports, data.next]);
    if (force || signature !== lastSignature) {
      lastSignature = signature;
      if (!dirty) resetDraft();
      renderRail();
      nextBanner();
      renderPanel(!!force && !dirty);
    }
    await renderLog();
  }

  const events = new EventSource(`${base}/events`);
  events.onmessage = () => refresh(false).catch((e) => banner(e.message));
  setInterval(() => { if (data && data.running) refresh(false).catch(() => {}); }, 4000);
  setInterval(() => { if (data) tickElapsed(); }, 1000);
  refresh(true).catch((e) => banner(e.message));
}

if (document.body.dataset.page === "home") initHome();
if (document.body.dataset.page === "batch") initBatch();
