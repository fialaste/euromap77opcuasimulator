"use strict";
// Euromap 77 Simulator UI. Plain JS, polls the local server once a second.

const S = {
  machines: [], cur: 0, tree: null, byPath: {}, state: null,
  sel: null, open: new Set(), tab: "events", search: "", onlyAS: false, sideDirty: true,
};
const $ = (q, el = document) => el.querySelector(q);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function toast(msg, err) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.className = "toast"), 2600);
}
async function act(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); await poll(); return r; } catch (e) { toast(e.message, true); }
}
const M = p => `/ui/m/${S.cur}/${p}`;

function fmt(v, node) {
  if (v === null || v === undefined) return "—";
  if (node && node.enum && !node.array && node.enum[String(v)] !== undefined) return node.enum[String(v)];
  if (typeof v === "number") {
    if (node && node.type === "Duration") return (v / 1000).toFixed(2) + " s";
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return v.length > 3 ? `[${v.length}]` : JSON.stringify(v);
  if (typeof v === "object") return "{…}";
  return String(v);
}
const unit = n => (n && n.unit ? " " + n.unit : "");

// ---------------------------------------------------------------- machines
async function loadMachines() {
  S.machines = await api("/ui/machines");
  renderTabs();
}
function statusDot(o) {
  if (!o.running) return "bad";
  return o.producing ? "ok" : "warn";
}
function renderTabs() {
  $("#tabs").innerHTML = S.machines.map((o, i) =>
    `<button class="${i === S.cur ? "active" : ""}" data-i="${i}"><span class="dot ${statusDot(o)}"></span>${esc(o.name)}</button>`).join("");
}
$("#tabs").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  S.cur = +b.dataset.i; S.sel = null; S.tree = null; S.open = new Set(); S.sideDirty = true;
  $("#editor").innerHTML = '<p class="muted">Vyberte uzel ve stromu vlevo.</p>';
  renderTabs(); loadTree();
});

async function loadTree() {
  S.tree = await api(M("tree"));
  S.byPath = {};
  for (const n of S.tree.nodes) S.byPath[n.path] = n;
  const root = S.byPath[""];
  if (root) { S.open.add(""); root.children.forEach(c => S.open.add(c)); }
  await poll();
  renderTree();
}

// --------------------------------------------------------------- overview
function renderOverview() {
  const o = S.state.overview;
  const ms = v => (v === null || v === undefined ? "—" : (v / 1000).toFixed(2) + " s");
  const kv = (k, v) => `<div class="kv"><b>${k}</b><span>${v}</span></div>`;
  $("#overview").innerHTML = [
    kv("Stroj", `${esc(o.model)} <span class="muted">(${esc(o.profile)})</span>`),
    kv("Endpoint", `<code>${esc(o.endpoint)}</code>`),
    kv("Server", o.running ? `<span class="pill ok">běží</span> ${o.clients} klient(ů)` : `<span class="pill bad">zastaven</span>`),
    kv("MachineMode", esc(o.machine_mode)),
    kv("ProductionStatus", `<span class="pill ${o.producing ? "ok" : "warn"}">${esc(o.production_status)}</span>`),
    o.job_status ? kv("Zakázka", `${esc(o.job)} · ${esc(o.job_status)}`) : kv("Zakázka", esc(o.job)),
    kv("Cykly", o.cycles ?? "—"),
    kv("Dobré / zmetky", `${o.good ?? "—"} / ${o.bad ?? "—"}${o.nominal ? ` z ${o.nominal}` : ""}`),
    kv("Poslední / průměr", `${ms(o.last_cycle_time)} / ${ms(o.average_cycle_time)}`),
    `<div class="kv"><b>${o.producing ? "Probíhá cyklus" : "Stojí: " + esc(o.reason)}</b><div class="progress"><div style="width:${Math.round(o.progress * 100)}%"></div></div></div>`,
    `<div class="actions">
      <button data-a="toggle_server">${o.running ? "Zastavit server" : "Spustit server"}</button>
      <button data-a="toggle_cycle">${o.cycle_enabled ? "Zastavit cyklus" : "Spustit cyklus"}</button>
      <button data-a="randomize">Vše náhodně</button>
      <button data-a="freeze">Zmrazit vše</button>
    </div>`,
  ].join("");
}
$("#overview").addEventListener("click", e => {
  const b = e.target.closest("button[data-a]"); if (!b) return;
  act(() => api(M("action"), { action: b.dataset.a }));
});

