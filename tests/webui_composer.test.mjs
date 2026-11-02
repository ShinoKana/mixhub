import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..');
// The composers and the approval bar type into a session through POST /api/sessions/<tool>/<id>/send
const isSend = url => /^\/api\/sessions\/[^/]+\/[^/]+\/send$/.test(url);

function createHarness(fetchImpl, opts = {}) {
  const elements = new Map();
  const storage = new Map();
  const docListeners = new Map();
  const winListeners = new Map();
  const clipboardWrites = [];
  const intervals = [];

  function makeClassList() {
    const set = new Set();
    return {
      add: (...cls) => cls.forEach(c => set.add(c)),
      remove: (...cls) => cls.forEach(c => set.delete(c)),
      toggle: (c, force) => {
        const next = force === undefined ? !set.has(c) : Boolean(force);
        if (next) set.add(c);
        else set.delete(c);
        return next;
      },
      contains: c => set.has(c),
    };
  }

  function makeElement(tag = 'div', id = '') {
    const listeners = new Map();
    const attrs = new Map();
    const el = {
      tagName: String(tag).toUpperCase(),
      id,
      className: '',
      textContent: '',
      innerHTML: '',
      value: '',
      title: '',
      placeholder: '',
      disabled: false,
      hidden: false,
      checked: false,
      open: false,
      tabIndex: 0,
      scrollTop: 0,
      scrollHeight: 0,
      clientHeight: 0,
      offsetTop: 0,
      dataset: {},
      style: {},
      children: [],
      classList: makeClassList(),
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(fn);
      },
      async trigger(type, eventInit = {}) {
        const list = listeners.get(type) || [];
        let prevented = false;
        const ev = {
          type,
          target: el,
          currentTarget: el,
          preventDefault() { prevented = true; this.defaultPrevented = true; },
          stopPropagation() {},
          closest: () => null,
          ...eventInit,
        };
        for (const fn of list) {
          await fn(ev);
        }
        return {prevented, ev};
      },
      click() {
        return el.trigger('click');
      },
      focus() {},
      select() {},
      remove() {},
       scrollIntoView() {},
      showModal() { el.open = true; },
      setAttribute(k, v) { attrs.set(k, String(v)); },
      getAttribute(k) { return attrs.has(k) ? attrs.get(k) : null; },
      append(...nodes) {
        for (const n of nodes) {
          if (n && n.tagName === 'FRAGMENT') el.children.push(...n.children);
          else el.children.push(n);
        }
      },
      replaceChildren(...nodes) {
        el.children = [];
        el.append(...nodes);
      },
      replaceWith() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      matches() { return false; },
    };
    return el;
  }

  function getEl(id) {
    if (!elements.has(id)) elements.set(id, makeElement('div', id));
    return elements.get(id);
  }

  const document = {
    readyState: 'complete',
    hidden: false,
    activeElement: null,
    documentElement: {dataset: {theme: 'dark', surface: 'subtle'}},
    body: makeElement('body'),
    getElementById: id => getEl(id),
    querySelector: sel => {
      if (sel.startsWith('#')) return getEl(sel.slice(1));
      return null;
    },
    querySelectorAll: () => [],
    createElement: tag => makeElement(tag),
    createDocumentFragment: () => makeElement('fragment'),
    createTextNode: text => ({textContent: String(text)}),
    createTreeWalker: () => ({nextNode: () => null}),
    execCommand: () => true,
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
  };

  for (const [k, v] of Object.entries(opts.storage || {})) storage.set(k, v);
  const localStorage = {
    getItem: k => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: k => storage.delete(k),
  };

  const strings = Object.fromEntries(['en', 'zh', 'ja'].map(lang =>
    [lang, JSON.parse(readFileSync(resolve(repoRoot, `mixhub/i18n/${lang}.json`), 'utf8'))]));
  const windowObj = {
    // Chinese UI, so the assertions below on Chinese toasts keep their meaning
    MIXHUB_I18N: {preference: opts.language || 'zh', default: 'en', strings},
    innerWidth: 1280,
    document,
    localStorage,
    navigator: {
      ...(opts.navLanguages ? {languages: opts.navLanguages} : {}),
      clipboard: {
        writeText: async text => { clipboardWrites.push(String(text)); },
      },
    },
    matchMedia: () => ({matches: true, addEventListener() {}}),
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, []);
      winListeners.get(type).push(fn);
    },
    fetch: (...args) => fetchImpl(...args),
    setTimeout: (fn, ms) => {
      if (ms === 0) fn();
      return 1;
    },
    clearTimeout: () => {},
    setInterval: (fn, ms) => { intervals.push({fn, ms}); return intervals.length; },
    clearInterval: () => {},
    structuredClone: val => JSON.parse(JSON.stringify(val)),
    NodeFilter: {SHOW_TEXT: 4},
    URL,
  };
  windowObj.window = windowObj;

  const code = readFileSync(resolve(repoRoot, 'mixhub/web/static/app.js'), 'utf8');
  vm.runInNewContext(code, windowObj, {filename: 'mixhub/web/static/app.js'});

  return {window: windowObj, document, getEl, clipboardWrites, intervals, docListeners};
}

