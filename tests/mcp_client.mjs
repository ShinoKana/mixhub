// Small JSON-RPC client for the locally installed chrome-devtools MCP server.
import {spawn} from 'node:child_process';
import {existsSync, readdirSync} from 'node:fs';
import {homedir} from 'node:os';
import {resolve} from 'node:path';
export async function connect() {
  const cache = resolve(homedir(), '.npm/_npx');
  const installed = process.env.CHROME_DEVTOOLS_MCP || readdirSync(cache).map(p => resolve(cache, p, 'node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js')).find(existsSync);
  if (!installed) throw new Error('Install chrome-devtools-mcp or set CHROME_DEVTOOLS_MCP');
  const child = spawn(process.execPath, [installed, '--headless', '--isolated', '--viewport=1440x1000', '--no-usage-statistics', '--no-performance-crux', '--workspace=' + process.cwd()], {stdio: ['pipe', 'pipe', 'pipe']});
  let id = 0, buffer = ''; const pending = new Map();
  child.stderr.on('data', data => {if (process.env.MCP_DEBUG) process.stderr.write(data);});
  child.stdout.on('data', data => {
    buffer += data;
    while (buffer.includes('\n')) {
      const end = buffer.indexOf('\n'), line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
      let msg; try {msg = JSON.parse(line);} catch {continue;}
      if (msg.method === 'roots/list') child.stdin.write(JSON.stringify({jsonrpc: '2.0', id: msg.id, result: {roots: [{uri: 'file://' + process.cwd(), name: 'session-hub'}]}}) + '\n');
      else if (pending.has(msg.id)) {const task = pending.get(msg.id); pending.delete(msg.id); clearTimeout(task.timer); msg.error ? task.reject(new Error(JSON.stringify(msg.error))) : task.resolve(msg.result);}
    }
  });
  child.on('exit', code => {for (const task of pending.values()) {clearTimeout(task.timer); task.reject(new Error('MCP exited ' + code));} pending.clear();});
  function request(method, params = {}) {
    return new Promise((resolve, reject) => {const requestId = ++id; const timer = setTimeout(() => {pending.delete(requestId); reject(new Error('MCP timeout: ' + method));}, 60000); pending.set(requestId, {resolve, reject, timer}); child.stdin.write(JSON.stringify({jsonrpc: '2.0', id: requestId, method, params}) + '\n');});
  }
  await request('initialize', {protocolVersion: '2024-11-05', capabilities: {roots: {listChanged: false}}, clientInfo: {name: 'session-hub-verifier', version: '1.0'}});
  child.stdin.write(JSON.stringify({jsonrpc: '2.0', method: 'notifications/initialized'}) + '\n');
  return {request, async call(name, args = {}) {const result = await request('tools/call', {name, arguments: args}); if (result.isError) throw new Error(JSON.stringify(result.content)); return result;}, async close() {child.stdin.end(); await new Promise(resolve => {const t = setTimeout(() => {child.kill('SIGTERM'); resolve();}, 3000); child.once('exit', () => {clearTimeout(t); resolve();});});}};
}
