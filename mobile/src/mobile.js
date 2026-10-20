/*
 * Universal для телефона: скачивание видео (свой cobalt или свой сервер Universal),
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

  const TITLES = { pageDl: 'Скачать', pageTools: 'Инструменты', pageFiles: 'Файлы', pageServer: 'Сервер' };
  function go(page) {
    document.querySelectorAll('.page').forEach(p => { p.hidden = p.id !== page; });
    document.querySelectorAll('nav.navbar button').forEach(b => {
      if (b.dataset.page === page) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    $('title').textContent = TITLES[page];
    window.scrollTo(0, 0);
    if (page === 'pageFiles') listFiles();
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

  /* ===================== скачивание через cobalt ===================== */
  function cobaltError(d) {
    const code = (d && d.error && d.error.code) || '';
    const short = code.replace(/^error\.api\./, '');
    return COBALT_ERRORS[short] || (short ? 'ошибка ' + short : 'неизвестная ошибка');
  }
  function cobaltHeaders() {
    const key = store.get('cobaltKey').trim(), h = { Accept: 'application/json', 'Content-Type': 'application/json' };
    if (key) h.Authorization = key.includes(' ') ? key : 'Api-Key ' + key;
    return h;
  }
  async function viaCobalt(url, o, job, stop) {
    const api = baseUrl(store.get('cobaltUrl'));
    if (!api) throw new Error('укажи адрес сервера cobalt в настройках внизу или выбери «мой сервер Universal»');
    const body = { url, filenameStyle: 'pretty', videoQuality: o.quality === 'best' ? 'max' : o.quality };
    if (o.mode === 'audio') {
      body.downloadMode = 'audio';
      body.audioFormat = ['mp3', 'opus', 'wav'].includes(o.audio) ? o.audio : 'best';
      body.audioBitrate = '320';
    } else {
      body.youtubeVideoCodec = 'h264';                // открывается в галерее любого телефона
      body.youtubeVideoContainer = 'mp4';
    }
    job.msg('Спрашиваю cobalt…');
    const r = await http('POST', api, { headers: cobaltHeaders(), data: body });
    const d = r.data && typeof r.data === 'object' ? r.data : {};
    if (d.status === 'error' || r.status >= 400) throw new Error('cobalt: ' + cobaltError(d));
    let items;
    if (d.status === 'tunnel' || d.status === 'redirect') items = [{ url: d.url, name: d.filename }];
    else if (d.status === 'picker') {
      const stamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-');
      items = (d.picker || []).map((p, i) => ({ url: p.url,
        name: `cobalt_${stamp}_${i + 1}.${p.type === 'photo' ? 'jpg' : p.type === 'gif' ? 'gif' : 'mp4'}` }));
      if (d.audio) items.push({ url: d.audio, name: d.audioFilename || `cobalt_${stamp}.mp3` });
    } else if (d.status === 'local-processing') {
      throw new Error('cobalt: этот сервер просит склеивать файл на устройстве, так приложение не умеет. Выбери «мой сервер Universal»');
    } else {
      throw new Error(`cobalt ответил непонятно (статус ${d.status || r.status}). Это точно адрес API cobalt?`);
    }
    const files = [];
    for (const it of items) {
      if (stop()) break;
      const f = await fetchTo(it.url, it.name, {}, job);
      if (!f) throw new Error(EMPTY_TUNNEL + '. Попробуй через «мой сервер Universal»');
      files.push(f);
    }
    return files;
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

  /* ===================== кнопка «Скачать» ===================== */
  const URL_RE = /https?:\/\/[^\s<>"']+/gi;
  const opts = () => ({ mode: qs('input[name=dm]:checked').value, quality: $('dQ').value, audio: $('dA').value, via: $('dVia').value });
  let running = false, stopAsked = false;
  function syncDl() {
    const o = opts();
    $('dQRow').hidden = o.mode === 'audio'; $('dARow').hidden = o.mode !== 'audio';
    $('dViaHint').textContent = o.via === 'server'
      ? `Качает твой Universal (yt-dlp и cobalt, с его VPN и настройками), потом файл приезжает на телефон. Сервер: ${host(baseUrl(store.get('serverUrl'))) || 'не указан, вкладка «Сервер»'}.`
      : `Качает сервер cobalt: ${host(baseUrl(store.get('cobaltUrl'))) || 'не указан, открой «Настройки cobalt» ниже'}.`;
    $('dGo').textContent = running ? (stopAsked ? 'Останавливаю…' : 'Остановить') : 'Скачать';
    $('dGo').disabled = running && stopAsked;
    for (const k of ['mode', 'quality', 'audio', 'via']) store.set('dl.' + k, o[k]);
  }
  async function start() {
    if (running) { stopAsked = true; syncDl(); return; }
    const urls = [...new Set(($('dUrls').value.match(URL_RE) || []))];
    if (!urls.length) { snack('Не нашёл ссылок: вставь адрес, начинающийся с http'); return; }
    const o = opts(), stop = () => stopAsked;
    running = true; stopAsked = false; syncDl();
    try {
      if (o.via === 'server') {
        const job = jobRow(urls.length === 1 ? urls[0] : `Ссылок: ${urls.length}`);
        try { const { files, errors } = await viaServer(urls, o, job, stop); job.finish(files, errors); }
        catch (e) { job.finish([], [String(e.message || e)]); }
      } else {
        for (const u of urls) {
          if (stopAsked) break;
          const job = jobRow(u);
          try { job.finish(await viaCobalt(u, o, job, stop), []); }
          catch (e) { job.finish([], [String(e.message || e)]); }
        }
      }
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
  document.querySelectorAll('input[name=dm]').forEach(r => r.addEventListener('change', syncDl));
  ['dQ', 'dA', 'dVia'].forEach(id => $(id).addEventListener('change', syncDl));
  {
    const m = store.get('dl.mode'); if (m) { const r = qs(`input[name=dm][value="${m}"]`); if (r) r.checked = true; }
    for (const [k, id] of [['quality', 'dQ'], ['audio', 'dA'], ['via', 'dVia']]) {
      const v = store.get('dl.' + k); if (v && [...$(id).options].some(op => op.value === v)) $(id).value = v;
    }
  }

  /* ===================== настройки cobalt ===================== */
  $('cUrl').value = store.get('cobaltUrl'); $('cKey').value = store.get('cobaltKey');
  $('cUrl').addEventListener('input', () => { store.set('cobaltUrl', $('cUrl').value.trim()); syncDl(); });
  $('cKey').addEventListener('input', () => store.set('cobaltKey', $('cKey').value.trim()));
  if (!store.get('cobaltUrl')) $('dSettings').open = true;
  $('cCheck').onclick = async () => {
    const api = baseUrl($('cUrl').value), st = $('cState');
    if (!api) { st.className = 'small grow bad'; st.textContent = 'Укажи адрес'; return; }
    st.className = 'small grow'; st.textContent = 'Проверяю…';
    try {
      const r = await http('GET', api, { headers: cobaltHeaders() });
      const info = r.data && r.data.cobalt;
      if (!info) throw new Error('это не сервер cobalt (нужен адрес API, а не сайта)');
      st.className = 'small grow ok';
      st.textContent = `cobalt ${info.version || ''} работает` + (info.services ? `, сайтов: ${info.services.length}` : '');
    } catch (e) { st.className = 'small grow bad'; st.textContent = String(e.message || e); }
  };

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
      const eng = r.data.engines || {}, have = Object.keys(eng).filter(k => eng[k]);
      st.className = 'small grow ok';
      st.textContent = 'Работает' + (have.length ? `: ${have.join(', ')}` : '');
    } catch (e) { st.className = 'small grow bad'; st.textContent = String(e.message || e); }
  };
  $('sOpen').onclick = async () => {
    const srv = baseUrl($('sUrl').value);
    if (!srv) { snack('Сначала укажи адрес сервера'); return; }
    if (native) await Browser.open({ url: srv }); else window.open(srv, '_blank');
  };
  $('ver').textContent = typeof APP_VERSION === 'string' ? APP_VERSION : '';
  $('gh').onclick = e => { if (native) { e.preventDefault(); Browser.open({ url: e.currentTarget.href }); } };

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

  /* ===================== «Поделиться → Universal» (Android) ===================== */
  if (platform === 'android') {
    const ShareIn = C.registerPlugin('ShareIn');
    ShareIn.addListener('shared', ({ text }) => {
      go('pageDl');
      if (addUrls(text)) snack('Ссылка добавлена', 'Скачать', start);
    }).catch(() => {});
  }

  syncDl();
})();
