"use strict";
const colors = ["#2563eb", "#dc2626", "#059669"];
const $ = id => document.getElementById(id);
const node = (tag, value) => { const el = document.createElement(tag); el.textContent = String(value ?? "—"); return el; };
const fmt = value => Number.isFinite(Number(value)) ? Number(value).toFixed(1) : "—";

function table(headers, rows) {
  const t = document.createElement("table"), head = t.createTHead().insertRow();
  headers.forEach(h => head.append(node("th", h)));
  const body = t.createTBody();
  rows.forEach(row => { const tr = body.insertRow(); row.forEach(v => tr.append(node("td", v))); });
  return t;
}
function show(id, headers, rows) { $(id).replaceChildren(table(headers, rows)); }
function groupWear(run, group) {
  const cars = Object.entries(run.cars || {}).filter(([, c]) => c.group === group);
  const floors = cars.map(([, c]) => Number(c.floors || 0));
  const high = Math.max(0, ...floors), low = Math.min(...floors);
  return floors.length ? `${cars.map(([id, c]) => `${id}: ${c.floors || 0}`).join(" · ")} floors; ${high ? ((high - low) / high * 100).toFixed(1) : "0.0"}%` : "—";
}
function render(runs) {
  const labels = runs.map((r, i) => r.meta?.name || r.meta?.run_id || `Run ${i + 1}`);
  show("metadata", ["Metric", ...labels], [
    ["Profile / seed", ...runs.map(r => `${r.meta?.profile ?? "—"} / ${r.meta?.seed ?? "—"}`)],
    ["Rate / duration", ...runs.map(r => `${r.meta?.rate ?? "—"} per min / ${fmt((r.meta?.sim_seconds || 0) / 60)} min`)],
    ["Average wait", ...runs.map(r => `${fmt(r.overall?.avg_wait_s)} s`)],
    ["Delivered", ...runs.map(r => r.overall?.n_delivered ?? "—")],
    ["Run time", ...runs.map(r => `${fmt(r.overall?.total_car_run_time_s)} s`)],
    ["Run-time change vs first", ...runs.map((r, i) => {
      const base = Number(runs[0].overall?.total_car_run_time_s);
      return i === 0 || !base ? "baseline" : `${((Number(r.overall?.total_car_run_time_s) / base - 1) * 100).toFixed(1)}%`;
    })],
  ]);
  const modes = [...new Set(runs.flatMap(r => Object.keys(r.by_mode || {})))].sort();
  const waitGroups = [...new Set(runs.flatMap(r => Object.keys(r.by_group || {})))].sort();
  show("group-waits", ["Group / metric", ...labels], waitGroups.flatMap(group => ["wait_avg_s", "wait_p95_s", "wait_max_s"].map(metric =>
    [`${group} / ${metric.replace("wait_", "").replace("_s", "")}`, ...runs.map(r => r.by_group?.[group] ? `${fmt(r.by_group[group][metric])} s` : "—")]
  )));
  show("waits", ["Mode / metric", ...labels], modes.flatMap(mode => ["wait_avg_s", "wait_p95_s", "wait_max_s"].map(metric =>
    [`${mode} / ${metric.replace("wait_", "").replace("_s", "")}`, ...runs.map(r => r.by_mode?.[mode] ? `${fmt(r.by_mode[mode][metric])} s` : "—")]
  )));
  const groups = [...new Set(runs.flatMap(r => Object.values(r.cars || {}).map(c => c.group)))].sort();
  show("wear", ["Group", ...labels], groups.map(g => [g, ...runs.map(r => groupWear(r, g))]));
  show("events", ["Metric", ...labels], [
    ["Mode changes", ...runs.map(r => r.events?.mode_changes?.length ?? 0)],
    ["Eco step-ups", ...runs.map(r => r.events?.eco_step_ups ?? 0)],
    ["Alerts", ...runs.map(r => JSON.stringify(r.events?.alerts_by_kind || {}))],
    ["Faults", ...runs.map(r => (r.meta?.faults || []).map(f => f.kind).join(", ") || "—")],
  ]);
  const svg = $("trend"); svg.replaceChildren();
  const series = runs.map(r => r.series || []), end = Math.max(1, ...series.flatMap(s => s.map(p => Number(p.t) || 0)));
  const high = Math.max(1, ...series.flatMap(s => s.map(p => Number(p.rolling_wait_avg_s) || 0)));
  series.forEach((points, i) => {
    if (!points.length) return;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("fill", "none"); line.setAttribute("stroke", colors[i]); line.setAttribute("stroke-width", "3");
    line.setAttribute("points", points.map(p => `${(Number(p.t) || 0) / end * 870 + 15},${205 - (Number(p.rolling_wait_avg_s) || 0) / high * 185}`).join(" "));
    svg.append(line);
  });
  $("legend").replaceChildren(...labels.map((label, i) => { const el = node("span", `● ${label}`); el.style.color = colors[i]; return el; }));
  $("comparison").hidden = false;
}

async function load() {
  try {
    const response = await fetch("/api/runs"); if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const rows = await response.json(), container = $("runs"); container.replaceChildren();
    $("status").textContent = rows.length ? `${rows.length} saved run(s). Select 2 or 3.` : "No saved runs yet. Start and save a run in Building.";
    rows.forEach(r => {
      const label = document.createElement("label"), box = document.createElement("input"); box.type = "checkbox"; box.value = r.run_id;
      label.className = "run"; label.append(box, node("strong", ` ${r.name || r.run_id}`), node("div", `${r.profile || "—"} · seed ${r.seed ?? "—"} · ${r.started_wall || "—"}`));
      label.lastChild.className = "muted"; container.append(label);
    });
    container.addEventListener("change", e => { const checked = [...container.querySelectorAll("input:checked")];
      if (checked.length > 3) e.target.checked = false;
      $("compare").disabled = container.querySelectorAll("input:checked").length < 2;
    });
  } catch (error) { $("status").textContent = `Could not load saved runs: ${error.message}`; }
}
$("compare").addEventListener("click", async () => {
  const ids = [...$("runs").querySelectorAll("input:checked")].map(e => e.value);
  $("status").textContent = "Loading comparison…";
  try {
    const responses = await Promise.all(ids.map(id => fetch(`/api/runs/${encodeURIComponent(id)}`)));
    if (responses.some(r => !r.ok)) throw new Error("A saved run could not be loaded");
    render(await Promise.all(responses.map(r => r.json()))); $("status").textContent = `Comparing ${ids.length} runs.`;
  } catch (error) { $("status").textContent = error.message; }
});
load();