// ------------------------------------------------------------------- tree
function visible(n) {
  if (S.onlyAS && n.nodeclass === "Variable" && !["A", "S"].includes(n.cls)) return false;
  if (S.onlyAS && n.nodeclass === "Method") return false;
  return true;
}
function matches(n) {
  return !S.search || n.path.toLowerCase().includes(S.search);
}
function subtreeMatches(n) {
  if (matches(n) && visible(n) && n.nodeclass !== "Object") return true;
  return n.children.some(c => S.byPath[c] && subtreeMatches(S.byPath[c]));
}
function renderTree() {
  if (!S.tree) return;
  const out = [];
  const walk = (path, depth) => {
    const n = S.byPath[path]; if (!n) return;
    const kids = n.children.filter(c => S.byPath[c]);
    if (path !== "" && (S.search || S.onlyAS) && !subtreeMatches(n)) return;
    const isOpen = S.open.has(path) || (S.search && kids.length);
    const tw = kids.length ? (isOpen ? "▾" : "▸") : "";
    const gen = S.state && S.state.generators[path] && S.state.generators[path].enabled ? '<span class="gen" title="aktivní generátor">⚙</span>' : "";
    const val = n.nodeclass === "Variable" ? `<span class="val" data-v="${esc(path)}">${esc(fmt(S.state?.values[path], n))}${esc(unit(n))}</span>` : "";
    const badge = n.cls ? `<span class="badge ${n.cls}">${n.cls}</span>` : "";
    const name = path === "" ? n.name : n.name;
    out.push(`<div class="node ${n.nodeclass === "Object" ? "obj" : ""} ${S.sel === path ? "sel" : ""}" data-p="${esc(path)}" style="padding-left:${8 + depth * 14}px">
      <span class="tw">${tw}</span>${badge}<span class="nm">${esc(name)}</span>${gen}${val}</div>`);
    if (isOpen) {
      const order = c => ({ Variable: 0, Method: 2 }[S.byPath[c].nodeclass] ?? 1);
      kids.slice().sort((a, b) => order(a) - order(b)).forEach(c => walk(c, depth + 1));
    }
  };
  walk("", 0);
  $("#tree").innerHTML = out.join("");
}
function updateTreeValues() {
  for (const el of document.querySelectorAll("#tree .val")) {
    const p = el.dataset.v, n = S.byPath[p];
    el.textContent = fmt(S.state.values[p], n) + unit(n);
  }
}
$("#tree").addEventListener("click", e => {
  const row = e.target.closest(".node"); if (!row) return;
  const p = row.dataset.p, n = S.byPath[p];
  if (n.children.length && (e.target.classList.contains("tw") || n.nodeclass === "Object")) {
    S.open.has(p) ? S.open.delete(p) : S.open.add(p);
  }
  S.sel = p; renderTree(); renderEditor();
});
$("#search").addEventListener("input", e => { S.search = e.target.value.trim().toLowerCase(); renderTree(); });
$("#onlyAS").addEventListener("change", e => { S.onlyAS = e.target.checked; renderTree(); });

