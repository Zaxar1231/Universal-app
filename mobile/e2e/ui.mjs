// Снимки интерфейса приложения в эмуляторе: Playwright подключается к WebView через DevTools
// (adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>, см. run.sh).
import { chromium } from 'playwright-core';

const out = process.argv[2] || '.';
const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
const ctx = browser.contexts()[0];
const page = ctx.pages().find(p => /localhost/.test(p.url())) || ctx.pages()[0];
console.log('страница оболочки:', page.url());
await page.screenshot({ path: `${out}/ui-1-download.png` });

// вкладка «Инструменты» = полная версия на телефоне
await page.click('nav.navbar button[data-page="pageTools"]');
await page.waitForSelector('#fullFrame:not([hidden])', { timeout: 120000 });
const frame = page.frameLocator('#fullFrame');
await frame.locator('#rail').waitFor({ timeout: 60000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/ui-2-full.png` });

const modes = [['Видео', 'video'], ['Голос в текст', 'stt'], ['Редактор PDF', 'pdfedit'], ['Документы', 'docs']];
let i = 3;
for (const [label, name] of modes) {
  try {
    await frame.locator(`label.nav:has-text("${label}")`).first().click({ timeout: 10000 });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${out}/ui-${i++}-${name}.png` });
    console.log('снято:', label);
  } catch (e) {
    console.log('не снялось', label, e.message);
  }
}
const errors = await frame.locator('body').evaluate(() => (window.__errors || []).join('\n')).catch(() => '');
if (errors) console.log('ошибки страницы:', errors);
await browser.close();
