/*
 * Редактор документов: Jodit (static/vendor/jodit, MIT) + /api/doc/open и /api/doc/save.
 * Открывает DOCX/DOC/ODT/RTF/HTML/MD/TXT (на сервере через LibreOffice или pandoc),
 * правится как в Word, сохраняется в DOCX/ODT/RTF/PDF/HTML/MD/TXT.
 * Черновик сам сохраняется в localStorage и восстанавливается при следующем открытии.
 */
(() => {
  'use strict';
  const view = document.getElementById('docView');
  if (!view) return;
  const DRAFT = 'docDraft';
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let editor = null, assets = null, caps = null, saveTimer = 0, dirty = false;

  view.innerHTML = `
    <div class="doc-bar card">
      <label class="tf doc-name"><span>Название</span><input type="text" id="docName" value="Новый документ" autocomplete="off"></label>
      <div class="doc-acts">
        <button class="btn text state" id="docNew" type="button">Новый</button>
        <label class="btn tonal state" for="docPick">Открыть…</label>
        <input type="file" id="docPick" accept=".docx,.doc,.odt,.rtf,.html,.htm,.md,.markdown,.txt,.epub" hidden>
        <label class="tf doc-fmt"><span>Формат</span><select id="docFmt">
          <option value="docx">Word (.docx)</option><option value="odt">OpenDocument (.odt)</option><option value="pdf">PDF</option>
          <option value="rtf">RTF</option><option value="html">Веб-страница (.html)</option><option value="md">Markdown (.md)</option>
          <option value="txt">Текст (.txt)</option></select></label>
        <button class="btn filled state" id="docSave" type="button">Сохранить</button>
      </div>
      <div class="doc-state t-body-m muted" id="docState"></div>
    </div>
    <div class="doc-page"><textarea id="docArea"></textarea></div>`;
  const $ = id => document.getElementById(id);
  const state = (text, bad) => { $('docState').textContent = text; $('docState').classList.toggle('err', !!bad); };

  function loadAssets() {
    if (assets) return assets;
    assets = new Promise((res, rej) => {
      const css = document.createElement('link');
      css.rel = 'stylesheet'; css.href = '/static/vendor/jodit/jodit.min.css';
      document.head.appendChild(css);
      const js = document.createElement('script');
      js.src = '/static/vendor/jodit/jodit.min.js';
      js.onload = res; js.onerror = () => rej(new Error('не загрузился редактор'));
      document.head.appendChild(js);
    });
    return assets;
  }

  function saveDraft() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try { localStorage.setItem(DRAFT, JSON.stringify({ name: $('docName').value, html: editor.value, ts: Date.now() })); } catch (_) { /* переполнено */ }
    }, 800);
  }

  function init() {
    const dark = matchMedia('(prefers-color-scheme: dark)').matches;
    editor = window.Jodit.make('#docArea', {
      language: 'ru', theme: dark ? 'dark' : 'default', height: Math.max(520, innerHeight - 260),
      toolbarAdaptive: false, toolbarSticky: true, spellcheck: true, iframe: false,
      placeholder: 'Начни печатать или открой документ…',
      uploader: { insertImageAsBase64URI: true },               // картинки внутри документа, без сервера
      askBeforePasteHTML: false, askBeforePasteFromWord: false, defaultActionOnPaste: 'insert_as_html',
      buttons: ['undo', 'redo', '|', 'paragraph', 'font', 'fontsize', '|', 'bold', 'italic', 'underline', 'strikethrough',
        'superscript', 'subscript', 'brush', 'eraser', '|', 'ul', 'ol', 'indent', 'outdent', 'align', 'lineHeight', '|',
        'table', 'image', 'link', 'hr', 'symbols', '|', 'find', 'selectall', 'copyformat', 'print', 'source', 'fullsize'],
      showCharsCounter: true, showWordsCounter: true, showXPathInStatusbar: false,
    });
    editor.events.on('change', () => { dirty = true; saveDraft(); });
    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(DRAFT) || 'null'); } catch (_) { /* битый черновик */ }
    if (draft && draft.html) {
      editor.value = draft.html; $('docName').value = draft.name || 'Документ';
      state(`Черновик от ${new Date(draft.ts).toLocaleString()} восстановлен.`);
    }
    dirty = false;
  }

  async function show() {
    if (!caps) {
      fetch('/api/doc/caps').then(r => r.json()).then(c => {
        caps = c;
        if (!c.libreoffice) {
          $('docFmt').querySelector('option[value=pdf]').disabled = true;
          state(c.pandoc ? 'Без LibreOffice: PDF недоступен, а при открытии документов могут потеряться шрифты и цвета. Поставь LibreOffice для полной совместимости.'
            : 'Нет ни LibreOffice, ни pandoc: открывать и сохранять можно только HTML и TXT.', !c.pandoc);
        }
      }).catch(() => {});
    }
    if (editor) return;
    state('Загружаю редактор…');
    try { await loadAssets(); init(); if ($('docState').textContent === 'Загружаю редактор…') state(''); }
    catch (e) { state('Не загрузился редактор: ' + e.message, true); }
  }

  $('docName').addEventListener('input', () => editor && saveDraft());
  $('docNew').onclick = () => {
    if (!editor) return;
    if (dirty && editor.value.trim() && !confirm('Начать новый документ? Несохранённое пропадёт.')) return;
    editor.value = ''; $('docName').value = 'Новый документ'; dirty = false; saveDraft(); state('');
  };
  $('docPick').onchange = async () => {
    const f = $('docPick').files[0]; $('docPick').value = '';
    if (!f || !editor) return;
    if (dirty && editor.value.trim() && !confirm(`Открыть «${f.name}»? Несохранённое пропадёт.`)) return;
    state(`Открываю ${f.name}…`);
    const fd = new FormData(); fd.append('file', f, f.name);
    try {
      const d = await fetch('/api/doc/open', { method: 'POST', body: fd }).then(r => r.json());
      if (d.error) { state(d.error, true); return; }
      editor.value = d.html; $('docName').value = d.name; dirty = false; saveDraft();
      state(`Открыт «${f.name}» (${d.engine}).` + (d.warning ? ' ' + d.warning : ''));
    } catch (_) { state('Нет связи с приложением', true); }
  };
  $('docSave').onclick = async () => {
    if (!editor) return;
    const fmt = $('docFmt').value, name = $('docName').value.trim() || 'Документ';
    state('Сохраняю…'); $('docSave').disabled = true;
    const fd = new FormData(); fd.append('html', editor.value); fd.append('format', fmt); fd.append('name', name);
    try {
      const d = await fetch('/api/doc/save', { method: 'POST', body: fd }).then(r => r.json());
      if (d.error) { state(d.error, true); return; }
      const a = document.createElement('a');
      a.href = d.url; a.download = d.name; document.body.appendChild(a); a.click(); a.remove();
      dirty = false; state(`Сохранено: ${d.name}`);
    } catch (_) { state('Нет связи с приложением', true); }
    finally { $('docSave').disabled = false; }
  };
  addEventListener('beforeunload', e => { if (dirty && editor && editor.value.trim()) saveDraft(); });

  window.UniversalDocEdit = { show };
})();