test('1. Dormant session click never sends the session send route dry_run or fake Sent toast; calls /resume and keeps draft', async () => {
  const calls = [];
  const dormantSession = {
    id: '11111111-2222-3333-4444-555555555555',
    tool: 'claude',
    title: 'Dormant Claude Session',
    cwd: '/tmp/work',
    isLive: false,
    pinned: false,
    processes: [],
    updatedAt: 1700000000,
    messageCount: 2,
  };

  const harness = createHarness(async (url, opts) => {
    const body = opts?.body ? JSON.parse(opts.body) : undefined;
    calls.push({url, body});
    if (url.startsWith('/api/sessions?')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          sessions: [dormantSession],
          counts: {total: 1, claude: 1, agy: 0, codex: 0, live: 0, pinned: 0, liveProcesses: 0, liveMemoryMB: 0},
          warnings: [],
          host: 'Local',
        }),
      };
    }
    if (url === `/api/sessions/claude/${dormantSession.id}`) {
      return {
        ok: true,
        status: 200,
        json: async () => ({session: dormantSession, messages: [], messageCount: 0}),
      };
    }
    if (url === `/api/sessions/claude/${dormantSession.id}/resume`) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ok: true, dry_run: Boolean(body?.dry_run), command: 'claude --resume 11111111'}),
      };
    }
    if (isSend(url)) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ok: true, dry_run: true, delivery_mode: 'headless_resume'}),
      };
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}})};
  });

  await new Promise(r => setImmediate(r));
  // Select the dormant session
  const sessionList = harness.getEl('session-list');
  const cardEl = sessionList.children.find(c => c.dataset?.key === `claude:${dormantSession.id}`);
  assert.ok(cardEl, 'Dormant session card rendered');
  await cardEl.click();
  await new Promise(r => setImmediate(r));

  calls.length = 0;
  const inp = harness.getEl('composer-input');
  const sendBtn = harness.getEl('composer-send-btn');
  inp.value = '帮我检查这段休眠草稿';
  await sendBtn.click();

  const inboundCalls = calls.filter(c => isSend(c.url));
  const resumeCalls = calls.filter(c => c.url === `/api/sessions/claude/${dormantSession.id}/resume`);
  assert.equal(inboundCalls.length, 0, 'Must NOT call the session send route for dormant session');
  assert.equal(resumeCalls.length, 1, 'Must call /resume to wake dormant session in Terminal');
  assert.equal(inp.value, '帮我检查这段休眠草稿', 'Draft text in #composer-input must NOT be cleared');
  assert.ok(harness.clipboardWrites.includes('帮我检查这段休眠草稿'), 'Draft text must be copied to clipboard');
  const toastText = harness.getEl('toast').textContent;
  assert.ok(!toastText.includes('已发送'), `Toast must NEVER falsely claim 已发送, got: ${toastText}`);
});