// ----------------------------------------------------------------- editor
function inputFor(n, v) {
  if (n.enum && !n.array) {
    return `<select id="val">${Object.entries(n.enum).map(([k, t]) => `<option value="${k}" ${String(v) === k ? "selected" : ""}>${k} – ${esc(t)}</option>`).join("")}</select>`;
  }
  if (n.type === "Boolean" && !n.array) {
    return `<select id="val"><option value="true" ${v ? "selected" : ""}>true</option><option value="false" ${!v ? "selected" : ""}>false</option></select>`;
  }
  if (n.array || (v && typeof v === "object")) {
    return `<textarea id="val">${esc(JSON.stringify(v, null, 1))}</textarea>`;
  }
  const numeric = ["Double", "Float", "Duration", "UInt16", "UInt32", "UInt64", "Int16", "Int32", "Int64", "Byte"].includes(n.type);
  return `<input id="val" ${numeric ? 'type="number" step="any"' : ""} value="${esc(v ?? "")}" style="min-width:220px">${esc(unit(n))}`;
}
function genEditor(n) {
  const kinds = n.cls === "A" ? S.tree.analog_kinds : S.tree.state_kinds;
  const g = S.state.generators[n.path];
  const kind = g ? g.kind : "";
  const params = g ? g.params : {};
  return `<h3>Generátor</h3>
    <div class="row">
      <select id="gkind"><option value="">— žádný —</option>${Object.keys(kinds).map(k => `<option ${k === kind ? "selected" : ""}>${k}</option>`).join("")}</select>
      <label class="chk"><input type="checkbox" id="genabled" ${!g || g.enabled ? "checked" : ""}> zapnutý</label>
      <input id="gseed" placeholder="seed" style="width:80px" value="${esc(g?.seed ?? "")}">
      <button class="primary" id="gsave">Uložit generátor</button>
    </div>
    <textarea id="gparams" rows="5">${esc(JSON.stringify(params, null, 1))}</textarea>
    <p class="muted">Parametry jsou JSON. Perioda je v sekundách. ${n.cls === "S" ? "U stavů jsou klíče vah hodnoty výčtu (např. {\"1\": 5, \"4\": 1})." : "follow sleduje sourozenecký uzel (target), např. NominalTemperature."}</p>`;
}
async function renderEditor() {
  const n = S.byPath[S.sel];
  const ed = $("#editor");
  if (!n) { ed.innerHTML = '<p class="muted">Vyberte uzel ve stromu vlevo.</p>'; return; }
  const v = S.state.values[n.path];
  const meta = `<dl class="meta">
      <dt>BrowsePath</dt><dd>${esc(n.path || n.name)}</dd>
      <dt>NodeId</dt><dd>${esc(n.nodeid)}</dd>
      ${n.type ? `<dt>DataType</dt><dd>${esc(n.type)}${n.array ? "[]" : ""}</dd>` : ""}
      ${n.cls ? `<dt>Třída</dt><dd>${n.cls}${n.writable ? " · zapisovatelný klientem" : ""}</dd>` : ""}
      ${n.range ? `<dt>EURange</dt><dd>${n.range[0]} … ${n.range[1]}${esc(unit(n))}</dd>` : ""}
    </dl>`;
  let body = "";
  if (n.nodeclass === "Variable") {
    body += `<div class="big" id="liveval">${esc(fmt(v, n))}${esc(unit(n))}</div>`;
    if (n.cls === "C") {
      body += `<p class="muted">Čítač nebo odvozená hodnota, počítá ji simulace cyklu. Nulování přes metody ResetJobCounters / ResetBoxCounters.</p>`;
    } else {
      body += `<h3>Ruční hodnota</h3><div class="row">${inputFor(n, v)}<button class="primary" id="setv">Nastavit</button></div>`;
      if (S.state.generators[n.path]) body += `<p class="muted">Ruční zápis pozastaví generátor uzlu.</p>`;
    }
    if (n.cls === "A" || n.cls === "S") body += genEditor(n);
  } else if (n.nodeclass === "Method") {
    const args = await api(M("method") + "?path=" + encodeURIComponent(n.path));
    body += `<h3>Argumenty</h3>` + (args.length ? `<div class="form">${args.map((a, i) => `
        <label title="${esc(a.description)}">${esc(a.name)} <span class="muted">${esc(a.type)}${a.array ? "[]" : ""}</span></label>
        ${a.template ? `<textarea data-arg="${i}" rows="6">${esc(JSON.stringify(a.template, null, 1))}</textarea>`
          : `<input data-arg="${i}" placeholder="${a.array ? "hodnoty oddělené čárkou" : ""}">`}`).join("")}</div>` : `<p class="muted">Metoda nemá vstupní argumenty.</p>`) +
      `<div class="row"><button class="primary" id="callm">Zavolat</button></div><pre id="callres" class="muted"></pre>`;
  } else {
    body += `<p class="muted">${n.children.length} podřízených uzlů.</p>`;
  }
  ed.innerHTML = `<h2>${esc(n.name)}</h2><div class="muted">${esc(n.description)}</div>${meta}${body}`;

  $("#setv")?.addEventListener("click", () => {
    const el = $("#val");
    act(() => api(M("set"), { path: n.path, value: el.value }), "Nastaveno");
  });
  $("#gkind")?.addEventListener("change", e => {
    const kinds = n.cls === "A" ? S.tree.analog_kinds : S.tree.state_kinds;
    const k = e.target.value;
    if (k) $("#gparams").value = JSON.stringify({ ...S.tree.common, ...kinds[k], ...defaultsFor(n, k) }, null, 1);
  });
  $("#gsave")?.addEventListener("click", () => {
    let params;
    try { params = JSON.parse($("#gparams").value || "{}"); } catch { return toast("Parametry nejsou platný JSON", true); }
    act(() => api(M("generator"), { path: n.path, kind: $("#gkind").value || null, params, enabled: $("#genabled").checked, seed: $("#gseed").value }), "Generátor uložen")
      .then(() => { renderTree(); });
  });
  $("#callm")?.addEventListener("click", async () => {
    const args = [...document.querySelectorAll("[data-arg]")].map(el => el.value);
    const r = await act(() => api(M("call"), { path: n.path, args }), "Metoda zavolána");
    if (r) $("#callres").textContent = r.result && r.result.length ? JSON.stringify(r.result, null, 1) : "OK";
  });
}
function defaultsFor(n, kind) {
  const v = S.state.values[n.path];
  if (n.cls === "S" && kind === "weighted" && n.enum) return { weights: Object.fromEntries(Object.keys(n.enum).map(k => [k, 1])) };
  if (n.cls === "S" && kind === "weighted" && n.type === "Boolean") return { weights: { true: 1, false: 1 } };
  if (n.cls === "A" && n.range) {
    const [lo, hi] = n.range;
    if (kind === "gauss") return { mean: v ?? (lo + hi) / 2, sigma: (hi - lo) / 200 };
    if (kind === "uniform") return { min: lo, max: hi };
    if (kind === "walk") return { step: (hi - lo) / 500, min: lo, max: hi };
    if (kind === "constant") return { value: v ?? lo };
    if (kind === "sine") return { offset: v ?? (lo + hi) / 2, amplitude: (hi - lo) / 20, period: 60 };
  }
  return {};
}
function updateEditorValue() {
  const n = S.byPath[S.sel]; const el = $("#liveval");
  if (n && el) el.textContent = fmt(S.state.values[n.path], n) + unit(n);
}

