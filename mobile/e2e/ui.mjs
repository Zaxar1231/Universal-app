// Снимки интерфейса приложения в эмуляторе через DevTools самого WebView
// (adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>, см. run.sh).
// Playwright к WebView не подключается (нет управления контекстами), поэтому голый CDP:
// снимок — Page.captureScreenshot, клики в полной версии — скрипт в изолированном мире её iframe.
import fs from 'node:fs';

const out = process.argv[2] || '.';
const list = await (await fetch('http://127.0.0.1:9222/json')).json();
const target = list.find(t => t.type === 'page' && /localhost/.test(t.url)) || list.find(t => t.type === 'page');
if (!target) throw new Error('нет страницы WebView: ' + JSON.stringify(list));
console.log('страница оболочки:', target.url);

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
let seq = 0;
const waiting = new Map();
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (!m.id || !waiting.has(m.id)) return;
  const [ok, bad] = waiting.get(m.id);
  waiting.delete(m.id);
  m.error ? bad(new Error(m.error.message)) : ok(m.result);
};
const send = (method, params = {}) => new Promise((ok, bad) => {
  const id = ++seq;
  waiting.set(id, [ok, bad]);
  ws.send(JSON.stringify({ id, method, params }));
});
const sleep = ms => new Promise(r => setTimeout(r, ms));
const shot = async name => {
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(`${out}/${name}.png`, Buffer.from(data, 'base64'));
  console.log('снято:', name);
};
const run = async (expression, contextId) => {
  const r = await send('Runtime.evaluate', { expression, contextId, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
};
const until = async (what, fn, ms) => {
  for (const t0 = Date.now(); Date.now() - t0 < ms; await sleep(1000)) {
    try { if (await fn()) return; } catch { /* ещё грузится */ }
  }
  throw new Error('не дождался: ' + what);
};

await send('Page.enable');
await shot('ui-1-download');

// вкладка «Инструменты» = полная версия на телефоне
await run(`document.querySelector('nav.navbar button[data-page="pageTools"]').click()`);
let ctx;
await until('полной версии во вкладке «Инструменты»', async () => {
  const { frameTree } = await send('Page.getFrameTree');
  const fr = (frameTree.childFrames || []).map(c => c.frame).find(f => /127\.0\.0\.1:47700/.test(f.url));
  if (!fr) return false;
  ({ executionContextId: ctx } = await send('Page.createIsolatedWorld', { frameId: fr.id, worldName: 'e2e' }));
  return run(`!!document.querySelector('#rail')`, ctx);
}, 120000);
await sleep(1500);
await shot('ui-2-full');

const modes = [['Видео', 'video'], ['Голос в текст', 'stt'], ['Редактор PDF', 'pdfedit'], ['Документы', 'docs']];
let i = 3;
for (const [label, name] of modes) {
  try {
    const ok = await run(`(() => {
      const el = [...document.querySelectorAll('label.nav')].find(l => l.textContent.includes(${JSON.stringify(label)}));
      if (el) el.click();
      return !!el;
    })()`, ctx);
    if (!ok) throw new Error('нет пункта меню');
    await sleep(1200);
    await shot(`ui-${i++}-${name}`);
  } catch (e) {
    console.log('не снялось', label, e.message);
  }
}
const errors = await run(`(window.__errors || []).join('\\n')`, ctx).catch(() => '');
if (errors) console.log('ошибки страницы:', errors);
ws.close();
