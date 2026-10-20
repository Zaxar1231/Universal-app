/*
 * Фоторедактор: всё на canvas, прямо в окне приложения.
 * Кадр (обрезка с пропорциями, поворот, отражение, размер), цвет (яркость, контраст,
 * насыщенность, тепло, ч/б, сепия, виньетка, размытие), готовые фильтры, надпись/водяной знак,
 * сохранение в JPG/PNG/WebP. Цвет считается попиксельно, без ctx.filter: в WebView на macOS
 * и Linux его нет, а так результат везде одинаковый. Предпросмотр — на уменьшенной копии,
 * сохранение — в полном размере. Форматы, которые браузер не открывает (HEIC, TIFF, PSD, RAW),
 * сначала превращаются в PNG на сервере (/api/photo/load).
 */
(() => {
  'use strict';
  const view = document.getElementById('photoView');
  if (!view) return;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

  const SLIDERS = [
    ['bright', 'Яркость', -100, 100], ['contrast', 'Контраст', -100, 100], ['sat', 'Насыщенность', -100, 100],
    ['warm', 'Тепло', -100, 100], ['vignette', 'Виньетка', 0, 100], ['blur', 'Размытие', 0, 20],
  ];
  const PRESETS = {
    none: {}, bw: { gray: true, contrast: 12 }, sepia: { sepia: true }, warm: { warm: 35, sat: 10 },
    cold: { warm: -35 }, vintage: { warm: 25, contrast: -12, sat: -30, vignette: 45 }, vivid: { sat: 45, contrast: 18, bright: 4 },
    soft: { contrast: -15, bright: 8, blur: 1 },
  };
  const PRESET_NAMES = { none: 'Без фильтра', bw: 'Ч/Б', sepia: 'Сепия', warm: 'Тёплый', cold: 'Холодный', vintage: 'Винтаж', vivid: 'Сочный', soft: 'Мягкий' };
  const POS = { tl: 'слева вверху', tc: 'вверху', tr: 'справа вверху', ml: 'слева', mc: 'в центре', mr: 'справа', bl: 'слева внизу', bc: 'внизу', br: 'справа внизу' };
  const blank = () => ({ bright: 0, contrast: 0, sat: 0, warm: 0, vignette: 0, blur: 0, gray: false, sepia: false });

  let base = null, history = [], adj = blank(), name = 'photo', crop = null, cropAspect = 0, raf = 0;

  view.innerHTML = `
  <div class="pe">
    <div class="card pe-stage">
      <div class="pe-tools">
        <label class="btn tonal state" for="pePick">Открыть фото…</label>
        <input type="file" id="pePick" accept="image/*,.heic,.heif,.tif,.tiff,.psd,.dng,.cr2,.nef,.arw" hidden>
        <span class="pe-sep"></span>
        <button class="icon-btn state" id="peUndo" title="Отменить (Ctrl+Z)" aria-label="Отменить"><svg viewBox="0 -960 960 960"><path d="M280-200v-80h284q63 0 109.500-40T720-420q0-60-46.500-100T564-560H312l104 104-56 56-200-200 200-200 56 56-104 104h252q97 0 166.500 63T800-420q0 94-69.500 157T564-200H280Z"/></svg></button>
        <button class="icon-btn state" id="peRotL" title="Повернуть влево" aria-label="Повернуть влево"><svg viewBox="0 -960 960 960"><path d="M440-80q-50-5-96-24.500T256-156l56-58q29 21 61.500 34t66.500 18v82Zm80 0v-82q104-15 172-93.500T760-440q0-117-81.500-198.500T480-720h-8l64 64-56 56-160-160 160-160 56 58-62 62h6q75 0 140.500 28.500t114 77q48.500 48.500 77 114T840-440q0 137-91 238.500T520-80ZM198-214q-32-42-51.500-88T122-400h82q5 34 18 66.500t34 61.500l-58 58Zm-76-266q6-52 25.500-98t51.500-88l58 58q-21 29-34 61.500T204-480h-82Z"/></svg></button>
        <button class="icon-btn state" id="peRotR" title="Повернуть вправо" aria-label="Повернуть вправо"><svg viewBox="0 -960 960 960" style="transform:scaleX(-1)"><path d="M440-80q-50-5-96-24.500T256-156l56-58q29 21 61.500 34t66.500 18v82Zm80 0v-82q104-15 172-93.500T760-440q0-117-81.500-198.500T480-720h-8l64 64-56 56-160-160 160-160 56 58-62 62h6q75 0 140.500 28.500t114 77q48.500 48.500 77 114T840-440q0 137-91 238.500T520-80ZM198-214q-32-42-51.500-88T122-400h82q5 34 18 66.500t34 61.500l-58 58Zm-76-266q6-52 25.500-98t51.500-88l58 58q-21 29-34 61.500T204-480h-82Z"/></svg></button>
        <button class="icon-btn state" id="peFlipH" title="Отразить по горизонтали" aria-label="Отразить по горизонтали"><svg viewBox="0 -960 960 960"><path d="M360-120H200q-33 0-56.500-23.500T120-200v-560q0-33 23.500-56.500T200-840h160v80H200v560h160v80Zm80 80v-880h80v880h-80Zm160-80v-80h80v80h-80Zm0-640v-80h80v80h-80Zm160 640v-80h80q0 33-23.500 56.500T760-120Zm0-160v-80h80v80h-80Zm0-160v-80h80v80h-80Zm0-160v-80h80v80h-80Zm0-160v-80q33 0 56.500 23.500T840-760h-80Z"/></svg></button>
        <button class="icon-btn state" id="peFlipV" title="Отразить по вертикали" aria-label="Отразить по вертикали"><svg viewBox="0 -960 960 960" style="transform:rotate(90deg)"><path d="M360-120H200q-33 0-56.500-23.500T120-200v-560q0-33 23.500-56.500T200-840h160v80H200v560h160v80Zm80 80v-880h80v880h-80Zm160-80v-80h80v80h-80Zm0-640v-80h80v80h-80Zm160 640v-80h80q0 33-23.500 56.500T760-120Zm0-160v-80h80v80h-80Zm0-160v-80h80v80h-80Zm0-160v-80h80v80h-80Zm0-160v-80q33 0 56.500 23.500T840-760h-80Z"/></svg></button>
        <span class="pe-sep"></span>
        <span class="t-body-m muted" id="peInfo"></span>
      </div>
      <div class="pe-area" id="peArea">
        <label class="pe-empty" for="pePick" id="peEmpty"><strong>Перетащи фото сюда или нажми, чтобы выбрать</strong>
          <span class="t-body-m muted">JPG, PNG, WebP, GIF, BMP, а также HEIC, TIFF, PSD и RAW (через ImageMagick)</span></label>
        <div class="pe-canvas" id="peWrap" hidden><canvas id="peCanvas"></canvas><div class="pe-crop" id="peCrop" hidden></div></div>
      </div>
    </div>
    <aside class="card pe-side" id="peSide">
      <details open><summary>Кадр</summary>
        <div class="chips" id="peAspect">
          ${[['0', 'Свободно'], ['1', '1:1'], ['1.3333', '4:3'], ['0.75', '3:4'], ['1.7778', '16:9'], ['0.5625', '9:16'], ['0.8', '4:5'], ['orig', 'Как у фото']]
            .map(([v, l], i) => `<input type="radio" name="peAsp" id="peAsp${i}" value="${v}" ${i === 0 ? 'checked' : ''}><label for="peAsp${i}">${l}</label>`).join('')}
        </div>
        <div class="row-btns"><button class="btn tonal state" id="peCropGo" type="button">Обрезать</button>
          <button class="btn text state" id="peCropCancel" type="button" hidden>Отмена</button></div>
        <p class="hint" id="peCropHint">Нажми «Обрезать», выдели область мышью, перетащи рамку и нажми «Применить».</p>
        <div class="two"><label class="tf"><span>Ширина, px</span><input type="number" id="peW" min="1"></label>
          <label class="tf"><span>Высота, px</span><input type="number" id="peH" min="1"></label></div>
        <label class="sw"><span class="t-body-m">Сохранять пропорции</span><input type="checkbox" id="peLock" checked><span class="track"></span></label>
        <button class="btn text state" id="peResize" type="button">Изменить размер</button>
      </details>
      <details open><summary>Цвет</summary>
        ${SLIDERS.map(([k, l, a, b]) => `<div class="slider-row"><div class="top"><label class="t-label-l muted" for="pe_${k}">${l}</label><span class="t-label-l" id="pe_${k}_v">0</span></div>
          <input type="range" id="pe_${k}" min="${a}" max="${b}" value="0" step="1"></div>`).join('')}
        <div class="chips"><input type="checkbox" id="pe_gray"><label for="pe_gray">Ч/Б</label><input type="checkbox" id="pe_sepia"><label for="pe_sepia">Сепия</label></div>
        <button class="btn text state" id="peResetAdj" type="button">Сбросить цвет</button>
      </details>
      <details><summary>Фильтры</summary>
        <div class="chips" id="pePresets">${Object.keys(PRESETS).map(k => `<button type="button" class="chip-btn" data-p="${k}">${PRESET_NAMES[k]}</button>`).join('')}</div>
      </details>
      <details><summary>Надпись и водяной знак</summary>
        <label class="tf"><span>Текст</span><input type="text" id="peText" placeholder="например © Имя 2026" autocomplete="off"></label>
        <div class="two"><label class="tf"><span>Где</span><select id="pePos">${Object.entries(POS).map(([k, l]) => `<option value="${k}" ${k === 'br' ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
          <label class="tf"><span>Цвет</span><input type="color" id="peColor" value="#ffffff"></label></div>
        <div class="slider-row"><div class="top"><label class="t-label-l muted" for="peSize">Размер</label><span class="t-label-l" id="peSize_v">5%</span></div><input type="range" id="peSize" min="2" max="20" value="5"></div>
        <div class="slider-row"><div class="top"><label class="t-label-l muted" for="peOpac">Непрозрачность</label><span class="t-label-l" id="peOpac_v">80%</span></div><input type="range" id="peOpac" min="10" max="100" value="80"></div>
      </details>
      <div class="pe-save">
        <label class="tf"><span>Имя файла</span><input type="text" id="peName" autocomplete="off"></label>
        <div class="seg"><input type="radio" name="peFmt" id="peF1" value="image/jpeg" checked><label for="peF1">JPG</label>
          <input type="radio" name="peFmt" id="peF2" value="image/png"><label for="peF2">PNG</label>
          <input type="radio" name="peFmt" id="peF3" value="image/webp"><label for="peF3">WebP</label></div>
        <div class="slider-row" id="peQRow"><div class="top"><label class="t-label-l muted" for="peQ">Качество</label><span class="t-label-l" id="peQ_v">92</span></div><input type="range" id="peQ" min="40" max="100" value="92"></div>
        <button class="btn filled lg state" id="peSave" type="button" disabled>Сохранить</button>
        <p class="hint">Сохранённое фото не содержит данных EXIF (место съёмки, модель телефона).</p>
      </div>
    </aside>
  </div>`;
  const $ = id => document.getElementById(id);
  const canvas = $('peCanvas'), ctx = canvas.getContext('2d', { willReadFrequently: true });

  /* ---------- открыть ---------- */
  async function decode(file) {
    try { return await createImageBitmap(file, { imageOrientation: 'from-image' }); } catch (_) { /* браузер не умеет */ }
    const fd = new FormData(); fd.append('file', file, file.name);
    const r = await fetch('/api/photo/load', { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'не открылось');
    return createImageBitmap(await r.blob());
  }
  async function open(file) {
    $('peInfo').textContent = `Открываю ${file.name}…`;
    try {
      const bmp = await decode(file);
      const c = document.createElement('canvas'); c.width = bmp.width; c.height = bmp.height;
      c.getContext('2d').drawImage(bmp, 0, 0);
      base = c; history = []; adj = blank(); syncControls();
      name = file.name.replace(/\.[^.]+$/, '') || 'photo'; $('peName').value = name + '_edit';
      $('peEmpty').hidden = true; $('peWrap').hidden = false; $('peSave').disabled = false;
      stopCrop(); render();
    } catch (e) { $('peInfo').textContent = 'Не открылось: ' + (e.message || e); }
  }
  $('pePick').onchange = () => { const f = $('pePick').files[0]; $('pePick').value = ''; if (f) open(f); };
  const area = $('peArea');
  ['dragenter', 'dragover'].forEach(e => area.addEventListener(e, ev => { ev.preventDefault(); area.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(e => area.addEventListener(e, ev => { ev.preventDefault(); area.classList.remove('over'); }));
  area.addEventListener('drop', ev => { const f = [...ev.dataTransfer.files].find(x => x.type.startsWith('image/') || /\.(heic|heif|tiff?|psd|dng|cr2|nef|arw)$/i.test(x.name)); if (f) open(f); });

  /* ---------- история и геометрия ---------- */
  function commit(c) { history.push(base); if (history.length > 15) history.shift(); base = c; syncSize(); render(); }
  function undo() { if (!history.length) return; base = history.pop(); stopCrop(); syncSize(); render(); }
  function transformed(w, h, draw) { const c = document.createElement('canvas'); c.width = w; c.height = h; const g = c.getContext('2d'); draw(g); return c; }
  $('peUndo').onclick = undo;
  addEventListener('keydown', e => { if (!view.hidden && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && document.activeElement.tagName !== 'INPUT') { e.preventDefault(); undo(); } });
  $('peRotL').onclick = () => base && commit(transformed(base.height, base.width, g => { g.translate(0, base.width); g.rotate(-Math.PI / 2); g.drawImage(base, 0, 0); }));
  $('peRotR').onclick = () => base && commit(transformed(base.height, base.width, g => { g.translate(base.height, 0); g.rotate(Math.PI / 2); g.drawImage(base, 0, 0); }));
  $('peFlipH').onclick = () => base && commit(transformed(base.width, base.height, g => { g.translate(base.width, 0); g.scale(-1, 1); g.drawImage(base, 0, 0); }));
  $('peFlipV').onclick = () => base && commit(transformed(base.width, base.height, g => { g.translate(0, base.height); g.scale(1, -1); g.drawImage(base, 0, 0); }));

  function syncSize() {
    if (!base) return;
    $('peW').value = base.width; $('peH').value = base.height;
    $('peInfo').textContent = `${base.width} × ${base.height} px`;
  }
  $('peW').addEventListener('input', () => { if (base && $('peLock').checked) $('peH').value = Math.max(1, Math.round($('peW').value * base.height / base.width)); });
  $('peH').addEventListener('input', () => { if (base && $('peLock').checked) $('peW').value = Math.max(1, Math.round($('peH').value * base.width / base.height)); });
  $('peResize').onclick = () => {
    if (!base) return;
    const w = clamp(+$('peW').value | 0, 1, 20000), h = clamp(+$('peH').value | 0, 1, 20000);
    if (w === base.width && h === base.height) return;
    // сильное уменьшение — ступеньками по половине, иначе картинка «рвётся»
    let src = base;
    while (src.width / 2 >= w && src.height / 2 >= h) {
      const s = src; src = transformed(Math.round(s.width / 2), Math.round(s.height / 2), g => { g.imageSmoothingQuality = 'high'; g.drawImage(s, 0, 0, Math.round(s.width / 2), Math.round(s.height / 2)); });
    }
    const s = src; commit(transformed(w, h, g => { g.imageSmoothingQuality = 'high'; g.drawImage(s, 0, 0, w, h); }));
  };

  /* ---------- обрезка ---------- */
  const cropBox = $('peCrop');
  const scale = () => canvas.width / base.width;                       // экран / оригинал
  function aspectVal() {
    const v = document.querySelector('input[name=peAsp]:checked').value;
    return v === 'orig' ? base.width / base.height : +v;
  }
  function startCrop() {
    if (!base) return;
    cropAspect = aspectVal();
    const W = canvas.width, H = canvas.height;
    let w = W * 0.9, h = H * 0.9;
    if (cropAspect) { if (w / h > cropAspect) w = h * cropAspect; else h = w / cropAspect; }
    crop = { x: (W - w) / 2, y: (H - h) / 2, w, h };
    cropBox.hidden = false; $('peCropGo').textContent = 'Применить'; $('peCropCancel').hidden = false; drawCrop();
  }
  function stopCrop() { crop = null; cropBox.hidden = true; $('peCropGo').textContent = 'Обрезать'; $('peCropCancel').hidden = true; }
  function drawCrop() {
    Object.assign(cropBox.style, { left: crop.x + 'px', top: crop.y + 'px', width: crop.w + 'px', height: crop.h + 'px' });
    cropBox.dataset.size = `${Math.round(crop.w / scale())} × ${Math.round(crop.h / scale())}`;
  }
  $('peCropGo').onclick = () => {
    if (!crop) { startCrop(); return; }
    const k = scale(), x = Math.round(crop.x / k), y = Math.round(crop.y / k), w = Math.max(1, Math.round(crop.w / k)), h = Math.max(1, Math.round(crop.h / k));
    stopCrop(); commit(transformed(w, h, g => g.drawImage(base, x, y, w, h, 0, 0, w, h)));
  };
  $('peCropCancel').onclick = stopCrop;
  $('peAspect').addEventListener('change', () => { if (crop) startCrop(); });
  // мышь/палец: внутри рамки — двигать, снаружи — рисовать новую
  let drag = null;
  $('peWrap').addEventListener('pointerdown', e => {
    if (!crop) return;
    const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    const inside = px > crop.x && px < crop.x + crop.w && py > crop.y && py < crop.y + crop.h;
    drag = inside ? { mode: 'move', dx: px - crop.x, dy: py - crop.y } : { mode: 'new', x0: px, y0: py };
    $('peWrap').setPointerCapture(e.pointerId); e.preventDefault();
  });
  $('peWrap').addEventListener('pointermove', e => {
    if (!drag || !crop) return;
    const r = canvas.getBoundingClientRect(), W = canvas.width, H = canvas.height;
    const px = clamp(e.clientX - r.left, 0, W), py = clamp(e.clientY - r.top, 0, H);
    if (drag.mode === 'move') {
      crop.x = clamp(px - drag.dx, 0, W - crop.w); crop.y = clamp(py - drag.dy, 0, H - crop.h);
    } else {
      let w = Math.abs(px - drag.x0), h = Math.abs(py - drag.y0);
      if (cropAspect) { if (w / Math.max(h, 1) > cropAspect) h = w / cropAspect; else w = h * cropAspect; }
      let x = px < drag.x0 ? drag.x0 - w : drag.x0, y = py < drag.y0 ? drag.y0 - h : drag.y0;
      if (x < 0 || y < 0 || x + w > W || y + h > H) return;              // за край не выходим
      if (w > 8 && h > 8) crop = { x, y, w, h };
    }
    drawCrop();
  });
  addEventListener('pointerup', () => { drag = null; });

  /* ---------- цвет и надпись ---------- */
  function readControls() {
    SLIDERS.forEach(([k]) => { adj[k] = +$('pe_' + k).value; $('pe_' + k + '_v').textContent = adj[k]; });
    adj.gray = $('pe_gray').checked; adj.sepia = $('pe_sepia').checked;
    $('peSize_v').textContent = $('peSize').value + '%'; $('peOpac_v').textContent = $('peOpac').value + '%';
    $('peQ_v').textContent = $('peQ').value; $('peQRow').hidden = document.querySelector('input[name=peFmt]:checked').value === 'image/png';
  }
  function syncControls() {
    SLIDERS.forEach(([k]) => { $('pe_' + k).value = adj[k]; });
    $('pe_gray').checked = adj.gray; $('pe_sepia').checked = adj.sepia; readControls();
  }
  $('peSide').addEventListener('input', () => { readControls(); render(); });
  $('peSide').addEventListener('change', () => { readControls(); render(); });
  $('peResetAdj').onclick = () => { adj = blank(); syncControls(); render(); };
  $('pePresets').addEventListener('click', e => {
    const b = e.target.closest('[data-p]'); if (!b) return;
    adj = Object.assign(blank(), PRESETS[b.dataset.p]); syncControls(); render();
  });

  function pixels(g, w, h) {
    const { bright, contrast, sat, warm, gray, sepia } = adj;
    if (!bright && !contrast && !sat && !warm && !gray && !sepia) return;
    const img = g.getImageData(0, 0, w, h), d = img.data;
    const c = contrast * 2.55, f = (259 * (c + 255)) / (255 * (259 - c)), b = bright * 1.6, s = 1 + sat / 100, wr = warm * 0.5;
    for (let i = 0; i < d.length; i += 4) {
      let r = d[i], gg = d[i + 1], bb = d[i + 2];
      r = f * (r + b - 128) + 128; gg = f * (gg + b - 128) + 128; bb = f * (bb + b - 128) + 128;
      const l = 0.299 * r + 0.587 * gg + 0.114 * bb;
      r = l + (r - l) * s; gg = l + (gg - l) * s; bb = l + (bb - l) * s;
      r += wr; bb -= wr;
      if (gray) { const y = 0.299 * r + 0.587 * gg + 0.114 * bb; r = gg = bb = y; }
      if (sepia) { const tr = 0.393 * r + 0.769 * gg + 0.189 * bb, tg = 0.349 * r + 0.686 * gg + 0.168 * bb, tb = 0.272 * r + 0.534 * gg + 0.131 * bb; r = tr; gg = tg; bb = tb; }
      d[i] = r < 0 ? 0 : r > 255 ? 255 : r; d[i + 1] = gg < 0 ? 0 : gg > 255 ? 255 : gg; d[i + 2] = bb < 0 ? 0 : bb > 255 ? 255 : bb;
    }
    g.putImageData(img, 0, 0);
  }
  const FILTER_OK = (() => { const t = document.createElement('canvas').getContext('2d'); if (!('filter' in t)) return false; t.filter = 'blur(2px)'; return t.filter === 'blur(2px)'; })();
  function paint(g, w, h, k) {
    // k — во сколько раз этот холст меньше оригинала (для размытия и надписи)
    g.clearRect(0, 0, w, h);
    if (adj.blur && FILTER_OK) g.filter = `blur(${adj.blur * Math.max(w, h) / 1000}px)`;
    if (adj.blur && !FILTER_OK) {        // без ctx.filter: размываем уменьшением и обратным растяжением
      const t = transformed(Math.max(1, w / (1 + adj.blur / 2)), Math.max(1, h / (1 + adj.blur / 2)), q => { q.imageSmoothingQuality = 'high'; q.drawImage(base, 0, 0, Math.max(1, w / (1 + adj.blur / 2)), Math.max(1, h / (1 + adj.blur / 2))); });
      g.imageSmoothingQuality = 'high'; g.drawImage(t, 0, 0, w, h);
    } else { g.imageSmoothingQuality = 'high'; g.drawImage(base, 0, 0, w, h); }
    g.filter = 'none';
    pixels(g, w, h);
    if (adj.vignette) {
      const gr = g.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.3, w / 2, h / 2, Math.hypot(w, h) / 2);
      gr.addColorStop(0, 'rgba(0,0,0,0)'); gr.addColorStop(1, `rgba(0,0,0,${adj.vignette / 100 * 0.85})`);
      g.fillStyle = gr; g.fillRect(0, 0, w, h);
    }
    const text = $('peText').value.trim();
    if (text) {
      const size = Math.max(6, w * $('peSize').value / 100 / 1.5), pos = $('pePos').value, m = Math.min(w, h) * 0.03;
      g.save(); g.globalAlpha = $('peOpac').value / 100; g.fillStyle = $('peColor').value;
      g.font = `600 ${size}px system-ui, "Segoe UI", Roboto, sans-serif`;
      g.textAlign = pos[1] === 'l' ? 'left' : pos[1] === 'r' ? 'right' : 'center';
      g.textBaseline = pos[0] === 't' ? 'top' : pos[0] === 'b' ? 'bottom' : 'middle';
      g.shadowColor = 'rgba(0,0,0,.45)'; g.shadowBlur = size / 6;
      const x = pos[1] === 'l' ? m : pos[1] === 'r' ? w - m : w / 2, y = pos[0] === 't' ? m : pos[0] === 'b' ? h - m : h / 2;
      g.fillText(text, x, y); g.restore();
    }
  }
  function render() {
    if (!base) return;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const maxW = Math.max(200, area.clientWidth - 2), maxH = Math.max(240, innerHeight - 250);
      const k = Math.min(1, maxW / base.width, maxH / base.height);
      const w = Math.max(1, Math.round(base.width * k)), h = Math.max(1, Math.round(base.height * k));
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; if (crop) startCrop(); }
      paint(ctx, w, h, k);
      syncSize();
    });
  }
  addEventListener('resize', () => { if (!view.hidden) render(); });

  /* ---------- сохранить ---------- */
  $('peSave').onclick = () => {
    if (!base) return;
    const mime = document.querySelector('input[name=peFmt]:checked').value, ext = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' }[mime];
    $('peSave').disabled = true; $('peSave').textContent = 'Сохраняю…';
    setTimeout(() => {
      const out = transformed(base.width, base.height, g => {
        if (mime === 'image/jpeg') { g.fillStyle = '#fff'; g.fillRect(0, 0, base.width, base.height); }
        paint(g, base.width, base.height, 1);
      });
      out.toBlob(blob => {
        $('peSave').disabled = false; $('peSave').textContent = 'Сохранить';
        if (!blob) { $('peInfo').textContent = 'Этот формат здесь не сохраняется, выбери JPG или PNG'; return; }
        const a = document.createElement('a'), u = URL.createObjectURL(blob);
        a.href = u; a.download = `${($('peName').value.trim() || name).replace(/[\\/:*?"<>|]+/g, '_')}.${ext}`;
        document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 10000);
        $('peInfo').textContent = `Сохранено: ${a.download} (${(blob.size / 1048576).toFixed(1)} МБ)`;
      }, mime, $('peQ').value / 100);
    }, 30);
  };

  readControls();
  window.UniversalPhotoEdit = { show: render, open };
})();
