// Wear long runs (NFR-W1): list runs/long_*.json and show the per-month and period imbalance.
(() => {
  const esc = v => String(v ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const pct = v => v == null ? "–" : (100 * v).toFixed(2) + "%";
  const verdict = ok => ok ? '<span style="color:#15803d">pass</span>' : '<span style="color:#b91c1c">fail</span>';
  const status = document.getElementById("long-status");
  const list = document.getElementById("long-list");
  const detail = document.getElementById("long-detail");

  async function show(runId) {
    const data = await (await fetch("/api/long-runs/" + encodeURIComponent(runId))).json();
    const rows = (data.imbalance || []).map(r =>
      `<tr><td>${esc(r.group)}</td><td>${esc(r.window)}</td><td>${pct(r.imbalance)}</td>` +
      `<td>${pct(r.limit)}</td><td>${verdict(r.ok)}</td>` +
      `<td>${Object.entries(r.floors || {}).map(([c, f]) => esc(c) + " " + Math.round(f)).join(", ")}</td></tr>`).join("");
    detail.innerHTML = `<h3>${esc(runId)}</h3><p class="muted">${esc(data.days)} day${data.days === 1 ? "" : "s"} from ${esc((data.start || "").slice(0, 10))}, ` +
      `seed ${esc(data.seed)}, commit ${esc((data.git_commit || "").slice(0, 8))}${data.git_dirty ? " (uncommitted changes)" : ""}, ` +
      `${Math.round((data.wall_s || 0) / 60)} min to run.</p>` +
      `<table><thead><tr><th>Group</th><th>Window</th><th>Imbalance</th><th>Limit</th><th>Result</th>` +
      `<th>Floors per car</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function load() {
    let runs;
    try { runs = await (await fetch("/api/long-runs")).json(); }
    catch (e) { status.textContent = "Could not load long runs."; return; }
    if (!runs.length) { status.textContent = "No long runs yet. Run python -m ems.sim.long_run --months 1 in the repo folder."; return; }
    status.textContent = "";
    const groups = [...new Set(runs.flatMap(r => Object.keys(r.period || {})))].sort();
    list.innerHTML = `<table><thead><tr><th>Run</th><th>Days</th><th>Seed</th><th>Commit</th>` +
      groups.map(g => `<th>${esc(g)} period</th>`).join("") + `<th></th></tr></thead><tbody>` +
      runs.map(r => `<tr><td>${esc(r.run_id)}<div class="muted">${esc(r.saved)}</div></td><td>${esc(r.days)}</td>` +
        `<td>${esc(r.seed)}</td><td>${esc(r.git_commit)}</td>` +
        groups.map(g => { const p = (r.period || {})[g]; return `<td>${p ? pct(p.imbalance) + " " + verdict(p.ok) : "–"}</td>`; }).join("") +
        `<td><button data-run="${esc(r.run_id)}">Details</button></td></tr>`).join("") + `</tbody></table>`;
    list.querySelectorAll("button[data-run]").forEach(b => b.onclick = () => show(b.dataset.run));
  }
  load();
})();
