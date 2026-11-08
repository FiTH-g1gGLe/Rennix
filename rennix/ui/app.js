/* Rennix app: talks to the Python engine through pywebview.api */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (id, cls = "ic") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"></use></svg>`;
const tip = (title, body = "", extra = "") => esc([title, body, extra].join("|"));
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const fmtMin = (m) => { if (!m) return ""; m = Math.round(m); return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`; };
const sxe = (m) => m && m.season != null && m.episodes && m.episodes.length
  ? `S${String(m.season).padStart(2, "0")}E${String(m.episodes[0]).padStart(2, "0")}${m.episodes.length > 1 ? "-E" + String(m.episodes[m.episodes.length - 1]).padStart(2, "0") : ""}` : "";
const initials = (t) => (t || "?").replace(/^(the|a|an) /i, "").split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase() || "?";
const shortFolder = (f) => { if (!f) return ""; const parts = f.split(/[\\/]/).filter(Boolean); return parts.slice(-2).join(" / ") + " /"; };
let api = null;
// Poster with lettered fallback: if the image can't load (offline), the letters show instead.
const posterHtml = (url, title, cls) => `<div class="poster ${cls}">${esc(initials(title))}${url ? `<img src="${esc(url)}" alt="" loading="lazy" onerror="this.remove()">` : ""}</div>`;

const S = {
  settings: null, formats: {}, tokens: [], version: "",
  items: new Map(), order: [], previews: {}, selected: null, filter: null,
  mode: "auto", busy: false, lastBatch: null, platform: "", view: "list",
};

// ------------------------------------------------------------------ tooltips
const tipEl = () => $("#tip");
let tipTimer = null, tipTarget = null;
function showTip(el) {
  const raw = el.getAttribute("data-tip");
  if (!raw) return;
  const [title, body = "", extra = ""] = raw.split("|");
  const lines = body.split("\n").filter(Boolean).map((l) => {
    const cls = l.startsWith("✓") ? "ok" : l.startsWith("✗") ? "bad" : l.startsWith("⚠") ? "warn" : "";
    return `<span class="${cls}" style="display:block">${esc(l)}</span>`;
  }).join("");
  const t = tipEl();
  t.innerHTML = `<b>${esc(title)}</b>${lines}${extra ? `<span class="x">${esc(extra)}</span>` : ""}`;
  const r = el.getBoundingClientRect();
  t.style.left = "0px"; t.style.top = "0px"; t.classList.add("show");
  const tw = t.offsetWidth, th = t.offsetHeight;
  let left = Math.min(Math.max(8, r.left + r.width / 2 - tw / 2), window.innerWidth - tw - 8);
  let top = r.bottom + 10;
  if (top + th > window.innerHeight - 8) top = r.top - th - 10;
  t.style.left = `${left}px`; t.style.top = `${Math.max(8, top)}px`;
}
function hideTip() { clearTimeout(tipTimer); tipTarget = null; tipEl().classList.remove("show"); }
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest("[data-tip]");
  if (el === tipTarget) return;
  hideTip();
  if (!el) return;
  tipTarget = el;
  tipTimer = setTimeout(() => showTip(el), 400);
});
document.addEventListener("focusin", (e) => { const el = e.target.closest("[data-tip]"); if (el) { tipTarget = el; showTip(el); } });
document.addEventListener("focusout", hideTip);
document.addEventListener("mousedown", hideTip);
document.addEventListener("scroll", hideTip, true);

// ------------------------------------------------------------------ toasts and banners
function toast(html, opts = {}) {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<span>${html}</span>`;
  if (opts.action) {
    const b = document.createElement("button");
    b.textContent = opts.action.label;
    b.onclick = () => { opts.action.fn(); el.remove(); };
    el.appendChild(b);
  }
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), opts.timeout || 6000);
}

function banner(id, cls, html, buttons = []) {
  removeBanner(id);
  const el = document.createElement("div");
  el.className = `banner ${cls}`;
  el.id = `banner-${id}`;
  el.innerHTML = `<span class="grow">${html}</span>`;
  for (const [label, fn, primary] of buttons) {
    const b = document.createElement("button");
    b.className = `btn small${primary ? " primary" : ""}`;
    b.textContent = label;
    b.onclick = fn;
    el.appendChild(b);
  }
  $("#banners").appendChild(el);
}
function removeBanner(id) { $(`#banner-${id}`)?.remove(); }

// ------------------------------------------------------------------ engine events
window.rennix = {
  on(event, data) { try { (handlers[event] || (() => {}))(data); } catch (e) { console.error(e); } },
};

const handlers = {
  status(d) { setStatus(d.text, true); },
  scanned(d) {
    for (const f of d.files) {
      if (!S.items.has(f.id)) S.order.push(f.id);
      S.items.set(f.id, f);
    }
    showScreen(S.order.length ? "main" : "start");
    renderAll();
    if (d.files.length) setStatus(`Matching ${d.files.length} file${d.files.length === 1 ? "" : "s"}…`, true);
    const notes = [];
    if (d.skipped) notes.push(`${d.skipped} sample file${d.skipped === 1 ? "" : "s"} skipped`);
    if (d.junk) notes.push(`${d.junk} junk file${d.junk === 1 ? "" : "s"} found`);
    if (d.errors && d.errors.length) notes.push(`${d.errors.length} couldn't be read`);
    if (!d.files.length) toast("🤔 No new videos found there.");
    else if (notes.length) toast(`📂 ${esc(notes.join(" · "))}`);
  },
  result(item) {
    const cur = S.items.get(item.id);
    if (!cur) S.order.push(item.id);
    S.items.set(item.id, item);
    if (item.preview) S.previews[item.id] = item.preview;
    renderRow(item.id);
    renderChips();
    if (S.selected === item.id) renderSide();
  },
  done(d) {
    if (d.previews) S.previews = { ...S.previews, ...d.previews };
    setStatus("");
    renderAll();
    const c = counts();
    if (d.count) toast(`✅ ${c.green} matched · ⚠️ ${c.amber} to check · ❌ ${c.red} no match`);
  },
  rename_progress(p) { setStatus(`Renaming ${p.done} of ${p.total}…`, true, p.done / p.total); },
  renamed(s) {
    setStatus("");
    S.busy = false;
    for (const id of s.removed_ids || []) { S.items.delete(id); delete S.previews[id]; }
    S.order = S.order.filter((id) => S.items.has(id));
    if (S.selected && !S.items.has(S.selected)) closeSide();
    renderAll();
    if (!S.order.length) showScreen("start");
    if (s.message) toast(`ℹ️ ${esc(s.message)}`);
    if (s.done) {
      S.lastBatch = s.batch_id;
      toast(`🎉 ${s.done} file${s.done === 1 ? "" : "s"} renamed${s.companions ? ` (+${s.companions} extra file${s.companions === 1 ? "" : "s"})` : ""}`,
        { action: { label: "Undo", fn: () => undoBatch(s.batch_id) }, timeout: 10000 });
    }
    for (const e of s.errors || []) toast(`❌ ${esc(fileName(e.src))}: ${esc(e.message)}`, { timeout: 9000, action: { label: "Details", fn: openConsole } });
    for (const p of (s.problems || []).filter((p) => p.level === "conflict" || p.level === "error")) {
      toast(`⚔️ ${esc(nameOf(p.file_id))}: ${esc(p.message)}`, { timeout: 9000 });
    }
    refreshStats();
  },
  error(d) { setStatus(""); S.busy = false; toast(`❌ ${esc(d.message)}`, { timeout: 10000, action: { label: "Details", fn: openConsole } }); },
};

function historyLine(b) {
  const total = b.count + b.undone, n = `${total} file${total === 1 ? "" : "s"}`;
  if (!b.undone) return `${n} renamed`;
  if (!b.count) return `↩️ ${n} renamed, then put back`;
  return `${n} renamed · ${b.undone} put back`;
}
const fileName = (p) => (p || "").split(/[\\/]/).pop();
const nameOf = (id) => (S.items.get(id) || {}).name || "A file";

function setStatus(text, spin = false, progress = null) {
  const el = $("#status-line");
  if (!el) return;
  if (!text) { el.innerHTML = ""; return; }
  el.innerHTML = `${spin ? '<span class="spinner"></span>' : ""}${esc(text)}${progress != null ? `<span class="progress"><span style="width:${Math.round(progress * 100)}%"></span></span>` : ""}`;
}

// ------------------------------------------------------------------ screens
function showScreen(name) {
  $("#screen-start").classList.toggle("hidden", name !== "start");
  $("#screen-main").classList.toggle("hidden", name !== "main");
}

function counts() {
  const c = { green: 0, amber: 0, red: 0, pending: 0 };
  for (const id of S.order) { const it = S.items.get(id); if (it) c[it.status in c ? it.status : "pending"]++; }
  return c;
}

function renderChips() {
  const c = counts();
  const chip = (key, cls, ic, label, tipText) => c[key] ? `<button class="chip ${cls}${S.filter === key ? " on" : ""}" data-filter="${key}" data-tip="${tipText}">${icon(ic)}${c[key]} ${label}</button>` : "";
  $("#chips").innerHTML =
    chip("green", "green", "check-c", "matched", tip("✅ Matched", "Rennix is confident about these.", "Click to show only these.")) +
    chip("amber", "amber", "alert", "to check", tip("⚠️ Needs a quick check", "Rennix found a likely match but wants you to confirm.", "Click a row to see the options.")) +
    chip("red", "red", "x-c", "no match", tip("❌ No match", "These won't be renamed until you pick a match.", "Click a row to search manually.")) +
    chip("pending", "", "clock", "searching", tip("🔎 Searching", "Still looking these up.")) +
    `<span class="status-line" id="status-line"></span>` +
    `<div class="seg view-seg" role="group" aria-label="View">${[
      ["list", "list", "List", tip("☰ List", "Every file with its old and new name.")],
      ["posters", "grid", "Posters", tip("🖼️ Posters", "Your matches as a poster wall. Spot wrong matches at a glance.")],
      ["folders", "tree", "Folders", tip("📁 Folders", "How your library will look after renaming, folder by folder.")],
    ].map(([k, ic, l, t]) => `<button data-view="${k}" class="${S.view === k ? "on" : ""}" data-tip="${t}">${icon(ic)}${l}</button>`).join("")}</div>`;
  for (const b of $$("#chips [data-filter]")) b.onclick = () => { S.filter = S.filter === b.dataset.filter ? null : b.dataset.filter; renderAll(); };
  for (const b of $$("#chips [data-view]")) b.onclick = () => setView(b.dataset.view);
  const total = S.order.length;
  const size = S.order.reduce((a, id) => a + ((S.items.get(id) || {}).size || 0), 0);
  $("#count span").textContent = `${total} file${total === 1 ? "" : "s"}${size ? " · " + humanSize(size) : ""}`;
  const n = c.green + ($("#chk-amber").checked ? c.amber : 0);
  const btn = $("#btn-rename");
  btn.querySelector("span").textContent = n ? `Rename ${n} file${n === 1 ? "" : "s"}` : "Rename";
  btn.disabled = !n || S.busy;
  btn.setAttribute("data-tip", tip(`✏️ Rename ${n} file${n === 1 ? "" : "s"}`,
    c.amber && !$("#chk-amber").checked ? "Amber and red files are skipped until you check them." : "Red files are skipped until you pick a match.",
    "⌨️ Shortcut: Ctrl + Enter · Every rename can be undone."));
}

