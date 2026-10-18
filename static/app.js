'use strict';

const state = { providers: [], routes: [], logs: [], catalog: [], discoveredModels: [], bootstrap: null, editingProvider: null, editingAlias: null, currentPage: 'overview', lastResponse: '' };
const $ = (id) => document.getElementById(id);
const pageNames = { overview: 'Overview', providers: 'Providers', models: 'Models & aliases', playground: 'Playground', logs: 'Request logs', settings: 'Settings' };
let providerPresets = { custom: null };

function toast(message, type = 'success') {
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.textContent = message;
  $('toast-stack').appendChild(node);
  window.setTimeout(() => node.remove(), 3600);
}
function formatNum(value) { return new Intl.NumberFormat().format(Number(value || 0)); }
function formatLatency(value) { return value ? `${formatNum(value)} ms avg` : 'Waiting for first request'; }
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}
function safeDate(value) {
  try { return new Date(value).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  catch { return String(value || '—'); }
}
async function api(path, options = {}) {
  const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) };
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
  const text = await response.text();
  let payload;
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = { message: text }; }
  if (response.status === 401 && !path.startsWith('/api/auth/')) {
    showLogin();
    throw new Error(payload?.detail || 'Your dashboard session expired. Sign in again.');
  }
  if (!response.ok) {
    const message = payload?.detail || payload?.error?.message || payload?.message || `Request failed (${response.status})`;
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
  }
  return payload;
}
function showLogin() {
  $('app-shell').classList.add('hidden');
  $('login-screen').classList.remove('hidden');
  $('password').value = '';
  $('password').focus();
}
function showApp() {
  $('login-screen').classList.add('hidden');
  $('app-shell').classList.remove('hidden');
  loadEverything().catch(err => toast(err.message, 'error'));
}
async function checkSession() {
  try {
    const status = await api('/api/auth/status');
    if (status.logged_in) showApp(); else showLogin();
  } catch { showLogin(); }
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('login-error').classList.add('hidden');
  const button = $('login-form').querySelector('button[type="submit"]');
  button.disabled = true; button.textContent = 'Unlocking…';
  try {
    await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ password: $('password').value }) });
    showApp();
  } catch (error) {
    $('login-error').textContent = error.message;
    $('login-error').classList.remove('hidden');
  } finally {
    button.disabled = false; button.innerHTML = 'Unlock dashboard <span aria-hidden="true">↗</span>';
  }
});
$('logout-button').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST', body: '{}' }); } catch { /* local session can expire on its own */ }
  showLogin();
});

function go(page) {
  if (!pageNames[page]) return;
  state.currentPage = page;
  document.querySelectorAll('.page').forEach(el => el.classList.toggle('active-page', el.id === `page-${page}`));
  document.querySelectorAll('.nav-item[data-page]').forEach(el => el.classList.toggle('active', el.dataset.page === page));
  $('breadcrumb-page').textContent = pageNames[page];
  $('page-title').textContent = pageNames[page];
  if (page === 'overview') refreshOverview();
  if (page === 'providers') loadProviders();
  if (page === 'models') { loadRoutes(); loadDiscoveredModels(); }
  if (page === 'logs') loadLogs();
  if (page === 'playground') fillPlaygroundModels();
  if (page === 'settings') loadBootstrap();
}
document.querySelectorAll('[data-page]').forEach(el => el.addEventListener('click', () => go(el.dataset.page)));
document.querySelectorAll('[data-goto]').forEach(el => el.addEventListener('click', () => go(el.dataset.goto)));
$('refresh-button').addEventListener('click', () => loadEverything().then(() => toast('Dashboard refreshed')).catch(err => toast(err.message, 'error')));

