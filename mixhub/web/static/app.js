'use strict';
const $ = id => document.getElementById(id);
// Interface language (`/i18n.js` sets MIXHUB_I18N): the settings choice, else the `language` preference, else the
// browser language, else English. A key a language lacks falls back to English.
const I18N = window.MIXHUB_I18N || {preference: 'auto', default: 'en', strings: {}};
const LS_LANGUAGE = 'mixhub_language';
const LANG = (() => {
  const known = code => (I18N.strings[code] ? code : '');
  let chosen = '';
  try {chosen = localStorage.getItem(LS_LANGUAGE) || '';} catch {}
  if (known(chosen)) return chosen;
  if (known(I18N.preference)) return I18N.preference;
  const nav = window.navigator || {};
  const wanted = nav.languages || (nav.language ? [nav.language] : []);
  return wanted.map(l => known(String(l).slice(0, 2).toLowerCase())).find(Boolean) || I18N.default || 'en';
})();
function t(key, vars) {
  const text = (I18N.strings[LANG] || {})[key] ?? (I18N.strings[I18N.default] || {})[key] ?? key;
  return vars ? text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : text;
}
// Static markup: data-i18n (text), data-i18n-title, data-i18n-aria (aria-label), data-i18n-ph (placeholder)
function applyI18n(root) {
  if (document.documentElement) document.documentElement.lang = LANG;
  root.querySelectorAll('[data-i18n]').forEach(el => {el.textContent = t(el.dataset.i18n);});
  for (const [data, attr] of [['i18nTitle', 'title'], ['i18nAria', 'aria-label'], ['i18nPh', 'placeholder']]) {
    const sel = '[data-' + data.replace(/[A-Z]/g, c => '-' + c.toLowerCase()) + ']';
    root.querySelectorAll(sel).forEach(el => el.setAttribute(attr, t(el.dataset[data])));
  }
  root.querySelectorAll('[data-quota-name]').forEach(el => {
    el.setAttribute('title', t('web.quota.item_title', {name: el.dataset.quotaTip || el.dataset.quotaName}));
    el.setAttribute('aria-label', t('web.quota.item_aria', {name: el.dataset.quotaName}));
  });
}
applyI18n(document);
const labels = {claude: 'Claude', codex: 'Codex', agy: 'Gemini', opencode: 'OpenCode', deepseek: 'DeepSeek'};
const PROVIDER_TOOLTIPS = {claude: 'Claude Code', codex: 'OpenAI Codex CLI', agy: 'Gemini (Antigravity)', opencode: 'OpenCode'};
const classes = {claude: 'c', codex: 'x', agy: 'a', opencode: 'o', deepseek: 'd'};
const BRAND_COLORS = {claude: '#d97757', codex: '#DDD6FE', agy: '#9B72CB', opencode: '#B7B1B1', deepseek: '#00afff'};
const BRAND_SVGS = {
  claude: '<svg class="brand-svg claude-svg" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="#d97757" d="m4.7144 15.9555 4.7174-2.6471.079-.2307-.079-.1275h-.2307l-.7893-.0486-2.6956-.0729-2.3375-.0971-2.2646-.1214-.5707-.1215-.5343-.7042.0546-.3522.4797-.3218.686.0608 1.5179.1032 2.2767.1578 1.6514.0972 2.4468.255h.3886l.0546-.1579-.1336-.0971-.1032-.0972L6.973 9.8356l-2.55-1.6879-1.3356-.9714-.7225-.4918-.3643-.4614-.1578-1.0078.6557-.7225.8803.0607.2246.0607.8925.686 1.9064 1.4754 2.4893 1.8336.3643.3035.1457-.1032.0182-.0728-.164-.2733-1.3539-2.4467-1.445-2.4893-.6435-1.032-.17-.6194c-.0607-.255-.1032-.4674-.1032-.7285L6.287.1335 6.6997 0l.9957.1336.419.3642.6192 1.4147 1.0018 2.2282 1.5543 3.0296.4553.8985.2429.8318.091.255h.1579v-.1457l.1275-1.706.2368-2.0947.2307-2.6957.0789-.7589.3764-.9107.7468-.4918.5828.2793.4797.686-.0668.4433-.2853 1.8517-.5586 2.9021-.3643 1.9429h.2125l.2429-.2429.9835-1.3053 1.6514-2.0643.7286-.8196.85-.9046.5464-.4311h1.0321l.759 1.1293-.34 1.1657-1.0625 1.3478-.8804 1.1414-1.2628 1.7-.7893 1.36.0729.1093.1882-.0183 2.8535-.607 1.5421-.2794 1.8396-.3157.8318.3886.091.3946-.3278.8075-1.967.4857-2.3072.4614-3.4364.8136-.0425.0304.0486.0607 1.5482.1457.6618.0364h1.621l3.0175.2247.7892.522.4736.6376-.079.4857-1.2142.6193-1.6393-.3886-3.825-.9107-1.3113-.3279h-.1822v.1093l1.0929 1.0686 2.0035 1.8092 2.5075 2.3314.1275.5768-.3218.4554-.34-.0486-2.2039-1.6575-.85-.7468-1.9246-1.621h-.1275v.17l.4432.6496 2.3436 3.5214.1214 1.0807-.17.3521-.6071.2125-.6679-.1214-1.3721-1.9246L14.38 17.959l-1.1414-1.9428-.1397.079-.674 7.2552-.3156.3703-.7286.2793-.6071-.4614-.3218-.7468.3218-1.4753.3886-1.9246.3157-1.53.2853-1.9004.17-.6314-.0121-.0425-.1397.0182-1.4328 1.9672-2.1796 2.9446-1.7243 1.8456-.4128.164-.7164-.3704.0667-.6618.4008-.5889 2.386-3.0357 1.4389-1.882.929-1.0868-.0062-.1579h-.0546l-6.3385 4.1164-1.1293.1457-.4857-.4554.0608-.7467.2307-.2429 1.9064-1.3114Z"/></svg>',
  codex: '<svg class="brand-svg codex-svg" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"/></svg>',
  opencode: '<svg class="brand-svg opencode-svg" viewBox="96 96 320 320" width="15" height="15" aria-hidden="true"><path fill="currentColor" opacity=".45" d="M320 224V352H192V224H320Z"/><path fill="currentColor" fill-rule="evenodd" d="M384 416H128V96H384V416ZM320 160H192V352H320V160Z"/></svg>',
  agy: '<svg class="brand-svg agy-svg" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="url(#gemini-star-grad)" d="M11.04 19.32Q12 21.51 12 24q0-2.49.93-4.68.96-2.19 2.58-3.81t3.81-2.55Q21.51 12 24 12q-2.49 0-4.68-.93a12.3 12.3 0 0 1-3.81-2.58 12.3 12.3 0 0 1-2.58-3.81Q12 2.49 12 0q0 2.49-.96 4.68-.93 2.19-2.55 3.81a12.3 12.3 0 0 1-3.81 2.58Q2.49 12 0 12q2.49 0 4.68.96 2.19.93 3.81 2.55t2.55 3.81"/></svg>',
  deepseek: '<svg class="brand-svg deepseek-svg" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="#00afff" d="M12 2.4L21.6 12L12 21.6L2.4 12L12 2.4ZM12 6.8L6.8 12L12 17.2L17.2 12L12 6.8Z"/></svg>'
};
const state = {sessions: [], counts: {total: 0, claude: 0, agy: 0, codex: 0, live: 0, pinned: 0, liveMemoryMB: 0}, activeSession: null, sidebarCollapsed: false, filter: {tool: 'all', query: '', showTemp: false}, lastError: null};
let messages = [], rendered = [], userTurns = [], activeTurnIdx = -1, matches = [], matchIndex = -1, selectedKey = '', limit = 60, readSequence = 0, listSequence = 0, loading = false, toastTimer, searchTimer;
// Local host label reported by `/api/sessions` (`data.host`, from gitignored `Preferences["host_label"]` or hostname).
let serverHostLabel = '';
const key = s => s.tool + ':' + s.id;
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
// legacy-greetings: Good morning | Good afternoon | Good evening | Hello, night owl
function detectHandoffChain(s) {
  if (Array.isArray(s.handoffChain) && s.handoffChain.length >= 2) return s.handoffChain;
  if (Array.isArray(s.relayTools) && s.relayTools.length >= 2) return s.relayTools;
  if (s.handoffFrom && s.handoffFrom !== s.tool) return [s.handoffFrom, s.tool];
  return [];
}
function createBadgeElement(s) {
  const chain = detectHandoffChain(s);
  const el = document.createElement('span');
  if (chain.length >= 2) {
    const prev = Object.hasOwn(BRAND_COLORS, chain[chain.length - 2]) ? chain[chain.length - 2] : 'claude';
    const cur = Object.hasOwn(BRAND_COLORS, chain[chain.length - 1]) ? chain[chain.length - 1] : 'codex';
    el.className = 'badge badge-stack';
    el.title = t('web.relay_chain', {chain: chain.map(x => labels[x] || x).join(' → ')});
    el.innerHTML = `<svg class="brand-svg relay-svg" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <defs><linearGradient id="relay-${prev}-${cur}" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="${BRAND_COLORS[prev]}"/><stop offset="50%" stop-color="${BRAND_COLORS[prev]}"/>
        <stop offset="50%" stop-color="${BRAND_COLORS[cur]}"/><stop offset="100%" stop-color="${BRAND_COLORS[cur]}"/>
      </linearGradient></defs>
      <path fill="url(#relay-${prev}-${cur})" stroke="#8787d7" stroke-width=".6" d="M12 2L22 12L12 22L2 12Z"/>
    </svg>`;
    return el;
  }
  el.className = 'badge ' + (classes[s.tool] || 'c');
  el.innerHTML = BRAND_SVGS[s.tool] || escapeHTML(s.badge || '●');
  return el;
}
function relative(value) {
  const seconds = Math.max(0, Date.now() / 1000 - value);
  if (!value) return '';
  if (seconds < 60) return t('web.age.now');
  if (seconds < 3600) return t('web.age.m', {n: Math.floor(seconds / 60)});
  if (seconds < 86400) return t('web.age.h', {n: Math.floor(seconds / 3600)});
  return t('web.age.d', {n: Math.floor(seconds / 86400)});
}
function cleanShortPath(raw) {
  return String(raw || '').replace(/^\/private\/tmp\b/, '/tmp').replace(/^\/private\/var\b/, '/var');
}
function projectLabel(rawCwd) {
  const path = String(rawCwd || '').trim()
    .replace(/^\/private\/tmp\b/, '/tmp')
    .replace(/^\/private\/var\b/, '/var')
    .replace(/^\/Users\/[^/]+/, '~');
  if (!path || path === '~' || path === '/' || path === 'local' || path.startsWith('/tmp') || path.startsWith('/var')) return '';
  if (/^~\/\.(?:claude|gemini|codex)(?:\/|$)/.test(path)) return path.endsWith('/memory') ? 'memory' : '';
  const segments = path.replace(/^(?:~\/|\/)/, '').split('/').filter(Boolean);
  const containers = new Set(['Downloads', 'Desktop', 'Documents', 'Movies', 'projects', 'repos', 'code', 'work', 'M1', 'M2']);
  while (segments.length && containers.has(segments[0])) segments.shift();
  if (!segments.length) return '';
  return segments.slice(-2).join('/').replace(/~/g, '');
}
function notify(text, error = false) {
  $('toast').textContent = text; $('toast').classList.toggle('error', error); $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => {$('toast').hidden = true;}, error ? 9000 : 3500);
}
function failed(error) { state.lastError = String(error.message || error); notify(state.lastError, true); }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok || data?.ok === false) {
    const err = new Error(data?.message || data?.error || t('web.request_failed', {status: response.status}));
    err.data = data;
    throw err;
  }
  return data;
}
const endpoint = s => '/api/sessions/' + s.tool + '/' + encodeURIComponent(s.id);
function action(s, name, body = {}) { return api(endpoint(s) + '/' + name, body); }
function button(text, fn, className = '', title = '') {
  const el = document.createElement('button'); el.type = 'button'; el.textContent = text; el.className = className;
  if (title) { el.title = title; el.setAttribute('aria-label', title); }
  el.addEventListener('click', async event => {
    event.stopPropagation(); el.disabled = true;
    try { await fn(event); } catch (error) { failed(error); } finally { el.disabled = false; }
  });
  return el;
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (_) {
    const input = document.createElement('textarea'); input.value = text; document.body.append(input); input.select();
    const ok = document.execCommand('copy'); input.remove(); if (!ok) throw new Error(t('web.clipboard_denied'));
  }
  notify(t('web.copied'));
}
function confirmSleep(title, text) {
  $('confirm-title').textContent = title; $('confirm-text').textContent = text;
  const dialog = $('confirm-dialog'); dialog.returnValue = 'cancel'; dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true}));
}
async function hibernate(s) {
  if (!await confirmSleep(t('web.sleep_one.title'), s.title + '\n' + t('web.sleep_one.text', {n: s.processes.length}))) return;
  const result = await action(s, 'hibernate');
  notify(result.pendingPids.length ? t('web.sleep.pending') : t('web.sleep.done'));
  await refresh();
}
function actionButtons(s, compact = false) {
  const container = document.createElement('div'); container.className = compact ? 'card-actions' : 'actions';
  if (compact) return container;
  if (s.isLive) {
    container.append(button(t('web.btn.terminal'), async () => {await action(s, 'focus'); notify(t('web.focused'));}, 'primary', t('web.focus')));
  } else {
    container.append(button(t('web.btn.resume'), async () => {await action(s, 'resume'); notify(t('web.resumed')); setTimeout(() => refresh().catch(failed), 2000);}, 'primary', t('web.resume')));
  }
  const relayWrap = document.createElement('div');
  relayWrap.className = 'relay-menu-wrap';
  const pop = document.createElement('div');
  pop.className = 'relay-menu-popover';
  pop.hidden = true;
  const relayTargets = ['claude', 'codex', 'agy', 'deepseek'].filter(t => t !== s.tool);
  for (const target of relayTargets) {
    const item = button('', async () => {
      pop.hidden = true;
      const r = await action(s, 'handoff', {to: target});
      notify(t('web.handoff.done', {target: labels[target] || target, tokens: r.token_estimate}));
      await refresh();
    }, '', t('web.handoff.to', {target: labels[target] || target}));
    const icon = document.createElement('span');
    icon.className = 'mini ' + (classes[target] || 'c');
    icon.innerHTML = BRAND_SVGS[target] || '●';
    const lbl = document.createElement('span');
    lbl.textContent = labels[target] || target;
    item.append(icon, lbl);
    pop.append(item);
  }
  const trigger = button(t('web.btn.relay'), ev => {
    ev.stopPropagation();
    pop.hidden = !pop.hidden;
  }, '', t('web.handoff'));
  relayWrap.append(trigger, pop);
  container.append(relayWrap);
  if (s.isLive) {
    // Sleep only what MixHub started; an agent in another terminal is the user's to quit there.
    if (s.processes.some(p => p.resident)) container.append(button('☾', () => hibernate(s), 'icon-pill', t('web.sleep_this')));
  } else {
    container.append(button('⧉', async () => {const r = await action(s, 'resume', {dry_run: true}); await copy(r.command);}, 'icon-pill', t('web.copy_resume')));
  }
  return container;
}
function filtered() {
  const {tool, query, showTemp} = state.filter, q = query.trim().toLocaleLowerCase();
  return state.sessions.filter(s => (showTemp || !s.isTemp || s.isLive || s.pinned) && (tool === 'all' || (tool === 'live' ? s.isLive : s.tool === tool)) && (!q || [s.title, s.lastPrompt, s.firstPrompt, s.cwd, s.id, s.slug].join(' ').toLocaleLowerCase().includes(q)));
}
function startRename(s, titleNode) {
  if (titleNode.querySelector('input')) return;
  const input = document.createElement('input'); input.className = 'card-title-input'; input.value = s.title; input.maxLength = 200; input.setAttribute('aria-label', t('web.session_title'));
  titleNode.replaceChildren(input); input.focus(); input.select(); let finished = false;
  async function finish(save) {
    if (finished) return; finished = true;
    const newTitle = input.value;
    titleNode.textContent = s.title;
    if (save && newTitle.trim() !== s.title) {
      try {await action(s, 'rename', {title: newTitle}); await refresh(); notify(t('web.title_saved'));}
      catch (error) {failed(error);}
    }
    renderList();
  }
  input.addEventListener('click', e => e.stopPropagation());
  input.addEventListener('keydown', e => {e.stopPropagation(); if (e.key === 'Enter') {e.preventDefault(); finish(true);} if (e.key === 'Escape') finish(false);});
  input.addEventListener('blur', () => finish(true));
}
const LS_READ_MAP = 'mixhub_read_map';
function getReadMap() {
  try { return JSON.parse(localStorage.getItem(LS_READ_MAP) || '{}') || {}; }
  catch (_) { return {}; }
}
function markSessionRead(s) {
  if (!s) return;
  const map = getReadMap();
  map[key(s)] = Math.max(Number(s.updatedAt) || 0, Date.now() / 1000);
  s.unread = false;
  try { localStorage.setItem(LS_READ_MAP, JSON.stringify(map)); } catch (_) {}
}
function isSessionWaitApproval(s) {
  return s?.agentState === 'wait_input' || Boolean(s?.pendingApproval);
}
function extractApprovalSummary(s) {
  const text = value => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
  const pending = text(s?.pendingCommand) || text(s?.question);
  if (pending) return pending;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.kind === 'tool_call' || m.role === 'tool_call' || m.role === 'assistant') {
      const summary = text(m.text);
      if (summary) return summary;
    }
  }
  return text(s?.lastPrompt) || text(s?.title) || t('web.approval.waiting');
}
function renderApprovalBar() {
  const s = state.activeSession;
  const wait = Boolean(s && isSessionWaitApproval(s));
  $('mobile-approval-bar').hidden = !wait;
  $('mobile-approval-summary').textContent = wait ? extractApprovalSummary(s) : '';
  const pill = $('mobile-status-pill');
  pill.hidden = !s;
  pill.classList.toggle('amber', wait);
  pill.textContent = !s ? '' : wait ? t('web.approval.badge') : s.isLive ? t('web.status.live') : t('web.status.ready');
}
function isSessionUnread(s) {
  if (!s) return false;
  if (s.unread === true) return true;
  const map = getReadMap();
  const seenAt = map[key(s)];
  if (seenAt !== undefined) return (Number(s.updatedAt) || 0) > seenAt;
  return Boolean(s.isLive && s.agentState === 'working');
}