function humanSize(n) {
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let s = n;
  while (s >= 1024 && i < u.length - 1) { s /= 1024; i++; }
  return `${i >= 2 ? s.toFixed(1) : Math.round(s)} ${u[i]}`;
}

function statusTip(it) {
  const labels = { green: "✅ Matched", amber: "⚠️ Needs a quick check", red: "❌ No match", pending: "🔎 Searching…" };
  const title = `${labels[it.status] || labels.pending}${it.status === "green" || it.status === "amber" ? ` · ${pct(it.confidence)} confident` : ""}`;
  const body = (it.reasons || []).join("\n") || (it.message || "");
  const extra = it.status === "pending" ? "" : it.status === "red" ? "👆 Click the row to search manually." : "👆 Click the row to change the match.";
  return tip(title, body, extra);
}

function rowHtml(it) {
  const st = it.status || "pending";
  const label = { green: "Matched", amber: "Check", red: "No match", pending: "Searching…" }[st] || "Searching…";
  const pv = S.previews[it.id] || it.preview || null;
  const m = it.match;
  let newName, sub;
  if (st === "pending") { newName = `<div class="newname dim">…</div>`; sub = ""; }
  else if (!m) { newName = `<div class="newname dim">Not renamed</div>`; sub = `<div class="subtle">${esc(it.message || "Click to search manually")}</div>`; }
  else if (pv && pv.same) { newName = `<div class="newname dim">Already named correctly</div>`; sub = ""; }
  else if (pv && pv.name) { newName = `<div class="newname" title="${esc(pv.full || "")}">${esc(pv.name)}</div>`; sub = `<div class="subtle">${esc(pv.problem ? "⚔️ " + pv.problem : shortFolder(pv.folder))}</div>`; }
  else { newName = `<div class="newname dim">…</div>`; sub = pv && pv.problem ? `<div class="subtle">⚔️ ${esc(pv.problem)}</div>` : ""; }
  let matched = `<div class="matched"><div class="poster">?</div><div class="mt"><b class="muted">—</b><div class="subtle">${st === "pending" ? "Looking it up" : "Nothing found"}</div></div></div>`;
  if (m) {
    const poster = posterHtml(m.poster_url, m.title, "");
    const meta = m.kind === "movie" ? `Movie · ${m.year || "?"} · ${pct(it.confidence)}` : `TV · ${sxe(m) || "?"} · ${pct(it.confidence)}`;
    matched = `<div class="matched">${poster}<div class="mt"><b>${esc(m.title)}${m.year ? ` (${m.year})` : ""}</b><div class="subtle">${esc(meta)}</div></div></div>`;
  }
  return `<div class="row${S.selected === it.id ? " sel" : ""}" role="row" tabindex="0" data-id="${esc(it.id)}">
    <div class="status st-${st}" data-tip="${statusTip(it)}"><span class="dot"></span>${label}</div>
    <div class="fname" title="${esc(it.path || "")}">${esc(it.name)}</div>
    ${icon("arrow", "ic muted")}
    <div style="min-width:0">${newName}${sub}</div>
    ${matched}
  </div>`;
}

function visible(it) { return !S.filter || (it.status || "pending") === S.filter; }

const EMPTY = `<div style="padding:24px;color:var(--muted)">No files to show.</div>`;

function cardHtml(it) {
  const st = it.status || "pending";
  const m = it.match;
  const label = { green: "Matched", amber: "Check", red: "No match", pending: "Searching…" }[st];
  const pv = S.previews[it.id] || it.preview || {};
  const img = m && m.poster_url ? `<img src="${esc(m.poster_large || m.poster_url)}" alt="" loading="lazy" onerror="this.remove()">` : "";
  const sub = m ? (m.kind === "episode" ? `${sxe(m)}${m.episode_titles && m.episode_titles[0] ? " · " + m.episode_titles[0] : ""}` : (pv.name || "")) : it.name;
  return `<button class="pcard${S.selected === it.id ? " sel" : ""}" data-id="${esc(it.id)}">
    <div class="pimg">${esc(m ? initials(m.title) : "?")}${img}<span class="pdot st-${st}"><span class="dot"></span>${label}</span>${m && (st === "green" || st === "amber") ? `<span class="pconf">${pct(it.confidence)}</span>` : ""}</div>
    <div class="pt">${esc(m ? m.title + (m.year ? ` (${m.year})` : "") : it.name)}</div>
    <div class="ps">${esc(sub || it.message || "")}</div></button>`;
}

function foldersHtml(items) {
  const groups = new Map();
  for (const it of items) {
    const pv = S.previews[it.id] || it.preview || {};
    const key = it.match && pv.folder ? pv.folder : (it.status === "pending" ? "⏳ Still searching" : "🚫 Not renamed");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(it);
  }
  const keys = [...groups.keys()].sort((a, b) => (a.startsWith("⏳") || a.startsWith("🚫")) - (b.startsWith("⏳") || b.startsWith("🚫")) || a.localeCompare(b));
  return `<div class="tree">${keys.map((k) => {
    const list = groups.get(k);
    return `<div class="tgroup"><div class="thead2">${icon("folder")}<span>${esc(k)}</span><span class="count">${list.length} file${list.length === 1 ? "" : "s"}</span></div>
      ${list.map((it) => {
        const pv = S.previews[it.id] || it.preview || {};
        const st = it.status || "pending";
        const nn = pv.same ? "Already named correctly" : (pv.name || (it.match ? "…" : it.name));
        return `<div class="tfile${S.selected === it.id ? " sel" : ""}" data-id="${esc(it.id)}"><span class="status st-${st}"><span class="dot"></span></span>
          <span class="nn">${esc(nn)}</span><span class="on">← ${esc(it.name)}</span></div>`;
      }).join("")}</div>`;
  }).join("")}</div>`;
}

function setView(view) {
  S.view = view;
  hideHover();
  saveSettings({ general: { view } });
  renderAll();
}

function renderAll() {
  const items = S.order.map((id) => S.items.get(id)).filter((it) => it && visible(it));
  const list = S.view === "list";
  $(".table").classList.toggle("hidden", !list);
  $("#altview").classList.toggle("hidden", list);
  if (list) $("#rows").innerHTML = items.map(rowHtml).join("") || EMPTY;
  else if (S.view === "posters") $("#altview").innerHTML = items.length ? `<div class="pgrid">${items.map(cardHtml).join("")}</div>` : EMPTY;
  else $("#altview").innerHTML = items.length ? foldersHtml(items) : EMPTY;
  renderChips();
}

let renderQueued = false;
function scheduleRender() { if (renderQueued) return; renderQueued = true; requestAnimationFrame(() => { renderQueued = false; renderAll(); }); }

function renderRow(id) {
  if (S.view !== "list") { scheduleRender(); return; }
  const it = S.items.get(id);
  if (!it) return;
  const el = $(`#rows .row[data-id="${CSS.escape(id)}"]`);
  if (!visible(it)) { el?.remove(); return; }
  if (el) el.outerHTML = rowHtml(it);
  else renderAll();
}

$("#rows").addEventListener("click", (e) => { const r = e.target.closest(".row"); if (r) openSide(r.dataset.id); });
$("#altview").addEventListener("click", (e) => { const r = e.target.closest("[data-id]"); if (r) { hideHover(); openSide(r.dataset.id); } });

// ------------------------------------------------------------------ quick preview on hover
const HC = { timer: null, id: null, x: 0, y: 0 };
function hideHover() { clearTimeout(HC.timer); HC.id = null; $("#hovercard").classList.remove("show"); }