test('2. Live Codex session in external tab (preflight returns headless_resume) never fires dry_run:false', async () => {
  const calls = [];
  const externalCodex = {
    id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    tool: 'codex',
    title: 'External Tab Codex',
    cwd: '/tmp/work',
    isLive: true,
    pinned: false,
    processes: [{pid: 999, status: 'idle', rss_mb: 50}],
    updatedAt: 1700000100,
    messageCount: 4,
  };

  const harness = createHarness(async (url, opts) => {
    const body = opts?.body ? JSON.parse(opts.body) : undefined;
    calls.push({url, body});
    if (url.startsWith('/api/sessions?')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          sessions: [externalCodex],
          counts: {total: 1, claude: 0, agy: 0, codex: 1, live: 1, pinned: 0, liveProcesses: 1, liveMemoryMB: 50},
          warnings: [],
          host: 'Local',
        }),
      };
    }
    if (url === `/api/sessions/codex/${externalCodex.id}`) {
      return {
        ok: true,
        status: 200,
        json: async () => ({session: externalCodex, messages: [], messageCount: 0}),
      };
    }
    if (isSend(url)) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ok: true, delivery_mode: 'headless_resume', command: ['codex', 'exec', 'resume']}),
      };
    }
    if (url === `/api/sessions/codex/${externalCodex.id}/focus`) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ok: true}),
      };
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}})};
  });

  await new Promise(r => setImmediate(r));
  const sessionList = harness.getEl('session-list');
  // Verify the live group title is the localized 'Active' label (test harness defaults to zh)
  const groupTitles = sessionList.children.filter(c => c.className === 'group-title').map(c => c.innerHTML);
  assert.ok(groupTitles.some(h => h.includes('活跃')), 'Live group header should be the localized Active label');

  const cardEl = sessionList.children.find(c => c.dataset?.key === `codex:${externalCodex.id}`);
  await cardEl.click();
  await new Promise(r => setImmediate(r));

  const inp = harness.getEl('composer-input');
  const sendBtn = harness.getEl('composer-send-btn');
  inp.value = '不应后台跑 headless codex exec';
  await sendBtn.click();

  const nonDryInbound = calls.filter(c => isSend(c.url) && c.body?.dry_run === false);
  assert.equal(nonDryInbound.length, 0, 'Must NEVER send dry_run:false when preflight is headless_resume');
  assert.equal(inp.value, '不应后台跑 headless codex exec', 'Input value must stay intact');
  assert.ok(!harness.getEl('toast').textContent.includes('已发送'), 'Toast must not claim 已发送');
});

test('3. HTTP 200 with {ok: false} in api() throws and routes to failed() error toast', async () => {
  const liveClaude = {
    id: '22222222-3333-4444-5555-666666666666',
    tool: 'claude',
    title: 'Live Claude Session',
    cwd: '/tmp/work',
    isLive: true,
    pinned: false,
    processes: [{pid: 1001, status: 'idle', rss_mb: 80}],
    updatedAt: 1700000200,
    messageCount: 1,
  };
  let inboundCallCount = 0;

  const harness = createHarness(async (url, opts) => {
    const body = opts?.body ? JSON.parse(opts.body) : undefined;
    if (url.startsWith('/api/sessions?')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          sessions: [liveClaude],
          counts: {total: 1, claude: 1, agy: 0, codex: 0, live: 1, pinned: 0, liveProcesses: 1, liveMemoryMB: 80},
          warnings: [],
          host: 'Local',
        }),
      };
    }
    if (url === `/api/sessions/claude/${liveClaude.id}`) {
      return {
        ok: true,
        status: 200,
        json: async () => ({session: liveClaude, messages: [], messageCount: 0}),
      };
    }
    if (isSend(url)) {
      inboundCallCount++;
      // Preflight passes as tmux_pane, but actual send returns 200 with {ok: false}
      if (body?.dry_run === true) {
        return {ok: true, status: 200, json: async () => ({ok: true, delivery_mode: 'tmux_pane'})};
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ok: false, tos_guard_blocked: true, message: 'Session is dormant (not in a live tmux pane).'}),
      };
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}})};
  });

  await new Promise(r => setImmediate(r));
  const cardEl = harness.getEl('session-list').children.find(c => c.dataset?.key === `claude:${liveClaude.id}`);
  await cardEl.click();
  await new Promise(r => setImmediate(r));

  const inp = harness.getEl('composer-input');
  const sendBtn = harness.getEl('composer-send-btn');
  inp.value = '测试 200 ok:false 拦截';
  await sendBtn.click();

  assert.ok(inboundCallCount >= 2, 'Preflight + actual send called');
  assert.equal(inp.value, '测试 200 ok:false 拦截', 'Input must not be cleared on {ok:false}');
  assert.ok(harness.getEl('toast').classList.contains('error'), 'Toast must have error class');
  assert.ok(harness.window.__app.state().lastError.includes('not in a live tmux pane'), 'lastError must record server error message');
});

