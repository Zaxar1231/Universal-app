/*
 * Universal для телефона: скачивание видео через свой сервер Universal (yt-dlp),
 * сохранённые файлы и вход в полную версию. QR, картинки и PDF берутся с сайта
 * (site/index.html) и сохраняют результат через window.universalSave.
 *
 * Сеть идёт через CapacitorHttp (нативный HTTP): так нет CORS и запретов
 * на http:// к домашнему серверу. Файлы качает Filesystem.downloadFile прямо на диск,
 * не через память WebView: видео бывают по гигабайту.
 */
(() => {
  'use strict';
  const C = window.capacitorExports;
  const native = !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
  const platform = native ? window.Capacitor.getPlatform() : 'web';
  const Filesystem = C.registerPlugin('Filesystem');
  const Share = C.registerPlugin('Share');
  const Browser = C.registerPlugin('Browser');
  const Clipboard = C.registerPlugin('Clipboard');
  const Http = C.CapacitorHttp;
  const DIR = 'DOCUMENTS';                 // Directory.Documents: на Android папка «Документы», на iPhone видна в «Файлах»
  const FOLDER = 'Universal';
  const WHERE = platform === 'ios' ? 'Файлы → На iPhone → Universal' : 'Документы/Universal';

  const $ = id => document.getElementById(id), qs = s => document.querySelector(s);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmtSize = b => b < 1048576 ? Math.max(1, Math.round(b / 1024)) + ' КБ'
    : b < 1073741824 ? (b / 1048576).toFixed(1) + ' МБ' : (b / 1073741824).toFixed(2) + ' ГБ';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const store = {
    get: k => { try { return localStorage.getItem('u.' + k) || ''; } catch (_) { return ''; } },
    set: (k, v) => { try { localStorage.setItem('u.' + k, v); } catch (_) { /* приватный режим */ } },
  };

  /* ===================== мелочи интерфейса ===================== */
  let snackTimer;
  function snack(text, action, fn) {
    const el = $('snack'), b = el.querySelector('button');
    el.querySelector('span').textContent = text;
    b.hidden = !action; b.textContent = action || '';
    b.onclick = () => { el.hidden = true; if (fn) fn(); };
    el.hidden = false;
    clearTimeout(snackTimer);
    snackTimer = setTimeout(() => { el.hidden = true; }, action ? 6000 : 3500);
  }

  const TITLES = { pageDl: 'Скачать', pageTools: 'Инструменты', pageFiles: 'Файлы', pageServer: 'Сервер', pageInfo: 'Информация' };
  function go(page) {
    document.querySelectorAll('.page').forEach(p => { p.hidden = p.id !== page; });
    document.querySelectorAll('nav.navbar button').forEach(b => {
      if (b.dataset.page === page) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    $('title').textContent = TITLES[page];
    window.scrollTo(0, 0);
    if (page === 'pageFiles') listFiles();
    if (page === 'pageInfo') loadInfo();
  }
  document.querySelectorAll('nav.navbar button').forEach(b => { b.onclick = () => go(b.dataset.page); });

  /* ===================== сеть ===================== */
  function baseUrl(u) {
    u = (u || '').trim();
    if (!u) return '';
    if (!/^https?:\/\//i.test(u)) u = 'http://' + u;
    return u.replace(/\/+$/, '') + '/';
  }
  const host = u => { try { return new URL(u).host; } catch (_) { return u; } };
  function basic(password) {
    // пароль может быть по-русски: btoa понимает только латиницу, поэтому сначала UTF-8
    const bytes = new TextEncoder().encode('universal:' + password);
    return 'Basic ' + btoa(String.fromCharCode(...bytes));
  }
  function serverAuth() {
    const p = store.get('serverPass');
    return p ? { Authorization: basic(p) } : {};
  }
  async function http(method, url, opts = {}) {
    let r;
    try {
      r = await Http.request({ method, url, headers: opts.headers || {}, data: opts.data,
        connectTimeout: 15000, readTimeout: opts.readTimeout || 60000 });
    } catch (e) {
      throw new Error(`нет связи с ${host(url)}` + (e && e.message ? ` (${e.message})` : ''));
    }
    let d = r.data;
    if (typeof d === 'string') { try { d = JSON.parse(d); } catch (_) { /* не JSON */ } }
    return { status: r.status, data: d };
  }

  /* ===================== файлы ===================== */
  function cleanName(name, fallback = 'file') {
    let n = String(name || '').replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '_').replace(/\s+/g, ' ').trim().replace(/^\.+/, '');
    if (!n) n = fallback;
    if (n.length > 150) {
      const m = n.match(/(\.[a-z0-9]{1,6})$/i), ext = m ? m[1] : '';
      n = n.slice(0, 150 - ext.length).trim() + ext;
    }
    return n;
  }
  let folderReady = false;
  async function ensureFolder() {
    if (folderReady) return;
    try { await Filesystem.requestPermissions(); } catch (_) { /* Android 11+ и iOS разрешение не спрашивают */ }
    try { await Filesystem.mkdir({ path: FOLDER, directory: DIR, recursive: true }); } catch (_) { /* уже есть */ }
    folderReady = true;
  }
  async function exists(path) {
    try { await Filesystem.stat({ path, directory: DIR }); return true; } catch (_) { return false; }
  }
  async function freePath(name) {
    const m = name.match(/^(.*?)(\.[^.]{1,6})?$/), stem = m[1], ext = m[2] || '';
    for (let i = 1; i < 1000; i++) {
      const p = `${FOLDER}/${i === 1 ? name : `${stem} (${i})${ext}`}`;
      if (!(await exists(p))) return p;
    }
    return `${FOLDER}/${Date.now()}_${name}`;
  }
  async function shareFile(uri, title) {
    try { await Share.share({ title, files: [uri] }); }
    catch (e) { if (!/cancel/i.test(String(e && e.message))) snack('Не получилось поделиться: ' + (e.message || e)); }
  }
  function blobToBase64(blob) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result).split(',')[1] || '');
      r.onerror = () => rej(r.error || new Error('не прочитался файл'));
      r.readAsDataURL(blob);
    });
  }
  // сохранение из инструментов сайта (QR, картинки, PDF)
  window.universalSave = async (blob, name) => {
    if (!native) {
      const a = document.createElement('a'), u = URL.createObjectURL(blob);
      a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(u), 10000);
      return;
    }
    try {
      await ensureFolder();
      const path = await freePath(cleanName(name));
      await Filesystem.writeFile({ path, directory: DIR, data: await blobToBase64(blob), recursive: true });
      const st = await Filesystem.stat({ path, directory: DIR });
      snack(`Сохранено в ${WHERE}: ${path.slice(FOLDER.length + 1)}`, 'Поделиться', () => shareFile(st.uri, name));
    } catch (e) {
      snack('Не сохранилось: ' + (e.message || e));
    }
  };

  /** Качает url прямо в файл. Возвращает {name, uri, size} или null, если пришёл пустой файл. */
  async function fetchTo(url, name, headers, job) {
    if (!native) { window.open(url, '_blank'); return { name, uri: url, size: 0 }; }
    await ensureFolder();
    const path = await freePath(cleanName(name, 'video.mp4'));
    const h = await Filesystem.addListener('progress', p => job.bytes(p.bytes, p.contentLength));
    try {
      await Filesystem.downloadFile({ url, path, directory: DIR, headers: headers || {}, progress: true, recursive: true,
        connectTimeout: 20000, readTimeout: 180000 });
    } catch (e) {
      await Filesystem.deleteFile({ path, directory: DIR }).catch(() => {});
      throw new Error('файл не скачался: ' + (e.message || e).replace(/^Error downloading file:\s*/i, ''));
    } finally {
      h.remove();
    }
    const st = await Filesystem.stat({ path, directory: DIR });
    if (!st.size) {
      await Filesystem.deleteFile({ path, directory: DIR }).catch(() => {});
      return null;
    }
    return { name: path.slice(FOLDER.length + 1), uri: st.uri, size: st.size };
  }

  /* ===================== скачивание: строки заданий ===================== */
  function jobRow(title) {
    const li = document.createElement('li');
    li.innerHTML = '<div class="t"></div><div class="bar2 ind"><i></i></div><div class="m"></div><div class="row" hidden></div>';
    li.querySelector('.t').textContent = title;
    $('dJobs').prepend(li);
    const bar = li.querySelector('.bar2'), m = li.querySelector('.m'), acts = li.querySelector('.row');
    const job = {
      msg(text, bad = false) { m.textContent = text; m.classList.toggle('bad', bad); },
      progress(p) {
        bar.classList.toggle('ind', p == null);
        bar.firstChild.style.width = p == null ? '' : Math.round(p * 100) + '%';
      },
      bytes(b, total) {
        job.progress(total > 0 ? b / total : null);
        job.msg(`Скачиваю на телефон: ${fmtSize(b)}` + (total > 0 ? ` из ${fmtSize(total)}` : ''));
      },
      finish(files, errors) {
        bar.hidden = true;
        const lines = [];
        if (files.length) lines.push(`Сохранено в ${WHERE}: ` + files.map(f => `${f.name} (${fmtSize(f.size)})`).join(', '));
        lines.push(...errors);
        job.msg(lines.join('\n') || 'Готово', !files.length);
        m.style.whiteSpace = 'pre-line';
        files.forEach(f => {
          const b = document.createElement('button');
          b.className = 'btn tonal sm';
          b.textContent = files.length > 1 ? 'Поделиться: ' + f.name : 'Поделиться';
          b.onclick = () => shareFile(f.uri, f.name);
          acts.appendChild(b);
        });
        acts.hidden = !files.length;
      },
    };
    return job;
  }

  /* ===================== скачивание через свой сервер ===================== */
  async function viaServer(urls, o, job, stop) {
    const srv = baseUrl(store.get('serverUrl'));
    if (!srv) throw new Error('укажи адрес своего сервера на вкладке «Сервер»');
    const auth = serverAuth();
    job.msg(`Отправляю на ${host(srv)}…`);
    const form = { urls: urls.join('\n'), mode: o.mode, quality: o.quality, container: 'mp4', audio_format: o.audio,
      compat: '1', thumbnail: '1', metadata: '1', playlist: '0', archive_name: 'downloads' };
    let r = await http('POST', srv + 'api/dl', { headers: { ...auth, 'Content-Type': 'application/x-www-form-urlencoded' }, data: form });
    if (r.status === 401) throw new Error('сервер не пустил: неверный пароль (вкладка «Сервер»)');
    if (r.status >= 400 || !r.data || !r.data.task) throw new Error((r.data && r.data.error) || `сервер ответил ${r.status}. Это точно Universal?`);
    let t = r.data, cancelled = false;
    while (!['done', 'error', 'cancelled'].includes(t.status)) {
      await sleep(1000);
      if (stop() && !cancelled) {
        cancelled = true;
        await http('POST', `${srv}api/task/${t.task}/cancel`, { headers: auth }).catch(() => {});
      }
      r = await http('GET', `${srv}api/task/${t.task}`, { headers: auth });
      if (r.status !== 200 || !r.data) throw new Error((r.data && r.data.error) || `сервер ответил ${r.status}`);
      t = r.data;
      job.progress(t.progress);
      job.msg('Сервер: ' + (t.message || '…'));
    }
    const errors = (t.failed || []).map(f => `${f.name}: ${f.error}`);
    if (t.status === 'error') throw new Error(t.error || 'ошибка на сервере');
    const files = [];
    for (const name of t.names || []) {
      if (stop() && files.length) break;
      const f = await fetchTo(`${srv}api/file/${t.job}/${encodeURIComponent(name)}`, name, auth, job);
      if (f) files.push(f); else errors.push(`${name}: пустой файл`);
    }
    if (!files.length && !errors.length) errors.push(t.status === 'cancelled' ? 'Отменено' : 'Сервер ничего не скачал');
    return { files, errors };
  }

  /* ===================== скачивание прямо на телефоне (Android) ===================== */
  // yt-dlp + Python + ffmpeg внутри приложения (плагин Ytdl, библиотека youtubedl-android, как в YTDLnis)
  const Ytdl = platform === 'android' ? C.registerPlugin('Ytdl') : null;
  let ytdlInit = null;
  function ytdlReady() {
    if (!ytdlInit) ytdlInit = Ytdl.init().catch(e => { ytdlInit = null; throw e; });
    return ytdlInit;
  }
  const fmtEta = s => (s > 0 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : '');
  async function viaPhone(urls, o, job, stop) {
    await ensureFolder();
    job.msg('Готовлю yt-dlp (первый раз до полуминуты)…');
    await ytdlReady();
    const files = [], errors = [];
    for (let i = 0; i < urls.length; i++) {
      if (stop()) break;
      const id = `dl${Date.now()}_${i}`, pre = urls.length > 1 ? `${i + 1} из ${urls.length}: ` : '';
      job.progress(null); job.msg(pre + 'Ищу видео…');
      const h = await Ytdl.addListener('progress', ev => {
        if (ev.id !== id) return;
        const p = ev.progress;
        if (p > 0) { job.progress(p / 100); job.msg(`${pre}Скачиваю: ${Math.round(p)}%` + (ev.eta > 0 ? ` · осталось ${fmtEta(ev.eta)}` : '')); }
        else if (/Merger|ExtractAudio|ffmpeg|Fixup/.test(ev.line || '')) { job.progress(null); job.msg(pre + 'Собираю файл…'); }
      });
      const watch = setInterval(() => { if (stop()) Ytdl.cancel({ id }).catch(() => {}); }, 500);
      try {
        const r = await Ytdl.download({ id, url: urls[i], mode: o.mode, quality: o.quality, audio: o.audio });
        const got = (r.files || []).map(f => ({ name: f.name, uri: f.path, size: f.size }));
        if (got.length) files.push(...got); else errors.push(`${urls[i]}: файл не появился`);
      } catch (e) {
        errors.push(e.code === 'CANCELLED' || stop() ? 'Остановлено' : `${urls[i]}: ${e.message || e}`);
      } finally {
        clearInterval(watch); h.remove();
      }
    }
    return { files, errors };
  }
  // сайты меняются часто: раз в сутки тихо подтягиваем свежий yt-dlp
  if (Ytdl && Date.now() - (+store.get('ytdlUpd') || 0) > 24 * 3600 * 1000) {
    setTimeout(() => ytdlReady().then(() => Ytdl.update()).then(() => store.set('ytdlUpd', String(Date.now()))).catch(() => {}), 8000);
  }
  const where = () => (Ytdl ? qs('input[name=dw]:checked').value : 'server');

  /* ===================== кнопка «Скачать» ===================== */
  const URL_RE = /https?:\/\/[^\s<>"']+/gi;
  const opts = () => ({ mode: qs('input[name=dm]:checked').value, quality: $('dQ').value, audio: $('dA').value });
  let running = false, stopAsked = false;
  function syncDl() {
    const o = opts();
    $('dQRow').hidden = o.mode === 'audio'; $('dARow').hidden = o.mode !== 'audio';
    const srv = host(baseUrl(store.get('serverUrl')));
    $('dWhere').hidden = !Ytdl;
    $('dViaHint').textContent = where() === 'phone'
      ? `Качает yt-dlp прямо на телефоне, компьютер не нужен. Файлы — в ${WHERE}.`
      : srv
        ? `Качает yt-dlp на твоём сервере ${srv} (с его VPN и настройками), потом файл сам приезжает на телефон.`
        : 'Качает yt-dlp на твоём сервере Universal: укажи его адрес на вкладке «Сервер».';
    if (Ytdl) store.set('dl.where', where());
    $('dGo').textContent = running ? (stopAsked ? 'Останавливаю…' : 'Остановить') : 'Скачать';
    $('dGo').disabled = running && stopAsked;
    for (const k of ['mode', 'quality', 'audio']) store.set('dl.' + k, o[k]);
  }
  async function start() {
    if (running) { stopAsked = true; syncDl(); return; }
    const urls = [...new Set(($('dUrls').value.match(URL_RE) || []))];
    if (!urls.length) { snack('Не нашёл ссылок: вставь адрес, начинающийся с http'); return; }
    const o = opts(), stop = () => stopAsked;
    running = true; stopAsked = false; syncDl();
    try {
      const job = jobRow(urls.length === 1 ? urls[0] : `Ссылок: ${urls.length}`);
      try { const { files, errors } = await (where() === 'phone' ? viaPhone : viaServer)(urls, o, job, stop); job.finish(files, errors); }
      catch (e) { job.finish([], [String(e.message || e)]); }
      $('dUrls').value = '';
    } finally {
      running = false; stopAsked = false; syncDl();
    }
  }
  $('dGo').onclick = start;
  $('dClear').onclick = () => { $('dUrls').value = ''; };
  $('dPaste').onclick = async () => {
    let text = '';
    try { text = native ? (await Clipboard.read()).value : await navigator.clipboard.readText(); } catch (_) { /* пусто */ }
    if (!text) { snack('В буфере обмена пусто'); return; }
    addUrls(text);
  };
  function addUrls(text) {
    const found = String(text).match(URL_RE) || [];
    if (!found.length) { snack('В тексте нет ссылки'); return 0; }
    const cur = $('dUrls').value.trim();
    $('dUrls').value = (cur ? cur + '\n' : '') + found.join('\n');
    return found.length;
  }
  document.querySelectorAll('input[name=dm], input[name=dw]').forEach(r => r.addEventListener('change', syncDl));
  ['dQ', 'dA'].forEach(id => $(id).addEventListener('change', syncDl));
  {
    const m = store.get('dl.mode'); if (m) { const r = qs(`input[name=dm][value="${m}"]`); if (r) r.checked = true; }
    const w = store.get('dl.where'); if (w) { const r = qs(`input[name=dw][value="${w}"]`); if (r) r.checked = true; }
    for (const [k, id] of [['quality', 'dQ'], ['audio', 'dA']]) {
      const v = store.get('dl.' + k); if (v && [...$(id).options].some(op => op.value === v)) $(id).value = v;
    }
  }

  /* ===================== свой сервер ===================== */
  $('sUrl').value = store.get('serverUrl'); $('sPass').value = store.get('serverPass');
  $('sUrl').addEventListener('input', () => { store.set('serverUrl', $('sUrl').value.trim()); syncDl(); });
  $('sPass').addEventListener('input', () => store.set('serverPass', $('sPass').value));
  $('sCheck').onclick = async () => {
    const srv = baseUrl($('sUrl').value), st = $('sState');
    if (!srv) { st.className = 'small grow bad'; st.textContent = 'Укажи адрес'; return; }
    st.className = 'small grow'; st.textContent = 'Проверяю…';
    try {
      const r = await http('GET', srv + 'api/formats', { headers: serverAuth() });
      if (r.status === 401) throw new Error('неверный пароль');
      if (r.status !== 200 || !r.data || !r.data.categories) throw new Error(`ответ ${r.status}, это не Universal`);
      const NAMES = { ytdlp: 'yt-dlp', ffmpeg: 'ffmpeg', ocr: 'распознавание текста', imagemagick: 'ImageMagick', pandoc: 'pandoc', poppler: 'poppler', libreoffice: 'LibreOffice' };
      const eng = r.data.engines || {}, have = Object.keys(NAMES).filter(k => eng[k] === true).map(k => NAMES[k]);
      st.className = 'small grow ok';
      st.textContent = 'Работает' + (have.length ? `: ${have.join(', ')}` : '');
    } catch (e) { st.className = 'small grow bad'; st.textContent = String(e.message || e); }
  };
  $('sOpen').onclick = async () => {
    const srv = baseUrl($('sUrl').value);
    if (!srv) { snack('Сначала укажи адрес сервера'); return; }
    if (native) await Browser.open({ url: srv }); else window.open(srv, '_blank');
  };

  /* ---------- подключение по ссылке universal://connect?url=…&password=… ---------- */
  const CONNECT_RE = /universal:\/\/connect\?[^\s"'<>]+/i;
  function applyConnect(text) {
    const m = String(text || '').match(CONNECT_RE);
    if (!m) return false;
    let url = '', pass = '';
    try { const q = new URL(m[0]).searchParams; url = q.get('url') || ''; pass = q.get('password') || ''; } catch (_) { return false; }
    if (!/^https?:\/\//i.test(url)) return false;
    $('sUrl').value = url; $('sPass').value = pass;
    store.set('serverUrl', url); store.set('serverPass', pass);
    syncDl(); go('pageServer');
    snack('Сервер подключён: ' + host(url));
    $('sCheck').click();
    return true;
  }
  $('sPaste').onclick = async () => {
    let text = '';
    try { text = native ? (await Clipboard.read()).value : await navigator.clipboard.readText(); } catch (_) { /* пусто */ }
    if (!applyConnect(text)) snack('В буфере нет ссылки universal://connect: скопируй её в окне «Сервер для телефона» на компьютере');
  };

  // сканер: BarcodeDetector (Android WebView) или jsQR (iPhone и старые WebView)
  let scanStream = null, scanTimer = 0;
  function stopScan() {
    clearTimeout(scanTimer);
    if (scanStream) scanStream.getTracks().forEach(t => t.stop());
    scanStream = null; $('scan').hidden = true;
  }
  $('scanClose').onclick = stopScan;
  $('sScan').onclick = async () => {
    try {
      scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
    } catch (e) {
      snack('Нет доступа к камере: разреши его в настройках телефона или вставь ссылку');
      return;
    }
    const v = $('scanVideo');
    v.srcObject = scanStream; $('scan').hidden = false;
    await v.play().catch(() => {});
    const detector = 'BarcodeDetector' in window ? new window.BarcodeDetector({ formats: ['qr_code'] }) : null;
    const canvas = document.createElement('canvas'), g = canvas.getContext('2d', { willReadFrequently: true });
    const tick = async () => {
      if (!scanStream) return;
      let text = '';
      try {
        if (detector) {
          const codes = await detector.detect(v);
          text = codes.map(c => c.rawValue).join('\n');
        } else if (v.videoWidth && window.jsQR) {
          const k = Math.min(1, 640 / Math.max(v.videoWidth, v.videoHeight));
          canvas.width = Math.round(v.videoWidth * k); canvas.height = Math.round(v.videoHeight * k);
          g.drawImage(v, 0, 0, canvas.width, canvas.height);
          const r = window.jsQR(g.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
          text = r ? r.data : '';
        }
      } catch (_) { /* кадр ещё не готов */ }
      if (text && CONNECT_RE.test(text)) { stopScan(); applyConnect(text); return; }
      scanTimer = setTimeout(tick, 200);
    };
    tick();
  };

  // ссылка из камеры телефона или из другого приложения открывает Universal
  if (native) {
    const App = C.registerPlugin('App');
    App.addListener('appUrlOpen', ({ url }) => applyConnect(url)).catch(() => {});
    App.getLaunchUrl().then(r => { if (r && r.url) applyConnect(r.url); }).catch(() => {});
  }

  $('ver').textContent = typeof APP_VERSION === 'string' ? APP_VERSION : '';
  // ссылки вкладки «Инфо» открываются во встроенном браузере, а не внутри приложения
  document.querySelectorAll('#pageInfo a[href^="http"]').forEach(a => {
    a.onclick = e => { e.preventDefault(); if (native) Browser.open({ url: a.href }); else window.open(a.href, '_blank'); };
  });

  /* ---------- «Для зануд» ---------- */
  let nerdData = null;
  async function collectNerd() {
    const mm = q => matchMedia(q).matches, secs = [];
    const fmtT = t => (t ? new Date(+t).toLocaleString() : '—');
    secs.push({ title: 'Приложение', rows: [
      ['Версия', typeof APP_VERSION === 'string' ? APP_VERSION : '—'], ['Платформа', platform],
      ['Нативное приложение', native ? 'да' : 'нет, браузер'],
      ['Плагины', native && window.Capacitor.PluginHeaders ? window.Capacitor.PluginHeaders.map(p => p.name).join(', ') : '—'] ] });
    let est = null;
    try { est = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : null; } catch (_) { /* нет API */ }
    secs.push({ title: 'Телефон', rows: [
      ['Движок', navigator.userAgent], ['Экран', `${screen.width}×${screen.height}, окно ${innerWidth}×${innerHeight}, масштаб ${devicePixelRatio}`],
      ['Язык', (navigator.languages || [navigator.language]).join(', ')], ['Часовой пояс', Intl.DateTimeFormat().resolvedOptions().timeZone],
      ['Тема', mm('(prefers-color-scheme: dark)') ? 'тёмная' : 'светлая'], ['Сеть', navigator.onLine ? 'есть' : 'нет'],
      ['Память WebView', est ? `${fmtSize(est.usage || 0)} из ${fmtSize(est.quota || 0)}` : '—'] ] });
    let files = '—';
    if (native) {
      try {
        const list = ((await Filesystem.readdir({ path: FOLDER, directory: DIR })).files || []).filter(f => f.type !== 'directory');
        files = `${list.length} шт., ${fmtSize(list.reduce((a, f) => a + (f.size || 0), 0))}`;
      } catch (_) { files = 'папки ещё нет'; }
    }
    let upd = {};
    try { upd = JSON.parse(store.get('updInfo') || '{}'); } catch (_) { /* битый кэш */ }
    secs.push({ title: 'Данные', rows: [['Папка', WHERE], ['Файлы', files], ['Последний релиз', upd.version || '—'],
      ['Проверено', fmtT(store.get('updChecked'))], ['Отложена версия', store.get('updSkip') || '—']] });
    const srv = baseUrl(store.get('serverUrl')), rows = [['Адрес', srv || 'не задан'], ['Пароль', store.get('serverPass') ? 'задан' : 'нет']];
    if (srv) {
      try {
        const t0 = Date.now(), r = await http('GET', srv + 'api/about', { headers: serverAuth() });
        rows.push(['Ответ', `${r.status} за ${Date.now() - t0} мс`]);
        if (r.status === 200 && r.data && r.data.version) {
          rows.push(['Universal', r.data.version], ['ОС', r.data.os], ['Python', r.data.python], ['Установка', r.data.install]);
          (r.data.tools || []).forEach(t => rows.push([t.name, t.installed ? (t.version || 'есть') : 'нет']));
        }
      } catch (e) { rows.push(['Ответ', String(e.message || e)]); }
    }
    secs.push({ title: 'Компьютер', rows });
    return secs;
  }
  async function loadNerd() {
    $('nerdState').textContent = 'Собираю…';
    nerdData = await collectNerd();
    $('nerdBody').innerHTML = nerdData.map(sec => `<h4>${esc(sec.title)}</h4><table>` +
      sec.rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') + '</table>').join('');
    $('nerdState').textContent = '';
  }
  $('nerd').addEventListener('toggle', () => { if ($('nerd').open) loadNerd(); });
  $('nerdCopy').onclick = async () => {
    if (!nerdData) await loadNerd();
    const text = '### Universal (телефон) — информация для отчёта\n' + nerdData.map(sec => `\n**${sec.title}**\n` +
      sec.rows.map(([k, v]) => `- ${k}: ${v}`).join('\n')).join('\n');
    try {
      if (native) await Clipboard.write({ string: text }); else await navigator.clipboard.writeText(text);
      $('nerdState').textContent = 'Скопировано';
    } catch (e) { $('nerdState').textContent = 'Не скопировалось: ' + (e.message || e); }
  };

  /* ---------- вкладка «Инфо»: что за компьютер подключён ---------- */
  async function loadInfo() {
    $('infoDevice').textContent = `Версия ${typeof APP_VERSION === 'string' ? APP_VERSION : ''} · ${platform === 'ios' ? 'iPhone' : platform === 'android' ? 'Android' : 'браузер'}`;
    const srv = baseUrl(store.get('serverUrl')), box = $('infoServer'), list = $('infoServerTools');
    if (!srv) return;
    box.textContent = `Проверяю ${host(srv)}…`; list.hidden = true;
    try {
      const r = await http('GET', srv + 'api/about', { headers: serverAuth() });
      if (r.status === 401) throw new Error('неверный пароль, обнови его на вкладке «Сервер»');
      if (r.status !== 200 || !r.data || !r.data.tools) throw new Error(`ответ ${r.status} (старая версия Universal на компьютере?)`);
      const d = r.data;
      box.textContent = `Подключён ${host(srv)}: Universal ${d.version}, ${d.os}.` +
        (typeof APP_VERSION === 'string' && d.version !== APP_VERSION ? ' Версии на телефоне и компьютере отличаются: лучше обновить оба.' : '');
      list.innerHTML = d.tools.map(t => `<li>${esc(t.name)} — ${t.installed ? esc(t.version || 'есть') : '<span class="bad">не установлен</span>'} <span class="muted">(${esc(t.for)})</span></li>`).join('');
      list.hidden = false;
    } catch (e) {
      box.textContent = `${host(srv)}: ${e.message || e}`;
    }
  }

  /* ===================== вкладка «Файлы» ===================== */
  $('fWhere').textContent = 'Папка: ' + WHERE;
  $('fRefresh').onclick = () => listFiles();
  async function listFiles() {
    const list = $('fList');
    if (!native) { list.innerHTML = ''; $('fEmpty').hidden = false; return; }
    let entries = [];
    try { entries = (await Filesystem.readdir({ path: FOLDER, directory: DIR })).files || []; } catch (_) { /* папки ещё нет */ }
    entries = entries.filter(f => f.type !== 'directory' && !f.name.startsWith('.')).sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    list.innerHTML = '';
    for (const f of entries) {
      const li = document.createElement('li'), ext = (f.name.split('.').pop() || '').slice(0, 4).toUpperCase();
      li.innerHTML = `<span class="ext">${esc(ext)}</span><span class="name">${esc(f.name)}</span><span class="small muted">${fmtSize(f.size || 0)}</span>` +
        '<span class="acts"><button class="x sh" title="Поделиться" aria-label="Поделиться">↗</button><button class="x del" title="Удалить" aria-label="Удалить">×</button></span>';
      li.querySelector('.sh').onclick = () => shareFile(f.uri, f.name);
      li.querySelector('.name').onclick = () => shareFile(f.uri, f.name);
      li.querySelector('.del').onclick = async () => {
        if (!confirm(`Удалить ${f.name}?`)) return;
        await Filesystem.deleteFile({ path: `${FOLDER}/${f.name}`, directory: DIR }).catch(e => snack('Не удалилось: ' + e.message));
        listFiles();
      };
      list.appendChild(li);
    }
    $('fEmpty').hidden = entries.length > 0;
  }

  /* ===================== обновления приложения ===================== */
  // раз в 6 часов смотрим последний релиз на GitHub; на Android APK скачивается и открывается установка
  const RELEASE_API = 'https://api.github.com/repos/Zaxar1231/Universal-app/releases/latest';
  const vparse = v => (String(v || '').match(/\d+/g) || []).slice(0, 3).map(Number);
  function isNewer(a, b) {
    const x = vparse(a), y = vparse(b);
    for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0);
    return false;
  }
  const notesList = md => String(md || '').split('\n').filter(l => /^\s*[-*] /.test(l)).slice(0, 3)
    .map(l => l.replace(/^\s*[-*] /, '').replace(/\*\*|`/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1'))
    .map(l => (l.length > 140 ? l.slice(0, 137) + '…' : l));
  function showUpdate(info) {
    const box = $('upd'), apk = platform === 'android' && info.apk;
    box.hidden = false;
    box.innerHTML = `<div><b>Вышла Universal ${esc(info.version)}</b>, у тебя ${esc(APP_VERSION)}.</div>` +
      (info.notes.length ? '<ul>' + info.notes.map(n => `<li>${esc(n)}</li>`).join('') + '</ul>' : '') +
      '<div class="row">' + (apk ? `<button class="btn filled" id="updGo">Обновить${info.apkSize ? ' (' + fmtSize(info.apkSize) + ')' : ''}</button>`
                                 : '<button class="btn filled" id="updPage">Скачать</button>') +
      '<button class="btn text" id="updLater">Позже</button></div>' +
      (platform === 'ios' ? '<p class="hint" style="color:inherit">Поставь новый .ipa через AltStore или Sideloadly поверх старого: настройки и файлы сохранятся.</p>' : '');
    $('updLater').onclick = () => { store.set('updSkip', info.version); box.hidden = true; };
    if ($('updPage')) $('updPage').onclick = () => (native ? Browser.open({ url: info.url }) : window.open(info.url, '_blank'));
    if ($('updGo')) $('updGo').onclick = () => installUpdate(info);
  }
  async function installUpdate(info) {
    const box = $('upd');
    box.innerHTML = `<div>Скачиваю Universal ${esc(info.version)}…</div><div class="bar2 ind"><i></i></div>`;
    const bar = box.querySelector('.bar2'), path = `universal-${info.version}.apk`;
    const h = await Filesystem.addListener('progress', p => {
      if (p.contentLength > 0) { bar.classList.remove('ind'); bar.firstChild.style.width = Math.round(p.bytes / p.contentLength * 100) + '%'; }
    });
    try {
      await Filesystem.downloadFile({ url: info.apk, path, directory: 'CACHE', progress: true, connectTimeout: 20000, readTimeout: 120000 });
      const { uri } = await Filesystem.getUri({ path, directory: 'CACHE' });
      const ApkInstall = C.registerPlugin('ApkInstall');
      const r = await ApkInstall.install({ path: uri });
      box.innerHTML = r.needPermission
        ? '<div>Разреши установку приложений от Universal в открывшихся настройках, вернись и нажми «Установить».</div><div class="row"><button class="btn filled" id="updAgain">Установить</button></div>'
        : '<div>Открыл установку: подтверди обновление. Настройки и файлы сохранятся.</div><div class="row"><button class="btn tonal" id="updAgain">Открыть установку ещё раз</button></div>';
      $('updAgain').onclick = async () => {
        const again = await ApkInstall.install({ path: uri }).catch(e => ({ error: e.message }));
        if (again.error) snack('Не открылась установка: ' + again.error);
      };
    } catch (e) {
      box.innerHTML = `<div>Не получилось обновиться: ${esc(e.message || e)}</div><div class="row"><button class="btn filled" id="updRetry">Ещё раз</button><button class="btn text" id="updPage2">Скачать вручную</button></div>`;
      $('updRetry').onclick = () => installUpdate(info);
      $('updPage2').onclick = () => Browser.open({ url: info.url });
    } finally {
      h.remove();
    }
  }
  async function checkUpdate(force) {
    const st = $('updState');
    if (!force) {
      const last = +store.get('updChecked') || 0;
      let cached = null;
      try { cached = JSON.parse(store.get('updInfo') || 'null'); } catch (_) { /* битый кэш */ }
      if (cached && Date.now() - last < 6 * 3600 * 1000) {
        if (isNewer(cached.version, APP_VERSION) && store.get('updSkip') !== cached.version) showUpdate(cached);
        return;
      }
    } else { st.className = 'small grow'; st.textContent = 'Проверяю…'; }
    try {
      const r = await http('GET', RELEASE_API, { headers: { Accept: 'application/vnd.github+json' } });
      if (r.status !== 200 || !r.data || !r.data.tag_name) throw new Error(r.status === 403 ? 'GitHub просит подождать, попробуй позже' : `GitHub ответил ${r.status}`);
      const assets = r.data.assets || [], apk = assets.find(a => /-android\.apk$/.test(a.name));
      const info = { version: r.data.tag_name.replace(/^v/, ''), url: r.data.html_url, notes: notesList(r.data.body),
        apk: apk ? apk.browser_download_url : '', apkSize: apk ? apk.size : 0 };
      store.set('updInfo', JSON.stringify(info)); store.set('updChecked', String(Date.now()));
      const fresh = isNewer(info.version, APP_VERSION);
      if (fresh && (force || store.get('updSkip') !== info.version)) showUpdate(info);
      if (force) { st.className = 'small grow ok'; st.textContent = fresh ? `Есть ${info.version}, кнопка вверху` : 'У тебя последняя версия'; }
    } catch (e) {
      if (force) { st.className = 'small grow bad'; st.textContent = String(e.message || e); }
    }
  }
  $('updCheck').onclick = () => { $('upd').hidden = true; checkUpdate(true); };
  if (typeof APP_VERSION === 'string' && APP_VERSION) setTimeout(() => checkUpdate(false), 2000);

  /* ===================== «Что нового» после обновления ===================== */
  function mdLite(md) {                       // заголовки ###, списки, **жирный**, `код`
    const inline = t => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '$1');
    let html = '', list = false;
    for (const raw of String(md).split('\n')) {
      const l = raw.trim(), item = l.match(/^[-*] (.*)/);
      if (!item && list) { html += '</ul>'; list = false; }
      if (item) { if (!list) { html += '<ul>'; list = true; } html += `<li>${inline(item[1])}</li>`; }
      else if (/^#{3,4} /.test(l)) html += `<h4>${inline(l.replace(/^#+ /, ''))}</h4>`;
      else if (l) html += `<p>${inline(l)}</p>`;
    }
    return html + (list ? '</ul>' : '');
  }
  function showNews(items, title) {
    const dlg = $('wn');
    if (!items.length || !dlg || !dlg.showModal) return;
    $('wnTitle').textContent = title;
    $('wnBody').innerHTML = items.map(x => `<h3>Версия ${esc(x.version)}${x.date ? ' · ' + esc(x.date) : ''}</h3>${mdLite(x.md)}`).join('<hr>');
    $('wnClose').onclick = () => dlg.close();
    dlg.showModal(); $('wnBody').scrollTop = 0;
  }
  if (typeof APP_CHANGES !== 'undefined' && typeof APP_VERSION === 'string' && APP_VERSION) {
    const seen = store.get('seenVersion');
    if (seen !== APP_VERSION) {
      store.set('seenVersion', APP_VERSION);
      // первый запуск после установки не считаем обновлением, а старые версии оставляли u.updChecked
      if (seen || store.get('updChecked')) setTimeout(() => showNews(APP_CHANGES.filter(x => !seen || isNewer(x.version, seen)), `Universal обновился до ${APP_VERSION}`), 600);
    }
  }



  /* ===================== голос в текст через компьютер ===================== */
  // файл уходит на сервер Universal (/api/stt) нативным HTTP как multipart (base64 внутри CapacitorHttp)
  const b64OfFile = f => new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(',')[1] || ''); r.onerror = () => rej(new Error('файл не прочитался'));
    r.readAsDataURL(f);
  });
  let sttBusy = false;
  async function sttRun(name, b64, mime, blob) {
    if (sttBusy) { snack('Уже распознаю, подожди'); return; }
    const srv = baseUrl(store.get('serverUrl'));
    if (!srv) { snack('Сначала подключи компьютер на вкладке «Сервер»', 'Открыть', () => go('pageServer')); return; }
    sttBusy = true;
    const bar = $('sttJob').querySelector('.bar2'), msg = t => { $('sttMsg').textContent = t; };
    $('sttJob').hidden = false; $('sttOut').hidden = true; bar.classList.add('ind'); bar.firstChild.style.width = '';
    msg(`Отправляю «${name}» на ${host(srv)}…`);
    const auth = serverAuth(), model = qs('input[name=sttm]:checked').value, lang = $('sttLang').value;
    try {
      let r;
      if (native) {
        const resp = await Http.request({ method: 'POST', url: srv + 'api/stt', headers: auth, dataType: 'formData', connectTimeout: 15000, readTimeout: 300000,
          data: [{ key: 'model', value: model, type: 'string' }, { key: 'lang', value: lang, type: 'string' }, { key: 'srt', value: '0', type: 'string' },
                 { key: 'files', value: b64, type: 'base64File', fileName: name, contentType: mime || 'application/octet-stream' }] });
        let d = resp.data; if (typeof d === 'string') { try { d = JSON.parse(d); } catch (_) { /* не JSON */ } }
        r = { status: resp.status, data: d };
      } else {
        const fd = new FormData(); fd.append('model', model); fd.append('lang', lang); fd.append('files', blob, name);
        const resp = await fetch(srv + 'api/stt', { method: 'POST', headers: auth, body: fd });
        r = { status: resp.status, data: await resp.json().catch(() => null) };
      }
      if (r.status === 401) throw new Error('компьютер не пустил: неверный пароль (вкладка «Сервер»)');
      if (r.status === 404) throw new Error('на компьютере старая версия Universal: обнови её до 2.3 или новее');
      if (r.status >= 400 || !r.data || !r.data.task) throw new Error((r.data && r.data.error) || `компьютер ответил ${r.status}`);
      let t = r.data;
      while (!['done', 'error', 'cancelled'].includes(t.status)) {
        await sleep(1000);
        const q = await http('GET', `${srv}api/task/${t.task}`, { headers: auth });
        if (q.status !== 200 || !q.data) throw new Error((q.data && q.data.error) || `компьютер ответил ${q.status}`);
        t = q.data;
        if (t.progress != null) { bar.classList.remove('ind'); bar.firstChild.style.width = Math.round(t.progress * 100) + '%'; }
        msg('Компьютер: ' + (t.message || '…'));
      }
      if (t.status === 'error') throw new Error(t.error || 'ошибка на компьютере');
      const tr = t.result && t.result.transcripts && t.result.transcripts[0];
      if (!tr) throw new Error(((t.failed || [])[0] || {}).error || 'не получилось распознать');
      $('sttText').textContent = tr.text || 'Речи не найдено';
      $('sttOut').hidden = false; $('sttJob').hidden = true;
      $('sttCopy').onclick = () => Clipboard.write({ string: tr.text }).then(() => snack('Текст скопирован')).catch(() => navigator.clipboard.writeText(tr.text));
      $('sttShare').onclick = () => (native ? Share.share({ text: tr.text }) : navigator.share ? navigator.share({ text: tr.text }) : null);
    } catch (e) {
      msg('Не получилось: ' + (e.message || e)); bar.hidden = false; bar.classList.remove('ind');
    } finally {
      sttBusy = false;
    }
  }
  $('sttPick').onchange = async () => {
    const f = $('sttPick').files[0]; $('sttPick').value = '';
    if (!f) return;
    if (f.size > 200 * 1024 * 1024) { snack('Файл больше 200 МБ: открой его на компьютере'); return; }
    try { await sttRun(f.name, native ? await b64OfFile(f) : '', f.type, f); } catch (e) { snack(e.message || String(e)); }
  };
  (() => {
    let saved = {};
    try { saved = JSON.parse(store.get('stt') || '{}'); } catch (_) { /* битое */ }
    if (saved.lang != null) $('sttLang').value = saved.lang;
    if (saved.model && $('sttm' + ({ fast: 1, normal: 2, best: 3 }[saved.model] || 2))) $('sttm' + { fast: 1, normal: 2, best: 3 }[saved.model]).checked = true;
    $('stt').addEventListener('change', e => { if (e.target.id !== 'sttPick') store.set('stt', JSON.stringify({ lang: $('sttLang').value, model: qs('input[name=sttm]:checked').value })); });
    if (platform === 'android') $('sttShareHint').textContent = ' Можно и так: в Telegram зажми голосовое → «Поделиться» → Universal.';
  })();

  /* ===================== «Поделиться → Universal» (Android) ===================== */
  if (platform === 'android') {
    const ShareIn = C.registerPlugin('ShareIn');
    ShareIn.addListener('shared', async ({ text, file, name, mime }) => {
      if (file) {                    // голосовое или видео из Telegram/WhatsApp: сразу в текст
        go('pageTools'); $('stt').scrollIntoView({ block: 'start' });
        try {
          const { data } = await Filesystem.readFile({ path: file });
          await sttRun(name || 'voice.ogg', data, mime);
        } catch (e) { snack('Не открылся файл: ' + (e.message || e)); }
        return;
      }
      if (applyConnect(text)) return;
      go('pageDl');
      if (addUrls(text)) snack('Ссылка добавлена', 'Скачать', start);
    }).catch(() => {});
  }

  syncDl();
})();