function hoverHtml(it) {
  const m = it.match;
  const st = it.status || "pending";
  const pv = S.previews[it.id] || it.preview || {};
  const label = { green: "✅ Matched", amber: "⚠️ Needs a quick check", red: "❌ No match", pending: "🔎 Searching…" }[st];
  const reasons = (it.reasons || []).slice(0, 5).map((r) => {
    const cls = r.startsWith("✓") ? "ok" : r.startsWith("✗") ? "bad" : r.startsWith("⚠") ? "warn" : "";
    return `<li class="${cls}">${esc(r)}</li>`;
  }).join("");
  if (!m) {
    const p = it.parsed || {};
    return `<div class="hp">?</div><div class="hb"><div class="ht">${esc(it.name)}</div>
      <div class="hm">${esc(label)}</div>
      <div class="ho">${esc(it.message || "")}</div>
      ${p.title ? `<div class="hx">Rennix read the name as: <b>${esc(p.title)}</b>${p.year ? ` (${p.year})` : ""}${p.season != null ? ` · Season ${p.season}` : ""}</div>` : ""}
      <div class="hx">👆 Click to search manually</div></div>`;
  }
  const img = m.poster_url ? `<img src="${esc(m.poster_large || m.poster_url)}" alt="" onerror="this.remove()">` : "";
  const bits = [];
  bits.push(m.kind === "episode" ? `${icon("tv")}TV · ${esc(sxe(m))}` : `${icon("film")}Movie`);
  if (m.year) bits.push(esc(String(m.year)));
  if (m.runtime) bits.push(`${icon("clock")}${esc(fmtMin(m.runtime))}`);
  if (m.rating) bits.push(`<span class="rate">${icon("star")}${Number(m.rating).toFixed(1)}</span>`);
  if (m.genres && m.genres.length) bits.push(esc(m.genres.join(", ")));
  const epTitle = m.kind === "episode" && m.episode_titles && m.episode_titles.filter(Boolean).length ? `<div style="font-weight:600;font-size:13.5px">“${esc(m.episode_titles.filter(Boolean).join(" & "))}”</div>` : "";
  return `<div class="hp">${esc(initials(m.title))}${img}</div><div class="hb">
    <div class="ht">${esc(m.title)}${m.year ? ` <span class="muted" style="font-weight:500">(${m.year})</span>` : ""}</div>
    <div class="hm">${bits.join("<span>·</span>")}</div>
    ${epTitle}
    ${m.overview ? `<div class="ho">${esc(m.overview)}</div>` : ""}
    <div class="hm"><b class="st-${st}">${esc(label)}</b>${st === "green" || st === "amber" ? `<span>· ${pct(it.confidence)} confident</span>` : ""}</div>
    ${reasons ? `<ul class="hr">${reasons}</ul>` : ""}
    ${pv.full ? `<div class="hn">${esc(pv.full)}</div>` : pv.same ? `<div class="hx">✓ Already named correctly</div>` : ""}
    <div class="hx">👆 Click to change the match</div></div>`;
}

function showHover(id) {
  const it = S.items.get(id);
  const card = $("#hovercard");
  if (!it || (it.status || "pending") === "pending" || S.settings.general.hover_preview === false) return;
  card.innerHTML = hoverHtml(it);
  card.classList.add("show");
  const w = card.offsetWidth, h = card.offsetHeight;
  let left = HC.x + 18, top = HC.y + 14;
  if (left + w > window.innerWidth - 12) left = HC.x - w - 18;
  if (top + h > window.innerHeight - 12) top = window.innerHeight - h - 12;
  card.style.left = `${Math.max(12, left)}px`; card.style.top = `${Math.max(12, top)}px`;
}

const hoverZone = $(".table-wrap");
hoverZone.addEventListener("mousemove", (e) => {
  HC.x = e.clientX; HC.y = e.clientY;
  const el = e.target.closest("[data-id]");
  const onTip = e.target.closest(".status[data-tip]");
  const id = el && !onTip ? el.dataset.id : null;
  if (id === HC.id) { if (id && $("#hovercard").classList.contains("show")) showHover(id); return; }
  hideHover();
  if (!id || $("#overlay")) return;
  HC.id = id;
  HC.timer = setTimeout(() => showHover(id), 450);
});
hoverZone.addEventListener("mouseleave", hideHover);
hoverZone.addEventListener("scroll", hideHover, true);
document.addEventListener("mousedown", hideHover);
$("#rows").addEventListener("keydown", (e) => { if (e.key === "Enter") { const r = e.target.closest(".row"); if (r) openSide(r.dataset.id); } });

// ------------------------------------------------------------------ side panel (fix or change a match)
const side = { kind: "movie", results: null, chosen: null, season: "", episodes: "", query: "", year: "" };

function openSide(id) {
  S.selected = id;
  const it = S.items.get(id);
  if (!it) return;
  const isTv = (it.match && it.match.kind === "episode") || (it.parsed && it.parsed.kind === "episode");
  side.kind = isTv ? "tv" : "movie";
  side.results = null;
  side.chosen = it.match ? it.match.tmdb_id : (it.candidates && it.candidates[0] ? it.candidates[0].tmdb_id : null);
  side.query = it.parsed ? it.parsed.title || "" : "";
  side.year = it.parsed && it.parsed.year ? String(it.parsed.year) : "";
  side.season = it.match && it.match.season != null ? String(it.match.season) : (it.parsed && it.parsed.season != null ? String(it.parsed.season) : "");
  const eps = (it.match && it.match.episodes && it.match.episodes.length ? it.match.episodes : (it.parsed && it.parsed.episodes) || []);
  side.episodes = eps.length > 1 ? `${eps[0]}-${eps[eps.length - 1]}` : eps.length ? String(eps[0]) : (it.parsed && it.parsed.absolute ? String(it.parsed.absolute) : "");
  $("#side").classList.remove("hidden");
  $$("#rows .row").forEach((r) => r.classList.toggle("sel", r.dataset.id === id));
  renderSide();
}

function closeSide() { S.selected = null; $("#side").classList.add("hidden"); $$("#rows .row.sel").forEach((r) => r.classList.remove("sel")); }

function candHtml(c, it) {
  const on = side.chosen === c.tmdb_id;
  const poster = posterHtml(c.poster_url, c.title, "big");
  const meta = `${c.kind === "tv" ? "TV show" : "Movie"} · ${c.year || "?"}${c.runtime ? " · " + fmtMin(c.runtime) : ""}`;
  const hint = (c.hints || [])[0] || "";
  const reasons = (c.reasons || []).join("\n");
  const score = c.confidence ? `<div class="score" data-tip="${tip("📊 How sure Rennix is", reasons)}">${pct(c.confidence)}</div>` : "";
  return `<button class="cand${on ? " on" : ""}" data-cand="${c.tmdb_id}" data-kind="${c.kind}">${poster}
    <div class="grow"><div class="t">${esc(c.title)}</div><div class="m">${esc(meta)}</div>${hint ? `<div class="h">${esc(hint)}</div>` : ""}</div>${score}</button>`;
}