test('4. IME composition (isComposing / keyCode 229) on Enter keydown never triggers send', async () => {
  const calls = [];
  const liveClaude = {
    id: '33333333-4444-5555-6666-777777777777',
    tool: 'claude',
    title: 'IME Test Session',
    cwd: '/tmp/work',
    isLive: true,
    pinned: false,
    processes: [{pid: 1002, status: 'idle', rss_mb: 80}],
    updatedAt: 1700000300,
    messageCount: 1,
  };

  const harness = createHarness(async (url, opts) => {
    const body = opts?.body ? JSON.parse(opts.body) : undefined;
    calls.push({url, body});
    if (url.startsWith('/api/sessions?')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          sessions: [liveClaude],
          counts: {total: 1, claude: 1, agy: 0, codex: 0, live: 1, pinned: 0, liveProcesses: 1, liveMemoryMB: 80},
          warnings: [],
          host: 'Local',
        }),
      };
    }
    if (url === `/api/sessions/claude/${liveClaude.id}`) {
      return {
        ok: true,
        status: 200,
        json: async () => ({session: liveClaude, messages: [], messageCount: 0}),
      };
    }
    if (isSend(url)) {
      return {ok: true, status: 200, json: async () => ({ok: true, delivery_mode: 'tmux_pane'})};
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}})};
  });

  await new Promise(r => setImmediate(r));
  const cardEl = harness.getEl('session-list').children.find(c => c.dataset?.key === `claude:${liveClaude.id}`);
  await cardEl.click();
  await new Promise(r => setImmediate(r));

  calls.length = 0;
  const inp = harness.getEl('composer-input');
  inp.value = '正在输入中文候选词';
  await inp.trigger('keydown', {key: 'Enter', isComposing: true});
  await inp.trigger('keydown', {key: 'Enter', isComposing: false, keyCode: 229});

  assert.equal(calls.length, 0, 'IME composition Enter must not trigger any network request');
  assert.equal(inp.value, '正在输入中文候选词', 'Input text preserved during IME composition');
});

