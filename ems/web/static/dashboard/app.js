const q = selector => document.querySelector(selector);
const fmt = n => Number(n || 0).toFixed(1);
const role = () => q('#role').value;
const credentials = {
  facility_manager: 'staff-fm-1', security: 'staff-security-1',
  technician: 'staff-technician-1', tenant_admin: 'staff-admin-T01'
};
const headers = (json = false) => ({
  'X-Role': role(), 'X-User': `dashboard-${role()}`, 'X-Tenant': 'T01',
  ...(json ? {'Content-Type': 'application/json'} : {})
});

async function request(url, options = {}) {
  const response = await fetch(url, {headers: headers(Boolean(options.body)), ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
const get = url => request(url);
const post = (url, body) => request(url, {method: 'POST', body: JSON.stringify(body)});

function applyRole() {
  document.querySelectorAll('[data-roles]').forEach(node => {
    node.hidden = !node.dataset.roles.split(' ').includes(role());
  });
}

function renderState(state) {
  q('#clock').textContent = new Date(state.time).toLocaleString();
  q('#cars').innerHTML = state.cars.map(car => `<tr><td><b>${car.car_id}</b></td><td>L${car.floor} / ${fmt(car.position)}</td><td>${car.direction}</td><td>${car.doors}</td><td>${fmt(car.load_pct)}%</td><td><span class="pill ${car.fault_code ? 'fault' : ''}">${car.state}</span></td><td>${state.modes[car.group]}</td><td>${car.fault_code || '<span class="ok">OK</span>'}</td></tr>`).join('');
  q('#alerts').innerHTML = state.alerts.length ? state.alerts.slice().reverse().map(item => `<div class="alert"><b>${item.kind}</b><br><span class="muted">${item.message}</span></div>`).join('') : '<div class="empty">No alerts</div>';
}

function renderReport(report) {
  const values = report.wait_and_run_time;
  q('#trips').textContent = values.trips;
  q('#wait').textContent = `${fmt(values.average_wait_s)} s`;
  q('#runtime').textContent = `${fmt(values.run_time_s / 3600)} h`;
  q('#energy').textContent = `${fmt(values.estimated_energy_kwh)} kWh`;
  q('#targets').innerHTML = `Average wait <b>${fmt(values.average_wait_s)} s</b><br>P95 wait <b>${fmt(values.p95_wait_s)} s</b><br>Run time <b>${fmt(values.run_time_s)} s</b><br>Ceiling breaches <b>${values.ceiling_breaches}</b><br>VIP effect on ordinary wait <b>${fmt(values.vip_effect_on_ordinary_wait_s)} s</b><br><small>${values.energy_model}</small>`;
}

function renderRecovery(status) {
  const blockers = status.blockers.length ? status.blockers.join(', ') : 'none';
  q('#recovery').innerHTML = `Recovery required: <b>${status.recovery_required}</b>; controller reports: <b>${status.normal_reports}/${status.expected_reports}</b>; blockers: <b>${blockers}</b>`;
  q('#confirm-recovery').disabled = !status.eligible;
}

function renderRemovals(rows) {
  q('#removals').innerHTML = rows.length ? rows.map(item => `<div class="alert"><b>${item.request_id}</b> · car ${item.car_id} · group ${item.group}<br><button data-removal="${item.request_id}" data-approve="true">Approve</button> <button data-removal="${item.request_id}" data-approve="false">Deny</button></div>`).join('') : '<div class="empty">No pending requests</div>';
}

function renderIncidents(rows) {
  q('#incidents').innerHTML = rows.length ? rows.map(item => `<div class="alert"><b>${item.kind}</b> · car ${item.car_id} · ${item.status}<br><span class="muted">${item.event_id}; candidates do not establish responsibility</span><br><button data-incident="${item.event_id}" data-decision="confirmed">Confirm</button> <button data-incident="${item.event_id}" data-decision="dismissed">Dismiss</button></div>`).join('') : '<div class="empty">No incidents awaiting review</div>';
}

async function poll() {
  applyRole();
  try { renderState(await get('/api/state')); } catch (error) { console.error(error); }
  const selected = role();
  if (['owner', 'facility_manager', 'system_admin'].includes(selected)) {
    try { renderReport(await get('/api/monitor/report')); } catch (error) { console.error(error); }
  }
  if (selected === 'facility_manager') {
    try { renderRecovery(await get('/api/monitor/recovery')); renderRemovals(await get('/api/monitor/maintenance/requests')); } catch (error) { console.error(error); }
  }
  if (['technician', 'facility_manager', 'system_admin'].includes(selected)) {
    try { const data = await get('/api/monitor/diagnostics'); q('#diagnostics').textContent = `${data.fault_history.length} recorded fault(s)`; } catch (error) { console.error(error); }
  }
  if (selected === 'tenant_admin') {
    try { const data = await get('/api/monitor/tenant-report'); q('#tenant-report').textContent = `${data.trips} trip(s), average wait ${fmt(data.average_wait_s)} s`; } catch (error) { console.error(error); }
  }
  if (['security', 'system_admin'].includes(selected)) {
    try { renderIncidents(await get('/api/monitor/incidents')); } catch (error) { console.error(error); }
  }
  if (['owner', 'system_admin'].includes(selected)) {
    try { const config = await get('/api/monitor/configuration'); q('#privacy').innerHTML = `${config.camera_notice_en}<br>${config.camera_notice_vi}<br><small>Enabled camera events: ${config.camera_event_types.join(', ')}</small>`; } catch (error) { console.error(error); }
    try { const audit = await get('/api/monitor/audit'); q('#audit').textContent = `${audit.length} accountable human change(s)`; } catch (error) { console.error(error); }
  }
}

async function confirmRecovery() {
  try { await post('/api/monitor/recovery/confirm', {credential_id: credentials.facility_manager}); await poll(); } catch (error) { alert(error.message); }
}
async function setMode() {
  const group = q('#group-choice').value || null;
  const duration = Number(q('#mode-duration').value) * 60;
  try { await post('/api/monitor/mode', {credential_id: credentials.facility_manager, mode: q('#mode-choice').value, group, duration_s: duration}); await poll(); } catch (error) { alert(error.message); }
}
async function reviewIncident(id, decision) {
  const evidence = prompt('Record the evidence for this human decision:');
  if (!evidence) return;
  const responsible = decision === 'confirmed' ? prompt('Responsible candidate credential (optional; leave blank if unattributed):') : '';
  try { await post(`/api/monitor/incidents/${id}/review`, {decision, evidence, responsible_credential: responsible || null}); await poll(); } catch (error) { alert(error.message); }
}

q('#role').addEventListener('change', poll);
q('#confirm-recovery').addEventListener('click', confirmRecovery);
q('#set-mode').addEventListener('click', setMode);
document.addEventListener('click', async event => {
  if (event.target.dataset.removal) {
    try { await post(`/api/monitor/maintenance/requests/${event.target.dataset.removal}`, {credential_id: credentials.facility_manager, approve: event.target.dataset.approve === 'true'}); await poll(); } catch (error) { alert(error.message); }
  }
  if (event.target.dataset.incident) await reviewIncident(event.target.dataset.incident, event.target.dataset.decision);
});
poll();
setInterval(poll, 1000);