function card(s) {
  const el = document.createElement('article');
  const proj = projectLabel(s.shortCwd || s.cwd);
  const waitApproval = isSessionWaitApproval(s);
  const unread = isSessionUnread(s);
  const amberAlert = waitApproval || unread;
  el.className = 'session-card' + (!proj ? ' single-line' : '') + (key(s) === selectedKey ? ' selected' : '') + (amberAlert ? ' has-amber-time' : '');
  el.dataset.key = key(s);
  el.dataset.timeAlert = waitApproval ? 'wait_input' : unread ? 'unread' : 'normal';
  el.tabIndex = 0;
  el.setAttribute('aria-label', s.title);

  // Row 1: [Official Brand SVG ONLY - zero status dots] Title + Right-aligned timestamp (amber if unread or wait_input)
  const heading = document.createElement('div');
  heading.className = 'card-heading';
  const badge = createBadgeElement(s);
  const title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = s.title;
  title.title = s.title;
  const timeEl = document.createElement('span');
  timeEl.className = 'card-time' + (s.isLive ? ' card-live' : '') + (amberAlert ? ' amber-alert' : '');
  timeEl.textContent = relative(s.updatedAt);
  if (waitApproval) timeEl.title = t('web.card.waiting');
  else if (unread) timeEl.title = t('web.card.unread');

  const editBtn = button('✎', () => startRename(s, title), 'card-edit', t('web.edit_title'));
  const pinBtn = button(s.pinned ? '★' : '☆', async () => {await action(s, 'pin', {pinned: !s.pinned}); await refresh();}, 'card-pin' + (s.pinned ? ' pinned' : ''), s.pinned ? t('web.unpin') : t('web.pin'));
  heading.append(badge, title, timeEl, editBtn, pinBtn);

  el.append(heading);
  if (proj) {
    const meta = document.createElement('div');
    meta.className = 'card-meta';
    const cwdSpan = document.createElement('span');
    cwdSpan.className = 'cwd';
    cwdSpan.title = s.cwd || '';
    cwdSpan.textContent = proj;
    meta.append(cwdSpan);
    el.append(meta);
  }

  if (state.filter.query.trim()) {
    const preview = document.createElement('div');
    preview.className = 'card-preview';
    preview.textContent = s.lastPrompt || s.firstPrompt || '';
    if (preview.textContent) el.append(preview);
  }

  el.addEventListener('click', () => select(s).catch(failed));
  el.addEventListener('keydown', e => {if (e.target === el && (e.key === 'Enter' || e.key === ' ')) {e.preventDefault(); select(s).catch(failed);}});
  return el;
}
function renderList() {
  if ($('session-list').querySelector('.card-title-input')) return;
  const visible = filtered(), fragment = document.createDocumentFragment();
  renderWelcomeRecent(visible);
  $('visible-count').textContent = String(visible.length);
  const groups = [
    [t('web.group.pinned'), visible.filter(s => s.pinned)],
    [t('web.group.active'), visible.filter(s => !s.pinned && s.isLive)],
    [t('web.group.recent'), visible.filter(s => !s.pinned && !s.isLive)]
  ];
  for (const [label, list] of groups) {
    if (!list.length) continue;
    const group = document.createElement('div');
    group.className = 'group-title';
    group.innerHTML = '<span>' + label + '</span><span class="group-count">' + list.length + '</span>';
    fragment.append(group);
    list.slice(0, limit).forEach(s => fragment.append(card(s)));
    if (list.length > limit) fragment.append(button(t('web.list.more', {n: list.length - limit}), () => {limit += 60; renderList();}, 'load-more'));
  }
  if (!visible.length) {
    const empty = document.createElement('div');
    empty.className = 'list-empty';
    empty.textContent = t('web.list.empty');
    fragment.append(empty);
  }
  $('session-list').replaceChildren(fragment);
}
function renderWelcomeRecent(visible = filtered()) {
  const rows = [...visible]
    .sort((a, b) => Number(isSessionWaitApproval(b)) - Number(isSessionWaitApproval(a)) || (Number(b.updatedAt) || 0) - (Number(a.updatedAt) || 0))
    .slice(0, 5)
    .map(s => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'welcome-recent-item';
      row.title = s.title;
      const title = document.createElement('span');
      title.className = 'wr-title';
      title.textContent = s.title;
      row.append(createBadgeElement(s), title);
      const proj = projectLabel(s.shortCwd || s.cwd);
      if (proj) {
        const path = document.createElement('span');
        path.className = 'wr-proj';
        path.textContent = proj;
        path.title = s.cwd || '';
        row.append(path);
      }
      const time = document.createElement('span');
      time.className = 'wr-time' + (isSessionWaitApproval(s) || isSessionUnread(s) ? ' amber-alert' : '');
      time.textContent = relative(s.updatedAt);
      row.append(time);
      row.addEventListener('click', () => select(s).catch(failed));
      return row;
    });
  $('welcome-recent').replaceChildren(...rows);
}
function setFilter(tool) {
  state.filter.tool = tool; limit = 60;
  for (const el of document.querySelectorAll('[data-filter]')) {
    const act = el.dataset.filter === tool;
    el.classList.toggle('active', act);
    el.setAttribute('aria-pressed', String(act));
  }
  const morePop = $('more-filter-popover');
  if (morePop) morePop.hidden = true;
  renderList();
}
function toggleSidebar(force) {
  state.sidebarCollapsed = force === undefined ? !state.sidebarCollapsed : force;
  $('app').classList.toggle('collapsed', state.sidebarCollapsed);
  $('collapse').setAttribute('aria-label', state.sidebarCollapsed ? t('web.sidebar.expand') : t('web.sidebar.collapse'));
  $('collapse').setAttribute('title', state.sidebarCollapsed ? t('web.sidebar.expand_title') : t('web.sidebar.collapse_title'));
  $('collapse').setAttribute('aria-expanded', String(!state.sidebarCollapsed));
  localStorage.setItem('hub.sidebarCollapsed', String(state.sidebarCollapsed));
}
async function refresh() {
  const seq = ++listSequence;
  const data = await api('/api/sessions?show_temp=1'); if (seq !== listSequence) return;
  state.sessions = data.sessions; state.counts = data.counts; state.lastError = null;
  if (data.host) {
    const cleanHost = window.__mixhubCleanHostName || (h => String(h).trim());
    serverHostLabel = cleanHost(data.host);
    window.__mixhubSyncNodeBar?.();
  }
  $('live-stats').textContent = t('web.live_count', {n: data.counts.live});  // sessions, as the sidebar's Active
  const memEl = $('live-mem-pill');
  if (memEl) {
    const mb = data.counts.liveMemoryMB || 0;
    memEl.hidden = mb <= 0;
    memEl.textContent = ' · ' + (mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : Math.round(mb) + ' MB');
  }
  $('hibernate-idle').disabled = !data.sessions.some(s => s.processes.some(p => p.status === 'idle' && p.resident));
  $('source-warning').hidden = !data.warnings.length; $('source-warning').textContent = data.warnings.join('  ');
  // Unified left-to-right order: Claude -> Codex -> OpenCode -> Gemini (count non-temp main sessions unless showTemp is checked)
  const mainPool = state.sessions.filter(s => state.filter.showTemp || !s.isTemp || s.isLive || s.pinned);
  const mainCounts = {claude: 0, codex: 0, opencode: 0, agy: 0};
  for (const s of mainPool) if (mainCounts[s.tool] !== undefined) mainCounts[s.tool]++;
  // Agents with no sessions on this machine get no tile
  const overviewTools = ['claude', 'codex', 'opencode', 'agy'].filter(t => mainCounts[t] > 0);
  $('overview').replaceChildren(...(overviewTools.length ? overviewTools : ['claude']).map(tool => {
    const el = button('', () => setFilter(tool), '', PROVIDER_TOOLTIPS[tool] || labels[tool]);
    const icon = document.createElement('span');
    icon.className = 'mini ' + classes[tool];
    icon.innerHTML = BRAND_SVGS[tool] || '●';
    const label = document.createElement('span');
    label.textContent = labels[tool];
    const count = document.createElement('span');
    count.className = 'overview-count';
    count.textContent = String(mainCounts[tool] ?? 0);
    el.append(icon, label, count);
    return el;
  }));
  renderList();
  const current = data.sessions.find(s => key(s) === selectedKey);
  const scroller = $('transcript');
  const atEnd = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  const changed = current && state.activeSession && !loading && current.updatedAt !== state.activeSession.updatedAt;
  if (current && state.activeSession) {state.activeSession = {...current, messageCount: messages.length}; renderHeader();}
  // A new reply in the open session: follow it, unless you scrolled up to read or are searching the transcript
  if (changed && atEnd && !$('transcript-query').value) select(current, {follow: true}).catch(failed);
}
function inline(text) {
  const slots = [];
  let safe = escapeHTML(text).replace(/`([^`]+)`/g, (_, code) => {slots.push('<code>' + code + '</code>'); return '\u0000' + (slots.length - 1) + '\u0000';});
  safe = safe.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>');
  return safe.replace(/\u0000(\d+)\u0000/g, (_, n) => slots[Number(n)]);
}
function markdown(text) {
  const lines = text.split('\n'), out = []; let code = null, codeLang = '', paragraph = [], list = '';
  function flush() {if (paragraph.length) {out.push('<p>' + paragraph.map(inline).join('<br>') + '</p>'); paragraph = [];} if (list) {out.push('</' + list + '>'); list = '';}}
  for (const line of lines) {
    const fence = line.match(/^\s*(```+|~~~+)\s*(.*)$/);
    if (fence) {if (code !== null) {out.push('<pre><span class="code-language">' + escapeHTML(codeLang) + '</span><code>' + escapeHTML(code.join('\n')) + '</code></pre>'); code = null;} else {flush(); code = []; codeLang = fence[2];} continue;}
    if (code !== null) {code.push(line); continue;}
    const heading = line.match(/^(#{1,6})\s+(.+)$/), bullet = line.match(/^\s*(?:([-*+])|\d+[.)])\s+(.+)$/);
    if (!line.trim()) {flush(); continue;}
    if (heading) {flush(); const tag = 'h' + heading[1].length; out.push('<' + tag + '>' + inline(heading[2]) + '</' + tag + '>'); continue;}
    if (bullet) {const tag = bullet[1] ? 'ul' : 'ol'; if (list !== tag) {flush(); list = tag; out.push('<' + tag + '>');} out.push('<li>' + inline(bullet[2]) + '</li>'); continue;}
    if (line.startsWith('> ')) {flush(); out.push('<blockquote>' + inline(line.slice(2)) + '</blockquote>'); continue;}
    if (list) flush(); paragraph.push(line);
  }
  flush(); if (code !== null) out.push('<pre><code>' + escapeHTML(code.join('\n')) + '</code></pre>'); return out.join('');
}
const composerProbeCache = new Map();
async function probeComposerDelivery(s, force = false) {
  if (!s || !s.isLive) return 'dormant';
  const cacheKey = key(s) + ':1';
  if (!force && composerProbeCache.has(cacheKey)) return composerProbeCache.get(cacheKey);
  let mode = 'external_tab';
  try {
    const res = await action(s, 'send', {text: '.', allow_headless_exec: false, dry_run: true});
    if (res?.ok && res?.delivery_mode === 'tmux_pane') mode = 'tmux_pane';
  } catch (_) {
    mode = 'external_tab';
  }
  composerProbeCache.set(cacheKey, mode);
  if (state.activeSession && key(state.activeSession) === key(s)) syncComposer();
  return mode;
}
function syncComposer() {
  const inp = $('composer-input');
  const btn = $('composer-send-btn');
  if (!inp || !btn) return;
  const s = state.activeSession;
  if (!s) {
    inp.disabled = true;
    inp.placeholder = t('web.composer.pick');
    btn.disabled = true;
    btn.textContent = t('web.send');
    btn.dataset.composerMode = 'none';
    return;
  }
  if (isSessionWaitApproval(s)) {
    inp.disabled = true;
    inp.placeholder = t('web.composer.approve_first');
    btn.disabled = true;
    btn.textContent = t('web.approval.short');
    btn.dataset.composerMode = 'wait_approval';
    return;
  }
  if (!s.isLive) {
    inp.disabled = false;
    inp.placeholder = t('web.composer.asleep');
    btn.disabled = false;
    btn.textContent = t('web.resume');
    btn.dataset.composerMode = 'dormant';
    return;
  }
  const cached = composerProbeCache.get(key(s) + ':1');
  if (!cached) {
    inp.disabled = false;
    inp.placeholder = t('web.composer.detecting');
    btn.disabled = true;
    btn.textContent = t('web.composer.detecting_short');
    btn.dataset.composerMode = 'probing';
    probeComposerDelivery(s).catch(() => {});
    return;
  }
  if (cached === 'tmux_pane') {
    inp.disabled = false;
    inp.placeholder = t('web.composer.to', {tool: labels[s.tool] || s.tool});
    btn.disabled = false;
    btn.textContent = t('web.send');
    btn.dataset.composerMode = 'tmux_pane';
  } else {
    inp.disabled = true;
    inp.placeholder = t('web.composer.elsewhere');
    btn.disabled = false;
    btn.textContent = t('web.focus');
    btn.dataset.composerMode = 'external_tab';
  }
}
function renderHeader() {
  renderApprovalBar();
  syncComposer();
  const s = state.activeSession; if (!s) return;
  const chain = detectHandoffChain(s);
  $('reader-eyebrow').replaceChildren(createBadgeElement(s));
  $('reader-title').textContent = s.title;
  const meta = $('reader-meta'); meta.replaceChildren();
  if (chain.length >= 2) {
    const relayTag = document.createElement('span');
    relayTag.className = 'relay-chain-pill';
    relayTag.innerHTML = chain.map(t => (BRAND_SVGS[t] || '') + '<span>' + escapeHTML(labels[t] || t) + '</span>').join(' <span>→</span> ');
    meta.append(relayTag);
  }
  const cwd = document.createElement('code'); cwd.textContent = cleanShortPath(s.cwd);
  const count = document.createElement('span'); count.id = 'message-count'; count.className = 'meta-item'; count.textContent = loading ? t('web.loading') : t('web.msgs', {n: s.messageCount});
  meta.append(cwd, count);
  if (s.model) {
    const model = document.createElement('span');
    model.className = 'meta-item';
    model.textContent = [s.model, s.reasoningEffort].filter(Boolean).join(' ');
    meta.append(model);
  }
  meta.append(button(s.id.slice(0, 8) + ' ⧉', () => copy(s.id), 'meta-copy-btn', t('web.copy_id')));
  const actions = actionButtons(s); actions.id = 'reader-actions'; $('reader-actions').replaceWith(actions);
}
function renderEntry(entry, index) {
  let el;
  if (entry.role === 'tool' || entry.role === 'thinking') {
    el = document.createElement('details'); el.className = 'tool-details';
    const summary = document.createElement('summary');
    const kindLabel = entry.kind === 'tool_result' ? t('web.tool.result') : entry.kind === 'thinking' ? '' : t('web.tool.call');
    summary.innerHTML = '<span class="tool-name">' + escapeHTML(entry.role === 'thinking' ? t('web.tool.thinking') : entry.name || t('web.tool.tool')) + '</span>' + (kindLabel ? '<span class="tool-kind">' + kindLabel + '</span>' : '') + '<span class="tool-snippet">' + escapeHTML(entry.text.replace(/\s+/g, ' ').slice(0, 100)) + '</span>';
    el.append(summary);
    el.addEventListener('toggle', () => {if (el.open && !el.querySelector('pre')) {const pre = document.createElement('pre'); pre.textContent = entry.text; el.append(pre); if ($('transcript-query').value.trim()) highlightElement(pre, $('transcript-query').value.trim());}});
    if (entry.sourceTruncated) {const note = document.createElement('p'); note.className = 'source-note'; note.textContent = t('web.source_truncated'); el.append(note);}
  } else {
    el = document.createElement('article'); el.className = 'message ' + entry.role;
    const role = entry.role === 'user' ? t('web.role.user') : (labels[state.activeSession?.tool] || t('web.role.assistant'));
    const time = entry.timestamp && !Number.isNaN(Date.parse(entry.timestamp)) ? new Date(entry.timestamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';
    const avatarHtml = entry.role === 'user' ? 'U' : (BRAND_SVGS[state.activeSession.tool] || escapeHTML(state.activeSession.badge));
    el.innerHTML = '<div class="message-label"><span class="avatar">' + avatarHtml + '</span><strong>' + escapeHTML(role) + '</strong><span>' + escapeHTML(time) + '</span></div><div class="message-body">' + markdown(entry.text) + '</div>';
  }
  el.dataset.index = index; return el;
}
// A run of tool calls, results and thinking between two messages, folded into one collapsed row.
function toolGroup() {
  const el = document.createElement('details'); el.className = 'tool-group';
  const summary = document.createElement('summary'); el.append(summary);
  const names = new Map(); let steps = 0;
  return {el, add(entry, child) {
    child.toolGroup = el; el.append(child);
    if (entry.kind === 'tool_result') return;
    const name = entry.role === 'thinking' ? t('web.tool.thinking') : entry.name || t('web.tool.tool');
    names.set(name, (names.get(name) || 0) + 1); steps++;
    const parts = [...names].map(([n, c]) => escapeHTML(n) + (c > 1 ? ' ×' + c : ''));
    summary.innerHTML = '<span class="tool-name">' + escapeHTML(steps === 1 ? t('web.tool.step') : t('web.tool.steps', {n: steps})) + '</span><span class="tool-snippet">' + parts.join(' · ') + '</span>';
  }};
}
function buildTurnRail() {
  userTurns = [];
  messages.forEach((m, idx) => {
    if (m.role === 'user' && m.text && m.text.trim()) {
      userTurns.push({msgIdx: idx, text: m.text.replace(/\s+/g, ' ').trim()});
    }
  });
  const rail = $('turn-rail');
  if (!rail) return;
  rail.replaceChildren();
  const total = Math.max(1, messages.length - 1);
  userTurns.forEach((turn, tIdx) => {
    const tick = document.createElement('div');
    tick.className = 'turn-tick';
    tick.dataset.turnIdx = String(tIdx);
    const pct = Math.min(96, Math.max(4, (turn.msgIdx / total) * 92 + 4));
    tick.style.top = pct.toFixed(1) + '%';
    tick.textContent = '─';
    tick.title = t('web.turn.tick', {n: tIdx + 1, total: userTurns.length, text: turn.text.slice(0, 80)});
    tick.addEventListener('click', () => jumpToTurnIndex(tIdx));
    rail.append(tick);
  });
  updateStickyAndRail();
}
function updateStickyAndRail() {
  const sticky = $('sticky-prompt-bar');
  const rail = $('turn-rail');
  if (!sticky || !userTurns.length) {
    if (sticky) sticky.hidden = true;
    activeTurnIdx = -1;
    return;
  }
  const scroller = $('transcript');
  const threshold = scroller.scrollTop + 60;
  let cur = 0;
  for (let i = 0; i < userTurns.length; i++) {
    const el = rendered[userTurns[i].msgIdx];
    if (el && el.offsetTop <= threshold) cur = i;
    else break;
  }
  activeTurnIdx = cur;
  sticky.hidden = false;
  $('sticky-turn-badge').textContent = (cur + 1) + '/' + userTurns.length;
  $('sticky-turn-text').textContent = userTurns[cur].text;
  if (rail) {
    for (const tick of rail.querySelectorAll('.turn-tick')) {
      const isAct = Number(tick.dataset.turnIdx) === cur;
      tick.classList.toggle('active', isAct);
      tick.textContent = isAct ? '━' : '─';
    }
  }
}
function jumpToTurnIndex(tIdx) {
  if (!userTurns.length) return;
  const clamped = Math.max(0, Math.min(userTurns.length - 1, tIdx));
  const el = rendered[userTurns[clamped].msgIdx];
  if (el) {
    el.scrollIntoView({block: 'start'});
    updateStickyAndRail();
  }
}
function jumpTurn(delta) {
  if (!userTurns.length) return;
  const cur = activeTurnIdx < 0 ? 0 : activeTurnIdx;
  jumpToTurnIndex(cur + delta);
}
// `follow`: reload the open session for a new reply, without the loading placeholder or the mobile sidebar toggle
async function select(session, {follow = false} = {}) {
  const seq = ++readSequence; selectedKey = key(session);
  markSessionRead(session);
  state.activeSession = {...session, messageCount: 0}; loading = true;
  messages = []; rendered = []; userTurns = []; activeTurnIdx = -1; matches = []; matchIndex = -1; $('transcript-query').value = ''; $('match-count').textContent = '';
  if ($('sticky-prompt-bar')) $('sticky-prompt-bar').hidden = true;
  if ($('turn-rail')) $('turn-rail').replaceChildren();
  $('welcome').hidden = true; $('reader').hidden = false;
  if (!follow) $('messages').innerHTML = '<div class="transcript-empty">' + escapeHTML(t('web.transcript.loading')) + '</div>';
  renderHeader(); renderList();
  if (innerWidth <= 768 && !follow) toggleSidebar(true);
  localStorage.setItem('hub.selected', selectedKey);
  try {
    const data = await api(endpoint(session)); if (seq !== readSequence) return;
    messages = data.messages; loading = false; state.activeSession = {...data.session, messageCount: data.messageCount};
    renderApprovalBar();
    const container = $('messages'); container.replaceChildren();
    let group = null, speaker = '';  // the agent's name shows once per reply, however many tool runs split it
    for (let start = 0; start < messages.length; start += 80) {
      if (seq !== readSequence) return;
      const fragment = document.createDocumentFragment();
      for (let i = start; i < Math.min(start + 80, messages.length); i++) {
        const m = messages[i];
        if (m.role === 'thinking' && !m.text.trim()) {rendered.push(null); continue;}
        const el = renderEntry(m, i); rendered.push(el);
        if (el.tagName !== 'DETAILS') {
          if (m.role === 'assistant' && speaker === 'assistant') el.classList.add('continued');
          speaker = m.role; group = null; fragment.append(el); continue;
        }
        if (!group) {group = toolGroup(); fragment.append(group.el);}
        group.add(m, el);
      }
      container.append(fragment); if (messages.length > 80) await new Promise(resolve => setTimeout(resolve, 0));
    }
    if (!messages.length) container.innerHTML = '<div class="transcript-empty">' + escapeHTML(session.synthetic ? t('web.transcript.no_pick') : t('web.transcript.none')) + '</div>';
    else {const end = document.createElement('div'); end.className = 'history-end'; end.textContent = t('web.transcript.count', {n: messages.length}); container.append(end);}
    buildTurnRail();
    renderHeader(); $('transcript').scrollTop = $('transcript').scrollHeight; updateStickyAndRail(); state.lastError = null;  // the latest turn, as in the terminal
  } catch (error) {if (seq !== readSequence) return; loading = false; $('messages').textContent = t('web.read_failed', {error: error.message}); renderHeader(); throw error;}
}
function highlightElement(root, query) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = []; let node;
  while ((node = walker.nextNode())) {if (node.parentElement?.tagName !== 'MARK') nodes.push(node);}
  const needle = query.toLocaleLowerCase();
  for (const textNode of nodes) {
    const text = textNode.textContent, lower = text.toLocaleLowerCase(); let index = lower.indexOf(needle), cursor = 0;
    if (index < 0) continue;
    const fragment = document.createDocumentFragment();
    while (index >= 0) {fragment.append(document.createTextNode(text.slice(cursor, index))); const mark = document.createElement('mark'); mark.textContent = text.slice(index, index + query.length); fragment.append(mark); cursor = index + query.length; index = lower.indexOf(needle, cursor);}
    fragment.append(document.createTextNode(text.slice(cursor))); textNode.replaceWith(fragment);
  }
}
function searchTranscript() {
  for (const mark of $('messages').querySelectorAll('mark')) {const parent = mark.parentNode; mark.replaceWith(document.createTextNode(mark.textContent)); parent.normalize();}
  const query = $('transcript-query').value.trim(); matches = []; matchIndex = -1;
  if (query) {
    messages.forEach((message, index) => {if (message.text.toLocaleLowerCase().includes(query.toLocaleLowerCase())) matches.push(index);});
    for (const index of matches) {const el = rendered[index]; if (el) highlightElement(el, query);}
  }
  $('match-count').textContent = matches.length ? String(matches.length) : query ? '0' : '';
  if (matches.length) jumpMatch(1);
}
function jumpMatch(direction) {
  if (!matches.length) return;
  matchIndex = (matchIndex + direction + matches.length) % matches.length;
  const el = rendered[matches[matchIndex]]; if (!el) return;
  if (el.toolGroup) el.toolGroup.open = true;
  if (el.tagName === 'DETAILS') el.open = true;
  $('messages').querySelectorAll('mark.current').forEach(mark => mark.classList.remove('current'));
  el.querySelector('mark')?.classList.add('current'); el.scrollIntoView({block: 'center'}); $('match-count').textContent = (matchIndex + 1) + '/' + matches.length;
}
$('collapse').addEventListener('click', () => toggleSidebar());
$('mobile-back-btn').addEventListener('click', () => toggleSidebar(false));
$('filters').addEventListener('click', event => {const el = event.target.closest('[data-filter]'); if (el) setFilter(el.dataset.filter);});
$('more-filter-popover')?.addEventListener('click', event => {const el = event.target.closest('[data-filter]'); if (el) setFilter(el.dataset.filter);});
$('more-filter-btn')?.addEventListener('click', event => {
  event.stopPropagation();
  const pop = $('more-filter-popover');
  if (pop) pop.hidden = !pop.hidden;
});
$('global-search').addEventListener('input', event => {state.filter.query = event.target.value; limit = 60; renderList();});
$('show-temp').addEventListener('change', event => {state.filter.showTemp = event.target.checked; renderList();});
$('refresh').addEventListener('click', () => refresh().catch(failed));
$('jump-top').addEventListener('click', () => {$('transcript').scrollTop = 0;});
$('jump-bottom').addEventListener('click', () => {$('transcript').scrollTop = $('transcript').scrollHeight;});
$('prev-turn')?.addEventListener('click', () => jumpTurn(-1));
$('next-turn')?.addEventListener('click', () => jumpTurn(1));
$('sticky-prompt-bar')?.addEventListener('click', () => {if (activeTurnIdx >= 0) jumpToTurnIndex(activeTurnIdx);});
$('transcript').addEventListener('scroll', () => updateStickyAndRail(), {passive: true});
$('transcript-query').addEventListener('input', () => {clearTimeout(searchTimer); searchTimer = setTimeout(searchTranscript, 180);});
$('transcript-query').addEventListener('keydown', event => {if (event.key === 'Enter') {event.preventDefault(); jumpMatch(event.shiftKey ? -1 : 1);}});
$('prev-match').addEventListener('click', () => jumpMatch(-1)); $('next-match').addEventListener('click', () => jumpMatch(1));
$('mobile-approval-bar').addEventListener('click', async event => {
  const btn = event.target.closest('[data-approval-action]');
  const s = state.activeSession;
  if (!btn || !s || !s.isLive || !isSessionWaitApproval(s)) return;
  const actionName = btn.dataset.approvalAction;
  const keyToSend = btn.dataset.approvalKey;
  const confirmations = {
    allow_once: t('web.approval.sent_once'),
    allow_always: t('web.approval.sent_always'),
    skip_continue: t('web.approval.sent_skip'),
    stop: t('web.approval.sent_stop'),
  };
  if (!confirmations[actionName]) return;
  const previous = {agentState: s.agentState, pendingApproval: s.pendingApproval};
  const nextState = actionName === 'stop' ? 'done_idle' : 'working';
  try {
    const mode = await probeComposerDelivery(s, true);
    if (mode !== 'tmux_pane') throw new Error(t('web.approval.external'));
    const result = await action(s, 'send', {text: keyToSend, allow_headless_exec: false, dry_run: false});
    if (result.dry_run || result.delivery_mode !== 'tmux_pane') throw new Error(t('web.approval.not_received'));
    s.agentState = nextState;
    s.pendingApproval = false;
    const inList = state.sessions.find(item => key(item) === key(s));
    if (inList) { inList.agentState = nextState; inList.pendingApproval = false; }
    markSessionRead(s);
    renderHeader();
    renderList();
    notify(confirmations[actionName]);
  } catch (error) {
    for (const item of [s, state.activeSession, state.sessions.find(item => key(item) === key(s))]) {
      if (item && key(item) === key(s)) Object.assign(item, previous);
    }
    renderHeader();
    renderList();
    failed(error);
  }
});
$('composer-send-btn')?.addEventListener('click', async () => {
  const inp = $('composer-input');
  const s = state.activeSession;
  if (!s || isSessionWaitApproval(s)) return;
  const text = (inp?.value || '').trim();
  if (!s.isLive) {
    try {
      if (text) await copy(text);
      await action(s, 'resume');
      notify(text ? t('web.resumed_draft') : t('web.resumed'));
      setTimeout(() => refresh().catch(failed), 2000);
    } catch (e) {failed(e);}
    return;
  }
  if ($('composer-send-btn')?.dataset.composerMode === 'external_tab' && !text) {
    try {
      await action(s, 'focus');
      notify(t('web.focused'));
    } catch (e) {failed(e);}
    return;
  }
  if (!text) return;
  try {
    const mode = await probeComposerDelivery(s, true);
    if (mode !== 'tmux_pane') {
      syncComposer();
      throw new Error(t('web.send.elsewhere'));
    }
    const res = await action(s, 'send', {text, allow_headless_exec: false, dry_run: false});
    if (res.dry_run || res.delivery_mode !== 'tmux_pane') {
      throw new Error(t('web.send.not_delivered'));
    }
    inp.value = '';
    if (state.activeSession && key(state.activeSession) === key(s)) {
      state.activeSession.agentState = 'working';
      state.activeSession.pendingApproval = false;
      const inList = state.sessions.find(item => key(item) === key(s));
      if (inList) { inList.agentState = 'working'; inList.pendingApproval = false; }
      markSessionRead(state.activeSession);
      renderHeader();
      renderList();
    }
    notify(t('web.sent', {text: text.slice(0, 36)}));
  } catch (e) {failed(e);}
});
$('composer-input')?.addEventListener('keydown', ev => {
  if (ev.isComposing || ev.keyCode === 229) return;
  if (ev.key === 'Enter') {
    ev.preventDefault();
    const s = state.activeSession;
    if (!s || !s.isLive || $('composer-send-btn')?.dataset.composerMode !== 'tmux_pane') return;
    $('composer-send-btn')?.click();
  }
});
async function loadNotifyChannels() {
  const listEl = $('notify-channel-list');
  if (!listEl) return;
  try {
    const data = await api('/api/channels/status');
    const channels = data.channels || {};
    // The generic webhook only gets a card once it is set up; Telegram and Slack always show.
    const defs = [['telegram', 'Telegram'], ['slack', 'Slack']];
    if (channels.webhook?.configured) defs.push(['webhook', 'Webhook']);
    const cards = defs.map(([id, name]) => {
      const info = channels[id] || {};
      const cfgPath = info.config_path || '~/.config/mixhub/channels.json';
      const configured = Boolean(info.configured);
      const card = document.createElement('div');
      card.className = 'notify-channel-card';
      card.dataset.channel = id;
      const top = document.createElement('div');
      top.className = 'notify-channel-top';
      const title = document.createElement('span');
      title.className = 'notify-channel-name';
      title.textContent = name;
      const pill = document.createElement('span');
      pill.className = 'notify-status-pill' + (configured ? ' ok' : '');
      pill.textContent = configured ? t('web.channel.configured') : t('web.channel.unconfigured');
      top.append(title, pill);
      const meta = document.createElement('div');
      meta.className = 'notify-channel-meta';
      meta.textContent = t('web.channel.config', {path: cfgPath});
      const actions = document.createElement('div');
      actions.className = 'notify-channel-actions';
      const testBtn = button(t('web.channel.test'), async () => {
        await api('/api/channels/notify', {channel: id, message: t('web.channel.test_message'), dry_run: false});
        notify(t('web.channel.test_sent', {name}));
      }, configured ? 'primary' : '', t('web.channel.test_title', {name}));
      testBtn.disabled = !configured;
      actions.append(testBtn);
      card.append(top, meta, actions);
      return card;
    });
    listEl.replaceChildren(...cards);
  } catch (e) {
    listEl.textContent = t('web.channel.failed', {error: e.message});
  }
}
function toggleNotifyDrawer(force) {
  const drawer = $('notify-drawer');
  const bell = $('notify-bell-btn');
  if (!drawer) return;
  const open = force === undefined ? drawer.hidden : Boolean(force);
  drawer.hidden = !open;
  if (bell) {
    bell.classList.toggle('active', open);
    bell.setAttribute('aria-expanded', String(open));
  }
  if (open) loadNotifyChannels().catch(failed);
}
$('notify-bell-btn')?.addEventListener('click', ev => {
  ev.stopPropagation();
  toggleNotifyDrawer();
});
$('notify-drawer-close')?.addEventListener('click', ev => {
  ev.stopPropagation();
  toggleNotifyDrawer(false);
});
$('notify-drawer')?.addEventListener('click', ev => ev.stopPropagation());
$('hibernate-idle').addEventListener('click', async () => {
  try {
    const idle = state.sessions.flatMap(s => s.processes.filter(p => p.status === 'idle'));
    if (!idle.length || !await confirmSleep(t('web.sleep_idle.title'), t('web.sleep_idle.text', {n: idle.length, mb: Math.round(idle.reduce((n, p) => n + p.rss_mb, 0))}))) return;
    $('hibernate-idle').disabled = true; const result = await api('/api/hibernate-idle', {}); notify(t('web.sleep_idle.done', {n: result.results.length})); await refresh();
  } catch (error) {failed(error); $('hibernate-idle').disabled = false;}
});
document.addEventListener('keydown', event => {if (event.metaKey || event.ctrlKey) {if (event.key.toLowerCase() === 'b') {event.preventDefault(); toggleSidebar();} if (event.key.toLowerCase() === 'k') {event.preventDefault(); toggleSidebar(false); $('global-search').focus();}}});
window.__app = Object.freeze({state: () => structuredClone({counts: state.counts, activeSession: state.activeSession ? {id: state.activeSession.id, tool: state.activeSession.tool, title: state.activeSession.title, isLive: state.activeSession.isLive, messageCount: state.activeSession.messageCount, handoffChain: state.activeSession.handoffChain || []} : null, sidebarCollapsed: state.sidebarCollapsed, filter: state.filter, lastError: state.lastError}), projectLabel, detectHandoffChain});
window.addEventListener('error', event => {state.lastError = event.message;});
window.addEventListener('unhandledrejection', event => {state.lastError = String(event.reason);});
toggleSidebar(innerWidth <= 768 ? false : localStorage.getItem('hub.sidebarCollapsed') === 'true');
// `#session=<id>` deep link (TUI sidebar `↗ web`) wins over the last-selected session
function sessionFromHash() {
  const m = /(?:^#|&)session=([^&]+)/.exec((window.location && window.location.hash) || '');
  if (!m) return null;
  let id = m[1]; try {id = decodeURIComponent(id);} catch {}
  return state.sessions.find(s => s.id === id || key(s) === id) || null;
}
// Footer quota: the same per-agent 5h / weekly numbers as the terminal sidebar; a dash where there is no data.
const QUOTA_IDS = {cc: 'claude', cx: 'codex', agy: 'agy'};
async function refreshQuota() {
  let items = [];
  try {items = (await api('/api/quota')).items || [];} catch (_) {}
  const byKey = Object.fromEntries(items.map(it => [it.key, it]));
  const pct = rem => (typeof rem === 'number' ? Math.max(0, Math.min(100, 100 - rem)) + '%' : '–');  // used, as in the sidebar
  for (const [k, id] of Object.entries(QUOTA_IDS)) {
    const it = byKey[k] || {};
    $('q-' + id).textContent = pct(it.rem_5h); $('q-' + id + '-t').textContent = it.reset_5h || '';
    $('q-' + id + '-wk').textContent = pct(it.rem_wk); $('q-' + id + '-d').textContent = it.reset_wk || '';
  }
}
refreshQuota();
setInterval(() => {if (!document.hidden) refreshQuota();}, 60000);
refresh().then(async () => {const target = sessionFromHash() || state.sessions.find(s => key(s) === localStorage.getItem('hub.selected')); if (target) await select(target);}).catch(failed);
window.addEventListener('hashchange', () => {const target = sessionFromHash(); if (target && key(target) !== selectedKey) select(target).catch(failed);});
// The list (and the open transcript) poll every 5 s while the tab is visible, and once more when it comes back
const listIdle = () => !document.hidden && !$('confirm-dialog').open && !$('session-list').querySelector('.card-title-input');
setInterval(() => {if (listIdle()) refresh().catch(failed);}, 5000);
document.addEventListener('visibilitychange', () => {if (listIdle()) refresh().catch(failed);});
// === MIXHUB_OBSIDIAN_JS_START ===
(() => {
  'use strict';
  const LS_THEME_MODE = 'mixhub_theme_mode'; // 'light' | 'dark' | 'system'
  const LS_SURFACE_MODE = 'mixhub_surface_mode';
  const LS_QUOTA_COLLAPSED = 'mixhub_quota_collapsed';
  const LS_HOST = 'mixhub_host_label';
  const root = document.documentElement;
  const $ = s => document.querySelector(s);
  const mqDark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : {matches: true};

  function cleanHostName(raw) {
    return String(raw || 'Local')
      .trim()
      .replace(/^MacBook-Pro\s*[·•-]\s*/i, '')
      .replace(/-\d+(\.local)?$/i, '')
      .replace(/\.local$/i, '') || 'Local';
  }

  function getThemeMode() {
    const saved = localStorage.getItem(LS_THEME_MODE);
    return (saved === 'light' || saved === 'dark' || saved === 'system') ? saved : 'system';
  }

  function resolveEffectiveTheme(mode) {
    if (mode === 'light' || mode === 'dark') return mode;
    return mqDark.matches ? 'dark' : 'light';
  }

  function applyThemeMode(mode) {
    const m = (mode === 'light' || mode === 'dark' || mode === 'system') ? mode : 'system';
    localStorage.setItem(LS_THEME_MODE, m);
    root.dataset.theme = resolveEffectiveTheme(m);
    root.dataset.themeMode = m;
    document.querySelectorAll('[data-theme-choice]').forEach(btn => {
      const act = btn.dataset.themeChoice === m;
      btn.classList.toggle('active', act);
      btn.setAttribute('aria-pressed', String(act));
    });
    syncNodeBar();
  }

  function getSurfaceMode() {
    const saved = localStorage.getItem(LS_SURFACE_MODE);
    return (saved === 'solid' || saved === 'subtle' || saved === 'deep') ? saved : 'subtle';
  }

  function applySurfaceMode(mode) {
    const selected = (mode === 'solid' || mode === 'subtle' || mode === 'deep') ? mode : 'subtle';
    localStorage.setItem(LS_SURFACE_MODE, selected);
    root.dataset.surface = selected;
    document.querySelectorAll('[data-surface-choice]').forEach(btn => {
      const active = btn.dataset.surfaceChoice === selected;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', String(active));
    });
    syncNodeBar();
  }

  function applyQuotaCollapsed(collapsed) {
    const isCollapsed = Boolean(collapsed);
    localStorage.setItem(LS_QUOTA_COLLAPSED, isCollapsed ? '1' : '0');
    const drawer = $('#quota-drawer');
    const expandBtn = $('#quota-expand-btn');
    if (drawer) drawer.hidden = isCollapsed;
    if (expandBtn) {
      expandBtn.hidden = false;
      expandBtn.textContent = t(isCollapsed ? 'web.quota.show' : 'web.quota.hide');
      expandBtn.setAttribute('aria-expanded', String(!isCollapsed));
      expandBtn.classList.toggle('active', !isCollapsed);
    }
  }

  function getHostLabel() {
    const saved = localStorage.getItem(LS_HOST);
    return cleanHostName(saved || serverHostLabel || 'Local');
  }

  function syncNodeBar() {
    const host = getHostLabel();
    if (localStorage.getItem(LS_HOST) && localStorage.getItem(LS_HOST) !== host) {
      localStorage.setItem(LS_HOST, host);
    }
    const labelEl = $('#user-name-label');
    if (labelEl) labelEl.textContent = host;
    const inp = $('#nsp-host-input');
    if (inp && document.activeElement !== inp) inp.value = host;
    const theme = t(root.dataset.theme === 'dark' ? 'web.theme.dark' : 'web.theme.light');
    const surface = getSurfaceMode();
    const surfaceLabel = t(surface === 'solid' ? 'web.surface.solid' : surface === 'deep' ? 'web.surface.deep' : 'web.surface.glass');
    const sub = $('#node-sub-status');
    if (sub) sub.textContent = `${theme} / ${surfaceLabel}`;
  }

  function toggleSettingsPopover(ev) {
    ev?.stopPropagation();
    const pop = $('#node-settings-popover');
    if (!pop) return;
    pop.hidden = !pop.hidden;
  }

  function init() {
    if (localStorage.getItem(LS_HOST)) {
      localStorage.setItem(LS_HOST, cleanHostName(localStorage.getItem(LS_HOST)));
    }
    applyThemeMode(getThemeMode());
    applySurfaceMode(getSurfaceMode());
    if (mqDark.addEventListener) {
      mqDark.addEventListener('change', () => {
        if (getThemeMode() === 'system') applyThemeMode('system');
      });
    }
    document.querySelectorAll('[data-theme-choice]').forEach(btn => {
      btn.addEventListener('click', ev => {
        ev.stopPropagation();
        applyThemeMode(btn.dataset.themeChoice);
      });
    });
    document.querySelectorAll('[data-surface-choice]').forEach(btn => {
      btn.addEventListener('click', ev => {
        ev.stopPropagation();
        applySurfaceMode(btn.dataset.surfaceChoice);
      });
    });
    $('#node-settings-btn')?.addEventListener('click', toggleSettingsPopover);
    $('#user-profile-bar')?.addEventListener('click', toggleSettingsPopover);
    $('#user-profile-bar')?.addEventListener('keydown', ev => {
      if (ev.target !== ev.currentTarget || (ev.key !== 'Enter' && ev.key !== ' ')) return;
      ev.preventDefault();
      toggleSettingsPopover(ev);
    });
    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape') {
        const pop = $('#node-settings-popover');
        if (pop && !pop.hidden) pop.hidden = true;
        const nd = $('#notify-drawer');
        if (nd && !nd.hidden) {
          nd.hidden = true;
          const bell = $('#notify-bell-btn');
          if (bell) { bell.classList.remove('active'); bell.setAttribute('aria-expanded', 'false'); }
        }
        document.querySelectorAll('.relay-menu-popover').forEach(p => { p.hidden = true; });
        return;
      }
      if (ev.defaultPrevented || ev.isComposing || (ev.key !== ',' && ev.code !== 'Comma') || ev.altKey || ev.ctrlKey || ev.shiftKey) return;
      const focused = document.activeElement;
      if (focused?.matches('input, textarea, select, [contenteditable], [contenteditable="true"]') || focused?.isContentEditable) return;
      ev.preventDefault();
      toggleSettingsPopover(ev);
    });
    applyQuotaCollapsed(localStorage.getItem(LS_QUOTA_COLLAPSED) === '1');
    $('#quota-collapse-btn')?.addEventListener('click', ev => {
      ev.stopPropagation();
      applyQuotaCollapsed(true);
    });
    $('#quota-expand-btn')?.addEventListener('click', ev => {
      ev.stopPropagation();
      const drawer = $('#quota-drawer');
      applyQuotaCollapsed(drawer ? !drawer.hidden : false);
    });

    $('#nsp-host-save')?.addEventListener('click', ev => {
      ev.stopPropagation();
      const val = cleanHostName($('#nsp-host-input')?.value || 'Local');
      localStorage.setItem(LS_HOST, val.slice(0, 28));
      syncNodeBar();
      const pop = $('#node-settings-popover');
      if (pop) pop.hidden = true;
    });
    // Language: stored per browser; "Auto" goes back to the preference / browser language. The page reloads to apply.
    let langChoice = 'auto';
    try {langChoice = localStorage.getItem(LS_LANGUAGE) || 'auto';} catch {}
    document.querySelectorAll('[data-language-choice]').forEach(btn => {
      const on = btn.dataset.languageChoice === langChoice;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-pressed', String(on));
      btn.addEventListener('click', ev => {
        ev.stopPropagation();
        const code = btn.dataset.languageChoice;
        try {
          if (code === 'auto') localStorage.removeItem(LS_LANGUAGE);
          else localStorage.setItem(LS_LANGUAGE, code);
        } catch {}
        location.reload();
      });
    });
    $('#node-settings-popover')?.addEventListener('click', ev => ev.stopPropagation());
    document.addEventListener('click', () => {
      const pop = $('#node-settings-popover');
      if (pop && !pop.hidden) pop.hidden = true;
      const morePop = $('#more-filter-popover');
      if (morePop && !morePop.hidden) morePop.hidden = true;
      const nd = $('#notify-drawer');
      if (nd && !nd.hidden) {
        nd.hidden = true;
        const bell = $('#notify-bell-btn');
        if (bell) { bell.classList.remove('active'); bell.setAttribute('aria-expanded', 'false'); }
      }
      document.querySelectorAll('.relay-menu-popover').forEach(p => { p.hidden = true; });
    });
    setInterval(syncNodeBar, 1500);
    setTimeout(syncNodeBar, 200);
  }

  window.__mixhubCleanHostName = cleanHostName;
  window.__mixhubSyncNodeBar = syncNodeBar;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