function renderSide() {
  const it = S.items.get(S.selected);
  const el = $("#side");
  if (!it) { closeSide(); return; }
  const st = it.status || "pending";
  const stLabel = { green: "Matched", amber: "Needs a quick check", red: "No match", pending: "Searching…" }[st];
  const chips = [];
  if (it.parsed && it.parsed.minutes) chips.push(`<span class="info-chip" data-tip="${tip("⏱️ Runtime", "Read from the video file itself.")}">${icon("clock")}Runtime ${fmtMin(it.parsed.minutes)}</span>`);
  if (it.parsed && it.parsed.resolution) chips.push(`<span class="info-chip">${icon("monitor")}${esc(it.parsed.resolution)}</span>`);
  if (it.size_text) chips.push(`<span class="info-chip">${icon("disk")}${esc(it.size_text)}</span>`);
  const list = side.results || (it.candidates || []).filter((c) => (side.kind === "tv") === (c.kind === "tv"));
  const chosen = list.find((c) => c.tmdb_id === side.chosen) || (it.match && it.match.tmdb_id === side.chosen ? { title: it.match.title, year: it.match.year, tmdb_id: it.match.tmdb_id } : null);
  const pv = S.previews[it.id];
  el.innerHTML = `
    <div class="side-head">
      <div class="top">
        <div class="status st-${st}"><span class="dot"></span>${esc(stLabel)}${st === "green" || st === "amber" ? ` · ${pct(it.confidence)}` : ""}</div>
        <button class="icon-btn" id="side-close" aria-label="Close panel" data-tip="${tip("✖️ Close", "Esc also closes this panel.")}">${icon("x")}</button>
      </div>
      <div class="mono" style="font-size:14px;margin-top:6px;word-break:break-all">${esc(it.name)}</div>
      <div class="info-chips">${chips.join("")}</div>
      ${(it.reasons || []).length ? `<ul class="reasons">${it.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    </div>
    <div class="side-body">
      <div class="seg" style="align-self:flex-start">
        <button data-k="movie" class="${side.kind === "movie" ? "on" : ""}">${icon("film")}Movie</button>
        <button data-k="tv" class="${side.kind === "tv" ? "on" : ""}">${icon("tv")}TV show</button>
      </div>
      <div class="sec-label">${side.results ? "Search results" : "Which one is it?"}</div>
      ${list.length ? list.map((c) => candHtml(c, it)).join("") : `<div class="muted" style="padding:8px">${side.results ? "Nothing found. Try another spelling, or remove the year." : "No suggestions yet. Search below."}</div>`}
    </div>
    <div class="side-foot">
      <label class="muted" style="font-size:13px">Not listed? Search TheMovieDB</label>
      <form class="search-row" id="side-search">
        <span class="with-icon">${icon("search")}<input type="search" id="sq" placeholder="Title" value="${esc(side.query)}" aria-label="Title"></span>
        <input type="text" id="sy" placeholder="Year" value="${esc(side.year)}" style="flex:0 0 78px;width:78px" aria-label="Year">
        <button class="btn small" type="submit">Search</button>
      </form>
      ${side.kind === "tv" ? `<div class="search-row">
        <label class="muted" style="display:flex;align-items:center;gap:6px;font-size:13px" data-tip="${tip("🔢 Season", "Use 0 for specials.")}">Season <input type="text" id="ss" value="${esc(side.season)}" style="width:64px"></label>
        <label class="muted" style="display:flex;align-items:center;gap:6px;font-size:13px" data-tip="${tip("🔢 Episode", "For double episodes type 1-2. For anime you can type the absolute number, like 147.")}">Episode <input type="text" id="se" value="${esc(side.episodes)}" style="width:80px"></label>
      </div>` : ""}
      ${pv && pv.full && it.match && chosen && it.match.tmdb_id === chosen.tmdb_id ? `<div class="final">Will become<span class="mono">${esc(pv.full)}</span></div>` : ""}
      <button class="btn primary" id="side-use" ${chosen ? "" : "disabled"}>${icon("check")}${chosen ? `Use ${esc(chosen.title)}${chosen.year ? ` (${chosen.year})` : ""}` : "Pick a match above"}</button>
    </div>`;
  $("#side-close").onclick = closeSide;
  for (const b of $$(".seg button", el)) b.onclick = () => { side.kind = b.dataset.k; side.results = null; side.chosen = null; renderSide(); };
  for (const b of $$(".cand", el)) b.onclick = () => { side.chosen = Number(b.dataset.cand); renderSide(); };
  $("#side-search").onsubmit = async (e) => {
    e.preventDefault();
    side.query = $("#sq").value.trim(); side.year = $("#sy").value.trim();
    if ($("#ss")) { side.season = $("#ss").value.trim(); side.episodes = $("#se").value.trim(); }
    if (!side.query) return;
    const res = await api.search(side.kind, side.query, side.year || null);
    side.results = res.results || [];
    side.chosen = side.results[0] ? side.results[0].tmdb_id : null;
    if (res.message) toast(`📡 ${esc(res.message)}`);
    renderSide();
  };
  $("#side-use").onclick = async () => {
    if ($("#ss")) { side.season = $("#ss").value.trim(); side.episodes = $("#se").value.trim(); }
    const res = await api.choose_match(it.id, side.kind, side.chosen, side.kind === "tv" ? side.season : null, side.kind === "tv" ? side.episodes : null);
    if (res.error) { toast(`❌ ${esc(res.error)}`); return; }
    S.items.set(res.id, res);
    if (res.preview) S.previews[res.id] = res.preview;
    side.results = null;
    renderRow(res.id); renderChips(); renderSide();
    toast(`👍 Matched to ${esc(res.match ? res.match.title : "")}. Rennix will remember this.`);
  };
}

// ------------------------------------------------------------------ adding files
async function addPaths(paths) {
  if (!paths || !paths.length) return;
  const res = await api.add_paths(paths, S.mode);
  if (!res.started) toast(`⏳ ${esc(res.message || "Busy")}`);
  else { showScreen("main"); setStatus("Looking for videos…", true); }
}
const chooseFolder = async () => addPaths(await api.choose_folder());
const chooseFiles = async () => addPaths(await api.choose_files());
$("#btn-choose-folder").onclick = chooseFolder;
$("#btn-choose-files").onclick = chooseFiles;
$("#btn-add-folder").onclick = chooseFolder;
$("#btn-add-files").onclick = chooseFiles;

for (const zone of ["#dropzone", "#dropbar"]) {
  const z = $(zone);
  z.addEventListener("dragover", (e) => { e.preventDefault(); z.classList.add("drag"); });
  z.addEventListener("dragleave", () => z.classList.remove("drag"));
  z.addEventListener("drop", () => z.classList.remove("drag"));
}
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => e.preventDefault());

// ------------------------------------------------------------------ mode
function setMode(mode) {
  S.mode = mode;
  for (const b of $$("#mode button")) b.classList.toggle("on", b.dataset.mode === mode);
}
for (const b of $$("#mode button")) b.onclick = () => setMode(b.dataset.mode);

// ------------------------------------------------------------------ renaming
async function doRename() {
  const c = counts();
  const includeAmber = $("#chk-amber").checked;
  const n = c.green + (includeAmber ? c.amber : 0);
  if (!n || S.busy) return;
  const go = async () => {
    S.busy = true; renderChips();
    const r = await api.rename(includeAmber, null);
    if (!r.started) { S.busy = false; renderChips(); toast(`⏳ ${esc(r.message || "Busy")}`); }
    else setStatus("Renaming…", true);
  };
  if (S.settings.safety.confirm_before_rename) {
    const skipped = (includeAmber ? 0 : c.amber) + c.red;
    const action = { move: "renamed and moved", copy: "copied", hardlink: "hardlinked", symlink: "symlinked" }[S.settings.library.action] || "renamed";
    confirmModal(`✏️ Rename ${n} file${n === 1 ? "" : "s"}?`,
      `${n} file${n === 1 ? " will be" : "s will be"} ${action}.${skipped ? ` ${skipped} file${skipped === 1 ? " is" : "s are"} skipped.` : ""} You can undo this from History.`,
      `Rename ${n}`, go);
  } else go();
}
$("#btn-rename").onclick = doRename;
$("#chk-amber").onchange = renderChips;
$("#btn-clear").onclick = async () => { await api.clear(null); S.items.clear(); S.order = []; S.previews = {}; closeSide(); showScreen("start"); };
$("#btn-export").onclick = async () => { const r = await api.export_plan($("#chk-amber").checked); if (r.saved) toast(`📋 Plan saved to ${esc(fileName(r.path))}`); };

async function undoBatch(id) {
  const r = await api.undo(id, null);
  if (r.undone) toast(`↩️ ${r.undone} file${r.undone === 1 ? "" : "s"} put back where they were.`);
  for (const e of r.errors || []) toast(`❌ ${esc(e.message)}`, { timeout: 9000 });
}

// ------------------------------------------------------------------ format selects
function fillFormatSelects() {
  const names = Object.keys(S.formats);
  for (const [sel, key] of [["#sel-movie-format", "movie_format"], ["#sel-episode-format", "episode_format"]]) {
    const cur = S.settings.naming[key];
    $(sel).innerHTML = names.map((n) => `<option value="${esc(n)}"${n === cur ? " selected" : ""}>${esc(n)}</option>`).join("");
    $(sel).onchange = async () => { await saveSettings({ naming: { [key]: $(sel).value } }); await refreshPreviews(); };
  }
}
async function refreshPreviews() { S.previews = await api.refresh_previews(); renderAll(); if (S.selected) renderSide(); }

async function saveSettings(changes) {
  S.settings = await api.save_settings(changes);
  applyLook();
  return S.settings;
}

function applyLook() {
  const g = S.settings.general;
  let theme = g.theme;
  if (theme === "system") theme = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-motion", g.reduce_motion ? "reduce" : "full");
  document.body.style.zoom = (g.text_size || 100) / 100;
}

// ------------------------------------------------------------------ modals
function openModal(html, cls = "") {
  closeModal();
  const o = document.createElement("div");
  o.className = "overlay";
  o.id = "overlay";
  o.innerHTML = `<div class="modal ${cls}" role="dialog" aria-modal="true">${html}</div>`;
  o.addEventListener("mousedown", (e) => { if (e.target === o) closeModal(); });
  $("#modal-root").appendChild(o);
  return o;
}
function closeModal() { $("#overlay")?.remove(); hideTip(); }

function confirmModal(title, text, okLabel, onOk) {
  const o = openModal(`<div class="modal-head"><h2>${esc(title)}</h2></div>
    <div class="modal-body pad"><p style="margin:0;color:var(--text-2)">${esc(text)}</p>
    <div style="display:flex;gap:10px;justify-content:flex-end"><button class="btn" id="m-cancel">Cancel</button><button class="btn primary" id="m-ok">${icon("check")}${esc(okLabel)}</button></div></div>`, "narrow");
  $("#m-cancel", o).onclick = closeModal;
  $("#m-ok", o).onclick = () => { closeModal(); onOk(); };
  $("#m-ok", o).focus();
}

// ------------------------------------------------------------------ settings
const SECTIONS = [
  ["general", "⚙️", "General"], ["library", "📚", "Library"], ["naming", "🏷️", "Naming"], ["matching", "🎯", "Matching"],
  ["conflicts", "⚔️", "Conflicts"], ["leftovers", "🧹", "Leftovers"], ["safety", "🛡️", "Safety"], ["notifications", "🔔", "Notifications"],
  ["metadata", "📝", "Metadata"], ["integrations", "🔌", "Integrations"], ["network", "🌐", "Network"], ["advanced", "🧪", "Advanced"],
  ["privacy", "🔒", "Privacy"], ["updates", "⬆️", "Updates"], ["about", "ℹ️", "About"],
];
let settingsSection = "general";

function sw(section, key, label, desc, emoji = "") {
  const on = !!S.settings[section][key];
  return `<div class="srow"><div><div class="lbl">${emoji ? emoji + " " : ""}${esc(label)}</div><div class="d">${esc(desc)}</div></div>
    <button class="switch" role="switch" aria-checked="${on}" aria-label="${esc(label)}" data-sw="${section}.${key}"><span></span></button></div>`;
}
function choices(section, key, opts) {
  const cur = S.settings[section][key];
  return `<div class="choices">${opts.map(([val, e, name, desc, tipText]) => `<button class="choice${cur === val ? " on" : ""}" data-choice="${section}.${key}" data-val="${esc(val)}"${tipText ? ` data-tip="${tipText}"` : ""}><div class="e">${e}</div><b>${esc(name)}</b><span>${esc(desc)}</span></button>`).join("")}</div>`;
}
function selectRow(section, key, label, desc, opts) {
  const cur = String(S.settings[section][key]);
  return `<div class="srow"><div><div class="lbl">${esc(label)}</div><div class="d">${esc(desc)}</div></div>
    <select data-sel="${section}.${key}">${opts.map(([v, l]) => `<option value="${esc(v)}"${String(v) === cur ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></div>`;
}
const soon = (text) => `<div class="card pad"><h4>🚧 Coming in a later version</h4><div class="desc" style="margin-top:0">${esc(text)}</div></div>`;

function settingsPane(sec) {
  const s = S.settings;
  const fmtOpts = Object.keys(S.formats).map((n) => [n, n]);
  switch (sec) {
    case "general": return `
      <div class="card pad"><h4>Theme</h4>${choices("general", "theme", [["dark", "🌙", "Dark", "Easy on the eyes"], ["light", "☀️", "Light", "Bright and clean"], ["system", "🖥️", "Follow system", "Matches your PC"]])}</div>
      <div class="card pad"><h4>Your media server</h4><div class="desc">Sets the naming templates that suit it.</div>${choices("general", "media_server", [["plex", "🟧", "Plex", "Plex layout"], ["jellyfin", "🟪", "Jellyfin", "Jellyfin layout"], ["emby", "🟩", "Emby", "Emby layout"], ["kodi", "🟦", "Kodi", "Kodi layout"], ["none", "📁", "None", "Simple names"]])}</div>
      <div class="card">${selectRow("general", "default_mode", "Default mode", "What dropped files are treated as.", [["auto", "✨ Auto"], ["movie", "🎬 Movies"], ["tv", "📺 TV Shows"]])}
      ${selectRow("general", "text_size", "Text size", "Make everything bigger or smaller.", [[90, "Small"], [100, "Normal"], [110, "Large"], [125, "Extra large"]])}
      ${sw("general", "hover_preview", "Quick preview on hover", "Shows the poster, plot and match details when you rest the mouse on a file.", "👀")}
      ${sw("general", "reduce_motion", "Reduce motion", "Turns off sliding and fading animations.", "🧘")}</div>`;
    case "library": return `
      <div class="card pad"><h4>Destination folders</h4>
        <div class="path-row"><label>${icon("film")}Movies</label><input type="text" data-text="library.movies_dir" value="${esc(s.library.movies_dir)}" placeholder="e.g. D:\\Media\\Movies"><button class="btn small" data-browse="library.movies_dir">${icon("folder")}Browse</button></div>
        <div class="path-row"><label>${icon("tv")}TV Shows</label><input type="text" data-text="library.tv_dir" value="${esc(s.library.tv_dir)}" placeholder="e.g. D:\\Media\\TV Shows"><button class="btn small" data-browse="library.tv_dir">${icon("folder")}Browse</button></div>
      </div>
      <div class="card">${sw("library", "sort_into_library", "Sort into the library", "Move files into the folders above, with the template's folders. Off renames files where they are (file name only).", "📂")}</div>
      <div class="card pad"><h4>What happens to each file</h4><div class="desc">Choose how renamed files get into place.</div>${choices("library", "action", [
        ["move", "📦", "Move", "Rename and move. The original location is emptied."],
        ["copy", "📄", "Copy", "A renamed copy. Uses double the space."],
        ["hardlink", "🔗", "Hardlink", "Renamed in your library, original untouched, no extra space.", tip("🔗 Hardlink", "Your library gets a properly named file, and the original stays put so torrents keep seeding.\n✓ Uses no extra disk space.", "⚠️ Source and library must be on the same drive.")],
        ["symlink", "🪞", "Symlink", "A shortcut-style link. For advanced setups."]])}</div>
      <div class="card">${sw("library", "tidy_empty_folders", "Tidy empty folders", "Remove folders left completely empty after moving.", "🧽")}
      ${sw("library", "extras_folders", "Keep extras together", "Trailers and featurettes into Plex-style Extras folders (coming soon; extras are skipped for now).", "🎞️")}</div>`;
    case "naming": return `
      <div class="card">${selectRow("naming", "movie_format", "Film format", "How films are named.", fmtOpts)}${selectRow("naming", "episode_format", "TV format", "How episodes are named.", fmtOpts)}
        <div class="srow"><div><div class="lbl">🛠️ Format editor</div><div class="d">Build your own format with a live preview.</div></div><button class="btn small" id="open-fe">${icon("tag")}Open editor</button></div></div>
      <div class="card">${sw("naming", "rename_subtitles", "Rename subtitles", "Keeps .srt and other subtitle files matched to their video.", "💬")}
      ${sw("naming", "rename_images", "Rename posters and artwork", "Moves poster.jpg and friends along with the video.", "🖼️")}
      ${sw("naming", "rename_nfo", "Rename .nfo files", "Keeps media-server info files with their video.", "📝")}</div>`;
    case "matching": return `
      <div class="card pad"><h4>How strict green matches are</h4><div class="desc">Stricter means more amber rows to check, but fewer surprises.</div>${choices("matching", "strictness", [["relaxed", "🙂", "Relaxed", "More greens"], ["normal", "🎯", "Normal", "Recommended"], ["strict", "🔍", "Strict", "Check more by hand"]])}</div>
      <div class="card">${selectRow("matching", "language", "Title language", "Language for titles and episode names.", [["en-US", "English (US)"], ["en-GB", "English (UK)"], ["fr-FR", "French"], ["de-DE", "German"], ["es-ES", "Spanish"], ["it-IT", "Italian"], ["nl-NL", "Dutch"], ["pt-BR", "Portuguese (Brazil)"], ["ja-JP", "Japanese"]])}
      ${selectRow("matching", "region", "Region", "Used for release years.", [["GB", "United Kingdom"], ["US", "United States"], ["IE", "Ireland"], ["CA", "Canada"], ["AU", "Australia"], ["DE", "Germany"], ["FR", "France"]])}
      ${selectRow("matching", "parallel_lookups", "Lookups at once", "More is faster; fewer is gentler on slow connections.", [[2, "2"], [4, "4"], [6, "6 (recommended)"], [8, "8"]])}
      ${sw("matching", "fingerprint", "File fingerprinting", "Identify badly named files from the video itself (coming soon).", "🧬")}</div>
      <div class="card pad"><h4>🔑 TheMovieDB key</h4><div class="desc">Rennix has a key built in. Add your own only if you want to.</div>
        <div class="search-row"><input type="text" data-text="tmdb.token" value="${esc(s.tmdb.token)}" placeholder="Optional: your API Read Access Token"><button class="btn small" id="check-tmdb">${icon("check")}Check connection</button></div><div id="tmdb-result" class="muted"></div></div>`;
    case "conflicts": return `<div class="card pad"><h4>When the new name already exists</h4><div class="desc">Rennix never overwrites anything silently.</div>${choices("conflicts", "policy", [
        ["skip", "⏭️", "Skip", "Leave that file alone"], ["number", "🔢", "Add a number", "Saves as “Name (2)”"],
        ["replace_if_better", "⬆️", "Replace if better", "Only if the new file has a higher resolution; the old one goes to the Recycle Bin"]])}</div>`;
    case "leftovers": return `<div class="card pad"><h4>Junk files</h4><div class="desc">Samples, adverts and release-group text files found next to your videos.</div>${choices("leftovers", "junk", [
        ["leave", "🙈", "Leave them", "Do nothing"], ["folder", "🗂️", "Move to a folder", "Into “_Rennix leftovers”"], ["recycle", "♻️", "Recycle Bin", "Never deleted permanently"]])}</div>`;
    case "safety": return `<div class="card">${sw("safety", "confirm_before_rename", "Confirm before renaming", "Shows a quick summary before anything changes.", "✋")}
      ${sw("safety", "skip_amber", "Always skip amber files", "Files Rennix isn't sure about are left alone unless you tick “Include amber”.", "⚠️")}
      ${selectRow("safety", "history_days", "Keep undo history for", "Older batches are cleared automatically.", [[30, "30 days"], [365, "1 year"], [0, "Forever"]])}</div>`;
    case "notifications": return `<div class="card">${sw("notifications", "on_finish", "Tell me when a batch finishes", "A message pops up when renaming is done.", "🔔")}</div>`;
    case "metadata": return soon("Writing cast, director, plot and posters into your files (and .nfo files beside them) arrives in version 2.");
    case "integrations": return soon("Right-click “Rename with Rennix” in Explorer, and refreshing Plex or Jellyfin after renaming.");
    case "network": return `<div class="card">${sw("network", "offline", "Offline mode", "Only use matches Rennix has already looked up.", "✈️")}
      <div class="srow"><div><div class="lbl">🌐 Proxy</div><div class="d">Only if your network needs one, e.g. http://host:8080</div></div><input type="text" data-text="network.proxy" value="${esc(s.network.proxy)}" style="width:240px"></div></div>`;
    case "advanced": return `<div class="card">${sw("general", "show_console", "Show the console button", "The 🐞 button opens a live log with error codes. Handy for reporting problems.", "🐞")}</div><div class="card">
      <div class="srow"><div><div class="lbl">🗄️ Match cache</div><div class="d">Saved lookups make repeat scans instant.</div></div><button class="btn small" id="clear-cache">${icon("trash")}Clear cache</button></div>
      <div class="srow"><div><div class="lbl">🪵 Log files</div><div class="d">Send these to support if something goes wrong.</div></div><button class="btn small" id="open-logs">${icon("open")}Open log folder</button></div></div>`;
    case "privacy": return `<div class="card">${sw("privacy", "crash_reports", "Send crash reports", "Off by default. Rennix doesn't send anything about you or your files.", "🔒")}</div>`;
    case "updates": return `<div class="card">${sw("updates", "auto_check", "Check for updates", "Looks for a new version at most once a day.", "⬆️")}
      ${selectRow("updates", "channel", "Update channel", "Beta gets new features early.", [["stable", "Stable"], ["beta", "Beta"]])}
      <div class="srow"><div><div class="lbl">🔎 Check now</div><div class="d" id="upd-result">You're on version ${esc(S.version)}.</div></div><button class="btn small" id="check-upd">Check now</button></div></div>`;
    case "about": {
      const st = S.stats || {};
      return `<div class="card pad"><div style="display:flex;gap:14px;align-items:center"><svg width="56" height="56"><use href="#i-logo"></use></svg>
        <div><div class="wordmark" style="font-size:28px">renni<svg width="20" height="20"><use href="#i-x-mark"></use></svg></div><div class="muted">Version ${esc(S.version)} · Rename and organise your movies and TV shows</div></div></div></div>
        <div class="card pad"><h4>📊 Your stats</h4><div>${st.renamed || 0} files renamed so far.</div></div>
        <div class="card pad"><h4>🎬 Credits</h4><div class="muted">This product uses the TMDB API but is not endorsed or certified by TMDB.</div>
        <div style="display:flex;gap:10px;flex-wrap:wrap"><button class="btn small" data-url="https://www.themoviedb.org/">${icon("open")}TheMovieDB</button><button class="btn small" data-url="https://github.com/FiTH-g1gGLe/Rennix">${icon("open")}Rennix on GitHub</button></div></div>`;
    }
  }
  return "";
}

function openSettings(section) {
  if (section) settingsSection = section;
  const o = openModal(`<div class="modal-head"><h2>${icon("gear", "ic lg")}Settings</h2><button class="btn small primary" id="set-done">${icon("check")}Done</button></div>
    <div class="modal-body"><nav class="snav" aria-label="Settings sections">
      <span class="with-icon" style="flex:0;margin-bottom:10px">${icon("search")}<input type="search" id="set-search" placeholder="Search settings" aria-label="Search settings"></span>
      ${SECTIONS.map(([k, e, n]) => `<button data-sec="${k}" class="${k === settingsSection ? "on" : ""}"><span style="width:22px;text-align:center">${e}</span>${n}</button>`).join("")}
    </nav><div class="spane" id="spane"></div></div>`);
  $("#set-done", o).onclick = closeModal;
  for (const b of $$(".snav button", o)) b.onclick = () => { settingsSection = b.dataset.sec; $$(".snav button", o).forEach((x) => x.classList.toggle("on", x === b)); drawPane(); };
  $("#set-search", o).oninput = (e) => {
    const q = e.target.value.toLowerCase();
    for (const b of $$(".snav button", o)) {
      const sec = b.dataset.sec;
      const text = (b.textContent + " " + settingsPane(sec).replace(/<[^>]+>/g, " ")).toLowerCase();
      b.classList.toggle("hidden", !!q && !text.includes(q));
    }
  };
  drawPane();
}

function drawPane() {
  const pane = $("#spane");
  if (!pane) return;
  const [, e, n] = SECTIONS.find((x) => x[0] === settingsSection);
  const resettable = !["about", "metadata", "integrations", "advanced"].includes(settingsSection);
  pane.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-end;gap:10px;flex-wrap:wrap"><h3>${e} ${n}</h3>
    ${resettable ? `<button class="btn small" id="reset-sec">${icon("history")}Reset to defaults</button>` : ""}</div>${settingsPane(settingsSection)}`;
  $("#reset-sec")?.addEventListener("click", async () => { S.settings = await api.reset_settings(settingsSection); applyLook(); fillFormatSelects(); drawPane(); });
  for (const b of $$("[data-sw]", pane)) b.onclick = async () => {
    const [sec, key] = b.dataset.sw.split(".");
    await saveSettings({ [sec]: { [key]: !S.settings[sec][key] } });
    b.setAttribute("aria-checked", String(!!S.settings[sec][key]));
    if (key === "show_console") setConsoleVisible(S.settings.general.show_console);
    if (sec === "library" || sec === "naming") refreshPreviews();
  };
  for (const b of $$("[data-choice]", pane)) b.onclick = async () => {
    const [sec, key] = b.dataset.choice.split(".");
    const val = b.dataset.val;
    const changes = { [sec]: { [key]: val } };
    if (sec === "general" && key === "media_server") {
      const tpl = { plex: "Plex", jellyfin: "Jellyfin", emby: "Emby", kodi: "Kodi", none: "Simple" }[val];
      changes.naming = { movie_format: tpl, episode_format: tpl };
    }
    await saveSettings(changes);
    fillFormatSelects(); drawPane(); refreshPreviews();
  };
  for (const s of $$("[data-sel]", pane)) s.onchange = async () => {
    const [sec, key] = s.dataset.sel.split(".");
    const raw = s.value;
    const val = /^\d+$/.test(raw) ? Number(raw) : raw;
    await saveSettings({ [sec]: { [key]: val } });
    if (sec === "naming") { fillFormatSelects(); refreshPreviews(); }
  };
  for (const i of $$("[data-text]", pane)) i.onchange = async () => {
    const [sec, key] = i.dataset.text.split(".");
    await saveSettings({ [sec]: { [key]: i.value.trim() } });
    if (sec === "library") refreshPreviews();
  };
  for (const b of $$("[data-browse]", pane)) b.onclick = async () => {
    const res = await api.choose_folder();
    if (res && res[0]) { const [sec, key] = b.dataset.browse.split("."); await saveSettings({ [sec]: { [key]: res[0] } }); drawPane(); refreshPreviews(); }
  };
  for (const b of $$("[data-url]", pane)) b.onclick = () => api.open_url(b.dataset.url);
  $("#open-fe")?.addEventListener("click", () => openFormatEditor());
  $("#check-tmdb")?.addEventListener("click", async () => { $("#tmdb-result").textContent = "Checking…"; const r = await api.check_tmdb(); $("#tmdb-result").textContent = (r.ok ? "✅ " : "❌ ") + r.message; });
  $("#clear-cache")?.addEventListener("click", async () => { await api.clear_cache(); toast("🗄️ Cache cleared."); });
  $("#open-logs")?.addEventListener("click", () => api.open_logs());
  $("#check-upd")?.addEventListener("click", async () => { const r = await api.check_updates(); $("#upd-result").textContent = r.available ? `🎉 Version ${r.version} is available.` : r.message; if (r.available) showUpdate(r); });
}

$("#btn-settings").onclick = () => openSettings();

// ------------------------------------------------------------------ format editor
const fe = { kind: "movie", name: "", text: "", timer: null };

function openFormatEditor(kind) {
  fe.kind = kind || fe.kind;
  const key = fe.kind === "movie" ? "movie_format" : "episode_format";
  fe.name = S.settings.naming[key];
  fe.text = (S.formats[fe.name] || S.formats.Plex)[fe.kind];
  const o = openModal(`<div class="modal-head"><h2>${icon("tag", "ic lg")}Naming formats</h2>
      <div style="display:flex;gap:10px"><button class="btn small" id="fe-cancel">Close</button><button class="btn small primary" id="fe-use">${icon("check")}Use this format</button></div></div>
    <div class="modal-body"><div class="fe-side" id="fe-side"></div><div class="fe-main" id="fe-main"></div></div>`);
  $("#fe-cancel", o).onclick = closeModal;
  $("#fe-use", o).onclick = useFormat;
  drawFE();
}

function drawFE() {
  const sideEl = $("#fe-side"), main = $("#fe-main");
  if (!sideEl) return;
  const names = Object.keys(S.formats);
  const builtins = names.filter((n) => S.formats[n].builtin), mine = names.filter((n) => !S.formats[n].builtin);
  const tplBtn = (n) => `<button class="tpl${n === fe.name ? " on" : ""}" data-tpl="${esc(n)}" data-tip="${tip("🏷️ " + n, S.formats[n][fe.kind + "_example"] || "", S.formats[n].description || "")}"><b>${esc(n)}</b><span>${esc(S.formats[n].description || "")}</span></button>`;
  sideEl.innerHTML = `<div class="seg" style="display:flex">
      <button data-fk="movie" class="${fe.kind === "movie" ? "on" : ""}" style="flex:1;justify-content:center">${icon("film")}Films</button>
      <button data-fk="episode" class="${fe.kind === "episode" ? "on" : ""}" style="flex:1;justify-content:center">${icon("tv")}TV</button></div>
    <div class="sec-label">Templates</div>${builtins.map(tplBtn).join("")}
    <div class="sec-label" style="margin-top:6px">My formats</div>${mine.map(tplBtn).join("") || `<div class="muted" style="font-size:13px;padding:0 8px">None yet.</div>`}
    <button class="tpl" id="fe-new" style="border-style:dashed;color:var(--text-2);flex-direction:row;align-items:center;gap:6px">${icon("plus")} New format</button>`;
  for (const b of $$("[data-fk]", sideEl)) b.onclick = () => { fe.kind = b.dataset.fk; fe.text = (S.formats[fe.name] || S.formats.Plex)[fe.kind]; drawFE(); };
  for (const b of $$("[data-tpl]", sideEl)) b.onclick = () => { fe.name = b.dataset.tpl; fe.text = S.formats[fe.name][fe.kind]; drawFE(); };
  $("#fe-new", sideEl).onclick = () => { fe.name = ""; drawFE(); $("#fe-name")?.focus(); };

  const tokens = S.tokens.filter((t) => t.kinds === "both" || t.kinds === fe.kind);
  const isBuiltin = S.formats[fe.name] && S.formats[fe.name].builtin;
  main.innerHTML = `
    <div class="card pad">
      <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start">
        <div><h4>Build your format</h4><div class="desc" style="margin-top:2px">Click a part to remove it. Dashed parts only appear when there's something to show.</div></div>
        <div style="display:flex;gap:8px;align-items:center">
          <input type="text" id="fe-name" placeholder="Name your format" value="${esc(isBuiltin ? "" : fe.name)}" style="width:200px" aria-label="Format name">
          <button class="btn small" id="fe-save">${icon("check")}Save</button>
          ${S.formats[fe.name] && !isBuiltin ? `<button class="btn small" id="fe-del" data-tip="${tip("🗑️ Delete this format", "Built-in templates can't be deleted.")}">${icon("trash")}</button>` : ""}
        </div>
      </div>
      ${isBuiltin ? `<div class="issue warning">💡 “${esc(fe.name)}” is a built-in template. Change it and save under your own name.</div>` : ""}
      <div class="builder" id="fe-builder"></div>
      <label class="muted" style="font-size:13px" data-tip="${tip("⌨️ Text mode", "{part} is filled in, [ … ] is optional, / starts a folder.\nUse \\{ \\} \\[ \\] for the literal characters.")}">Text version</label>
      <textarea id="fe-text" spellcheck="false" aria-label="Format as text">${esc(fe.text)}</textarea>
      <div><div class="sec-label" style="padding-left:0">Add a part</div><div class="palette" id="fe-palette">
        ${tokens.map((t) => `<button data-tok="${esc(t.name)}" data-tip="${tip(t.label, t.help, "Example: " + t.example)}">+ ${esc(t.label)}</button>`).join("")}
        <button data-raw="[ - ]" data-tip="${tip("💭 Optional part", "Adds [ - ] for an optional piece. Put parts inside it.")}">+ Optional [ ]</button>
        <button data-raw="/" data-tip="${tip("📁 Folder break", "Everything before / becomes a folder.")}">+ Folder /</button>
      </div></div>
      <div class="issues" id="fe-issues"></div>
    </div>
    <div class="card"><div style="padding:14px 16px;border-bottom:1px solid var(--line);font-weight:600">Live preview</div><div id="fe-examples"></div></div>`;
  const ta = $("#fe-text", main);
  ta.oninput = () => { fe.text = ta.value; scheduleCheck(); };
  for (const b of $$("#fe-palette button", main)) b.onclick = () => {
    const ins = b.dataset.tok ? `{${b.dataset.tok}}` : b.dataset.raw;
    const s = ta.selectionStart ?? ta.value.length, e = ta.selectionEnd ?? ta.value.length;
    ta.value = ta.value.slice(0, s) + ins + ta.value.slice(e);
    ta.focus(); ta.selectionStart = ta.selectionEnd = s + ins.length;
    fe.text = ta.value; scheduleCheck();
  };
  $("#fe-save", main).onclick = saveFormat;
  $("#fe-del", main)?.addEventListener("click", async () => { const r = await api.delete_format(fe.name); S.formats = r.formats; S.settings = r.settings; fe.name = "Plex"; fe.text = S.formats.Plex[fe.kind]; fillFormatSelects(); drawFE(); refreshPreviews(); });
  checkFormat();
}

function scheduleCheck() { clearTimeout(fe.timer); fe.timer = setTimeout(checkFormat, 250); }

function partsHtml(parts, path = []) {
  return parts.map((p, i) => {
    const at = [...path, i].join(".");
    if (p.t === "text") {
      return p.v.split("/").map((seg, j, arr) => (seg ? `<span class="part txt">${esc(seg)}</span>` : "") + (j < arr.length - 1 ? `<span class="part dir" data-tip="${tip("📁 Folder break", "Everything before this is a folder.")}">/</span>` : "")).join("");
    }
    if (p.t === "token") {
      const meta = S.tokens.find((t) => t.name === p.name);
      const label = meta ? meta.label : p.name;
      return `<span class="part" data-tip="${tip(meta ? meta.label : p.name, meta ? meta.help : "Unknown part", p.opts.length ? "Options: " + p.opts.join(", ") : "")}">${esc(label)}${p.opts.length ? `<small style="opacity:.7">:${esc(p.opts.join(","))}</small>` : ""}<button data-rm="${at}" aria-label="Remove ${esc(label)}">✕</button></span>`;
    }
    return `<span class="part opt" data-tip="${tip("💭 Optional part", "Disappears when anything inside it is empty.")}">${partsHtml(p.parts, [...path, i])}<button data-rm="${at}" aria-label="Remove optional part">✕</button></span>`;
  }).join("");
}

async function checkFormat() {
  const r = await api.check_format(fe.text, fe.kind);
  const b = $("#fe-builder");
  if (!b) return;
  b.innerHTML = r.parts.length ? partsHtml(r.parts) : `<span class="muted">Add parts below, or type in the text box.</span>`;
  for (const x of $$("[data-rm]", b)) x.onclick = async (e) => {
    e.stopPropagation();
    const path = x.dataset.rm.split(".").map(Number);
    const parts = JSON.parse(JSON.stringify(r.parts));
    let arr = parts;
    for (const idx of path.slice(0, -1)) arr = arr[idx].parts;
    arr.splice(path[path.length - 1], 1);
    fe.text = await api.format_from_parts(parts);
    $("#fe-text").value = fe.text;
    checkFormat();
  };
  const real = r.issues.filter((i) => i.level !== "info");
  $("#fe-issues").innerHTML = r.issues.map((i) => `<div class="issue ${i.level}">${i.level === "error" ? "❌" : i.level === "info" ? "💡" : "⚠️"} ${esc(i.message)}</div>`).join("")
    + (real.length ? "" : `<div class="issue ok">✅ Looks good. No name clashes.</div>`);
  $("#fe-examples").innerHTML = r.examples.map((x) => `<div class="ex"><div class="o">${esc(x.old)}</div><div class="n">${esc(x.new)}</div></div>`).join("") || `<div class="ex muted">Fix the problems above to see a preview.</div>`;
}

async function saveFormat() {
  const name = ($("#fe-name").value || "").trim();
  if (!name) { toast("✍️ Give your format a name first."); $("#fe-name").focus(); return; }
  const r = await api.save_format(name, fe.kind === "movie" ? fe.text : null, fe.kind === "episode" ? fe.text : null, "Your own format");
  if (!r.ok) { toast(`❌ ${esc(r.message)}`); return false; }
  S.formats = r.formats; fe.name = name;
  fillFormatSelects(); drawFE();
  toast(`💾 Saved “${esc(name)}”.`);
  return true;
}

async function useFormat() {
  let name = fe.name;
  const current = S.formats[name];
  if (!current || current[fe.kind] !== fe.text) {
    if (current && current.builtin || !name) {
      toast("💾 Save your changes under a name first."); $("#fe-name")?.focus(); return;
    }
    if (!(await saveFormat())) return;
    name = fe.name;
  }
  await saveSettings({ naming: { [fe.kind === "movie" ? "movie_format" : "episode_format"]: name } });
  fillFormatSelects(); closeModal(); refreshPreviews();
  toast(`🏷️ ${fe.kind === "movie" ? "Films" : "TV episodes"} now use “${esc(name)}”.`);
}

$("#btn-format").onclick = () => openFormatEditor();

// ------------------------------------------------------------------ history
async function openHistory() {
  const batches = await api.history();
  const o = openModal(`<div class="modal-head"><h2>${icon("history", "ic lg")}Rename history</h2><button class="btn small" id="h-close">Close</button></div>
    <div class="modal-body" style="flex-direction:column">${batches.length ? batches.map((b) => `
      <div class="batch"><div class="top"><div><b>${esc(new Date(b.created).toLocaleString())}</b><div class="muted" style="font-size:13px">${historyLine(b)}</div></div>
      ${b.count > b.undone ? `<button class="btn small" data-undo="${esc(b.id)}" data-tip="${tip("↩️ Undo this batch", "Puts every file back exactly where it was.")}">${icon("undo")}Undo</button>` : `<span class="muted">✅ Undone</span>`}</div>
      <details><summary>Show files</summary>${b.ops.filter((x) => x.kind === "video").map((x) => `<div class="op${x.state === "undone" ? " undone" : ""}">${esc(fileName(x.src))} → ${esc(x.dst)}</div>`).join("")}</details></div>`).join("")
      : `<div style="padding:28px" class="muted">🕘 Nothing renamed yet. Every rename will appear here, ready to undo.</div>`}</div>`, "narrow");
  $("#h-close", o).onclick = closeModal;
  for (const b of $$("[data-undo]", o)) b.onclick = async () => { await undoBatch(b.dataset.undo); openHistory(); };
}
$("#btn-history").onclick = openHistory;
$("#link-history").onclick = openHistory;

// ------------------------------------------------------------------ welcome
function openWelcome(step = 1) {
  const dots = `<div class="welcome-steps">${[1, 2, 3].map((n) => `<span class="${n <= step ? "on" : ""}"></span>`).join("")}</div>`;
  let body = "";
  if (step === 1) body = `<h3 style="margin:0">👋 Welcome to Rennix</h3><p class="muted" style="margin:0">Which media server do you use? Rennix picks naming that suits it. You can change this any time.</p>
    ${choices("general", "media_server", [["plex", "🟧", "Plex", ""], ["jellyfin", "🟪", "Jellyfin", ""], ["emby", "🟩", "Emby", ""], ["kodi", "🟦", "Kodi", ""], ["none", "📁", "None", "Simple names"]])}`;
  else if (step === 2) body = `<h3 style="margin:0">📚 Where's your library?</h3><p class="muted" style="margin:0">Optional. Set these and Rennix can sort renamed files into place. Skip it to rename files where they are.</p>
    <div class="path-row"><label>${icon("film")}Movies</label><input type="text" data-text="library.movies_dir" value="${esc(S.settings.library.movies_dir)}"><button class="btn small" data-browse="library.movies_dir">${icon("folder")}Browse</button></div>
    <div class="path-row"><label>${icon("tv")}TV Shows</label><input type="text" data-text="library.tv_dir" value="${esc(S.settings.library.tv_dir)}"><button class="btn small" data-browse="library.tv_dir">${icon("folder")}Browse</button></div>`;
  else body = `<h3 style="margin:0">🎉 You're all set</h3><p class="muted" style="margin:0">Drop a folder in to try it. Nothing changes until you press Rename, and everything can be undone.</p><div id="w-tmdb" class="muted">Checking the connection to TheMovieDB…</div>`;
  const o = openModal(`<div class="modal-body pad">${dots}${body}
    <div style="display:flex;justify-content:space-between;gap:10px"><button class="btn" id="w-back" ${step === 1 ? "disabled" : ""}>Back</button>
    <button class="btn primary" id="w-next">${step === 3 ? `${icon("check")}Start renaming` : "Next"}</button></div></div>`, "narrow");
  for (const b of $$("[data-choice]", o)) b.onclick = async () => {
    const tpl = { plex: "Plex", jellyfin: "Jellyfin", emby: "Emby", kodi: "Kodi", none: "Simple" }[b.dataset.val];
    await saveSettings({ general: { media_server: b.dataset.val }, naming: { movie_format: tpl, episode_format: tpl } });
    fillFormatSelects(); openWelcome(1);
  };
  for (const i of $$("[data-text]", o)) i.onchange = async () => { const [sec, key] = i.dataset.text.split("."); await saveSettings({ [sec]: { [key]: i.value.trim() } }); };
  for (const b of $$("[data-browse]", o)) b.onclick = async () => { const r = await api.choose_folder(); if (r && r[0]) { const [sec, key] = b.dataset.browse.split("."); await saveSettings({ [sec]: { [key]: r[0] } }); openWelcome(2); } };
  $("#w-back", o).onclick = () => openWelcome(step - 1);
  $("#w-next", o).onclick = async () => {
    if (step === 2) {
      const lib = S.settings.library;
      if (lib.movies_dir || lib.tv_dir) await saveSettings({ library: { sort_into_library: true } });
    }
    if (step < 3) openWelcome(step + 1);
    else { await saveSettings({ general: { first_run_done: true } }); closeModal(); }
  };
  if (step === 3) api.check_tmdb().then((r) => { const el = $("#w-tmdb"); if (el) el.textContent = (r.ok ? "✅ " : "⚠️ ") + r.message; });
}

// ------------------------------------------------------------------ updates, recovery, stats
function showUpdate(r) {
  banner("update", "info", `🎉 Rennix ${esc(r.version)} is ready.`, [["See what's new", () => api.open_url(r.url), true], ["Later", () => removeBanner("update")]]);
}

function showRecovery(j) {
  banner("journal", "", `⚠️ Rennix was interrupted while renaming (${j.done} done, ${j.pending} left). Nothing is lost.`, [
    ["Finish renaming", async () => { const r = await api.recover("finish"); removeBanner("journal"); toast(`✅ Finished: ${r.done || 0} file(s) renamed.`); }, true],
    ["Put everything back", async () => { const r = await api.recover("reverse"); removeBanner("journal"); toast(`↩️ ${r.undone || 0} file(s) put back.`); }],
  ]);
}

async function refreshStats() {
  const st = await api.get_state();
  S.stats = st.stats;
  const n = (st.stats && st.stats.renamed) || 0;
  $("#start-stats").textContent = n ? `🎬 ${n} file${n === 1 ? "" : "s"} renamed so far` : "";
}

// ------------------------------------------------------------------ keyboard
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { if ($("#overlay")) closeModal(); else if (S.selected) closeSide(); return; }
  const typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || "");
  if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "l") { e.preventDefault(); toggleConsole(); }
  else if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); doRename(); }
  else if (e.ctrlKey && e.key.toLowerCase() === "z" && !typing && S.lastBatch) { e.preventDefault(); undoBatch(S.lastBatch); S.lastBatch = null; }
  else if (e.key === "?" && !typing) {
    const o = openModal(`<div class="modal-head"><h2>⌨️ Keyboard shortcuts</h2><button class="btn small" id="k-close">Close</button></div>
      <div class="modal-body pad">${[["Ctrl + Enter", "Rename"], ["Ctrl + Z", "Undo the last rename"], ["Ctrl + Shift + L", "Open the console"], ["Enter", "Open the selected row"], ["Esc", "Close a panel or window"], ["?", "Show these shortcuts"]]
        .map(([k, d]) => `<div style="display:flex;justify-content:space-between"><span>${d}</span><span class="mono">${k}</span></div>`).join("")}</div>`, "narrow");
    $("#k-close", o).onclick = closeModal;
  }
});


