// Собирает www/ для Capacitor: свой каркас приложения (src/) плюс браузерные
// инструменты с сайта (site/index.html), чтобы QR, картинки и PDF жили в одном месте.
// Запуск: node build-www.mjs (ничего ставить не нужно), затем npx cap sync.
import { readFileSync, writeFileSync, mkdirSync, rmSync, copyFileSync } from 'node:fs';
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
const page = readFileSync(join(here, 'src/index.html'), 'utf8')
  .replace('/*SITE_CSS*/', () => css)
  .replace('<!--SITE_TOOLS-->', () => tools)
  .replace('/*SITE_TOOLS_JS*/', () => toolsJs)
  .replace('/*APP_VERSION*/', () => `const APP_VERSION = ${JSON.stringify(version)};`);
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
console.log(`www/ готов: версия ${version}`);