async function loadEverything() {
  await Promise.all([loadBootstrap(), loadProviderCatalog(), loadProviders(), loadRoutes(), loadLogs(), loadDiscoveredModels(), refreshOverview()]);
  $('version-label').textContent = `v${state.bootstrap?.version || '0.1.0'}`;
}
async function loadBootstrap() {
  state.bootstrap = await api('/api/bootstrap');
  $('settings-base-url').textContent = state.bootstrap.base_url;
  $('settings-api-key').textContent = state.bootstrap.local_api_key;
  $('settings-curl').textContent = `curl ${state.bootstrap.base_url}/models \\\n  -H "Authorization: Bearer ${state.bootstrap.local_api_key}"`;
  const match = state.bootstrap.base_url.match(/:(\d+)\//);
  if (match) $('port-label').textContent = match[1];
  return state.bootstrap;
}
async function loadProviderCatalog() {
  state.catalog = await api('/api/catalog/providers');
  providerPresets = { custom: null };
  state.catalog.forEach(item => { providerPresets[item.id] = item; });
  const select = $('provider-preset');
  select.innerHTML = '<option value="custom">Custom endpoint</option>';
  const grouped = new Map();
  state.catalog.forEach(item => {
    const category = item.category || 'Other';
    if (!grouped.has(category)) grouped.set(category, []);
    grouped.get(category).push(item);
  });
  for (const [category, items] of grouped.entries()) {
    const group = document.createElement('optgroup'); group.label = category;
    for (const item of items) {
      const option = document.createElement('option'); option.value = item.id; option.textContent = item.name + (item.free_tier_status && item.free_tier_status !== 'unknown' ? ` · ${item.free_tier_status}` : '');
      group.appendChild(option);
    }
    select.appendChild(group);
  }
  return state.catalog;
}
async function loadProviders() {
  state.providers = await api('/api/providers');
  $('nav-provider-count').textContent = state.providers.filter(p => p.is_active).length;
  renderProviders();
  fillPlaygroundModels();
  return state.providers;
}
async function loadRoutes() {
  state.routes = await api('/api/models');
  renderRoutes();
  fillPlaygroundModels();
  return state.routes;
}
async function loadDiscoveredModels() {
  state.discoveredModels = await api('/api/discovery/models?limit=2000');
  renderDiscoveredModels();
  return state.discoveredModels;
}
async function loadLogs() {
  state.logs = await api('/api/logs?limit=200');
  renderLogs();
  return state.logs;
}
async function refreshOverview() {
  const [stats, logs, analytics] = await Promise.all([api('/api/dashboard'), api('/api/logs?limit=7'), api('/api/analytics?window_hours=24')]);
  $('stat-providers').textContent = formatNum(stats.providers);
  $('stat-aliases').textContent = formatNum(stats.aliases);
  $('stat-requests').textContent = formatNum(stats.requests_today);
  $('stat-tokens').textContent = formatNum(stats.tokens_today);
  $('stat-success-text').textContent = stats.requests_today ? `${stats.success_rate_today}% successful today` : 'No traffic yet';
  $('stat-latency-text').textContent = formatLatency(stats.avg_latency_ms);
  $('analytics-p50').textContent = `${formatNum(analytics.latency_ms?.p50)} ms`;
  $('analytics-p95').textContent = `${formatNum(analytics.latency_ms?.p95)} ms`;
  $('analytics-p99').textContent = `${formatNum(analytics.latency_ms?.p99)} ms`;
  $('analytics-success').textContent = `${Number(analytics.success_rate || 0).toFixed(1)}%`;
  $('analytics-count').textContent = `${formatNum(analytics.requests)} requests · ${formatNum(analytics.tokens)} tokens`;
  const providerTarget = $('analytics-provider-list');
  const providers = (analytics.providers || []).slice(0, 4);
  providerTarget.innerHTML = providers.length ? providers.map(p => `<div class="telemetry-provider-row"><span class="telemetry-provider-name">${escapeHtml(p.name)}</span><span>${formatNum(p.requests)} req</span><span class="telemetry-success">${Number(p.success_rate || 0).toFixed(1)}%</span><span>${formatNum(p.avg_latency_ms)} ms avg</span></div>`).join('') : '<span class="muted">No provider request history yet.</span>';
  renderSmallLogs(logs, 'overview-logs');
}

function renderSmallLogs(logs, targetId) {
  const target = $(targetId);
  if (!logs.length) {
    target.innerHTML = '<div class="empty-state compact"><div class="empty-symbol">≋</div><strong>No requests yet</strong><span>Make a call from the Playground to see traffic here.</span></div>';
    return;
  }
  target.innerHTML = `<table class="data-table"><thead><tr><th>Alias / route</th><th>Result</th><th>Latency</th><th>Time</th></tr></thead><tbody>${logs.map(log => `<tr><td><span class="table-main">${escapeHtml(log.model_alias)}</span><span class="table-sub">${escapeHtml(log.upstream_provider || 'No provider')}${log.upstream_model ? ` · ${escapeHtml(log.upstream_model)}` : ''}</span></td><td>${statusPill(log.status_code)}</td><td>${formatNum(log.latency_ms)} ms</td><td>${safeDate(log.created_at)}</td></tr>`).join('')}</tbody></table>`;
}
function statusPill(status) {
  const n = Number(status || 0);
  if (n >= 200 && n < 400) return `<span class="status-pill good"><span class="status-dot"></span>${n}</span>`;
  if (n >= 400) return `<span class="status-pill bad">${n}</span>`;
  return '<span class="status-pill neutral">—</span>';
}
function renderProviders() {
  const target = $('providers-list');
  if (!state.providers.length) {
    target.innerHTML = `<div class="panel empty-state empty-provider-panel"><div class="empty-symbol">⇄</div><strong>No providers connected</strong><span>Add a provider with your own API key or a local server such as Ollama, LM Studio, or a custom OpenAI-compatible endpoint.</span><button class="button primary empty-action" id="empty-add-provider">+ Add your first provider</button></div>`;
    $('empty-add-provider').addEventListener('click', () => openProviderModal());
    return;
  }
  const authLabels = { auto: 'Auto auth', none: 'No auth', bearer: 'Bearer', api_key: 'API key header', session: 'Session token', cookie: 'Cookie header', custom_header: 'Custom header', custom_headers: 'Custom headers', oauth2: 'OAuth 2.0' };
  target.innerHTML = state.providers.map(p => `<article class="provider-card"><div class="provider-card-top"><div class="provider-logo">${p.protocol === 'anthropic' ? '✳' : '◈'}</div><div class="provider-title"><h3>${escapeHtml(p.name)}</h3><p title="${escapeHtml(p.base_url)}">${escapeHtml(p.base_url)}</p></div><span class="provider-state ${p.is_active ? 'on' : 'off'}">${p.is_active ? 'Enabled' : 'Disabled'}</span></div><div class="provider-meta"><span class="meta-chip">${p.protocol === 'anthropic' ? 'Anthropic Messages' : 'OpenAI-compatible'}</span><span class="meta-chip">${escapeHtml(authLabels[p.auth_type] || p.auth_type)}</span><span class="meta-chip ${p.oauth_connected || p.api_key_set ? 'green' : 'orange'}">${p.oauth_connected ? 'OAuth connected' : p.api_key_set ? 'Credential stored' : p.auth_type === 'none' ? 'Keyless' : 'No credential'}</span>${p.cooldown_remaining > 0 ? `<span class="meta-chip orange">Cooldown ${p.cooldown_remaining}s</span>` : ''}${p.rate_limits && Object.values(p.rate_limits).some(v => Number(v) > 0) ? `<span class="meta-chip" title="${escapeHtml(JSON.stringify(p.rate_limits))}">Quota caps active</span>` : ''}</div><div class="provider-actions"><button class="mini-button" data-test-provider="${p.id}">▷ Test connection</button><button class="mini-button" data-discover-provider="${p.id}">⌕ Discover models</button><button class="mini-button" data-quota-provider="${p.id}">▤ Quota</button>${p.auth_type === 'oauth2' ? `<button class="mini-button" data-oauth-provider="${p.id}">${p.oauth_connected ? 'Reconnect OAuth' : 'Connect OAuth'}</button>` : ''}<button class="mini-button" data-edit-provider="${p.id}">Edit</button><button class="mini-button danger" data-delete-provider="${p.id}">Delete</button><span class="push-right">ID ${p.id}</span></div><div id="provider-test-${p.id}" class="field-hint provider-test-result hidden"></div></article>`).join('');
  target.querySelectorAll('[data-test-provider]').forEach(button => button.addEventListener('click', () => testProvider(Number(button.dataset.testProvider))));
  target.querySelectorAll('[data-discover-provider]').forEach(button => button.addEventListener('click', () => discoverProvider(Number(button.dataset.discoverProvider))));
  target.querySelectorAll('[data-quota-provider]').forEach(button => button.addEventListener('click', () => viewProviderQuota(Number(button.dataset.quotaProvider))));
  target.querySelectorAll('[data-edit-provider]').forEach(button => button.addEventListener('click', () => openProviderModal(state.providers.find(p => p.id === Number(button.dataset.editProvider)))));
  target.querySelectorAll('[data-oauth-provider]').forEach(button => button.addEventListener('click', () => connectOAuth(Number(button.dataset.oauthProvider))));
  target.querySelectorAll('[data-delete-provider]').forEach(button => button.addEventListener('click', () => deleteProvider(Number(button.dataset.deleteProvider))));
}
function renderRoutes() {
  const target = $('routes-list');
  if (!state.routes.length) {
    target.innerHTML = `<div class="panel empty-state"><div class="empty-symbol">⌘</div><strong>No model aliases yet</strong><span>Connect a provider first, then create an alias with an upstream model ID. Add a second candidate to enable fallback routing.</span><button class="button primary empty-action" id="empty-add-route">+ Create model alias</button></div>`;
    $('empty-add-route').addEventListener('click', () => openRouteModal());
    return;
  }
  target.innerHTML = state.routes.map(route => `<article class="route-card"><div class="route-card-head"><div class="route-symbol">⌘</div><div class="route-name"><h3>${escapeHtml(route.alias)}</h3><p>${route.candidates.length} candidate${route.candidates.length === 1 ? '' : 's'} · ${escapeHtml(route.strategy || 'balanced')} strategy</p></div><span class="protocol-chip">${route.candidates.filter(c => c.protocol === 'openai').length ? 'OpenAI API' : ''}${route.candidates.some(c => c.protocol === 'anthropic') ? (route.candidates.some(c => c.protocol === 'openai') ? ' + Anthropic' : 'Anthropic') : ''}</span></div><div class="route-candidates">${route.candidates.map((candidate, i) => `<div class="route-candidate"><span class="candidate-index">${String(i + 1).padStart(2, '0')}</span><div class="candidate-main"><strong>${escapeHtml(candidate.provider_name)}${candidate.provider_active ? '' : ' · disabled'}</strong><small>${escapeHtml(candidate.upstream_model)} · ${candidate.protocol === 'anthropic' ? 'Anthropic Messages' : 'OpenAI-compatible'}</small></div><span class="candidate-arrow">${i + 1 === route.candidates.length ? '✓' : '↓'}</span></div>`).join('')}</div><div class="route-actions"><button class="mini-button" data-edit-route="${escapeHtml(route.alias)}">Edit chain</button><button class="mini-button danger" data-delete-route="${route.id}">Delete alias</button></div></article>`).join('');
  target.querySelectorAll('[data-edit-route]').forEach(button => button.addEventListener('click', () => openRouteModal(state.routes.find(r => r.alias === button.dataset.editRoute))));
  target.querySelectorAll('[data-delete-route]').forEach(button => button.addEventListener('click', () => deleteRoute(Number(button.dataset.deleteRoute))));
}
function renderLogs() {
  const target = $('logs-table');
  if (!state.logs.length) {
    target.innerHTML = '<div class="empty-state"><div class="empty-symbol">≋</div><strong>No logged requests</strong><span>Once clients send requests through MosaicRoute, outcomes and latency appear here.</span></div>';
    return;
  }
  target.innerHTML = `<table class="data-table"><thead><tr><th>Timestamp</th><th>Alias</th><th>Upstream</th><th>Status</th><th>Latency</th><th>Tokens</th><th>Error</th></tr></thead><tbody>${state.logs.map(log => `<tr><td>${safeDate(log.created_at)}</td><td><span class="table-main">${escapeHtml(log.model_alias)}</span></td><td><span class="table-main">${escapeHtml(log.upstream_provider || '—')}</span><span class="table-sub">${escapeHtml(log.upstream_model || '')}</span></td><td>${statusPill(log.status_code)}</td><td>${formatNum(log.latency_ms)} ms</td><td>${formatNum((log.prompt_tokens || 0) + (log.completion_tokens || 0))}</td><td title="${escapeHtml(log.error_text)}">${escapeHtml(log.error_text ? log.error_text.slice(0, 60) : '—')}</td></tr>`).join('')}</tbody></table>`;
}

$('add-provider-button').addEventListener('click', () => openProviderModal());
$('provider-cancel').addEventListener('click', closeModal);
$('modal-close').addEventListener('click', closeModal);
$('modal-backdrop').addEventListener('click', (e) => { if (e.target === $('modal-backdrop')) closeModal(); });
$('provider-form').addEventListener('submit', saveProvider);
function updateAuthFields() {
  const mode = $('provider-auth-type').value;
  $('provider-auth-header-fields').classList.toggle('hidden', !['api_key', 'session', 'custom_header'].includes(mode));
  $('provider-oauth-fields').classList.toggle('hidden', mode !== 'oauth2');
  const labels = {
    auto: 'API key / credential', none: 'Credential (not used)', bearer: 'Bearer API token', api_key: 'API key / credential',
    session: 'Session / access token', cookie: 'Cookie header / session cookie', custom_header: 'Credential value',
    custom_headers: 'Credential (use the custom headers box)', oauth2: 'OAuth access token (managed after Connect OAuth)'
  };
  $('provider-key-label').textContent = labels[mode] || 'Credential';
  $('provider-key').placeholder = mode === 'cookie' ? 'Paste a complete Cookie header only for an endpoint you are authorized to use' : mode === 'session' ? 'Paste your access/session token' : 'Paste provider credential (leave blank to keep saved credential)';
  $('provider-key').disabled = mode === 'none' || mode === 'custom_headers' || mode === 'oauth2';
  $('provider-key-hint').textContent = mode === 'cookie' ? 'Cookies are highly sensitive. Paste them manually only for services that document cookie-auth access; MosaicRoute never scans your browser.' : mode === 'oauth2' ? 'OAuth access and refresh tokens are encrypted after the authorization flow.' : 'Credential data and custom headers are encrypted at rest and never returned to the browser.';
}
$('provider-auth-type').addEventListener('change', updateAuthFields);
function describePresetAccess(preset) {
  const status = String(preset.free_tier_status || 'unknown').replaceAll('-', ' ');
  const access = preset.access_note || 'Free access is not guaranteed; check the provider account and current terms.';
  return `Access status: ${status}. ${access} Model discovery is attempted using the configured endpoint; this is not a guarantee that every model or endpoint is free or available.`;
}
$('provider-preset').addEventListener('change', () => {
  const preset = providerPresets[$('provider-preset').value];
  if (!preset) return;
  $('provider-name').value = preset.name;
  $('provider-preset-note').textContent = describePresetAccess(preset);
  $('provider-url').value = preset.base_url;
  $('provider-models-path').value = preset.models_path || '/models';
  $('provider-models-path-mode').value = preset.models_path_mode || 'base';
  $('provider-protocol').value = preset.protocol;
  $('provider-auth-type').value = preset.auth_type || 'auto';
  $('provider-auth-header').value = preset.auth_header || (preset.auth_type === 'api_key' ? 'x-api-key' : '');
  $('provider-auth-prefix').value = preset.auth_prefix || '';
  $('provider-key').value = '';
  $('provider-custom-headers').value = preset.default_headers ? JSON.stringify(preset.default_headers, null, 2) : '';
  updateAuthFields();
});
$('provider-protocol').addEventListener('change', () => {
  if ($('provider-auth-type').value === 'auto') updateAuthFields();
});
function openProviderModal(provider = null) {
  state.editingProvider = provider?.id ?? null;
  $('modal-title').textContent = provider ? 'Edit provider' : 'Add provider';
  $('modal-description').textContent = provider ? 'Update connection details. Leave secret fields blank to keep saved values.' : 'Choose an endpoint and its documented authentication method.';
  $('provider-form').classList.remove('hidden'); $('route-form').classList.add('hidden'); $('modal-message').classList.add('hidden');
  $('provider-id').value = provider?.id ?? '';
  $('provider-preset').value = 'custom';
  $('provider-preset-note').textContent = 'For a custom provider, use the official API endpoint and consult the provider documentation for authentication and billing.';
  $('provider-name').value = provider?.name ?? '';
  $('provider-url').value = provider?.base_url ?? '';
  $('provider-models-path').value = provider?.models_path ?? '/models';
  $('provider-models-path-mode').value = provider?.models_path_mode ?? 'base';
  $('provider-protocol').value = provider?.protocol ?? 'openai';
  $('provider-auth-type').value = provider?.auth_type ?? 'auto';
  $('provider-auth-header').value = provider?.auth_header ?? '';
  $('provider-auth-prefix').value = provider?.auth_prefix ?? '';
  $('provider-key').value = '';
  $('provider-key').disabled = ['none', 'custom_headers', 'oauth2'].includes($('provider-auth-type').value);
  $('provider-key').placeholder = provider?.api_key_set ? 'Stored credential (leave blank to keep)' : 'Paste provider credential';
  $('provider-custom-headers').value = '';
  $('provider-custom-headers').placeholder = provider?.custom_headers_set ? 'Saved custom headers (leave blank to keep)' : '{"HTTP-Referer":"http://localhost","X-Title":"MosaicRoute"}';
  const oauth = provider?.oauth_config || {};
  $('oauth-authorize-url').value = oauth.authorization_url || '';
  $('oauth-token-url').value = oauth.token_url || '';
  $('oauth-client-id').value = oauth.client_id || '';
  $('oauth-client-secret').value = '';
  $('oauth-client-secret').placeholder = provider?.auth_type === 'oauth2' ? 'Saved secret (blank keeps current)' : 'Only if required';
  $('oauth-scopes').value = oauth.scopes || '';
  $('oauth-auth-method').value = oauth.token_auth_method || 'client_secret_post';
  $('provider-rpm-limit').value = provider?.rate_limits?.rpm_limit ?? 0;
  $('provider-rpd-limit').value = provider?.rate_limits?.rpd_limit ?? 0;
  $('provider-tpm-limit').value = provider?.rate_limits?.tpm_limit ?? 0;
  $('provider-tpd-limit').value = provider?.rate_limits?.tpd_limit ?? 0;
  $('provider-concurrency-limit').value = provider?.rate_limits?.max_concurrency ?? 0;
  $('provider-active').checked = provider?.is_active ?? true;
  updateAuthFields();
  $('modal-backdrop').classList.remove('hidden'); $('provider-name').focus();
}
function closeModal() { $('modal-backdrop').classList.add('hidden'); state.editingProvider = null; state.editingAlias = null; }
async function saveProvider(event) {
  event.preventDefault();
  let customHeaders = null;
  const headersText = $('provider-custom-headers').value.trim();
  if (headersText) {
    try {
      customHeaders = JSON.parse(headersText);
      if (!customHeaders || typeof customHeaders !== 'object' || Array.isArray(customHeaders) || Object.values(customHeaders).some(v => typeof v !== 'string')) throw new Error('Custom headers must be a JSON object of string values.');
    } catch (error) { $('modal-message').textContent = `Invalid custom headers JSON: ${error.message}`; $('modal-message').classList.remove('hidden'); return; }
  }
  const payload = {
    name: $('provider-name').value.trim(), base_url: $('provider-url').value.trim(), models_path: $('provider-models-path').value.trim() || '/models', models_path_mode: $('provider-models-path-mode').value, protocol: $('provider-protocol').value,
    auth_type: $('provider-auth-type').value, auth_header: $('provider-auth-header').value.trim(), auth_prefix: $('provider-auth-prefix').value,
    api_key: $('provider-key').value, custom_headers: customHeaders,
    oauth_config: { authorization_url: $('oauth-authorize-url').value.trim(), token_url: $('oauth-token-url').value.trim(), client_id: $('oauth-client-id').value.trim(), client_secret: $('oauth-client-secret').value, scopes: $('oauth-scopes').value.trim(), token_auth_method: $('oauth-auth-method').value },
    rpm_limit: Math.max(0, Number($('provider-rpm-limit').value || 0)),
    rpd_limit: Math.max(0, Number($('provider-rpd-limit').value || 0)),
    tpm_limit: Math.max(0, Number($('provider-tpm-limit').value || 0)),
    tpd_limit: Math.max(0, Number($('provider-tpd-limit').value || 0)),
    max_concurrency: Math.max(0, Number($('provider-concurrency-limit').value || 0)),
    is_active: $('provider-active').checked
  };
  const button = $('provider-save'); button.disabled = true; button.textContent = 'Saving…';
  try {
    const wasEditing = Boolean(state.editingProvider);
    const saved = await api(state.editingProvider ? `/api/providers/${state.editingProvider}` : '/api/providers', { method: state.editingProvider ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    closeModal(); await loadProviders(); await loadDiscoveredModels(); await refreshOverview();
    toast(wasEditing ? 'Provider updated' : 'Provider saved');
    // OAuth connections need the user to complete the browser authorization flow
    // before a model list can be fetched; other connections are discovered now.
    if (saved?.id && saved.is_active && saved.auth_type === 'oauth2' && !saved.oauth_connected) {
      toast('Provider saved. Click Connect OAuth on its card; MosaicRoute will discover models after the callback.');
    } else if (saved?.id && saved.is_active) {
      const discovered = await api(`/api/providers/${saved.id}/discover`, { method: 'POST', body: '{}' });
      await Promise.all([loadDiscoveredModels(), loadRoutes(), refreshOverview()]);
      if (discovered.ok) toast(`Auto-discovered ${discovered.count} model(s) from ${saved.name}`);
      else toast(`Provider saved; model discovery needs configuration: ${discovered.message}`, 'error');
    }
  } catch (error) { $('modal-message').textContent = error.message; $('modal-message').classList.remove('hidden'); }
  finally { button.disabled = false; button.textContent = 'Save provider'; }
}
async function connectOAuth(id) {
  try {
    const result = await api(`/api/providers/${id}/oauth/start`, { method: 'POST', body: '{}' });
    window.location.assign(result.authorization_url);
  } catch (error) { toast(error.message, 'error'); }
}
async function viewProviderQuota(id) {
  const target = $(`provider-test-${id}`); target.classList.remove('hidden'); target.textContent = 'Loading local quota ledger…';
  try {
    const result = await api(`/api/providers/${id}/quota`);
    const limits = result.limits || {};
    const minute = result.last_minute || {};
    const today = result.today_utc || {};
    target.textContent = `Local guardrails: ${minute.requests || 0}/${limits.rpm_limit || '∞'} req/min; ${today.requests || 0}/${limits.rpd_limit || '∞'} req/UTC day; ${minute.tokens || 0}/${limits.tpm_limit || '∞'} reserved/actual tokens/min; ${today.tokens || 0}/${limits.tpd_limit || '∞'} tokens/day; ${result.in_flight || 0} in flight. Zero limits are not configured.`;
    target.classList.remove('test-failed'); target.classList.add('test-ok');
  } catch (error) { target.textContent = error.message; target.classList.add('test-failed'); }
}
async function testProvider(id) {
  const target = $(`provider-test-${id}`); target.classList.remove('hidden'); target.textContent = 'Testing endpoint…';
  try {
    const result = await api(`/api/providers/${id}/test`, { method: 'POST', body: '{}' });
    target.textContent = `${result.ok ? '✓ Connection successful' : '× Connection failed'} · ${result.status || 'network'} · ${result.latency_ms} ms · ${result.message}`;
    target.classList.toggle('test-ok', result.ok); target.classList.toggle('test-failed', !result.ok);
    await loadProviders();
  } catch (error) { target.textContent = error.message; target.classList.remove('test-ok'); target.classList.add('test-failed'); }
}
async function discoverProvider(id) {
  const provider = state.providers.find(p => p.id === id);
  try {
    toast(`Discovering models from ${provider?.name || 'provider'}…`);
    const result = await api(`/api/providers/${id}/discover`, { method: 'POST', body: '{}' });
    if (result.ok) toast(`${result.count} models discovered from ${provider?.name || 'provider'}`);
    else toast(result.message || 'Discovery failed; check provider settings.', 'error');
    await Promise.all([loadDiscoveredModels(), loadProviders(), loadRoutes(), refreshOverview()]);
    go('models');
  } catch (error) { toast(error.message, 'error'); }
}
function renderDiscoveredModels() {
  const target = $('discovered-models-list');
  if (!target) return;
  const filter = ($('discovery-search')?.value || '').trim().toLowerCase();
  const rows = state.discoveredModels.filter(m => !filter || `${m.model_id} ${m.provider_name}`.toLowerCase().includes(filter));
  $('discovery-status').textContent = state.discoveredModels.length
    ? `${state.discoveredModels.length} currently available model entries across ${new Set(state.discoveredModels.map(m => m.provider_id)).size} providers. Free labels are hints from endpoint metadata/model IDs, not guarantees of future free access.`
    : 'Model catalogs have not been refreshed yet. Provider model-list endpoints and authentication vary.';
  if (!rows.length) {
    target.innerHTML = `<div class="empty-state compact"><div class="empty-symbol">⌕</div><strong>${filter ? 'No matching models' : 'No discovered models yet'}</strong><span>${filter ? 'Try a shorter search.' : 'Add a provider, then click Discover models on its card or use Discover all providers.'}</span></div>`;
    return;
  }
  target.innerHTML = `<table class="data-table"><thead><tr><th>Model ID</th><th>Provider</th><th>Protocol</th><th>Cost hint</th><th>Last seen</th><th></th></tr></thead><tbody>${rows.map(m => {
    const hint = m.free_status === 'provider-priced-zero' ? 'Provider priced $0' : m.free_status === 'explicit-free-model-id-hint' ? 'Free-ID hint' : 'Unknown';
    const klass = m.free_status === 'provider-priced-zero' ? 'good' : m.free_status === 'explicit-free-model-id-hint' ? 'neutral' : 'neutral';
    return `<tr><td><span class="table-main">${escapeHtml(m.model_id)}</span><span class="table-sub">${escapeHtml(m.display_name || '')}</span></td><td>${escapeHtml(m.provider_name)}</td><td>${escapeHtml(m.protocol)}</td><td><span class="status-pill ${klass}">${escapeHtml(hint)}</span></td><td>${safeDate(m.last_seen_at)}</td><td><button class="mini-button" data-use-model="${escapeHtml(m.provider_id)}" data-model-id="${escapeHtml(m.model_id)}" data-protocol="${escapeHtml(m.protocol)}">Create alias</button></td></tr>`;
  }).join('')}</tbody></table>`;
  target.querySelectorAll('[data-use-model]').forEach(button => button.addEventListener('click', () => createAliasFromDiscoveredModel(Number(button.dataset.useModel), button.dataset.modelId, button.dataset.protocol)));
}
async function createAliasFromDiscoveredModel(providerId, modelId, protocol) {
  const safeSlug = String(modelId).toLowerCase().replace(/[^a-z0-9._/-]+/g, '-').replace(/\/+|\.+/g, '-').replace(/^-+|-+$/g, '').slice(0, 70) || 'discovered-model';
  const suggested = `${safeSlug}`;
  const alias = window.prompt('Choose a local alias for this discovered model:', suggested);
  if (!alias) return;
  try {
    await api('/api/models', { method: 'POST', body: JSON.stringify({ alias: alias.trim(), strategy: 'balanced', candidates: [{ provider_id: providerId, upstream_model: modelId }] }) });
    await Promise.all([loadRoutes(), refreshOverview()]);
    toast(`Alias “${alias.trim()}” created`);
  } catch (error) { toast(error.message, 'error'); }
}
async function refreshAllDiscovery() {
  const button = $('discover-all-button');
  if (button) { button.disabled = true; button.textContent = 'Discovering…'; }
  try {
    const result = await api('/api/discovery/refresh', { method: 'POST', body: '{}' });
    await Promise.all([loadDiscoveredModels(), loadProviders(), loadRoutes(), refreshOverview()]);
    const failed = result.results.filter(r => !r.ok).length;
    const failures = result.results.filter(r => !r.ok).slice(0, 5).map(r => `${r.provider || 'Provider ' + r.provider_id}: ${r.message}`).join(' · ');
    $('discovery-status').textContent = `Checked ${result.providers_checked} providers; ${result.providers_succeeded} succeeded; ${result.models_available} models available; ${failed} provider(s) did not expose a readable model catalog.${failures ? ' Examples: ' + failures : ''}`;
    toast(`Discovery finished: ${result.models_available} model entries`);
  } catch (error) { toast(error.message, 'error'); }
  finally { if (button) { button.disabled = false; button.textContent = '↻ Discover all providers'; } }
}
async function buildAutoRoutes() {
  const button = $('create-auto-routes-button');
  if (button) { button.disabled = true; button.textContent = 'Building…'; }
  try {
    const result = await api('/api/discovery/create-auto-routes', { method: 'POST', body: '{}' });
    await Promise.all([loadRoutes(), refreshOverview()]);
    const made = (result.created_or_updated || []).map(r => `${r.alias} (${r.candidates})`).join(', ');
    const skipped = (result.skipped || []).map(r => r.alias).join(', ');
    toast(`Smart aliases updated: ${made || 'none matched'}` + (skipped ? `. Skipped: ${skipped}` : ''), made ? 'success' : 'error');
    $('discovery-status').textContent = `${result.note || ''} ${skipped ? 'No candidates for: ' + skipped + '.' : ''}`;
  } catch (error) { toast(error.message, 'error'); }
  finally { if (button) { button.disabled = false; button.textContent = '✦ Build smart aliases'; } }
}

async function deleteProvider(id) {
  const provider = state.providers.find(p => p.id === id);
  if (!window.confirm(`Delete provider “${provider?.name || id}”? Any route candidates for it will be removed.`)) return;
  try { await api(`/api/providers/${id}`, { method: 'DELETE' }); await loadProviders(); await loadRoutes(); await loadDiscoveredModels(); await refreshOverview(); toast('Provider deleted'); }
  catch (error) { toast(error.message, 'error'); }
}

$('discover-all-button').addEventListener('click', refreshAllDiscovery);
$('create-auto-routes-button').addEventListener('click', buildAutoRoutes);
$('discovery-search').addEventListener('input', renderDiscoveredModels);
$('add-route-button').addEventListener('click', () => openRouteModal());
$('route-cancel').addEventListener('click', closeModal);
$('add-candidate-button').addEventListener('click', () => addCandidateRow());
$('route-form').addEventListener('submit', saveRoute);
function openRouteModal(route = null) {
  if (!state.providers.length) { toast('Add a provider before creating an alias.', 'error'); go('providers'); return; }
  state.editingAlias = route?.alias ?? null;
  $('modal-title').textContent = route ? `Edit “${route.alias}”` : 'Create model alias';
  $('modal-description').textContent = 'Define a stable model ID and an ordered fallback chain.';
  $('route-form').classList.remove('hidden'); $('provider-form').classList.add('hidden'); $('modal-message').classList.add('hidden');
  $('route-alias').value = route?.alias ?? '';
  $('route-strategy').value = route?.strategy || 'balanced';
  $('candidate-list').innerHTML = '';
  if (route) route.candidates.forEach(c => addCandidateRow(c)); else addCandidateRow();
  $('modal-backdrop').classList.remove('hidden'); $('route-alias').focus();
}
function addCandidateRow(candidate = null) {
  const row = document.createElement('div'); row.className = 'candidate-row';
  const options = state.providers.map(p => `<option value="${p.id}" ${candidate && candidate.provider_id === p.id ? 'selected' : ''}>${escapeHtml(p.name)}${p.is_active ? '' : ' (disabled)'} · ${p.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI'}</option>`).join('');
  row.innerHTML = `<select aria-label="Candidate provider" required>${options}</select><input aria-label="Upstream model ID" required maxlength="200" placeholder="Model ID, e.g. llama3.2" value="${escapeHtml(candidate?.upstream_model ?? '')}"><button type="button" class="remove-candidate" title="Remove fallback" aria-label="Remove fallback">×</button>`;
  row.querySelector('.remove-candidate').addEventListener('click', () => { if ($('candidate-list').children.length > 1) row.remove(); else toast('Keep at least one candidate.', 'error'); });
  $('candidate-list').appendChild(row);
}
async function saveRoute(event) {
  event.preventDefault();
  const rows = [...$('candidate-list').querySelectorAll('.candidate-row')];
  const payload = { alias: $('route-alias').value.trim(), strategy: $('route-strategy').value, candidates: rows.map(row => ({ provider_id: Number(row.querySelector('select').value), upstream_model: row.querySelector('input').value.trim() })) };
  const button = $('route-save'); button.disabled = true; button.textContent = 'Saving…';
  try {
    await api('/api/models', { method: 'POST', body: JSON.stringify(payload) });
    closeModal(); await loadRoutes(); await refreshOverview(); toast('Model alias saved');
  } catch (error) { $('modal-message').textContent = error.message; $('modal-message').classList.remove('hidden'); }
  finally { button.disabled = false; button.textContent = 'Save alias'; }
}
async function deleteRoute(id) {
  const route = state.routes.find(r => r.id === id);
  if (!window.confirm(`Delete model alias “${route?.alias || id}”?`)) return;
  try { await api(`/api/models/${id}`, { method: 'DELETE' }); await loadRoutes(); await refreshOverview(); toast('Model alias deleted'); }
  catch (error) { toast(error.message, 'error'); }
}

function fillPlaygroundModels() {
  const select = $('playground-model');
  const current = select.value;
  const models = state.routes.filter(route => route.candidates.some(c => c.protocol === 'openai' && c.provider_active));
  select.innerHTML = models.length ? models.map(r => `<option value="${escapeHtml(r.alias)}">${escapeHtml(r.alias)}</option>`).join('') : '<option value="">Add an active OpenAI-compatible alias first</option>';
  if (models.some(r => r.alias === current)) select.value = current;
  $('playground-submit').disabled = !models.length;
}
$('playground-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const alias = $('playground-model').value;
  const prompt = $('playground-prompt').value.trim();
  if (!alias || !prompt || !state.bootstrap?.local_api_key) { toast('Choose an alias, enter a prompt, and check API settings.', 'error'); return; }
  const stream = $('playground-stream').checked;
  const button = $('playground-submit'); button.disabled = true; button.textContent = 'Requesting…';
  const output = $('playground-response'); output.textContent = '';
  $('response-subtitle').textContent = `Requesting ${alias}…`;
  $('copy-response').disabled = true; state.lastResponse = '';
  const started = performance.now();
  try {
    const response = await fetch('/v1/chat/completions', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${state.bootstrap.local_api_key}`, 'Accept': stream ? 'text/event-stream' : 'application/json' },
      body: JSON.stringify({ model: alias, messages: [{ role: 'user', content: prompt }], stream })
    });
    if (!response.ok) {
      const body = await response.text();
      let message = body; try { const data = JSON.parse(body); message = data?.error?.message || data?.detail || body; } catch { /* use raw text */ }
      throw new Error(`HTTP ${response.status}: ${String(message).slice(0, 2000)}`);
    }
    if (!stream) {
      const data = await response.json();
      const answer = data?.choices?.[0]?.message?.content ?? JSON.stringify(data, null, 2);
      output.textContent = typeof answer === 'string' ? answer : JSON.stringify(answer, null, 2);
    } else {
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let answer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/); buffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim(); if (!raw || raw === '[DONE]') continue;
          try {
            const data = JSON.parse(raw);
            const delta = data?.choices?.[0]?.delta?.content;
            if (typeof delta === 'string') { answer += delta; output.textContent = answer; }
          } catch { /* incomplete or non-JSON SSE frame */ }
        }
      }
      if (!answer && !output.textContent) output.textContent = 'Stream ended without text content. Check provider compatibility or use non-streaming mode.';
    }
    state.lastResponse = output.textContent;
    $('copy-response').disabled = !state.lastResponse;
    $('response-subtitle').textContent = `Completed in ${Math.round(performance.now() - started)} ms · ${alias}`;
    toast('Request completed');
    await Promise.all([loadLogs(), refreshOverview()]);
  } catch (error) {
    output.textContent = error.message;
    state.lastResponse = output.textContent;
    $('copy-response').disabled = false;
    $('response-subtitle').textContent = 'Request failed';
    toast('Request failed — see the response panel.', 'error');
    await Promise.all([loadLogs().catch(() => {}), refreshOverview().catch(() => {})]);
  } finally { button.disabled = false; button.innerHTML = 'Run request <span>↗</span>'; }
});
$('copy-response').addEventListener('click', async () => { if (!state.lastResponse) return; try { await navigator.clipboard.writeText(state.lastResponse); toast('Response copied'); } catch { toast('Clipboard access was blocked by your browser.', 'error'); } });
$('reload-logs').addEventListener('click', () => loadLogs().then(() => toast('Logs refreshed')).catch(err => toast(err.message, 'error')));
$('clear-logs-button').addEventListener('click', async () => {
  if (!window.confirm('Delete all request metadata logs? This does not affect providers or aliases.')) return;
  try { await api('/api/logs', { method: 'DELETE' }); await loadLogs(); await refreshOverview(); toast('Request logs cleared'); }
  catch (error) { toast(error.message, 'error'); }
});

document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  const kind = button.dataset.copy;
  let value = '';
  if (kind === 'base-url') value = state.bootstrap?.base_url || '';
  if (kind === 'api-key') value = state.bootstrap?.local_api_key || '';
  if (kind === 'curl') value = $('settings-curl').textContent;
  try { await navigator.clipboard.writeText(value); toast('Copied to clipboard'); }
  catch { toast('Clipboard access was blocked by your browser.', 'error'); }
}));


// Backup & Restore handlers
const downloadBackupBtn = $('download-backup-button');
if (downloadBackupBtn) {
  downloadBackupBtn.addEventListener('click', async () => {
    try {
      const data = await api('/api/backup');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `mosaicroute-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast('Backup exported successfully');
    } catch (err) {
      toast(err.message, 'error');
    }
  });
}

const restoreBackupInput = $('restore-backup-input');
if (restoreBackupInput) {
  restoreBackupInput.addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const payload = JSON.parse(text);
      if (!window.confirm(`Restore configuration from "${file.name}"? This replaces current providers and model aliases.`)) {
        restoreBackupInput.value = '';
        return;
      }
      const res = await api('/api/restore', { method: 'POST', body: JSON.stringify(payload) });
      toast(`Restored ${res.restored_providers} providers and ${res.restored_routes} routes`);
      await loadEverything();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      restoreBackupInput.value = '';
    }
  });
}