test('5. Notification settings drawer (#notify-bell-btn) renders 4 channels and sends test notification', async () => {
  const calls = [];
  const harness = createHarness(async (url, opts) => {
    const body = opts?.body ? JSON.parse(opts.body) : undefined;
    calls.push({url, body});
    if (url.startsWith('/api/sessions?')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          sessions: [],
          counts: {total: 0, claude: 0, agy: 0, codex: 0, live: 0, pinned: 0, liveProcesses: 0, liveMemoryMB: 0},
          warnings: [],
          host: 'Local',
        }),
      };
    }
    if (url === '/api/channels/status') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ok: true,
          channels: {
            telegram: {configured: true, config_path: '~/.config/tg-notify/config.json'},
            slack: {configured: false},
            webhook: {configured: false},
          },
        }),
      };
    }
    if (url === '/api/channels/notify') {
      return {ok: true, status: 200, json: async () => ({ok: true})};
    }
    return {ok: true, status: 200, json: async () => ({ok: true})};
  });

  await new Promise(r => setImmediate(r));
  const bell = harness.getEl('notify-bell-btn');
  const drawer = harness.getEl('notify-drawer');
  drawer.hidden = true;
  await bell.click();
  await new Promise(r => setImmediate(r));

  assert.equal(drawer.hidden, false, 'Clicking bell opens notify-drawer');
  const cards = harness.getEl('notify-channel-list').children;
  assert.equal(cards.length, 2, 'Renders 2 channel cards (telegram, slack); an unconfigured webhook gets none');
  const tgCard = cards.find(c => c.dataset?.channel === 'telegram');
  const slCard = cards.find(c => c.dataset?.channel === 'slack');
  assert.ok(tgCard.children[1].textContent.includes('~/.config/tg-notify/config.json'), 'Shows the file the channel is read from');
  assert.ok(slCard.children[1].textContent.includes('~/.config/mixhub/channels.json'), 'Defaults to channels.json');
  assert.equal(tgCard.children[2].children[0].disabled, false, 'Configured telegram test button enabled');
  assert.equal(slCard.children[2].children[0].disabled, true, 'Unconfigured slack test button disabled');

  await tgCard.children[2].children[0].click();
  assert.ok(calls.some(c => c.url === '/api/channels/notify' && c.body?.channel === 'telegram' && c.body?.dry_run === false), 'Test button calls /api/channels/notify');
});

test('6. No dictation button (system dictation covers it); OpenCode has a filter between Codex and Gemini', async () => {
  const indexHtml = readFileSync(resolve(repoRoot, 'mixhub/web/static/index.html'), 'utf8');
  assert.ok(!/dictate|voice-[a-z]+-btn/i.test(indexHtml), 'no microphone button in the composer');
  assert.ok(!indexHtml.includes('data-quota-choice'), 'Settings popover must not duplicate Quota Expanded/Collapsed toggle');
  assert.ok(indexHtml.includes('id="quota-expand-btn"'), 'Sidebar bottom #quota-expand-btn remains intact');
  const filters = [...indexHtml.matchAll(/<button data-filter="([a-z]+)"/g)].map(m => m[1]);
  assert.deepEqual(filters, ['all', 'claude', 'codex', 'opencode', 'agy']);
  assert.ok(/data-filter="opencode"[^>]*><span class="mini o"><svg class="brand-svg opencode-svg"/.test(indexHtml), 'OpenCode chip uses its mark');
  for (const name of ['Claude', 'Codex', 'OpenCode', 'Gemini']) assert.ok(indexHtml.includes('aria-label="' + name + '"'), name + ' chip keeps an accessible name');
});

