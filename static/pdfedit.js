/*
 * Редактор PDF прямо в окне приложения: pdf.js (static/vendor/pdfjs, Apache-2.0) рисует страницы,
 * pdf-lib (static/vendor/pdflib, MIT) собирает новый файл. Сервер не нужен.
 *
 * Страницы: порядок перетаскиванием, поворот, удаление, пустая страница, страницы из другого PDF.
 * Поверх страницы: текст, перо, маркер, рамка, «замазать», картинка, подпись.
 * Объекты хранятся в пунктах в системе «как страница выглядит без нашего поворота» (базовый вид),
 * при сохранении накладываются картинкой PNG только на занятую область: исходный текст
 * и векторная графика остаются как были. Режим «сплющить» превращает страницы в картинки:
 * так замазанное исчезает из файла по-настоящему.
 */
(() => {
  'use strict';
  const view = document.getElementById('pdfView');
  if (!view) return;
  const V = '/static/vendor/';
  const FONT = 'Arial, "Helvetica Neue", "Liberation Sans", sans-serif';
  const TOOLS = [
    ['select', 'Выбор', 'M480-80 360-200l56-56 64 63 64-63 56 56L480-80ZM200-320 80-440l120-120 56 56-63 64 63 64-56 56Zm560 0-56-56 63-64-63-64 56-56 120 120-120 120ZM480-400q-33 0-56.500-23.500T400-480q0-33 23.500-56.500T480-560q33 0 56.500 23.500T560-480q0 33-23.500 56.500T480-400Zm-64-304-56-56 120-120 120 120-56 56-64-63-64 63Z'],
    ['text', 'Текст', 'M420-160v-520H200v-120h560v120H540v520H420Z'],
    ['pen', 'Перо', 'M160-120v-170l527-526q12-12 27-18t30-6q16 0 30.500 6t25.500 18l56 57q12 11 17.500 25.500T879-704q0 15-5.500 30T856-647L330-120H160Zm80-80h56l393-392-28-29-29-28-392 393v56Z'],
    ['hl', 'Маркер', 'M80 0v-160h800V0H80Zm504-480L480-584 320-424l103 104 161-160Zm-47-160 103 103 160-159-104-104-159 160Zm-84-29 216 216-189 190q-24 24-56.500 24T367-263l-27 23H140l126-125q-24-24-25-57.500t23-57.500l189-189Zm0 0 187-187q24-24 56.500-24t56.500 24l104 103q24 24 24 56.500T857-460L669-453 453-669Z'],
    ['rect', 'Рамка', 'M200-120q-33 0-56.500-23.500T120-200v-560q0-33 23.500-56.500T200-840h560q33 0 56.500 23.500T840-760v560q0 33-23.500 56.500T760-120H200Zm0-80h560v-560H200v560Z'],
    ['white', 'Замазать', 'M690-240h190v80H610l80-80Zm-500 80-85-85q-23-23-23.500-57t22.500-58l440-456q23-24 56.500-24t56.500 23l199 199q23 23 23 57t-23 57L520-160H190Zm296-80 314-322-198-198-442 456 64 64h262Z'],
  ];
  const TEXT_SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 64];
  const LINE_W = [1, 1.5, 2, 3, 4, 6, 8, 12];
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const icon = d => `<svg viewBox="0 -960 960 960"><path d="${d}"/></svg>`;

  // sources: открытые PDF {bytes, js (pdf.js), lib (pdf-lib или null), name}
  // pages: {src (индекс или -1 для пустой), idx, base (поворот из файла), rot (наш), bw, bh (размер базового вида, pt), objs}
  let pdfjs = null, libs = null, sources = [], pages = [], cur = 0, zoom = 0, tool = 'select', sel = null;
  let history = [], ta = null, taNew = false, editing = null, drag = null, name = 'document';
  const IMGS = {};   // картинки и подписи: id -> canvas (в историю не копируются)
  let imgSeq = 0;

  view.innerHTML = `
  <div class="pdx">
    <div class="card pdx-bar">
      <label class="btn tonal state" for="pdxPick">Открыть PDF…</label>
      <input type="file" id="pdxPick" accept="application/pdf,.pdf" multiple hidden>
      <span class="pe-sep"></span>
      ${TOOLS.map(([k, l, d]) => `<button class="icon-btn state pdx-tool" data-tool="${k}" title="${l}" aria-label="${l}">${icon(d)}</button>`).join('')}
      <label class="icon-btn state" for="pdxImg" title="Картинка" aria-label="Картинка">${icon('M200-120q-33 0-56.500-23.500T120-200v-560q0-33 23.500-56.500T200-840h560q33 0 56.500 23.500T840-760v560q0 33-23.500 56.500T760-120H200Zm0-80h560v-560H200v560Zm40-80h480L570-480 450-320l-90-120-120 160Zm-40 80v-560 560Z')}</label>
      <input type="file" id="pdxImg" accept="image/*" hidden>
      <button class="icon-btn state" id="pdxSign" title="Подпись" aria-label="Подпись">${icon('M563-491q73-54 114-118.500T718-738q0-32-10.500-47T679-800q-47 0-83 79.500T560-541q0 14 .500 26.500T563-491ZM120-120v-80h80v80h-80Zm160 0v-80h80v80h-80Zm160 0v-80h80v80h-80Zm160 0v-80h80v80h-80Zm160 0v-80h80v80h-80ZM136-280l-56-56 64-64-64-64 56-56 64 64 64-64 56 56-64 64 64 64-56 56-64-64-64 64Zm482-40q-30 0-55-11.500T520-369q-25 14-51.500 25T414-322l-28-75q28-10 53.500-21.500T489-443q-5-22-7.500-48t-2.500-56q0-144 57-238.500T679-880q52 0 85 38.500T797-734q0 86-54.500 170T591-413q7 7 14.500 10.500T621-399q26 0 60.500-33t62.500-87l73 34q-7 17-11 41t1 42q10-5 23.500-17t27.500-30l63 49q-26 36-60 58t-63 22q-21 0-37.500-12.500T733-371q-28 25-57 38t-58 13Z')}</button>
      <input type="color" class="pdx-color" id="pdxColor" value="#d32f2f" title="Цвет" aria-label="Цвет">
      <label class="tf pdx-mini" id="pdxSizeBox"><span>Кегль</span><select id="pdxSize">${TEXT_SIZES.map(s => `<option ${s === 14 ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
      <label class="tf pdx-mini" id="pdxWBox" hidden><span>Толщина</span><select id="pdxW">${LINE_W.map(s => `<option ${s === 2 ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
      <span class="pe-sep"></span>
      <button class="icon-btn state" id="pdxUndo" title="Отменить (Ctrl+Z)" aria-label="Отменить">${icon('M280-200v-80h284q63 0 109.500-40T720-420q0-60-46.500-100T564-560H312l104 104-56 56-200-200 200-200 56 56-104 104h252q97 0 166.500 63T800-420q0 94-69.500 157T564-200H280Z')}</button>
      <button class="icon-btn state" id="pdxDel" title="Удалить выбранное (Delete)" aria-label="Удалить выбранное">${icon('M280-120q-33 0-56.500-23.500T200-200v-520h-40v-80h200v-40h240v40h200v80h-40v520q0 33-23.500 56.500T680-120H280Zm400-600H280v520h400v-520ZM360-280h80v-360h-80v360Zm160 0h80v-360h-80v360ZM280-720v520-520Z')}</button>
      <span class="pe-sep"></span>
      <button class="icon-btn state" id="pdxZoomOut" title="Мельче" aria-label="Мельче">${icon('M200-440v-80h560v80H200Z')}</button>
      <button class="btn text state pdx-zoom" id="pdxZoomFit" title="Вся страница">100%</button>
      <button class="icon-btn state" id="pdxZoomIn" title="Крупнее" aria-label="Крупнее">${icon('M440-440H200v-80h240v-240h80v240h240v80H520v240h-80v-240Z')}</button>
      <span class="t-body-m muted pdx-info" id="pdxInfo"></span>
    </div>
    <div class="pdx-main">
      <aside class="card pdx-side">
        <div class="pdx-pagebtns">
          <button class="btn text state" id="pdxRotL" type="button" title="Повернуть страницу влево">⟲</button>
          <button class="btn text state" id="pdxRotR" type="button" title="Повернуть страницу вправо">⟳</button>
          <button class="btn text state" id="pdxBlank" type="button" title="Пустая страница после текущей">+ лист</button>
          <label class="btn text state" for="pdxAdd" title="Добавить страницы из другого PDF в конец">+ PDF</label>
          <input type="file" id="pdxAdd" accept="application/pdf,.pdf" multiple hidden>
          <button class="btn text state" id="pdxDelPage" type="button" title="Удалить страницу">Удалить</button>
        </div>
        <div class="pdx-thumbs" id="pdxThumbs"></div>
      </aside>
      <div class="card pdx-stage" id="pdxStage">
        <label class="pe-empty" for="pdxPick" id="pdxEmpty"><strong>Перетащи PDF сюда или нажми, чтобы выбрать</strong>
          <span class="t-body-m muted">Добавить текст и подпись, замазать, выделить маркером, переставить, повернуть и удалить страницы.
          Несколько PDF склеятся по порядку.</span></label>
        <div class="pdx-page" id="pdxPage" hidden><canvas id="pdxCanvas"></canvas><canvas id="pdxOver"></canvas></div>
      </div>
    </div>
    <div class="card pdx-save">
      <label class="tf"><span>Имя файла</span><input type="text" id="pdxName" autocomplete="off"></label>
      <label class="sw"><span class="t-body-m">Сплющить в картинки: замазанный текст удалится из файла по-настоящему, но выделять текст станет нельзя</span>
        <input type="checkbox" id="pdxFlat"><span class="track"></span></label>
      <button class="btn filled lg state" id="pdxSave" type="button" disabled>Сохранить PDF</button>
    </div>
  </div>
  <dialog class="pdx-sign" id="pdxSignDlg">
    <form method="dialog" class="pdx-signbox">
      <strong>Распишись мышью или пальцем</strong>
      <canvas id="pdxSignPad" width="900" height="300"></canvas>
      <div class="row-btns"><button class="btn text state" value="clear" id="pdxSignClear" type="button">Очистить</button>
        <span style="flex:1"></span><button class="btn text state" value="cancel">Отмена</button>
        <button class="btn filled state" value="ok" id="pdxSignOk">Поставить</button></div>
    </form>
  </dialog>`;
  const $ = id => document.getElementById(id);
  const info = (t, bad) => { $('pdxInfo').textContent = t || ''; $('pdxInfo').classList.toggle('err', !!bad); };
  const canvas = $('pdxCanvas'), over = $('pdxOver'), octx = over.getContext('2d');

  /* ---------- библиотеки ---------- */
  function loadLibs() {
    if (libs) return libs;
    libs = (async () => {
      pdfjs = await import(V + 'pdfjs/pdf.min.js');
      pdfjs.GlobalWorkerOptions.workerSrc = V + 'pdfjs/pdf.worker.min.js';
      await new Promise((res, rej) => {
        const s = document.createElement('script');
        s.src = V + 'pdflib/pdf-lib.min.js'; s.onload = res; s.onerror = () => rej(new Error('не загрузился pdf-lib'));
        document.head.appendChild(s);
      });
    })();
    libs.catch(() => { libs = null; });
    return libs;
  }

  /* ---------- геометрия: базовый вид <-> экран ---------- */
  const P = () => pages[cur];
  const dispSize = p => (p.rot % 180 ? [p.bh, p.bw] : [p.bw, p.bh]);
  function toBase(p, dx, dy) {                         // точка на экране (pt) -> базовый вид
    switch (p.rot) {
      case 90: return [dy, p.bh - dx];
      case 180: return [p.bw - dx, p.bh - dy];
      case 270: return [p.bw - dy, dx];
      default: return [dx, dy];
    }
  }
  function toDisp(p, x, y) {
    switch (p.rot) {
      case 90: return [p.bh - y, x];
      case 180: return [p.bw - x, p.bh - y];
      case 270: return [y, p.bw - x];
      default: return [x, y];
    }
  }
  function baseTransform(g, p, s) {                  // рисовать в базовом виде на повёрнутом холсте
    g.setTransform(s, 0, 0, s, 0, 0);
    if (p.rot === 90) { g.translate(p.bh, 0); g.rotate(Math.PI / 2); }
    else if (p.rot === 180) { g.translate(p.bw, p.bh); g.rotate(Math.PI); }
    else if (p.rot === 270) { g.translate(0, p.bw); g.rotate(-Math.PI / 2); }
  }

  /* ---------- объекты ---------- */
  const measure = document.createElement('canvas').getContext('2d');
  function bbox(o) {
    if (o.t === 'text') {
      measure.font = `${o.bold ? '700 ' : ''}${o.size}px ${FONT}`;
      const lines = o.text.split('\n');
      return { x: o.x, y: o.y, w: Math.max(4, ...lines.map(l => measure.measureText(l).width)), h: lines.length * o.size * 1.25 };
    }
    if (o.t === 'ink') {
      const xs = o.pts.map(p => p[0]), ys = o.pts.map(p => p[1]), r = o.w / 2;
      const x = Math.min(...xs) - r, y = Math.min(...ys) - r;
      return { x, y, w: Math.max(...xs) + r - x, h: Math.max(...ys) + r - y };
    }
    return { x: o.x, y: o.y, w: o.w, h: o.h };
  }
  function drawObj(g, o) {
    g.save();
    if (o.t === 'text') {
      g.font = `${o.bold ? '700 ' : ''}${o.size}px ${FONT}`; g.fillStyle = o.color; g.textBaseline = 'top';
      o.text.split('\n').forEach((l, i) => g.fillText(l, o.x, o.y + i * o.size * 1.25 + o.size * 0.1));
    } else if (o.t === 'ink') {
      g.globalAlpha = o.alpha || 1; g.strokeStyle = o.color; g.lineWidth = o.w; g.lineCap = o.alpha ? 'butt' : 'round'; g.lineJoin = 'round';
      g.beginPath(); o.pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
      if (o.pts.length === 1) g.lineTo(o.pts[0][0] + 0.01, o.pts[0][1]);
      g.stroke();
    } else if (o.t === 'rect') {
      g.strokeStyle = o.color; g.lineWidth = o.lw; g.strokeRect(o.x + o.lw / 2, o.y + o.lw / 2, Math.max(0, o.w - o.lw), Math.max(0, o.h - o.lw));
    } else if (o.t === 'white') {
      g.fillStyle = o.color || '#fff'; g.fillRect(o.x, o.y, o.w, o.h);
    } else if (o.t === 'img' && IMGS[o.ref]) {
      g.drawImage(IMGS[o.ref], o.x, o.y, o.w, o.h);
    }
    g.restore();
  }
  function hit(p, x, y) {
    for (let i = p.objs.length - 1; i >= 0; i--) {
      const b = bbox(p.objs[i]), pad = 4;
      if (x >= b.x - pad && x <= b.x + b.w + pad && y >= b.y - pad && y <= b.y + b.h + pad) return p.objs[i];
    }
    return null;
  }

  /* ---------- история ---------- */
  const snap = () => JSON.stringify({ pages, cur });
  function remember() { history.push(snap()); if (history.length > 60) history.shift(); }
  function undo() {
    if (!history.length) return;
    const s = JSON.parse(history.pop()); pages = s.pages; cur = Math.min(s.cur, pages.length - 1); sel = null;
    renderThumbs(); renderPage();
  }

  /* ---------- открыть ---------- */
  async function readSource(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let js;
    try { js = await pdfjs.getDocument({ data: bytes.slice(), cMapUrl: V + 'pdfjs/cmaps/', cMapPacked: true, standardFontDataUrl: V + 'pdfjs/standard_fonts/' }).promise; }
    catch (e) {
      if (e && e.name === 'PasswordException') throw new Error(`«${file.name}» под паролем. Сними его в «PDF-инструменты → Снять пароль» и открой снова`);
      throw new Error(`«${file.name}» не читается как PDF`);
    }
    let lib = null, warn = '';
    try { lib = await window.PDFLib.PDFDocument.load(bytes, { updateMetadata: false }); }
    catch (e) {
      if (/encrypt/i.test(e && e.message)) warn = `«${file.name}» защищён от изменений: сохранится только «сплющенным» в картинки. Чтобы сохранить текст, сними защиту в «PDF-инструменты → Снять пароль».`;
      else warn = `«${file.name}» повреждён: сохранится только «сплющенным» в картинки.`;
    }
    const src = sources.push({ bytes, js, lib, name: file.name }) - 1;
    const add = [];
    for (let i = 0; i < js.numPages; i++) {
      const pg = await js.getPage(i + 1), vp = pg.getViewport({ scale: 1 });
      add.push({ src, idx: i, base: pg.rotate % 360, rot: 0, bw: vp.width, bh: vp.height, objs: [] });
    }
    return { add, warn };
  }
  async function openFiles(list, append) {
    const pdfs = [...list].filter(f => /\.pdf$/i.test(f.name) || f.type === 'application/pdf');
    if (!pdfs.length) { info('Это не PDF', true); return; }
    info('Открываю…');
    try { await loadLibs(); } catch (e) { info('Не загрузился редактор: ' + e.message, true); return; }
    if (!append) { sources.forEach(s => s.js.destroy()); sources = []; pages = []; history = []; cur = 0; sel = null; zoom = 0; }
    else remember();
    const warns = [];
    for (const f of pdfs) {
      try { const r = await readSource(f); pages.push(...r.add); if (r.warn) warns.push(r.warn); }
      catch (e) { warns.push(e.message); }
    }
    if (!pages.length) { info(warns.join(' ') || 'Не открылось', true); return; }
    if (!append) { name = pdfs[0].name.replace(/\.pdf$/i, '') || 'document'; $('pdxName').value = name + '_edit'; }
    $('pdxEmpty').hidden = true; $('pdxPage').hidden = false; $('pdxSave').disabled = false;
    if (sources.some(s => !s.lib)) { $('pdxFlat').checked = true; }
    renderThumbs(); await renderPage();
    info(warns.length ? warns.join(' ') : `${pages.length} стр.`, warns.length > 0);
  }
  $('pdxPick').onchange = () => { const f = $('pdxPick').files; if (f.length) openFiles([...f], false); $('pdxPick').value = ''; };
  $('pdxAdd').onchange = () => { const f = $('pdxAdd').files; if (f.length) openFiles([...f], pages.length > 0); $('pdxAdd').value = ''; };
  const stage = $('pdxStage');
  ['dragenter', 'dragover'].forEach(e => stage.addEventListener(e, ev => { if (ev.dataTransfer.types.includes('Files')) { ev.preventDefault(); stage.classList.add('over'); } }));
  ['dragleave', 'drop'].forEach(e => stage.addEventListener(e, () => stage.classList.remove('over')));
  stage.addEventListener('drop', ev => { if (!ev.dataTransfer.files.length) return; ev.preventDefault(); openFiles([...ev.dataTransfer.files], pages.length > 0); });

  /* ---------- отрисовка страницы ---------- */
  function fitZoom() {
    const p = P(); if (!p) return 1;
    // страница целиком в окне: по ширине области и по высоте экрана
    const [w, h] = dispSize(p), availW = stage.clientWidth - 32, availH = Math.max(300, innerHeight - 190);
    return Math.max(0.25, Math.min(3, availW / w, availH / h));
  }
  async function paintPage(p, c, scale) {           // сама страница без наших объектов, scale = пикселей на пункт
    if (c._task) { c._task.cancel(); c._task = null; }
    const [w, h] = dispSize(p), g = c.getContext('2d');
    c.width = Math.max(1, Math.round(w * scale)); c.height = Math.max(1, Math.round(h * scale));
    g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height);
    if (p.src < 0) return;
    const pg = await sources[p.src].js.getPage(p.idx + 1);
    const vp = pg.getViewport({ scale: c.width / w, rotation: (p.base + p.rot) % 360 });
    const task = c._task = pg.render({ canvasContext: g, viewport: vp });
    try { await task.promise; } catch (e) { if (e && e.name !== 'RenderingCancelledException') throw e; return false; }
    finally { if (c._task === task) c._task = null; }
    return true;
  }
  async function renderPage() {
    const p = P(); if (!p) return;
    const z = zoom || fitZoom(), dpr = devicePixelRatio || 1, [w, h] = dispSize(p);
    $('pdxZoomFit').textContent = Math.round(z * 100) + '%';
    for (const c of [canvas, over]) { c.style.width = w * z + 'px'; c.style.height = h * z + 'px'; }
    over.width = Math.round(w * z * dpr); over.height = Math.round(h * z * dpr);
    drawOverlay();
    markThumb();
    try { await paintPage(p, canvas, z * dpr); } catch (e) { info('Страница не отрисовалась: ' + (e.message || e), true); }
  }
  function drawOverlay() {
    const p = P(); if (!p) return;
    const s = over.width / dispSize(p)[0];
    octx.setTransform(1, 0, 0, 1, 0, 0); octx.clearRect(0, 0, over.width, over.height);
    baseTransform(octx, p, s);
    p.objs.forEach(o => { if (o !== editing) drawObj(octx, o); });
    if (drag && drag.preview) drawObj(octx, drag.preview);
    if (sel && p.objs.includes(sel)) {
      const b = bbox(sel);
      octx.save(); octx.lineWidth = 1.5 / s; octx.setLineDash([4 / s, 3 / s]); octx.strokeStyle = '#1a73e8';
      octx.strokeRect(b.x - 2, b.y - 2, b.w + 4, b.h + 4);
      if (sel.t !== 'text' && sel.t !== 'ink') { octx.setLineDash([]); octx.fillStyle = '#1a73e8'; const r = 5 / s; octx.fillRect(b.x + b.w - r, b.y + b.h - r, 2 * r, 2 * r); }
      octx.restore();
    }
  }

  /* ---------- миниатюры ---------- */
  const thumbsEl = $('pdxThumbs');
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { io.unobserve(e.target); paintThumb(+e.target.dataset.i); } }), { root: thumbsEl, rootMargin: '200px' });
  function renderThumbs() {
    io.disconnect();
    thumbsEl.innerHTML = pages.map((p, i) => `<div class="pdx-thumb" draggable="true" data-i="${i}"><canvas></canvas><span class="t-label-m">${i + 1}</span></div>`).join('');
    thumbsEl.querySelectorAll('.pdx-thumb').forEach(el => io.observe(el));
    markThumb();
  }
  async function paintThumb(i) {
    const el = thumbsEl.children[i], p = pages[i]; if (!el || !p) return;
    const c = el.querySelector('canvas'), scale = 110 / dispSize(p)[0];
    try {
      await paintPage(p, c, scale);
      const g = c.getContext('2d'); baseTransform(g, p, scale); p.objs.forEach(o => drawObj(g, o));
    } catch (_) { /* миниатюра не важна */ }
  }
  let thumbTimer = 0;
  const refreshThumb = () => { clearTimeout(thumbTimer); const i = cur; thumbTimer = setTimeout(() => paintThumb(i), 300); };
  function markThumb() {
    thumbsEl.querySelectorAll('.pdx-thumb').forEach(el => el.classList.toggle('on', +el.dataset.i === cur));
    const on = thumbsEl.children[cur]; if (on) on.scrollIntoView({ block: 'nearest' });
  }
  function go(i) { if (i < 0 || i >= pages.length || i === cur) return; commitText(); cur = i; sel = null; renderPage(); }
  thumbsEl.addEventListener('click', e => { const t = e.target.closest('.pdx-thumb'); if (t) go(+t.dataset.i); });
  let dragFrom = -1;
  thumbsEl.addEventListener('dragstart', e => { const t = e.target.closest('.pdx-thumb'); if (!t) return; dragFrom = +t.dataset.i; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', 'page'); });
  thumbsEl.addEventListener('dragover', e => { if (dragFrom < 0) return; e.preventDefault(); e.stopPropagation(); });
  thumbsEl.addEventListener('drop', e => {
    if (dragFrom < 0) return; e.preventDefault(); e.stopPropagation();
    const t = e.target.closest('.pdx-thumb'), to = t ? +t.dataset.i : pages.length - 1;
    if (to !== dragFrom) {
      remember(); const [p] = pages.splice(dragFrom, 1); pages.splice(to, 0, p); cur = to; renderThumbs(); renderPage();
    }
    dragFrom = -1;
  });
  thumbsEl.addEventListener('dragend', () => { dragFrom = -1; });

  /* ---------- страницы ---------- */
  function rotate(d) { const p = P(); if (!p) return; remember(); commitText(); p.rot = (p.rot + d + 360) % 360; sel = null; renderPage(); paintThumb(cur); }
  $('pdxRotL').onclick = () => rotate(-90);
  $('pdxRotR').onclick = () => rotate(90);
  $('pdxDelPage').onclick = () => {
    if (!pages.length) return;
    if (pages.length === 1) { info('Последнюю страницу не удалить', true); return; }
    remember(); pages.splice(cur, 1); cur = Math.min(cur, pages.length - 1); sel = null; renderThumbs(); renderPage();
  };
  $('pdxBlank').onclick = () => {
    if (!pages.length) return;
    const p = P(), [w, h] = dispSize(p);
    remember(); pages.splice(cur + 1, 0, { src: -1, idx: 0, base: 0, rot: 0, bw: w, bh: h, objs: [] }); cur++; sel = null; renderThumbs(); renderPage();
  };

  /* ---------- инструменты ---------- */
  function setTool(t) {
    commitText(); tool = t; if (t !== 'select') sel = null;
    view.querySelectorAll('.pdx-tool').forEach(b => b.classList.toggle('on', b.dataset.tool === t));
    $('pdxSizeBox').hidden = t !== 'text'; $('pdxWBox').hidden = !['pen', 'hl', 'rect'].includes(t);
    if (t === 'hl') $('pdxColor').value = '#ffeb3b';
    over.style.cursor = { select: 'default', text: 'text' }[t] || 'crosshair';
    drawOverlay();
  }
  view.querySelectorAll('.pdx-tool').forEach(b => { b.onclick = () => setTool(b.dataset.tool); });
  setTool('select');
  function point(e) {
    const r = over.getBoundingClientRect(), p = P(), [w] = dispSize(p), k = w / r.width;
    return toBase(p, (e.clientX - r.left) * k, (e.clientY - r.top) * k);
  }
  // правка выбранного объекта цветом/размером
  $('pdxColor').addEventListener('input', () => { if (sel && sel.t !== 'img') { sel.color = $('pdxColor').value; drawOverlay(); refreshThumb(); } });
  $('pdxSize').addEventListener('change', () => { if (sel && sel.t === 'text') { remember(); sel.size = +$('pdxSize').value; drawOverlay(); refreshThumb(); } });
  $('pdxW').addEventListener('change', () => { if (sel && (sel.t === 'ink' || sel.t === 'rect')) { remember(); sel[sel.t === 'rect' ? 'lw' : 'w'] = +$('pdxW').value; drawOverlay(); refreshThumb(); } });

  over.addEventListener('pointerdown', e => {
    const p = P(); if (!p || e.button > 0) return;
    if (editing) { commitText(); return; }
    const [x, y] = point(e);
    over.setPointerCapture(e.pointerId);
    if (tool === 'select') {
      sel = hit(p, x, y);
      if (sel) {
        const b = bbox(sel), corner = sel.t !== 'text' && sel.t !== 'ink' && Math.abs(x - (b.x + b.w)) < 10 && Math.abs(y - (b.y + b.h)) < 10;
        drag = { mode: corner ? 'size' : 'move', x, y, orig: JSON.stringify(sel), saved: snap() };
        if (sel.t === 'text') { $('pdxSize').value = sel.size; }
        if (sel.color) $('pdxColor').value = sel.color;
      }
      drawOverlay();
    } else if (tool === 'text') {
      e.preventDefault();                                  // иначе фокус уйдёт с поля ввода
      editText({ t: 'text', x, y: y - (+$('pdxSize').value) * 0.6, size: +$('pdxSize').value, color: $('pdxColor').value, text: '' }, true);
    } else if (tool === 'pen' || tool === 'hl') {
      const w = +$('pdxW').value;
      drag = { preview: { t: 'ink', pts: [[x, y]], color: $('pdxColor').value, w: tool === 'hl' ? Math.max(10, w * 5) : w, alpha: tool === 'hl' ? 0.4 : 0 } };
    } else if (tool === 'rect' || tool === 'white') {
      drag = { x, y, preview: tool === 'rect' ? { t: 'rect', x, y, w: 0, h: 0, color: $('pdxColor').value, lw: +$('pdxW').value } : { t: 'white', x, y, w: 0, h: 0, color: '#ffffff' } };
    }
  });
  over.addEventListener('pointermove', e => {
    if (!drag) {
      if (tool === 'select' && P()) {
        const [x, y] = point(e), o = hit(P(), x, y);
        if (o && o === sel && o.t !== 'text' && o.t !== 'ink') { const b = bbox(o); over.style.cursor = Math.abs(x - (b.x + b.w)) < 10 && Math.abs(y - (b.y + b.h)) < 10 ? 'nwse-resize' : 'move'; }
        else over.style.cursor = o ? 'move' : 'default';
      }
      return;
    }
    const [x, y] = point(e);
    if (drag.mode === 'move') {
      const o = JSON.parse(drag.orig), dx = x - drag.x, dy = y - drag.y;
      if (sel.t === 'ink') sel.pts = o.pts.map(([a, b]) => [a + dx, b + dy]); else { sel.x = o.x + dx; sel.y = o.y + dy; }
      drag.moved = true;
    } else if (drag.mode === 'size') {
      const o = JSON.parse(drag.orig); let w = Math.max(8, o.w + x - drag.x), h = Math.max(8, o.h + y - drag.y);
      if (sel.t === 'img') h = w * o.h / o.w;                 // картинки и подписи не растягиваем
      sel.w = w; sel.h = h; drag.moved = true;
    } else if (drag.preview && drag.preview.t === 'ink') {
      drag.preview.pts.push([x, y]);
    } else if (drag.preview) {
      Object.assign(drag.preview, { x: Math.min(x, drag.x), y: Math.min(y, drag.y), w: Math.abs(x - drag.x), h: Math.abs(y - drag.y) });
    }
    drawOverlay();
  });
  over.addEventListener('pointerup', () => {
    if (!drag) return;
    const p = P(), d = drag; drag = null;
    if (d.mode) { if (d.moved) { history.push(d.saved); refreshThumb(); } }
    else if (d.preview) {
      const o = d.preview, ok = o.t === 'ink' || (o.w > 3 && o.h > 3);
      if (ok) { remember(); p.objs.push(o); refreshThumb(); }
    }
    drawOverlay();
  });
  over.addEventListener('dblclick', e => {
    if (tool !== 'select' || !P()) return;
    const [x, y] = point(e), o = hit(P(), x, y);
    if (o && o.t === 'text') editText(o, false);
  });

  /* ---------- текст: поле ввода прямо на странице ---------- */
  function editText(o, isNew) {
    commitText();
    const p = P(), z = over.getBoundingClientRect().width / dispSize(p)[0];
    editing = o; taNew = isNew;
    ta = document.createElement('textarea'); ta.className = 'pdx-ta'; ta.value = o.text;
    const [dx, dy] = toDisp(p, o.x, o.y);
    Object.assign(ta.style, { left: dx * z + 'px', top: dy * z + 'px', fontSize: o.size * z + 'px', color: o.color, fontFamily: FONT, fontWeight: o.bold ? 700 : 400 });
    const fit = () => { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; ta.style.width = 'auto'; ta.style.width = Math.max(60, ta.scrollWidth + 8) + 'px'; };
    ta.addEventListener('input', fit);
    ta.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); if (taNew) ta.value = ''; commitText(); }
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); commitText(); }
      e.stopPropagation();
    });
    ta.addEventListener('blur', () => setTimeout(commitText, 0));
    $('pdxPage').appendChild(ta); fit(); drawOverlay(); ta.focus();
  }
  function commitText() {
    if (!ta || !editing) return;
    const o = editing, text = ta.value.replace(/\s+$/, ''), p = P();
    ta.remove(); ta = null; editing = null;
    if (taNew) { if (text) { remember(); o.text = text; p.objs.push(o); sel = o; } }
    else if (text !== o.text) { remember(); if (text) o.text = text; else p.objs.splice(p.objs.indexOf(o), 1); }
    drawOverlay(); refreshThumb();
  }

  /* ---------- картинка и подпись ---------- */
  function placeImage(c) {
    const p = P(); if (!p) return;
    const id = 'i' + (++imgSeq); IMGS[id] = c;
    const [w0, h0] = [p.bw * 0.3, p.bw * 0.3 * c.height / c.width];
    const [cx, cy] = toBase(p, ...dispSize(p).map(v => v / 2));
    const o = { t: 'img', ref: id, x: cx - w0 / 2, y: cy - h0 / 2, w: w0, h: h0 };
    remember(); p.objs.push(o); setTool('select'); sel = o; drawOverlay(); refreshThumb();
    info('Перетащи картинку на место, за уголок меняется размер');
  }
  $('pdxImg').onchange = async () => {
    const f = $('pdxImg').files[0]; $('pdxImg').value = '';
    if (!f || !P()) return;
    try {
      const bmp = await createImageBitmap(f, { imageOrientation: 'from-image' });
      const k = Math.min(1, 2400 / Math.max(bmp.width, bmp.height)), c = document.createElement('canvas');
      c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k); c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
      placeImage(c);
    } catch (_) { info('Эту картинку браузер не открыл: возьми JPG или PNG', true); }
  };
  const dlg = $('pdxSignDlg'), pad = $('pdxSignPad'), pg2 = pad.getContext('2d');
  let padInk = false;
  function clearPad() { pg2.clearRect(0, 0, pad.width, pad.height); padInk = false; }
  $('pdxSign').onclick = () => { if (!P()) return; clearPad(); dlg.returnValue = ''; dlg.showModal(); };
  $('pdxSignClear').onclick = clearPad;
  let padLast = null;
  pad.addEventListener('pointerdown', e => { pad.setPointerCapture(e.pointerId); const r = pad.getBoundingClientRect(); padLast = [(e.clientX - r.left) * pad.width / r.width, (e.clientY - r.top) * pad.height / r.height]; });
  pad.addEventListener('pointermove', e => {
    if (!padLast) return;
    const r = pad.getBoundingClientRect(), pt = [(e.clientX - r.left) * pad.width / r.width, (e.clientY - r.top) * pad.height / r.height];
    pg2.strokeStyle = $('pdxColor').value === '#ffeb3b' ? '#1a237e' : $('pdxColor').value; pg2.lineWidth = 5; pg2.lineCap = pg2.lineJoin = 'round';
    pg2.beginPath(); pg2.moveTo(...padLast); pg2.lineTo(...pt); pg2.stroke(); padLast = pt; padInk = true;
  });
  pad.addEventListener('pointerup', () => { padLast = null; });
  dlg.addEventListener('close', () => {
    if (dlg.returnValue !== 'ok' || !padInk) return;
    // обрезаем пустые поля вокруг росчерка
    const d = pg2.getImageData(0, 0, pad.width, pad.height).data; let x0 = pad.width, y0 = pad.height, x1 = 0, y1 = 0;
    for (let y = 0; y < pad.height; y++) for (let x = 0; x < pad.width; x++) if (d[(y * pad.width + x) * 4 + 3]) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    const c = document.createElement('canvas'); c.width = x1 - x0 + 9; c.height = y1 - y0 + 9;
    c.getContext('2d').drawImage(pad, x0 - 4, y0 - 4, c.width, c.height, 0, 0, c.width, c.height);
    placeImage(c);
    const o = sel; if (o) { const k = (P().bw * 0.22) / o.w; o.w *= k; o.h *= k; drawOverlay(); refreshThumb(); }
  });

  /* ---------- клавиши, масштаб ---------- */
  function deleteSel() { const p = P(); if (!sel || !p) return; remember(); p.objs.splice(p.objs.indexOf(sel), 1); sel = null; drawOverlay(); refreshThumb(); }
  $('pdxDel').onclick = deleteSel;
  $('pdxUndo').onclick = undo;
  addEventListener('keydown', e => {
    if (view.hidden || !pages.length || ta || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName) || dlg.open) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
    else if ((e.key === 'Delete' || e.key === 'Backspace') && sel) { e.preventDefault(); deleteSel(); }
    else if (e.key === 'PageDown' || (e.key === 'ArrowRight' && !sel)) go(cur + 1);
    else if (e.key === 'PageUp' || (e.key === 'ArrowLeft' && !sel)) go(cur - 1);
    else if (e.key === 'Escape') { sel = null; drawOverlay(); }
  });
  const setZoom = z => { commitText(); zoom = Math.max(0.25, Math.min(4, z)); renderPage(); };
  $('pdxZoomIn').onclick = () => setZoom((zoom || fitZoom()) * 1.25);
  $('pdxZoomOut').onclick = () => setZoom((zoom || fitZoom()) / 1.25);
  $('pdxZoomFit').onclick = () => { zoom = 0; commitText(); renderPage(); };
  let rz = 0;
  addEventListener('resize', () => { if (view.hidden || zoom) return; clearTimeout(rz); rz = setTimeout(renderPage, 150); });

  /* ---------- сохранить ---------- */
  function overlayPng(p, objs, scale) {             // занятая объектами область базового вида -> PNG
    const boxes = objs.map(bbox);
    const x0 = Math.max(0, Math.min(...boxes.map(b => b.x)) - 2), y0 = Math.max(0, Math.min(...boxes.map(b => b.y)) - 2);
    const x1 = Math.min(p.bw, Math.max(...boxes.map(b => b.x + b.w)) + 2), y1 = Math.min(p.bh, Math.max(...boxes.map(b => b.y + b.h)) + 2);
    if (x1 <= x0 || y1 <= y0) return null;
    const c = document.createElement('canvas');
    c.width = Math.ceil((x1 - x0) * scale); c.height = Math.ceil((y1 - y0) * scale);
    const g = c.getContext('2d'); g.setTransform(scale, 0, 0, scale, -x0 * scale, -y0 * scale);
    objs.forEach(o => drawObj(g, o));
    return { c, x: x0, y: y0, w: c.width / scale, h: c.height / scale };
  }
  const blobOf = (c, type, q) => new Promise(r => c.toBlob(b => r(b), type, q));
  const bytesOf = async b => new Uint8Array(await b.arrayBuffer());
  async function buildVector() {
    const { PDFDocument, degrees, rgb } = window.PDFLib, out = await PDFDocument.create();
    // копируем страницы пачкой из каждого источника, потом раскладываем по нашему порядку
    const copied = new Map();
    for (let s = 0; s < sources.length; s++) {
      const idxs = [...new Set(pages.filter(p => p.src === s).map(p => p.idx))];
      if (!idxs.length) continue;
      const got = await out.copyPages(sources[s].lib, idxs);
      idxs.forEach((ix, k) => copied.set(`${s}:${ix}`, got[k]));
    }
    const used = new Set();
    for (let i = 0; i < pages.length; i++) {
      const p = pages[i];
      info(`Собираю страницу ${i + 1} из ${pages.length}…`);
      let pg;
      if (p.src < 0) pg = out.addPage([p.bw, p.bh]);
      else {
        const key = `${p.src}:${p.idx}`;
        // одна и та же страница дважды (дубль) — копируем ещё раз, иначе объекты лягут на обе
        pg = used.has(key) ? (await out.copyPages(sources[p.src].lib, [p.idx]))[0] : copied.get(key);
        used.add(key); out.addPage(pg);
      }
      if (p.objs.length) {
        const cb = pg.getCropBox(), W = cb.width, H = cb.height, R = p.src < 0 ? 0 : p.base;
        // точка базового вида (u, v), начало сверху слева -> координаты PDF без поворота
        const map = (u, v) => R === 90 ? [cb.x + v, cb.y + u] : R === 180 ? [cb.x + W - u, cb.y + v] : R === 270 ? [cb.x + W - v, cb.y + H - u] : [cb.x + u, cb.y + H - v];
        // «замазать» рисуем вектором, остальное одной картинкой; порядок слоёв: сначала заливки
        p.objs.filter(o => o.t === 'white').forEach(o => {
          const [ax, ay] = map(o.x, o.y + o.h);
          pg.drawRectangle({ x: ax, y: ay, width: o.w, height: o.h, rotate: degrees(R), color: rgb(1, 1, 1) });
        });
        const rest = p.objs.filter(o => o.t !== 'white'), ov = rest.length && overlayPng(p, rest, 4);
        if (ov) {
          const img = await out.embedPng(await bytesOf(await blobOf(ov.c, 'image/png')));
          const [ax, ay] = map(ov.x, ov.y + ov.h);
          pg.drawImage(img, { x: ax, y: ay, width: ov.w, height: ov.h, rotate: degrees(R) });
        }
      }
      pg.setRotation(degrees(((p.src < 0 ? 0 : p.base) + p.rot) % 360));
    }
    return out.save();
  }
  async function buildFlat() {
    const { PDFDocument } = window.PDFLib, out = await PDFDocument.create(), scale = 200 / 72, c = document.createElement('canvas');
    for (let i = 0; i < pages.length; i++) {
      const p = pages[i], [w, h] = dispSize(p);
      info(`Рисую страницу ${i + 1} из ${pages.length}…`);
      await paintPage(p, c, scale);
      const g = c.getContext('2d'); baseTransform(g, p, c.width / w); p.objs.forEach(o => drawObj(g, o));
      const img = await out.embedJpg(await bytesOf(await blobOf(c, 'image/jpeg', 0.88)));
      out.addPage([w, h]).drawImage(img, { x: 0, y: 0, width: w, height: h });
    }
    return out.save();
  }
  $('pdxSave').onclick = async () => {
    if (!pages.length) return;
    commitText();
    const flat = $('pdxFlat').checked;
    if (!flat && pages.some(p => p.src >= 0 && !sources[p.src].lib)) { $('pdxFlat').checked = true; info('Этот PDF сохраняется только «сплющенным»: включил галочку, нажми ещё раз', true); return; }
    $('pdxSave').disabled = true; $('pdxSave').textContent = 'Сохраняю…';
    try {
      const bytes = flat ? await buildFlat() : await buildVector();
      const blob = new Blob([bytes], { type: 'application/pdf' }), a = document.createElement('a'), u = URL.createObjectURL(blob);
      a.href = u; a.download = `${($('pdxName').value.trim() || name).replace(/[\\/:*?"<>|]+/g, '_')}.pdf`;
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 20000);
      info(`Сохранено: ${a.download} (${(blob.size / 1048576).toFixed(1)} МБ)`);
    } catch (e) { info('Не сохранилось: ' + (e.message || e), true); }
    finally { $('pdxSave').disabled = false; $('pdxSave').textContent = 'Сохранить PDF'; }
  };

  function show() {
    loadLibs().catch(e => info('Не загрузился редактор: ' + e.message, true));
    if (pages.length) renderPage();
  }
  window.UniversalPdfEdit = { show, open: f => openFiles(f, false) };
})();