// Command Palette (Ctrl+K / Cmd+K)
const cmdPalette = $('cmd-palette-backdrop');
const cmdInput = $('cmd-palette-input');
const cmdResults = $('cmd-palette-results');

const commands = [
  { title: 'Go to Overview', icon: '◫', action: () => selectPage('overview') },
  { title: 'Go to Providers', icon: '⇄', action: () => selectPage('providers') },
  { title: 'Go to Models & Aliases', icon: '⌘', action: () => selectPage('models') },
  { title: 'Go to Playground', icon: '✳', action: () => selectPage('playground') },
  { title: 'Go to Request Logs', icon: '≋', action: () => selectPage('logs') },
  { title: 'Go to Settings', icon: '⚙', action: () => selectPage('settings') },
  { title: 'Add New Provider', icon: '+', action: () => { selectPage('providers'); $('add-provider-button').click(); } },
  { title: 'Discover All Providers', icon: '↻', action: () => { selectPage('models'); $('discover-all-button').click(); } },
  { title: 'Build Smart Aliases', icon: '✦', action: () => { selectPage('models'); $('create-auto-routes-button').click(); } },
  { title: 'Clear Request Logs', icon: '✕', action: () => { selectPage('logs'); $('clear-logs-button').click(); } }
];

function renderCommands(filter = '') {
  if (!cmdResults) return;
  cmdResults.innerHTML = '';
  const filtered = commands.filter(c => c.title.toLowerCase().includes(filter.toLowerCase()));
  if (filtered.length === 0) {
    cmdResults.innerHTML = '<div style="padding:10px; color:#94a3b8; font-size:0.9rem;">No matching commands found</div>';
    return;
  }
  filtered.forEach((cmd, idx) => {
    const item = document.createElement('div');
    item.style.cssText = 'display:flex; align-items:center; gap:10px; padding:8px 12px; border-radius:6px; cursor:pointer; font-size:0.92rem; color:#f1f5f9; transition:background 0.15s;';
    item.innerHTML = `<span style="opacity:0.7;">${cmd.icon}</span> <span>${escapeHtml(cmd.title)}</span>`;
    item.addEventListener('mouseenter', () => item.style.background = 'rgba(255,255,255,0.08)');
    item.addEventListener('mouseleave', () => item.style.background = 'transparent');
    item.addEventListener('click', () => {
      cmdPalette.classList.add('hidden');
      cmd.action();
    });
    cmdResults.appendChild(item);
  });
}

window.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (cmdPalette) {
      cmdPalette.classList.toggle('hidden');
      if (!cmdPalette.classList.contains('hidden')) {
        cmdInput.value = '';
        renderCommands('');
        cmdInput.focus();
      }
    }
  } else if (e.key === 'Escape' && cmdPalette && !cmdPalette.classList.contains('hidden')) {
    cmdPalette.classList.add('hidden');
  }
});

if (cmdInput) {
  cmdInput.addEventListener('input', () => renderCommands(cmdInput.value));
}

if (cmdPalette) {
  cmdPalette.addEventListener('click', (e) => {
    if (e.target === cmdPalette) cmdPalette.classList.add('hidden');
  });
}

checkSession();