// ------------------------------------------------------------- side panel
$("#sideTabs").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  S.tab = b.dataset.tab; S.sideDirty = true;
  for (const x of document.querySelectorAll("#sideTabs button")) x.classList.toggle("active", x === b);
  renderSide();
});
function renderEvents() {
  const evs = S.state.events;
  if (!evs.length) return '<p class="muted">Zatím žádné události.</p>';
  return evs.map((e, i) => `<div class="ev ${S.openEv === e.time + e.type ? "open" : ""}" data-k="${esc(e.time + e.type)}">
    <div><span class="t">${esc(e.time.slice(11, 19))}</span> <span class="ty">${esc(e.type.replace(/^Sim/, ""))}</span></div>
    <div>${esc(e.message)}</div><pre>${esc(JSON.stringify(e.fields, null, 1))}</pre></div>`).join("");
}
function renderCycle() {
  const c = S.state.cycle;
  const last = (c.last && c.last[0]) || {};
  const q = { 0: "GOOD_CYCLE", 1: "BAD_CYCLE", 2: "TEST_SAMPLE_CYCLE", 3: "FAILED_CYCLE" }[c.quality] ?? "—";
  return `<p>Poslední cyklus: <b>${q}</b>. Parametry se vzorkují z normálního rozdělení kolem nominálu, mimo toleranci je díl zmetek. Časy jsou v ms.</p>
    <table class="cyc"><tr><th>Parametr</th><th>nominál</th><th>σ</th><th>tol ±</th><th>poslední</th><th></th></tr>
    ${Object.entries(c.cfg).map(([k, v]) => `<tr data-cp="${esc(k)}"><td>${esc(k)}</td>
      <td><input data-f="nominal" value="${v.nominal ?? ""}"></td><td><input data-f="sigma" value="${v.sigma ?? ""}"></td>
      <td><input data-f="tol" value="${v.tol ?? ""}"></td><td class="num">${last[k] !== undefined ? last[k] : "—"}</td>
      <td><button class="small" data-save>Uložit</button></td></tr>`).join("")}</table>
    <div class="row"><button data-a="test_sample">Další cyklus jako testovací díl</button></div>`;
}
function renderJobs() {
  const o = S.state.overview;
  if (!o.facets.includes("jobs")) return '<p class="muted">Profil stroje nemá Jobs facet.</p>';
  const j = S.state.jobs;
  const reqs = S.tree.requests.filter(r => r.includes("Job") || r.includes("Material"));
  return `<h3 style="margin-top:0">Fronta cyklických zakázek (SendCyclicJobList)</h3>
    ${j.queue.length ? `<table><tr><th>JobName</th><th>NominalParts</th><th>MouldId</th></tr>${j.queue.map(x => `<tr><td>${esc(x.JobName)}</td><td>${esc(x.NominalParts)}</td><td>${esc(x.MouldId)}</td></tr>`).join("")}</table>` : '<p class="muted">Prázdná.</p>'}
    <div class="row"><button data-a="prepare_next">Připravit další z fronty</button><button data-a="new_box">Nová krabice</button></div>
    <h3>Seznam zakázek (SendJobList)</h3>
    ${j.list.length ? `<table><tr><th>JobName</th><th>Zákazník</th><th></th></tr>${j.list.map((x, i) => `<tr><td>${esc(x.JobName)}</td><td>${esc(x.CustomerName)}</td><td><button class="small" data-prep="${i}">Připravit</button></td></tr>`).join("")}</table>` : '<p class="muted">Prázdný.</p>'}
    <p class="muted">StartJob, InterruptJob, FinishJob a další metody najdete ve stromu pod Jobs/ActiveJobValues.</p>
    <h3>Požadavky stroje na MES</h3>${requestForms(reqs)}`;
}
const REQ_FIELDS = {
  RequestCyclicJobWriteEventType: ["JobName"],
  RequestAddMaterialEventType: ["Id"],
  RequestProductionDatasetListEventType: ["MouldId", "NameFilter"],
  RequestProductionDatasetReadEventType: ["Name", "Storage"],
  RequestProductionDatasetWriteEventType: ["Name", "Storage", "Components"],
};
function requestForms(reqs) {
  return reqs.map(r => `<div class="row" data-req="${r}"><b style="min-width:100%">${r.replace("EventType", "")}</b>
    ${(REQ_FIELDS[r] || []).map(f => `<input data-rf="${f}" placeholder="${f}${f === "Storage" ? " (1/2/4)" : ""}" style="width:130px">`).join("")}
    <button class="small" data-send>Vyvolat</button></div>`).join("");
}
function renderDatasets() {
  const d = S.state.datasets;
  if (!d) return '<p class="muted">Profil stroje nemá ProductionDatasetManagement facet.</p>';
  const reqs = S.tree.requests.filter(r => r.includes("Dataset"));
  return `<h3 style="margin-top:0">Aktivní datová sada</h3><p><b>${esc(d.active?.Name)}</b> ${esc(d.active?.Description)}</p>
    <h3>Úložiště stroje (FILE_SYSTEM)</h3>
    <table><tr><th>Name</th><th>MouldId</th><th>Uloženo</th></tr>${d.library.map(x => `<tr><td>${esc(x.Name)}</td><td>${esc(x.MouldId)}</td><td>${esc((x.LastSaveTimestamp || "").slice(0, 19))}</td></tr>`).join("")}</table>
    <p class="muted">Probíhající přenosy: ${d.transfers}. Load/Save a přenos souborů najdete ve stromu pod ProductionDatasetManagement.</p>
    <h3>Seznam sad z MES (SendProductionDatasetList)</h3>
    ${d.mes_list.length ? `<table><tr><th>Name</th><th>MouldId</th></tr>${d.mes_list.map(x => `<tr><td>${esc(x.Name)}</td><td>${esc(x.MouldId)}</td></tr>`).join("")}</table>` : '<p class="muted">Prázdný.</p>'}
    <h3>Požadavky stroje na MES</h3>${requestForms(reqs)}`;
}
function renderMessages() {
  const cls = S.tree.classifications;
  const v = S.state.values;
  const reasons = v["MachineMESConfiguration/StandstillReasons"] || [];
  const users = S.tree.users.map(u => `<tr><td>${esc(v[u + "/Name"])}</td><td>${esc(v[u + "/Id"])}</td>
    <td>${v[u + "/IsPresent"] ? '<span class="pill ok">přihlášen</span>' : '<span class="pill">odhlášen</span>'}</td>
    <td><button class="small" data-user="${esc(u)}" data-present="${v[u + "/IsPresent"] ? "false" : "true"}">${v[u + "/IsPresent"] ? "Odhlásit" : "Přihlásit"}</button></td></tr>`).join("");
  return `<h3 style="margin-top:0">Hlášení stroje (MessageLogType)</h3>
    <div class="form">
      <label>Id</label><input id="mid" value="E-101">
      <label>Text</label><input id="mtext" value="Teplota trysky mimo toleranci">
      <label>Závažnost</label><input id="msev" type="number" value="600" min="1" max="1000">
      <label>Klasifikace</label><select id="mcls">${Object.entries(cls).map(([k, t]) => `<option value="${k}" ${k === "111" ? "selected" : ""}>${k} ${esc(t)}</option>`).join("")}</select>
      <label>Prostoj</label><label class="chk"><input type="checkbox" id="mstand"> zastaví výrobu (StandstillMessage)</label>
    </div>
    <div class="row"><button class="primary" id="msend">Vyvolat hlášení</button>
      ${S.state.overview.standstill ? '<button class="danger" data-a="clear_standstill">Zrušit prostoj</button>' : ""}</div>
    <h3>Důvod prostoje (StandstillReasonId)</h3>
    <div class="row"><select id="reason"><option value="">—</option>${reasons.map(r => `<option value="${esc(r.Id)}" ${v["MachineMESStatus/StandstillReasonId"] === r.Id ? "selected" : ""}>${esc(r.Id)} ${esc(r.Text)}</option>`).join("")}</select>
      <button id="rset">Nastavit</button></div>
    <h3>Obsluha (UserLogType)</h3><table>${users}</table>`;
}
function renderSide() {
  if (!S.state || !S.tree) return;
  const side = $("#side");
  // Re-render forms only when the tab changes, so typing is not interrupted; lists refresh every poll.
  const live = ["events", "jobs", "datasets"].includes(S.tab);
  if (!live && !S.sideDirty) {
    if (S.tab === "cycle") for (const tr of side.querySelectorAll("tr[data-cp]")) {
      const k = tr.dataset.cp, last = (S.state.cycle.last && S.state.cycle.last[0]) || {};
      tr.querySelector("td.num").textContent = last[k] !== undefined ? last[k] : "—";
    }
    return;
  }
  if (live && side.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
  S.sideDirty = false;
  side.innerHTML = { events: renderEvents, cycle: renderCycle, jobs: renderJobs, datasets: renderDatasets, messages: renderMessages }[S.tab]();
}
$("#side").addEventListener("click", e => {
  const ev = e.target.closest(".ev");
  if (ev) { S.openEv = S.openEv === ev.dataset.k ? null : ev.dataset.k; ev.classList.toggle("open"); return; }
  const a = e.target.closest("button[data-a]");
  if (a) return act(() => api(M("action"), { action: a.dataset.a }), "Hotovo").then(() => (S.sideDirty = true));
  const save = e.target.closest("button[data-save]");
  if (save) {
    const tr = save.closest("tr"), body = { action: "cycle_param", name: tr.dataset.cp };
    for (const i of tr.querySelectorAll("input")) body[i.dataset.f] = i.value;
    return act(() => api(M("action"), body), "Parametr uložen");
  }
  const prep = e.target.closest("button[data-prep]");
  if (prep) return act(() => api(M("action"), { action: "prepare_job", index: +prep.dataset.prep }), "Zakázka připravena");
  const send = e.target.closest("button[data-send]");
  if (send) {
    const row = send.closest("[data-req]"), fields = {};
    for (const i of row.querySelectorAll("input")) fields[i.dataset.rf] = i.value;
    return act(() => api(M("action"), { action: "request", type: row.dataset.req, fields }), "Událost odeslána");
  }
  if (e.target.id === "msend") {
    return act(() => api(M("action"), {
      action: "message", id: $("#mid").value, text: $("#mtext").value, severity: $("#msev").value,
      classification: $("#mcls").value, standstill: $("#mstand").checked,
    }), "Hlášení vyvoláno").then(() => (S.sideDirty = true));
  }
  if (e.target.id === "rset") {
    return act(() => api(M("set"), { path: "MachineMESStatus/StandstillReasonId", value: $("#reason").value }), "Nastaveno");
  }
  const u = e.target.closest("button[data-user]");
  if (u) return act(() => api(M("set"), { path: u.dataset.user + "/IsPresent", value: u.dataset.present }), "Hotovo").then(() => (S.sideDirty = true));
});

// ------------------------------------------------------------------- loop
async function poll() {
  if (!S.tree) return;
  try {
    S.state = await api(M("state"));
  } catch (e) { return; }
  S.machines[S.cur] = S.state.overview;
  renderOverview();
  updateTreeValues();
  updateEditorValue();
  if (S.tab === "messages") {
    const v = S.state.values;
    const sig = JSON.stringify([S.state.overview.standstill, v["MachineMESStatus/StandstillReasonId"], S.tree.users.map(u => v[u + "/IsPresent"])]);
    if (sig !== S.msgSig) { S.msgSig = sig; S.sideDirty = true; }
  }
  renderSide();
}
async function main() {
  await loadMachines();
  await loadTree();
  setInterval(poll, 1000);
  setInterval(async () => { try { S.machines = await api("/ui/machines"); renderTabs(); } catch { } }, 3000);
}
main();
