// Собирает www/ для Capacitor: свой каркас приложения (src/) плюс браузерные
// инструменты с сайта (site/index.html), чтобы QR, картинки и PDF жили в одном месте.
// Запуск: node build-www.mjs (ничего ставить не нужно), затем npx cap sync.
import { readFileSync, writeFileSync, mkdirSync, rmSync, copyFileSync, cpSync, readdirSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const out = join(here, 'www');
const read = p => readFileSync(join(root, p), 'utf8');

function pick(text, re, what) {
  const m = text.match(re);
  if (!m) throw new Error(`site/index.html: не нашёл ${what}`);
  return m[1] ?? m[0];
}

const site = read('site/index.html');
const css = pick(site, /<style>([\s\S]*?)<\/style>/, 'стили');
let tools = pick(site, /<section id="try">([\s\S]*?)<\/section>/, 'раздел инструментов');
tools = tools.replace(/^\s*<h2>[\s\S]*?<\/h2>\s*<p[^>]*>[\s\S]*?<\/p>/, '');   // заголовок и пояснение про браузер
const toolsJs = pick(site, /<script>\s*([\s\S]*?)<\/script>\s*<\/body>/, 'скрипт инструментов');

const version = process.env.UNIVERSAL_VERSION || JSON.parse(readFileSync(join(here, 'package.json'), 'utf8')).version;
// «Что нового»: разделы CHANGELOG.md не новее этой версии (последние пять)
const vkey = v => (v.match(/\d+/g) || []).slice(0, 3).map(Number).reduce((a, x) => a * 1000 + x, 0);
const changes = [];
{
  const text = read('CHANGELOG.md'), heads = [...text.matchAll(/^## v(\d+(?:\.\d+)*)\s*(?:—|-)?\s*(.*)$/gm)];
  heads.forEach((m, i) => {
    const end = i + 1 < heads.length ? heads[i + 1].index : text.length;
    if (vkey(m[1]) <= vkey(version)) changes.push({ version: m[1], date: m[2].trim(), md: text.slice(m.index + m[0].length, end).trim() });
  });
}
const page = readFileSync(join(here, 'src/index.html'), 'utf8')
  .replace('/*SITE_CSS*/', () => css)
  .replace('<!--SITE_TOOLS-->', () => tools)
  .replace('/*SITE_TOOLS_JS*/', () => toolsJs)
  .replace('/*APP_VERSION*/', () => `const APP_VERSION = ${JSON.stringify(version)};\nconst APP_CHANGES = ${JSON.stringify(changes.slice(0, 5)).replace(/</g, '\\u003c')};`);
for (const mark of ['/*SITE_CSS*/', '<!--SITE_TOOLS-->', '/*SITE_TOOLS_JS*/', '/*APP_VERSION*/'])
  if (page.includes(mark)) throw new Error('не подставилось ' + mark);

rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, 'vendor'), { recursive: true });
mkdirSync(join(out, 'img'), { recursive: true });
writeFileSync(join(out, 'index.html'), page);
copyFileSync(join(here, 'src/mobile.js'), join(out, 'mobile.js'));
for (const f of ['qrcode.js', 'pdf-lib.min.js', 'jszip.min.js'])
  copyFileSync(join(root, 'site/vendor', f), join(out, 'vendor', f));
copyFileSync(join(root, 'site/img/icon-192.png'), join(out, 'img/icon-192.png'));
copyFileSync(join(here, 'node_modules/@capacitor/core/dist/capacitor.js'), join(out, 'vendor/capacitor.js'));
copyFileSync(join(here, 'node_modules/jsqr/dist/jsQR.js'), join(out, 'vendor/jsQR.js'));   // QR-код подключения к серверу
console.log(`www/ готов: версия ${version}`);

// Полная версия на Android: сервер Universal (модули *.py, шаблоны, static) как Python-пакет universal_srv
// для встроенного Python (Chaquopy). version.py с номером этой сборки.
const srv = join(here, 'android/app/src/main/python/universal_srv');
rmSync(srv, { recursive: true, force: true });
mkdirSync(srv, { recursive: true });
for (const f of readdirSync(root)) if (f.endsWith('.py')) copyFileSync(join(root, f), join(srv, f));
cpSync(join(root, 'templates'), join(srv, 'templates'), { recursive: true });
cpSync(join(root, 'static'), join(srv, 'static'), { recursive: true });
copyFileSync(join(root, 'CHANGELOG.md'), join(srv, 'CHANGELOG.md'));
writeFileSync(join(srv, '__init__.py'), '"""Сервер Universal для встроенного Python на Android (собирается build-www.mjs)."""\n');
writeFileSync(join(srv, 'version.py'), `VERSION = ${JSON.stringify(version)}\nCOMMIT = ""\nBUILT = ""\n`);
if (!existsSync(join(srv, 'app.py'))) throw new Error('universal_srv: нет app.py');
console.log('universal_srv/ готов для Android');