// ------------------------------------------------------------------ console (alpha and beta builds)
const CON = { open: false, last: 0, entries: [], level: "all", query: "", codes: null, timer: null, seenErrors: 0, expanded: new Set() };

function setConsoleVisible(show) {
  $("#btn-console").classList.toggle("hidden", !show);
  if (!show && CON.open) closeConsole();
}

async function pollLogs() {
  if (!api) return;
  try {
    const r = await api.get_logs(CON.last);
    if (r.entries.length) {
      CON.entries.push(...r.entries);
      if (CON.entries.length > 3000) CON.entries.splice(0, CON.entries.length - 3000);
      CON.last = r.entries[r.entries.length - 1].id;
    }
    const newErrors = Math.max(0, r.counts.errors - CON.seenErrors);
    const badge = $("#console-badge");
    badge.textContent = newErrors > 99 ? "99+" : String(newErrors);
    badge.classList.toggle("hidden", CON.open || newErrors === 0);
    if (CON.open) { CON.seenErrors = r.counts.errors; if (r.entries.length) drawLogs(true); }
  } catch (e) { /* the window may be closing */ }
}

function logRow(e) {
  const fix = e.code && CON.codes && CON.codes[e.code] ? CON.codes[e.code].fix : "";
  const ctx = e.ctx && Object.keys(e.ctx).length ? JSON.stringify(e.ctx, null, 2) : "";
  const detail = [fix ? `💡 ${fix}` : "", ctx, e.detail].filter(Boolean).join("\n\n");
  const open = CON.expanded.has(e.id);
  return `<div class="log ${esc(e.level)}${detail ? " has-detail" : ""}" data-log="${e.id}">
    <span class="t">${esc(e.time)}</span><span class="lv">${esc(e.level === "CRITICAL" ? "CRASH" : e.level)}</span>
    <span class="cd">${esc(e.code || "")}</span><span class="wh">${esc(e.where)}</span><span class="msg">${esc(e.message)}${detail && !open ? ' <span class="muted">▸</span>' : ""}</span>
    ${open && detail ? `<div class="det">${fix ? `<span class="fix">💡 ${esc(fix)}</span>\n\n` : ""}${esc([ctx, e.detail].filter(Boolean).join("\n\n"))}</div>` : ""}</div>`;
}