test('7. Transcript folds runs of tool calls into one collapsed group and drops empty thinking', async () => {
  const s = {
    id: '99999999-2222-3333-4444-555555555555', tool: 'claude', title: 'Tool heavy', cwd: '/tmp/work',
    isLive: false, pinned: false, processes: [], updatedAt: 1700000000, messageCount: 9,
  };
  const msgs = [
    {role: 'user', kind: 'message', text: 'check the repo'},
    {role: 'thinking', kind: 'thinking', text: ''},
    {role: 'tool', kind: 'tool_call', name: 'Bash', text: '{"command": "ls"}'},
    {role: 'tool', kind: 'tool_result', name: 'Bash', text: 'a.txt'},
    {role: 'thinking', kind: 'thinking', text: '   '},
    {role: 'tool', kind: 'tool_call', name: 'Bash', text: '{"command": "wc -l a.txt"}'},
    {role: 'tool', kind: 'tool_call', name: 'Read', text: '{"file_path": "a.txt"}'},
    {role: 'tool', kind: 'tool_result', name: 'Read', text: 'hello'},
    {role: 'assistant', kind: 'message', text: 'one file'},
  ];
  const harness = createHarness(async url => {
    if (url.startsWith('/api/sessions?')) {
      return {ok: true, status: 200, json: async () => ({
        sessions: [s], counts: {total: 1, claude: 1, agy: 0, codex: 0, live: 0, pinned: 0, liveProcesses: 0, liveMemoryMB: 0},
        warnings: [], host: 'Local',
      })};
    }
    if (url === `/api/sessions/claude/${s.id}`) {
      return {ok: true, status: 200, json: async () => ({session: s, messages: msgs, messageCount: msgs.length})};
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}})};
  });
  await new Promise(r => setImmediate(r));
  const card = harness.getEl('session-list').children.find(c => c.dataset?.key === `claude:${s.id}`);
  await card.click();
  await new Promise(r => setImmediate(r));

  const shown = harness.getEl('messages').children.filter(c => c.className !== 'history-end');
  assert.deepEqual(shown.map(c => c.tagName + '.' + c.className),
    ['ARTICLE.message user', 'DETAILS.tool-group', 'ARTICLE.message assistant']);
  const group = shown[1];
  assert.equal(group.open, false, 'tool group starts collapsed');
  const summary = group.children[0].innerHTML;
  assert.match(summary, /3 步/);
  assert.match(summary, /Bash ×2/);
  assert.match(summary, /Read/);
  assert.doesNotMatch(summary, /思考/, 'empty thinking is not counted');
  const inner = group.children.slice(1);
  assert.equal(inner.length, 5, 'the group holds the calls and results, not the empty thinking');
  assert.ok(inner.every(c => c.className === 'tool-details'));
});

test('7b. One reply split by tool calls shows the agent name once, until the user speaks again', async () => {
  const s = {
    id: '99999999-2222-3333-4444-666666666666', tool: 'claude', title: 'Long reply', cwd: '/tmp/work',
    isLive: false, pinned: false, processes: [], updatedAt: 1700000000, messageCount: 7,
  };
  const msgs = [
    {role: 'user', kind: 'message', text: 'why is it slow'},
    {role: 'assistant', kind: 'message', text: 'Reading the log.'},
    {role: 'tool', kind: 'tool_call', name: 'Bash', text: '{"command": "tail log"}'},
    {role: 'assistant', kind: 'message', text: 'Found it.'},
    {role: 'assistant', kind: 'message', text: 'Fixed.'},
    {role: 'user', kind: 'message', text: 'thanks'},
    {role: 'assistant', kind: 'message', text: 'Welcome.'},
  ];
  const harness = createHarness(async url => {
    if (url.startsWith('/api/sessions?')) {
      return {ok: true, status: 200, json: async () => ({
        sessions: [s], counts: {total: 1, claude: 1, agy: 0, codex: 0, live: 0, pinned: 0, liveProcesses: 0, liveMemoryMB: 0},
        warnings: [], host: 'Local',
      })};
    }
    if (url === `/api/sessions/claude/${s.id}`) {
      return {ok: true, status: 200, json: async () => ({session: s, messages: msgs, messageCount: msgs.length})};
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}})};
  });
  await new Promise(r => setImmediate(r));
  const card = harness.getEl('session-list').children.find(c => c.dataset?.key === `claude:${s.id}`);
  await card.click();
  await new Promise(r => setImmediate(r));

  const articles = harness.getEl('messages').children.filter(c => c.tagName === 'ARTICLE');
  assert.deepEqual(articles.map(a => a.classList.contains('continued')), [false, false, true, true, false, false]);
});

