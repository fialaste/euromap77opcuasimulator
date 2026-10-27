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
const M = p => `/ui/m/${encodeURIComponent(S.machines[S.cur]?.name ?? "")}/${p}`;

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
function statusDot(o) {
  if (!o.running) return "bad";
  return o.producing ? "ok" : "warn";
}
function renderTabs() {
  const full = S.machines.length >= (S.max || 3);
  $("#addMachine").disabled = full;
  $("#addMachine").title = full ? "Běží nejvíc strojů, kolik simulátor pojme. Nejdřív některý odeberte." : "";
  $("#tabs").innerHTML = S.machines.map((o, i) =>
    `<button class="${i === S.cur ? "active" : ""}" data-i="${i}"><span class="dot ${statusDot(o)}"></span>${esc(o.name)}</button>`).join("");
}
$("#tabs").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  S.cur = +b.dataset.i; S.sel = null; S.tree = null; S.open = new Set(); S.sideDirty = true;
  $("#editor").innerHTML = '<p class="muted">Vyberte uzel ve stromu vlevo.</p>';
  renderTabs(); loadTree();
});

async function reloadMachines(cur) {
  S.machines = await api("/ui/machines");
  S.cur = Math.min(cur ?? S.cur, Math.max(0, S.machines.length - 1));
  S.sel = null; S.tree = null; S.state = null; S.open = new Set(); S.sideDirty = true;
  $("#editor").innerHTML = '<p class="muted">Vyberte uzel ve stromu vlevo.</p>';
  renderTabs();
  const none = !S.machines.length;
  $("#empty").hidden = !none; $("main").hidden = none; $("#overview").hidden = none;
  if (!none) await loadTree();
}
// A destructive button asks once more on its own label instead of a dialog
S.armed = {};
const armed = id => (S.armed[id] || 0) > Date.now();
function confirmClick(btn, label, fn) {
  if (armed(btn.id)) { S.armed[btn.id] = 0; return fn(); }
  const text = btn.textContent;
  S.armed[btn.id] = Date.now() + 3000;
  btn.textContent = label; btn.classList.add("confirm");
  setTimeout(() => { if (!armed(btn.id)) { btn.textContent = text; btn.classList.remove("confirm"); } }, 3100);
}
$("#resetMachines").addEventListener("click", e => confirmClick(e.target, "Opravdu obnovit?", () =>
  act(() => api("/ui/fleet/reset", {}), "Výchozí stroje obnoveny").then(() => reloadMachines(0))));

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
      <a class="button" href="${M("export")}" download="${esc(o.name)}.yaml">Uložit jako YAML</a>
      ${armed("removeMachine") ? '<button id="removeMachine" class="danger confirm">Opravdu odebrat?</button>' : '<button id="removeMachine" class="danger">Odebrat stroj</button>'}
    </div>`,
  ].join("");
}
$("#overview").addEventListener("click", e => {
  if (e.target.id === "removeMachine") return confirmClick(e.target, "Opravdu odebrat?", () =>
    act(() => api("/ui/fleet/remove", { name: S.machines[S.cur].name }), "Stroj odebrán").then(() => reloadMachines(Math.max(0, S.cur - 1))));
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
function genDefaults(n) {
  const v = S.state.values[n.path];
  if (n.cls === "S") return { nominal: v, deviation: 0.02, tau: 30, enabled: true };
  const sib = n.path.replace(/[^/]+$/, "NominalTemperature");
  const lo = n.range ? n.range[0] : 0, hi = n.range ? n.range[1] : 100;
  return { nominal: S.byPath[sib] ? "NominalTemperature" : (v ?? (lo + hi) / 2), deviation: +((hi - lo) / 500).toPrecision(2), tau: 10, enabled: true };
}
function genEditor(n) {
  const g = S.state.generators[n.path];
  const x = g || genDefaults(n);
  const st = n.cls === "S";
  let nominal;
  if (st) {
    const opts = n.enum ? Object.entries(n.enum) : [["true", "true"], ["false", "false"]];
    nominal = `<select id="gnom">${opts.map(([k, t]) => `<option value="${k}" ${String(x.nominal) === k ? "selected" : ""}>${n.enum ? k + " – " : ""}${esc(t)}</option>`).join("")}</select>`;
  } else {
    nominal = `<input id="gnom" value="${esc(x.nominal ?? "")}" placeholder="aktuální hodnota">`;
  }
  const dev = st ? Math.round(x.deviation * 1000) / 10 : x.deviation;
  return `<h3>Generátor ${g ? (g.enabled ? '<span class="pill ok">běží</span>' : '<span class="pill">vypnutý</span>') : ""}</h3>
    <p class="muted">${st ? "Hodnota stojí na nominálním stavu, občas na chvíli přejde do jiného stavu a vrátí se."
      : "Hodnota sleduje nominál s časovou konstantou a přidává se k ní náhodný šum."}</p>
    <div class="gen-fields">
      <label for="gnom">${st ? "Nominální stav" : "Nominál"}</label>${nominal}<span class="muted">${st ? "" : "číslo" + esc(unit(n)) + " nebo sousední uzel, např. NominalTemperature; prázdné = aktuální hodnota"}</span>
      <label for="gdev">${st ? "Odchylka" : "Odchylka σ"}</label><input id="gdev" type="number" step="any" min="0" value="${esc(dev)}"><span class="muted">${st ? "% šance za sekundu" : esc(unit(n).trim() || "")}</span>
      <label for="gtau">${st ? "Doba odchylky" : "Časová konstanta"}</label><input id="gtau" type="number" step="any" min="0" value="${esc(x.tau)}"><span class="muted">s</span>
    </div>
    <div class="row">
      <label class="chk"><input type="checkbox" id="genabled" ${x.enabled ? "checked" : ""}> zapnutý</label>
      <button class="primary" id="gsave">${g ? "Uložit generátor" : "Zapnout generátor"}</button>
      ${g ? '<button id="gdel">Odebrat generátor</button>' : ""}
    </div>`;
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
  $("#gsave")?.addEventListener("click", () => {
    const st = n.cls === "S";
    const dev = parseFloat($("#gdev").value || "0");
    const body = { path: n.path, nominal: $("#gnom").value, deviation: st ? dev / 100 : dev, tau: $("#gtau").value, enabled: $("#genabled").checked };
    act(() => api(M("generator"), body), "Generátor uložen").then(() => { renderTree(); renderEditor(); });
  });
  $("#gdel")?.addEventListener("click", () => {
    act(() => api(M("generator"), { path: n.path, generate: false }), "Generátor odebrán").then(() => { renderTree(); renderEditor(); });
  });
  $("#callm")?.addEventListener("click", async () => {
    const args = [...document.querySelectorAll("[data-arg]")].map(el => el.value);
    const r = await act(() => api(M("call"), { path: n.path, args }), "Metoda zavolána");
    if (r) $("#callres").textContent = r.result && r.result.length ? JSON.stringify(r.result, null, 1) : "OK";
  });
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

// ------------------------------------------------------------- wizard
const W = { step: 0, cat: null, preset: "generic", form: null, port: "" };
const LEVELS = [
  { id: "basic", title: "Základ", fixed: true, d: "Sledování stroje: kdo stroj je, v jakém je stavu a co právě vyrábí.",
    ex: "Identita, režim (automat, seřizování…), stav výroby, teploty a časy cyklu, čítače dílů, událost po každém cyklu, hlášení a prostoje." },
  { id: "jobs", title: "Zakázky", d: "MES posílá stroji zakázky a stroj hlásí jejich průběh.",
    ex: "Spuštění, přerušení a ukončení zakázky, požadovaný počet kusů, dobré díly a zmetky na zakázku." },
  { id: "datasets", title: "Datové sady", d: "Přenos nastavení stroje (programu formy) mezi strojem a MES.",
    ex: "Seznam uložených sad, stažení sady ze stroje, nahrání sady do stroje, aktivace sady." },
];
async function openWizard() {
  try { W.cat = await api("/ui/catalog"); } catch (e) { return toast(e.message, true); }
  if (W.cat.count >= W.cat.max) return toast(`Běží ${W.cat.max} stroje, víc simulátor nepojme. Nejdřív některý odeberte.`, true);
  W.step = 0; pickPreset("generic"); W.port = W.cat.next_port;
  W.opener = document.activeElement;
  $("#wizard").hidden = false; renderWizard();
  $("#wizard .dialog [data-preset]")?.focus();
}
function closeWizard() {
  $("#wizard").hidden = true;
  W.opener?.focus?.();
}
function pickPreset(id) {
  W.preset = id;
  const e = W.cat.entries.find(x => x.id === id);
  W.form = JSON.parse(JSON.stringify(e.form));
  W.title = e.title;
}
function readStep() {
  if (W.step === 0) {
    for (const el of document.querySelectorAll("#wzBody [data-f]")) {
      W.form[el.dataset.f] = el.type === "number" ? +el.value : el.value;
    }
    W.port = $("#wzPort").value;
  } else if (W.step === 1) {
    W.form.facets = [...document.querySelectorAll("#wzBody [data-facet]:checked")].map(x => x.dataset.facet);
    W.form.options = [...document.querySelectorAll("#wzBody [data-opt]:checked")].map(x => x.dataset.opt);
  }
}
function stepType() {
  const card = e => `<button type="button" class="opt" data-preset="${e.id}" aria-pressed="${e.id === W.preset}"><span class="t">${esc(e.title)}</span><span class="s">${esc(e.description)}</span></button>`;
  const f = W.form;
  const field = (k, label, type = "text", attrs = "") => `<label>${label}<input data-f="${k}" type="${type}" value="${esc(f[k])}" ${attrs}></label>`;
  return `<h3>Jaký stroj chcete simulovat?</h3>
    <p class="lead">Vyberte předvolbu, nebo obecný lis, a upravte parametry. Předvolby pilotních strojů jsou odvozené z typového označení.</p>
    <div><div class="tag">Obecné</div><div class="cards">${W.cat.entries.filter(e => e.id === "generic").map(card).join("")}</div></div>
    <div><div class="tag">Předvolby pilotních strojů</div><div class="cards">${W.cat.entries.filter(e => e.id !== "generic").map(card).join("")}</div></div>
    <div><div class="tag">Parametry stroje</div><div class="fields" style="margin-top:6px">
      ${field("manufacturer", "Výrobce")}${field("model", "Typ")}${field("serial_number", "Sériové číslo")}
      ${field("controller_name", "Řízení")}${field("machine_name", "Název v hale")}
      <label>Pohon<select data-f="drive">${W.cat.drives.map(d => `<option ${d === f.drive ? "selected" : ""}>${d}</option>`).join("")}</select></label>
      ${field("injection_units", "Vstřikovací jednotky", "number", 'min="1" max="4"')}
      ${field("zones", "Topné zóny válce", "number", 'min="1" max="12"')}
      ${field("moulds", "Formy", "number", 'min="1" max="2"')}
      <label>Port OPC UA<input id="wzPort" type="number" min="1" max="65535" value="${esc(W.port)}"></label>
    </div></div>`;
}
function stepModel() {
  const f = W.form;
  const lvl = l => l.fixed
    ? `<div class="level fixed"><input type="checkbox" checked disabled><span class="t">${l.title} <span class="tag">povinné</span></span><span class="d">${l.d}</span><span class="ex">${l.ex}</span></div>`
    : `<label class="level"><input type="checkbox" data-facet="${l.id}" ${f.facets.includes(l.id) ? "checked" : ""}><span class="t">${l.title}</span><span class="d">${l.d}</span><span class="ex">${l.ex}</span></label>`;
  const grp = g => {
    const off = g.needs && !f.facets.includes(g.needs);
    return `<label class="group ${off ? "off" : ""}"><input type="checkbox" data-opt="${g.id}" ${f.options.includes(g.id) && !off ? "checked" : ""} ${off ? "disabled" : ""}><span>${esc(g.title)}</span><span class="s">${esc(g.text)}${off ? " Jen se zapnutou částí " + (g.needs === "jobs" ? "Zakázky" : "Datové sady") + "." : ""}</span></label>`;
  };
  return `<h3>Jaká data má stroj nabízet?</h3>
    <p class="lead">Datový model je Euromap 77, standard pro komunikaci vstřikovacího lisu s MES. Skládá se z povinného základu a dvou rozšíření. Určuje, co uvidí připojený klient.</p>
    ${LEVELS.map(lvl).join("")}
    <div><div class="tag">Volitelné údaje · ${W.cat.groups.filter(g => f.options.includes(g.id) && !(g.needs && !f.facets.includes(g.needs))).length} z ${W.cat.groups.length}</div>
      <p class="muted" style="margin:4px 0 0">Standard tyto údaje nevyžaduje a skutečné stroje je mají jen někdy. Předvolba je nastaví podle typu stroje.</p>
      <div class="groups">${W.cat.groups.map(grp).join("")}</div></div>`;
}
function stepSummary() {
  const f = W.form;
  const parts = ["Základ"].concat(f.facets.includes("jobs") ? ["Zakázky"] : []).concat(f.facets.includes("datasets") ? ["Datové sady"] : []);
  const opts = W.cat.groups.filter(g => f.options.includes(g.id) && !(g.needs && !f.facets.includes(g.needs))).map(g => g.title);
  const rows = [
    ["Předvolba", W.title], ["Stroj", `${f.manufacturer} ${f.model}`], ["Sériové číslo", f.serial_number], ["Řízení", f.controller_name],
    ["Konfigurace", `${f.drive} pohon, ${f.injection_units}× vstřikovací jednotka, ${f.zones} topných zón, ${f.moulds}× forma`],
    ["Datový model", "Euromap 77: " + parts.join(" + ")], ["Volitelné údaje", opts.length ? opts.join(", ") : "žádné"],
    ["Adresa pro klienta", `opc.tcp://<počítač>:${W.port || W.cat.next_port}/`],
  ];
  return `<h3>Shrnutí</h3><dl class="sum">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    <div class="note">${W.cat.persist ? `Stroj se uloží do <code>${esc(W.cat.save_path)}</code> a naběhne i po příštím spuštění. Smazáním souboru nebo tlačítkem Obnovit výchozí stroje se vrátí původní stroje.`
      : "Simulátor běží se stroji z příkazové řádky (-m), proto se změna po ukončení neuloží."} Poběží ${W.cat.count + 1}. stroj z ${W.cat.max}.</div>`;
}
function renderWizard() {
  $("#wzBody").innerHTML = [stepType, stepModel, stepSummary][W.step]();
  for (const li of document.querySelectorAll("#wzSteps li")) { const i = +li.dataset.i; li.className = i === W.step ? "on" : (i < W.step ? "done" : ""); }
  $("#wzBack").disabled = W.step === 0;
  $("#wzNext").textContent = W.step === 2 ? "Přidat a spustit" : "Další";
}
$("#addMachine").addEventListener("click", openWizard);
$("#wzClose").addEventListener("click", closeWizard);
// While the wizard is open, Escape closes it and Tab stays inside the dialog
document.addEventListener("keydown", e => {
  if ($("#wizard").hidden) return;
  if (e.key === "Escape") return closeWizard();
  if (e.key !== "Tab") return;
  const f = [...$("#wizard .dialog").querySelectorAll("button, input, select, a[href]")].filter(x => !x.disabled && x.offsetParent);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (!$("#wizard .dialog").contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
$("#wzBody").addEventListener("click", e => {
  const b = e.target.closest("[data-preset]"); if (!b) return;
  pickPreset(b.dataset.preset); renderWizard();
});
$("#wzBody").addEventListener("change", e => {
  if (e.target.dataset.facet) { readStep(); renderWizard(); }
});
$("#wzBack").addEventListener("click", () => { readStep(); W.step--; renderWizard(); });
$("#wzNext").addEventListener("click", async () => {
  readStep();
  if (W.step < 2) { W.step++; return renderWizard(); }
  const btn = $("#wzNext"); btn.disabled = true; btn.textContent = "Spouštím…";
  try {
    const r = await api("/ui/fleet/add", { preset: W.preset, title: W.title, form: W.form, port: W.port });
    closeWizard();
    toast(`Stroj ${r.name} běží`);
    await reloadMachines(r.index);
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; renderWizard();
});

// ------------------------------------------------------------------- loop
async function poll() {
  if (!S.tree || !S.machines.length) return;
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
  try { S.max = (await api("/ui/catalog")).max; } catch { }
  await reloadMachines(0);
  setInterval(poll, 1000);
  setInterval(async () => { try { S.machines = await api("/ui/machines"); renderTabs(); } catch { } }, 3000);
}
main();