function drawLogs(stick) {
  const body = $("#console-body");
  if (!body) return;
  const atBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 30;
  const q = CON.query.toLowerCase();
  const rows = CON.entries.filter((e) => {
    if (CON.level === "errors" && !["ERROR", "CRITICAL"].includes(e.level)) return false;
    if (CON.level === "warnings" && !["ERROR", "CRITICAL", "WARNING"].includes(e.level)) return false;
    if (CON.level === "info" && e.level === "DEBUG") return false;
    return !q || `${e.message} ${e.code} ${e.where} ${e.detail} ${JSON.stringify(e.ctx)}`.toLowerCase().includes(q);
  });
  body.innerHTML = rows.slice(-1500).map(logRow).join("") || `<div style="padding:16px" class="muted">Nothing here yet. Everything Rennix does shows up in this console.</div>`;
  if (!stick || atBottom) body.scrollTop = body.scrollHeight;
}

async function openConsole() {
  CON.open = true;
  if (!CON.codes) CON.codes = await api.error_codes();
  const el = $("#console");
  el.classList.remove("hidden");
  el.innerHTML = `<div class="console-head">
      <b>${icon("bug")}Console</b>
      <div class="seg" id="con-level">
        ${[["all", "Everything"], ["info", "Info"], ["warnings", "Warnings"], ["errors", "Errors"]].map(([k, l]) => `<button data-lv="${k}" class="${CON.level === k ? "on" : ""}">${l}</button>`).join("")}
      </div>
      <input type="search" id="con-q" placeholder="Search logs or codes (RNX-R001)" value="${esc(CON.query)}" aria-label="Search logs">
      <span class="spacer"></span>
      <button class="btn small primary" id="con-copy" data-tip="${tip("📋 Copy report", "Copies your version, system, settings (keys hidden) and recent log.", "Paste it to Claude to get the problem fixed fast.")}">${icon("copy")}Copy report</button>
      <button class="btn small" id="con-save" data-tip="${tip("💾 Save report", "Saves the same report as a text file.")}">${icon("file")}Save</button>
      <button class="btn small" id="con-folder" data-tip="${tip("🪵 Log files", "Opens the folder with the full log files.")}">${icon("folder")}Logs</button>
      <button class="btn small" id="con-clear" data-tip="${tip("🧹 Clear", "Clears this view. The log files are kept.")}">${icon("trash")}</button>
      <button class="icon-btn" id="con-close" aria-label="Close console" style="width:36px;height:36px">${icon("x")}</button>
    </div><div class="console-body" id="console-body"></div>`;
  for (const b of $$("#con-level button", el)) b.onclick = () => { CON.level = b.dataset.lv; $$("#con-level button", el).forEach((x) => x.classList.toggle("on", x === b)); drawLogs(false); };
  $("#con-q", el).oninput = (e) => { CON.query = e.target.value; drawLogs(false); };
  $("#con-copy", el).onclick = copyReport;
  $("#con-save", el).onclick = async () => { const r = await api.save_report(); if (r.saved) toast(`💾 Report saved to ${esc(fileName(r.path))}`); };
  $("#con-folder", el).onclick = () => api.open_logs();
  $("#con-clear", el).onclick = async () => { await api.clear_logs(); CON.entries = []; CON.last = 0; CON.seenErrors = 0; drawLogs(false); };
  $("#con-close", el).onclick = closeConsole;
  $("#console-body", el).onclick = (e) => {
    const row = e.target.closest(".log.has-detail");
    if (!row || window.getSelection().toString()) return;
    const id = Number(row.dataset.log);
    CON.expanded.has(id) ? CON.expanded.delete(id) : CON.expanded.add(id);
    drawLogs(true);
  };
  $("#console-badge").classList.add("hidden");
  drawLogs(false);
  restartPolling();
}

