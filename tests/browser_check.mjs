import assert from 'node:assert/strict';
import {mkdirSync, readFileSync, writeFileSync, existsSync, renameSync} from 'node:fs';
import {resolve} from 'node:path';
import {connect} from './mcp_client.mjs';

const url = process.env.HUB_URL || 'http://127.0.0.1:8765';
const out = resolve('screenshots'); mkdirSync(out, {recursive: true});
const report = {url, driver: 'chrome-devtools-mcp', assertions: [], screenshots: []};
const statePath = resolve('hub_state.json');
const before = existsSync(statePath) ? JSON.parse(readFileSync(statePath, 'utf8')) : {sessions: {}};
const mcp = await connect(); let pageId, selected, testKey;
const text = result => result.content.filter(x => x.type === 'text').map(x => x.text).join('\n');
async function evaluate(fn) {
  const result = text(await mcp.call('evaluate_script', {pageId, function: typeof fn === 'string' ? fn : fn.toString()}));
  const match = result.match(/```json\n([\s\S]*?)\n```/);
  if (!match || match[1] === 'undefined') throw new Error('Unexpected evaluation result: ' + result);
  return JSON.parse(match[1]);
}
async function check(name, expression) {
  const result = await evaluate('() => (' + expression + ')'); assert.ok(result, name); report.assertions.push(name); console.log('PASS ' + name);
}
async function wait(expression) {
  const result = await evaluate('async () => {for (let i=0;i<200;i++) {if (' + expression + ') return true; await new Promise(r=>setTimeout(r,100));} return false;}');
  assert.ok(result, 'Timed out: ' + expression);
}
async function uid(role, label) {
  const snapshot = text(await mcp.call('take_snapshot', {pageId}));
  const line = snapshot.split('\n').find(line => line.includes(' ' + role + ' "' + label + '"'));
  if (!line) throw new Error('No ' + role + ': ' + label);
  return line.match(/uid=(\S+)/)[1];
}
async function click(role, label) {await mcp.call('click', {pageId, uid: await uid(role, label)});}
async function fill(label, value, role = 'searchbox') {await mcp.call('fill', {pageId, uid: await uid(role, label), value});}
async function screenshot(name) {await mcp.call('take_screenshot', {pageId, filePath: resolve(out, name)}); report.screenshots.push(name);}
try {
  const created = text(await mcp.call('new_page', {url}));
  pageId = Number(created.match(/(\d+):[^\n]*\[selected\]/)[1]);
  await wait('window.__app?.state().counts.total > 0');
  await check('three stores and live memory loaded', "['claude','agy','codex','live','liveMemoryMB'].every(k=>window.__app.state().counts[k]>0)");
  await screenshot('01-discovery.png');
  await click('button', '运行中');
  await check('live filter', "window.__app.state().filter.tool==='live' && [...document.querySelectorAll('.session-card')].every(el=>el.querySelector('.card-live'))");
  await click('button', 'C Claude'); await check('Claude filter', "window.__app.state().filter.tool==='claude' && [...document.querySelectorAll('.session-card')].every(el=>el.dataset.key.startsWith('claude:'))");
  await click('button', 'X Codex'); await check('Codex filter', "window.__app.state().filter.tool==='codex'");
  await click('button', 'A agy'); await check('agy filter', "window.__app.state().filter.tool==='agy'");
  const checkboxSnapshot = text(await mcp.call('take_snapshot', {pageId}));
  const checkbox = checkboxSnapshot.split('\n').find(line => line.includes('checkbox "显示临时/子会话'));
  await mcp.call('fill', {pageId, uid: checkbox.match(/uid=(\S+)/)[1], value: 'true'});
  await check('temporary session toggle', 'window.__app.state().filter.showTemp===true');
  const list = await (await fetch(url + '/api/sessions?show_temp=1')).json();
  selected = list.sessions.find(s => s.tool === 'agy' && !s.isLive && !s.isTemp && s.hasTranscript && s.messageCount >= 20 && s.messageCount < 250);
  assert.ok(selected, 'A local history session is available'); testKey = selected.tool + ':' + selected.id;
  await fill('搜索会话', selected.id);
  await check('search narrows to exact session', 'document.querySelectorAll(".session-card").length===1');
  await click('article', selected.title);
  await wait('window.__app.state().activeSession?.messageCount > 0 && document.querySelector(".history-end")');
  await check('complete reader and collapsed tools', 'document.querySelectorAll("#messages > [data-index]").length===window.__app.state().activeSession.messageCount && document.querySelectorAll(".tool-details").length>0 && document.querySelectorAll("details[open]").length===0');
  await check('user and assistant messages', 'document.querySelectorAll(".message.user").length>0 && document.querySelectorAll(".message.assistant").length>0');
  await screenshot('02-reader.png');
  await click('button', '↓ 跳到末尾');
  await check('jump to end', 'Math.abs(document.querySelector("#transcript").scrollHeight-document.querySelector("#transcript").clientHeight-document.querySelector("#transcript").scrollTop)<4');
  await click('button', '↑ 跳到开头'); await check('jump to beginning', 'document.querySelector("#transcript").scrollTop===0');
  const needle = await evaluate('() => document.querySelector(".message.assistant .message-body").textContent.trim().slice(0,4)');
  await fill('搜索对话', needle); await wait('document.querySelectorAll("mark").length>0');
  await check('transcript search highlights', 'document.querySelectorAll("mark").length>0 && document.querySelector("#match-count").textContent.includes("/")');
  await screenshot('03-search.png');
  await fill('搜索对话', '');
  await click('button', '编辑标题');
  await fill('会话标题', 'Hub 验收 · title persistence', 'textbox');
  await mcp.call('press_key', {pageId, key: 'Enter'});
  await wait('window.__app.state().activeSession.title === "Hub 验收 · title persistence" && !document.querySelector(".card-title-input")');
  const pinLabel = selected.pinned ? '取消置顶' : '置顶会话';
  if (!selected.pinned) await click('button', pinLabel);
  await wait('document.querySelector(".card-pin.pinned")');
  await mcp.call('navigate_page', {pageId, type: 'reload'});
  await wait('window.__app?.state().activeSession?.title === "Hub 验收 · title persistence" && document.querySelector(".history-end")');
  await check('rename and pin survive reload', 'window.__app.state().counts.pinned>0 && document.querySelector(".group-title").textContent.includes("Pinned")');
  await screenshot('04-pinned.png');
  await click('button', '折叠侧边栏'); await check('sidebar button collapse', 'window.__app.state().sidebarCollapsed && document.querySelector("#sidebar").getBoundingClientRect().width<=49');
  await screenshot('05-collapsed.png');
  await mcp.call('press_key', {pageId, key: 'Meta+b'}); await check('Command B expands', '!window.__app.state().sidebarCollapsed');
  await mcp.call('press_key', {pageId, key: 'Control+b'}); await check('Control B collapses', 'window.__app.state().sidebarCollapsed');
  await mcp.call('resize_page', {pageId, width: 390, height: 844});
  await check('mobile has no page overflow', 'document.documentElement.scrollWidth<=innerWidth && document.querySelector("#reader").getBoundingClientRect().right<=innerWidth');
  await screenshot('06-mobile.png');
  await click('button', '展开侧边栏'); await check('mobile sidebar opens', '!window.__app.state().sidebarCollapsed');
  await screenshot('07-mobile-sidebar.png');
  await mcp.call('resize_page', {pageId, width: 1440, height: 1000});
  const dryRuns = [];
  for (const tool of ['claude','agy','codex']) {
    const s = list.sessions.find(s=>s.tool===tool&&!s.synthetic);
    const r = await fetch(url + '/api/sessions/' + tool + '/' + s.id + '/resume', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry_run:true})});
    const d = await r.json(); assert.equal(d.ok,true); assert.ok(d.command.includes({claude:'claude --resume',agy:'agy --conversation',codex:'codex resume'}[tool])); dryRuns.push(tool);
  }
  const live = list.sessions.find(s=>s.isLive);
  for (const action of ['focus','hibernate']) {
    const r = await fetch(url + '/api/sessions/' + live.tool + '/' + live.id + '/' + action, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry_run:true})}); assert.equal((await r.json()).ok,true);
  }
  report.assertions.push('three resume commands plus focus and hibernate dry-run');
  await check('read-only debug snapshot', '(()=>{const s=window.__app.state();s.counts.total=-1;return window.__app.state().counts.total>0})()');
  await check('no app errors', 'window.__app.state().lastError===null');
  const consoleResult = text(await mcp.call('list_console_messages', {pageId,types:['error','warn'],includePreservedMessages:true}));
  assert.ok(consoleResult.includes('<no console messages found>'),consoleResult); report.console = consoleResult;
  report.state = await evaluate(() => window.__app.state()); report.ok = true;
} catch (error) {
  report.ok = false; report.error = String(error.stack || error); console.error(report.error); process.exitCode = 1;
} finally {
  // Restore only the metadata entry exercised by this test, preserving other edits.
  if (testKey && existsSync(statePath)) {
    const current = JSON.parse(readFileSync(statePath,'utf8'));
    if (Object.hasOwn(before.sessions,testKey)) current.sessions[testKey] = before.sessions[testKey];
    else delete current.sessions[testKey];
    writeFileSync(statePath+'.browser-test.tmp', JSON.stringify(current,null,2)+'\n', {mode:0o600}); renameSync(statePath+'.browser-test.tmp',statePath);
  }
  writeFileSync(resolve(out,'browser-report.json'),JSON.stringify(report,null,2)+'\n');
  await mcp.close();
}