test('8. Footer quota shows used % from /api/quota, like the terminal sidebar, not placeholder numbers', async () => {
  const harness = createHarness(async url => {
    if (url.startsWith('/api/sessions?')) {
      return {ok: true, status: 200, json: async () => ({
        sessions: [], counts: {total: 0, claude: 0, agy: 0, codex: 0, live: 0, pinned: 0, liveProcesses: 0, liveMemoryMB: 0},
        warnings: [], host: 'Local',
      })};
    }
    if (url === '/api/quota') {
      return {ok: true, status: 200, json: async () => ({items: [
        {key: 'cc', rem_5h: 94, reset_5h: '19:10', rem_wk: 84, reset_wk: '11/04'},
        {key: 'agy', rem_5h: null, reset_5h: '', rem_wk: 50, reset_wk: '11/01'},
      ]})};
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}})};
  });
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));
  assert.equal(harness.getEl('q-claude').textContent, '6%', 'used, not remaining');
  assert.equal(harness.getEl('q-claude-t').textContent, '19:10');
  assert.equal(harness.getEl('q-claude-wk').textContent, '16%');
  assert.equal(harness.getEl('q-agy').textContent, '–');
  assert.equal(harness.getEl('q-agy-wk').textContent, '50%');
  assert.equal(harness.getEl('q-codex').textContent, '–', 'no data: a dash, never a made-up number');
});

test('8b. "N active" counts sessions, not processes (a Gemini session runs a filter process plus the agent)', async () => {
  const harness = createHarness(async url => {
    if (url.startsWith('/api/sessions?')) {
      return {ok: true, status: 200, json: async () => ({
        sessions: [], counts: {total: 5, claude: 2, agy: 1, codex: 1, live: 5, pinned: 0, liveProcesses: 6, liveMemoryMB: 0},
        warnings: [], host: 'Local',
      })};
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}, items: []})};
  });
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));
  assert.match(harness.getEl('live-stats').textContent, /^5\D/);
});

test('9. Interface language: settings choice, then preference, then browser language, then English', () => {
  const okFetch = async () => ({ok: true, status: 200, json: async () => ({sessions: [], items: []})});
  const lang = opts => {
    const h = createHarness(okFetch, opts);
    return [h.window.t('web.send'), h.window.document.documentElement.lang];
  };
  assert.deepEqual(lang({language: 'auto'}), ['Send', 'en'], 'no preference, no browser language: English');
  assert.deepEqual(lang({language: 'auto', navLanguages: ['fr-FR', 'ja-JP']}), ['送信', 'ja'], 'first supported browser language');
  assert.deepEqual(lang({language: 'zh', navLanguages: ['ja-JP']}), ['发送', 'zh'], 'the preference beats the browser');
  assert.deepEqual(lang({language: 'zh', storage: {mixhub_language: 'en'}}), ['Send', 'en'], 'the settings choice beats the preference');
  const h = createHarness(okFetch, {language: 'ja'});
  assert.equal(h.window.t('web.list.more', {n: 3}), '残り 3 件を表示', 'placeholders are filled');
  assert.equal(h.window.t('web.no_such_key'), 'web.no_such_key', 'unknown keys show the key');
});

function liveSessionHarness(s, msgsFor) {
  const reads = [];
  const harness = createHarness(async url => {
    if (url.startsWith('/api/sessions?')) {
      return {ok: true, status: 200, json: async () => ({
        sessions: [s()], counts: {total: 1, claude: 1, agy: 0, codex: 0, live: 1, pinned: 0, liveProcesses: 1, liveMemoryMB: 0},
        warnings: [], host: 'Local',
      })};
    }
    if (url.startsWith('/api/sessions/claude/') && !isSend(url)) {
      reads.push(url);
      const msgs = msgsFor(reads.length);
      return {ok: true, status: 200, json: async () => ({session: s(), messages: msgs, messageCount: msgs.length})};
    }
    return {ok: true, status: 200, json: async () => ({ok: true, channels: {}, items: []})};
  });
  return {harness, reads};
}
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(r => setImmediate(r)); };