function closeConsole() { CON.open = false; $("#console").classList.add("hidden"); restartPolling(); }
function toggleConsole() { CON.open ? closeConsole() : openConsole(); }
function restartPolling() { clearInterval(CON.timer); CON.timer = setInterval(pollLogs, CON.open ? 1000 : 4000); pollLogs(); }

async function copyReport() {
  const text = await api.diagnostics_report();
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch { /* fall back below */ }
  if (!ok) {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
  }
  toast(ok ? "📋 Report copied. Paste it to Claude to get help." : "⚠️ Couldn't copy. Use Save instead.");
}

$("#btn-console").onclick = toggleConsole;

// Errors in the window itself are logged with a code too
window.addEventListener("error", (e) => {
  try { api && api.log_js("error", e.message || "Window error", `${e.filename || ""}:${e.lineno || ""}:${e.colno || ""}\n${(e.error && e.error.stack) || ""}`); } catch { /* ignore */ }
});
window.addEventListener("unhandledrejection", (e) => {
  const msg = (e.reason && (e.reason.message || String(e.reason))) || "Unknown problem";
  try { api && api.log_js("error", `Action failed: ${msg}`, (e.reason && e.reason.stack) || ""); } catch { /* ignore */ }
  toast(`❌ Something went wrong (RNX-W001). Details are in the console.`, { action: { label: "Open console", fn: openConsole }, timeout: 9000 });
});

