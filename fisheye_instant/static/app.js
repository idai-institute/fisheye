'use strict';
const $ = selector => document.querySelector(selector);
const state = {settings: null, snapshot: null, selection: '', reviewer: '', editing: null, timer: null};
const names = {warning: 'Raise a warning', email: 'Send an email', shutdown: 'Shut down an agent'};
const icons = {warning: '!', email: '✉', shutdown: '⏻'};
const statusNames = {completed: 'Completed', pending: 'Queued', executing: 'Running', blocked: 'Needs setup', failed: 'Failed', unknown: 'Check outcome', cancelled: 'Cancelled', preview: 'Demo preview'};
function el(tag, cls, text) { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; }
function button(text, callback, cls = 'text-button') { const node = el('button', cls, text); node.type = 'button'; node.onclick = callback; return node; }
function toast(text) { $('#toast').textContent = text; $('#toast').hidden = false; clearTimeout(state.timer); state.timer = setTimeout(() => $('#toast').hidden = true, 4500); }
async function api(path, options = {}) {
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch('/instant/api/' + path, {...options, signal: controller.signal, headers: {'Content-Type': 'application/json', ...(state.reviewer ? {'X-Review-Key': state.reviewer} : {}), ...options.headers}});
    const data = await response.json();
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : (data.detail || []).map(item => item.msg).join(' · ');
      const error = new Error(detail || `Request failed (${response.status})`); error.status = response.status; throw error;
    }
    return data;
  } finally { clearTimeout(timeout); }
}
function openPage(tab) {
  for (const page of document.querySelectorAll('.page')) page.hidden = page.id !== 'page-' + tab;
  for (const item of document.querySelectorAll('[data-tab]')) item.classList.toggle('active', item.dataset.tab === tab);
  $('#breadcrumb').textContent = {overview: 'Overview', rules: 'Response rules', connect: 'Connect an agent', settings: 'Settings'}[tab];
  history.replaceState(null, '', '#' + tab); window.scrollTo({top: 0, behavior: 'smooth'});
}
for (const node of document.querySelectorAll('[data-tab], [data-open]')) node.onclick = () => openPage(node.dataset.tab || node.dataset.open);
for (const node of document.querySelectorAll('[data-close]')) node.onclick = () => node.closest('dialog').close();
$('#access-button').onclick = () => $('#access-dialog').showModal();
$('#access-form').onsubmit = event => {event.preventDefault(); state.reviewer = event.target.elements.key.value; event.target.reset(); $('#access-dialog').close(); toast('Reviewer key ready for this tab.');};
$('#score-info').onclick = () => openPage('settings');
function ago(value, now = Date.now() / 1000) { const seconds = Math.max(0, now - value); return seconds < 60 ? 'Just now' : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : `${Math.floor(seconds / 3600)}h ago`; }
function color(score) { return score >= 85 ? '#c67661' : score >= 60 ? '#c6a067' : '#79ad90'; }
function renderChart(history) {
  const svg = $('#trend'), ns = 'http://www.w3.org/2000/svg'; svg.replaceChildren();
  function shape(tag, attributes, text) { const node = document.createElementNS(ns, tag); for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value); if (text) node.textContent = text; svg.append(node); return node; }
  for (const mark of [0, 50, 100]) { const y = 160 - mark * 1.4; shape('line', {x1: 28, y1: y, x2: 694, y2: y, stroke: '#ecf0ed', 'stroke-dasharray': '4 5'}); shape('text', {x: 0, y: y + 3, fill: '#99a59d', 'font-size': 9}, String(mark)); }
  if (!history.length) { shape('text', {x: 340, y: 85, 'text-anchor': 'middle', fill: '#9ba69f', 'font-size': 12}, 'Waiting for the next event'); return; }
  const points = history.map((row, i) => [30 + i / Math.max(1, history.length - 1) * 657, 160 - row.score * 1.4]);
  const line = points.map(([x, y], i) => `${i ? 'L' : 'M'}${x},${y}`).join(' ');
  shape('path', {d: `${line} L${points.at(-1)[0]},160 L30,160 Z`, fill: '#eff6f1'});
  shape('path', {d: line, fill: 'none', stroke: '#83ae97', 'stroke-width': 2.5, 'stroke-linejoin': 'round'});
  const last = points.at(-1); shape('circle', {cx: last[0], cy: last[1], r: 4, fill: color(history.at(-1).score), stroke: 'white', 'stroke-width': 2});
  $('#history-start').textContent = new Date(history[0].observed * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
function renderSignals(selected) {
  const root = $('#signals'); root.replaceChildren();
  if (!selected.channels.length) {root.append(el('p', 'empty-small', 'No recent positive signals. Keep an eye on detector coverage and the next event.')); return;}
  for (const item of selected.channels) {
    const row = el('div', 'signal'), top = el('div', 'signal-top');
    top.append(el('span', 'signal-title', item.label), el('strong', '', `${Math.round(item.score * 100)}`));
    const meter = el('div', 'signal-meter'), fill = el('span'); fill.style.width = `${item.score * 100}%`; fill.style.background = color(item.score * 100); meter.append(fill);
    const details = el('details'); details.append(el('summary', '', 'View contributing evidence'), el('pre', '', JSON.stringify(item.evidence, null, 2)));
    const link = el('a', '', 'Open source event ↗'); link.href = '/v2/events/' + encodeURIComponent(item.event_id); link.target = '_blank'; link.rel = 'noopener'; details.append(link);
    row.append(top, el('small', '', item.agent_id + ' · ' + item.category.replaceAll('_', ' ')), meter, details); root.append(row);
  }
}
function renderActivity(actions) {
  const root = $('#activity'); root.replaceChildren(); $('#activity-count').textContent = actions.length;
  if (!actions.length) {root.append(el('p', 'empty-small', 'All quiet here. Threshold crossings and their outcomes will appear in this feed.')); return;}
  for (const action of actions.slice(0, 8)) {
    const row = el('div', 'activity-item'), content = el('div', 'activity-content');
    content.append(el('div', '', action.rule.name), el('small', '', `${action.workflow_id} · score ${Math.round(action.score)}`), el('time', '', ago(action.created)));
    if (action.error_type) content.append(el('small', '', action.error_type));
    row.append(el('div', 'action-icon', icons[action.rule.action]), content, el('span', 'activity-status ' + action.status, statusNames[action.status] || action.status)); root.append(row);
  }
}
function renderRules() {
  if (!state.settings) return;
  const rules = state.settings.rules, root = $('#rules-list'), preview = $('#rule-preview-list'); root.replaceChildren(); preview.replaceChildren();
  $('#rule-count').textContent = rules.length; $('#enabled-count').textContent = rules.filter(rule => rule.enabled).length;
  for (const rule of rules) {
    const row = el('div', 'rule-preview-row'), label = el('div', '', rule.name); label.append(el('small', '', rule.enabled ? names[rule.action] : 'Paused'));
    row.append(el('div', 'action-icon', icons[rule.action]), label, el('span', 'threshold', `≥ ${rule.threshold}`)); preview.append(row);
    const card = el('article', 'card rule-card'), header = el('div', 'rule-header');
    header.append(el('div', 'action-icon', icons[rule.action]), el('span', 'pill ' + (rule.enabled ? '' : 'stale'), rule.enabled ? 'Enabled' : 'Paused'));
    const footer = el('div', 'rule-card-bottom'); footer.append(el('span', 'threshold', `Score ≥ ${rule.threshold}`), button('Edit rule', () => editRule(rule)), button(rule.enabled ? 'Pause' : 'Enable', () => toggleRule(rule)), button('Delete', () => deleteRule(rule), 'danger-button'));
    card.append(header, el('h2', '', rule.name), el('p', '', names[rule.action] + (rule.workflow_id ? ` · ${rule.environment} / ${rule.workflow_id}` : ' · Every workflow')), footer); root.append(card);
  }
  if (!rules.length) {root.append(el('p', 'empty-small', 'No response rules yet. Add one to start watching for threshold crossings.')); preview.append(el('p', 'empty-small', 'Add a response rule to choose what happens next.'));}
}
function renderSnapshot(data) {
  state.snapshot = data;
  const selected = data.selected, selector = $('#workflow-select');
  selector.replaceChildren();
  for (const workflow of data.workflows) { const option = el('option', '', `${workflow.workflow_id} · ${workflow.environment}`); option.value = JSON.stringify([workflow.workflow_id, workflow.environment]); option.selected = selected && workflow.scope === selected.scope; selector.append(option); }
  if (!data.workflows.length) selector.append(el('option', '', 'No connected workflows'));
  $('#empty-state').hidden = !!selected; $('#dashboard-content').hidden = !selected;
  $('#demo-button').hidden = !data.demo.enabled; $('#demo-button').disabled = !data.demo.running;
  if (!data.demo.running) $('#demo-button').textContent = 'Demo agent stopped';
  $('#connection').classList.remove('offline'); $('#connection').replaceChildren(el('i'), document.createTextNode('Connected'));
  const degraded = Object.values(data.health).some(Boolean);
  $('#notice').hidden = !degraded;
  if (degraded) $('#notice').textContent = 'Part of the monitoring pipeline needs attention. Check the health endpoint before relying on a quiet score.';
  $('#last-updated').textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  if (!selected) return;
  const status = degraded ? 'limited' : selected.status;
  const labels = {healthy: ['Looking steady', 'Recent signals are below the warning band.'], warning: ['Worth a closer look', 'An unusual pattern has appeared in this workflow.'], critical: ['Attention needed', 'Strong anomaly signals are contributing to this score.'], limited: ['Coverage is limited', 'Some checks could not evaluate the latest input.'], stale: ['Waiting for recent data', 'No events in the last five minutes. This is not a clean bill of health.']};
  $('#score-value').textContent = Math.round(selected.score); $('#score-title').textContent = labels[status][0]; $('#score-description').textContent = labels[status][1];
  $('#risk-badge').textContent = {healthy:'Within range', warning:'Elevated', critical:'High anomaly', limited:'Partial coverage', stale:'Stale data'}[status]; $('#risk-badge').className = 'pill ' + status;
  $('#gauge-value').style.strokeDasharray = `${selected.score * .75} 100`; $('#gauge-value').style.stroke = color(selected.score); $('#gauge-status').textContent = {healthy:'STEADY',warning:'ELEVATED',critical:'HIGH',limited:'PARTIAL',stale:'STALE'}[status];
  $('#signal-count').textContent = selected.channels.length; $('#workflow-count').textContent = data.workflows.length;
  const complete = !['limited', 'stale'].includes(status); $('#coverage').classList.toggle('incomplete', !complete); $('#coverage span').textContent = complete ? 'Latest checks completed' : status === 'stale' ? 'Awaiting fresh observations' : 'Some checks have incomplete coverage';
  renderChart(data.history); renderSignals(selected); renderActivity(data.actions); renderRules();
}
async function loadSettings() {
  state.settings = await api('settings'); renderRules();
  const form = $('#mail-form'); for (const [key, value] of Object.entries(state.settings.mail)) form.elements[key].value = value;
  $('#password-state').textContent = state.settings.password_set ? 'A password is saved securely on the server' : 'No password saved';
}
async function saveSettings(rules, mail = state.settings.mail, password = undefined) {
  try { state.settings = await api('settings', {method: 'PUT', body: JSON.stringify({revision: state.settings.revision, rules, mail, ...(password !== undefined ? {password} : {})})}); renderRules(); return state.settings; }
  catch (error) { if (error.status === 409) state.settings = await api('settings'); throw error; }
}
function showActionFields() {
  const action = $('#rule-form').elements.action.value;
  $('#recipients-field').hidden = action !== 'email'; $('#target-field').hidden = action !== 'shutdown';
  $('#action-help').textContent = {warning:'Warnings appear in the response activity feed.', email:'Set up email delivery in Settings first. Demo workflow emails stay in the activity feed.', shutdown:'Only workflows with a registered host callback can be stopped.'}[action];
}
function editRule(rule = null) {
  if (!state.settings) return toast('Waiting for settings to load.');
  state.editing = rule ? rule.id : null;
  const form = $('#rule-form'); form.reset(); form.querySelector('.form-status').textContent = '';
  $('#rule-dialog-title').textContent = rule ? 'Edit response rule' : 'Add a response rule';
  form.elements.target.replaceChildren(el('option', '', 'Select a registered workflow')); form.elements.target.firstChild.value = '';
  for (const target of state.settings.shutdown_targets) { const option = el('option', '', `${target.workflow_id} · ${target.environment}`); option.value = JSON.stringify([target.workflow_id, target.environment]); form.elements.target.append(option); }
  if (rule) {
    for (const key of ['name', 'threshold', 'action', 'cooldown_seconds', 'hysteresis']) form.elements[key].value = rule[key];
    form.elements.enabled.checked = rule.enabled; form.elements.recipients.value = rule.recipients.join(', ');
    if (rule.workflow_id) form.elements.target.value = JSON.stringify([rule.workflow_id, rule.environment]);
  }
  showActionFields(); $('#rule-dialog').showModal(); form.elements.name.focus();
}
$('#add-rule').onclick = () => editRule(); $('#rule-form').elements.action.onchange = showActionFields;
$('#rule-form').onsubmit = async event => {
  event.preventDefault(); const form = event.target, fields = form.elements, submit = form.querySelector('[type=submit]'); submit.disabled = true;
  try {
    const target = fields.action.value === 'shutdown' && fields.target.value ? JSON.parse(fields.target.value) : [null, null];
    const rule = {id: state.editing || crypto.randomUUID(), name: fields.name.value.trim(), action: fields.action.value, threshold: Number(fields.threshold.value), enabled: fields.enabled.checked, cooldown_seconds: Number(fields.cooldown_seconds.value), hysteresis: Number(fields.hysteresis.value), workflow_id: target[0], environment: target[1], recipients: fields.action.value === 'email' ? fields.recipients.value.split(',').map(s => s.trim()).filter(Boolean) : []};
    await saveSettings([...state.settings.rules.filter(item => item.id !== rule.id), rule]); $('#rule-dialog').close(); toast('Response rule saved.');
  } catch (error) {form.querySelector('.form-status').textContent = error.message;} finally {submit.disabled = false;}
};
async function toggleRule(rule) { try {await saveSettings(state.settings.rules.map(item => item.id === rule.id ? {...item, enabled: !item.enabled} : item)); toast(rule.enabled ? 'Rule paused.' : 'Rule enabled.');} catch (error) {toast(error.message);} }
async function deleteRule(rule) { if (!confirm(`Delete “${rule.name}”? Queued actions for this rule will be cancelled.`)) return; try {await saveSettings(state.settings.rules.filter(item => item.id !== rule.id)); toast('Rule deleted.');} catch (error) {toast(error.message);} }
$('#mail-form').onsubmit = async event => {
  event.preventDefault(); const form = event.target, fields = form.elements, submit = form.querySelector('[type=submit]'); submit.disabled = true;
  try {
    const mail = Object.fromEntries(['host','sender','username','security'].map(key => [key,fields[key].value])); mail.port = Number(fields.port.value);
    await saveSettings(state.settings.rules, mail, fields.password.value || undefined); fields.password.value = ''; form.querySelector('.form-status').textContent = ''; $('#password-state').textContent = state.settings.password_set ? 'Password saved on this server' : 'No password saved'; toast('Email settings saved.');
  } catch (error) {form.querySelector('.form-status').textContent = error.message;} finally {submit.disabled = false;}
};
$('#demo-button').onclick = async () => { const button = $('#demo-button'); button.disabled = true; try {const result = await api('demo', {method:'POST', body:'{}'}); state.selection = JSON.stringify([result.workflow_id, result.environment]); await refresh(); toast('Sample workflow analyzed. Inspect its signals below.');} catch (error) {toast(error.message);} finally {button.disabled = state.snapshot && !state.snapshot.demo.running;} };
$('#workflow-select').onchange = async event => {state.selection = event.target.value; try {await refresh();} catch (error) {toast(error.message);}};
const example = `curl ${location.origin}/v1/events \\\n+  -H 'Content-Type: application/json' \\\n+  -d '{"event_type":"llm.message",
       "agent_id":"my-agent",
       "run_id":"my-workflow",
       "payload":{"content":"Hello, Fisheye"}}'`;
$('#curl-example').textContent = example;
$('#copy-example').onclick = async () => {try {await navigator.clipboard.writeText(example); toast('Example copied.');} catch {toast('Select the example to copy it.');}};
async function refresh() { const selected = state.selection ? JSON.parse(state.selection) : null; const query = selected ? '?' + new URLSearchParams({workflow_id:selected[0], environment:selected[1]}) : ''; renderSnapshot(await api('snapshot' + query)); }
async function poll() {
  try {if (!state.settings) await loadSettings(); await refresh();}
  catch (error) {$('#connection').classList.add('offline'); $('#connection').replaceChildren(el('i'), document.createTextNode('Disconnected')); $('#notice').hidden = false; $('#notice').textContent = 'Connection unavailable. Retrying automatically. ' + error.message;}
  finally {setTimeout(poll, 2500);}
}
const firstTab = location.hash.slice(1); if (['overview','rules','connect','settings'].includes(firstTab)) openPage(firstTab);
poll();