test('10. Opening a session lands at the end of the transcript, like the terminal', async () => {
  const s = {id: 'aaaaaaaa-1111-2222-3333-444444444444', tool: 'claude', title: 'Long one', cwd: '/tmp/w',
    isLive: true, pinned: false, processes: [], updatedAt: 1700000000, messageCount: 2};
  const {harness} = liveSessionHarness(() => s, () => [
    {role: 'user', kind: 'message', text: 'go'}, {role: 'assistant', kind: 'message', text: 'done'}]);
  await settle();
  harness.getEl('transcript').scrollHeight = 5000;
  await harness.getEl('session-list').children.find(c => c.dataset?.key === `claude:${s.id}`).click();
  await settle();
  assert.equal(harness.getEl('transcript').scrollTop, 5000);
});

test('11. The list refreshes every few seconds while the page is open, and at once when it comes back', async () => {
  const s = {id: 'bbbbbbbb-1111-2222-3333-444444444444', tool: 'claude', title: 'x', cwd: '/tmp/w',
    isLive: true, pinned: false, processes: [], updatedAt: 1700000000, messageCount: 0};
  const {harness} = liveSessionHarness(() => s, () => []);
  await settle();
  const listTimer = harness.intervals.find(it => String(it.fn).includes('refresh()'));
  assert.ok(listTimer && listTimer.ms <= 5000, `list refresh every ${listTimer?.ms} ms`);
  let calls = 0;
  const orig = harness.window.fetch;
  harness.window.fetch = (...args) => { if (String(args[0]).startsWith('/api/sessions?')) calls++; return orig(...args); };
  for (const fn of harness.docListeners.get('visibilitychange') || []) fn();
  await settle();
  assert.equal(calls, 1, 'coming back to the tab refreshes right away');
});

test('12. The open transcript follows new replies when you are at the end, and leaves you alone when you scrolled up', async () => {
  let updatedAt = 1700000000;
  const s = () => ({id: 'cccccccc-1111-2222-3333-444444444444', tool: 'claude', title: 'live', cwd: '/tmp/w',
    isLive: true, pinned: false, processes: [], updatedAt, messageCount: 0});
  const {harness, reads} = liveSessionHarness(s, n => Array.from({length: n + 1}, (_, i) => ({role: 'assistant', kind: 'message', text: 'm' + i})));
  await settle();
  const scroller = harness.getEl('transcript');
  scroller.scrollHeight = 3000; scroller.clientHeight = 600;
  await harness.getEl('session-list').children.find(c => c.dataset?.key === `claude:${s().id}`).click();
  await settle();
  assert.equal(reads.length, 1);
  const tick = harness.intervals.find(it => String(it.fn).includes('refresh()')).fn;

  updatedAt += 10; scroller.scrollHeight = 3400;
  tick(); await settle();
  assert.equal(reads.length, 2, 'a new reply reloads the transcript');
  assert.equal(scroller.scrollTop, 3400, 'and stays at the end');

  scroller.scrollTop = 100;
  updatedAt += 10;
  tick(); await settle();
  assert.equal(reads.length, 2, 'scrolled up to read: no reload under your feet');
});

test('13. Sleep is offered only for sessions MixHub started; an agent in another terminal is left alone', async () => {
  const base = {tool: 'claude', title: 'x', cwd: '/tmp/w', isLive: true, pinned: false, updatedAt: 1700000000, messageCount: 0};
  const outside = {...base, id: 'cccccccc-1111-2222-3333-444444444444', processes: [{pid: 7, status: 'idle', rss_mb: 1, resident: false}]};
  const inside = {...base, id: 'dddddddd-1111-2222-3333-444444444444', processes: [{pid: 8, status: 'idle', rss_mb: 1, resident: true}]};
  const labelsOf = s => harness.window.actionButtons(s).children.map(c => c.textContent);
  let current = outside;
  const {harness} = liveSessionHarness(() => current, () => []);
  await settle();
  assert.ok(!labelsOf(outside).includes('☾'), 'no sleep button for an outside agent');
  assert.equal(harness.getEl('hibernate-idle').disabled, true, 'nothing of ours to put to sleep');
  assert.ok(labelsOf(inside).includes('☾'));
  current = inside;
  await harness.window.refresh();
  assert.equal(harness.getEl('hibernate-idle').disabled, false);
});