// ------------------------------------------------------------------ start up
async function init() {
  api = window.pywebview.api;
  const st = await api.get_state();
  S.settings = st.settings; S.formats = st.formats; S.tokens = st.tokens; S.version = st.version; S.stats = st.stats; S.platform = st.platform;
  applyLook();
  setConsoleVisible(S.settings.general.show_console !== false);
  S.view = S.settings.general.view || "list";
  restartPolling();
  setMode(S.settings.general.default_mode || "auto");
  fillFormatSelects();
  $("#chk-amber").checked = !S.settings.safety.skip_amber;
  for (const it of st.items || []) { S.items.set(it.id, it); S.order.push(it.id); }
  if (S.order.length) { showScreen("main"); renderAll(); }
  refreshStats();
  if (st.pending_journal) showRecovery(st.pending_journal);
  if (!st.token_set) banner("token", "", "🔑 No TheMovieDB key found, so matching won't work yet.", [["Add a key", () => openSettings("matching"), true]]);
  if (!st.mediainfo) console.info("MediaInfo not available: runtime checks are off.");
  if (!S.settings.general.first_run_done) openWelcome(1);
  if (S.settings.updates.auto_check) {
    const last = Number(localStorageGet("rennix-update-check") || 0);
    if (Date.now() - last > 86400000) {
      localStorageSet("rennix-update-check", String(Date.now()));
      api.check_updates().then((r) => { if (r.available) showUpdate(r); });
    }
  }
}
function localStorageGet(k) { try { return window.localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { window.localStorage.setItem(k, v); } catch { /* ignore */ } }

if (window.pywebview && window.pywebview.api) init();
else window.addEventListener("pywebviewready", init);
