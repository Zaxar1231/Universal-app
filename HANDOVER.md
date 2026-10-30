# Конвертер файлов (проект «popple») — документ для передачи

Этот файл содержит всё, что нужно другому ИИ или разработчику, чтобы продолжить работу над проектом без предыстории. Скопируй его целиком в новый чат и приложи `popple.zip` (или скажи, что код нужно восстановить по этому описанию).

---

## 0. Как начать в новом чате

Вставь в первое сообщение:

> Продолжаем разработку локальной веб-морды для конвертации файлов (Flask + ffmpeg/ImageMagick/pandoc/poppler/LibreOffice, интерфейс в Material You). Прикладываю `HANDOVER.md` и `popple.zip`. Прочитай документ, не переписывай существующее с нуля, а расширяй. Тон общения: неформальный, дружеский, можно с подколами.

**Важно про владельца.** Пользователь общается по-русски, неформально, ждёт шуток и лёгкого тона. Не любит воду: делай, а не спрашивай, если задача понятна. Работает на **Windows** (в `run.bat` есть launcher), имеет домашний сервер (упоминал возможный Docker-вариант).

---

## 1. Что это за проект

Локальная веб-морда (Flask, `localhost:5000`), которая конвертирует файлы пачками. Ничего не уходит в интернет. Результат отдаётся ZIP-архивом с именем исходной папки или одним файлом.

Проект вырос в три этапа:

1. **PDF в картинки** через poppler (`pdftoppm`/`pdftocairo`).
2. **Фото в A4** и **Фото в PDF** (Pillow).
3. **Универсальный конвертер** картинок/аудио/видео/документов, набор форматов взят из проекта **VERT** (`VERT-main.zip`, Svelte + WebAssembly, лицензия AGPL-3.0), но реализован на нативных ffmpeg/ImageMagick/pandoc. Плюс интерфейс переделан под **Material You**.

### Семь режимов интерфейса

| Режим (`value`) | Заголовок | Эндпоинт | Движок |
|---|---|---|---|
| `image` | Картинки | `/api/universal` | ImageMagick |
| `audio` | Аудио | `/api/universal` | ffmpeg |
| `video` | Видео | `/api/universal` | ffmpeg |
| `doc` | Документы | `/api/universal` | pandoc (+ LibreOffice для `.doc`) |
| `pdf` | PDF в картинки | `/api/convert` | poppler |
| `photo` | Фото в A4 | `/api/photos` | Pillow |
| `topdf` | Фото в PDF | `/api/photos2pdf` | Pillow |

Первые четыре режима идут через общий `/api/universal`, последние три — исторические, их логику **не ломать** (они проверены регрессионно).

---

## 2. Структура проекта

```
popple/
├── app.py               # Flask: все маршруты, poppler-логика, A4/PDF-логика (735 строк)
├── converters.py        # Универсальные движки ffmpeg/ImageMagick/pandoc/LO (514 строк)
├── templates/
│   └── index.html       # ВЕСЬ фронтенд: разметка + CSS + JS в одном файле (790 строк, Jinja)
├── requirements.txt     # flask>=3.0, pillow>=10.0
├── run.bat              # Windows: создаёт .venv, ставит зависимости, открывает браузер
├── run.sh               # Linux/macOS: то же самое
└── README.md            # Инструкция по установке и запуску (актуальна: все 7 режимов)
```

Запуск: `pip install -r requirements.txt && python app.py` → http://localhost:5000

### Зависимости от внешних программ (должны быть в PATH)

| Программа | Нужна для | Обязательна |
|---|---|---|
| `pdftoppm`, `pdftocairo` (poppler) | режим `pdf` | только для этого режима |
| `ffmpeg`, `ffprobe` | `audio`, `video` | для этих режимов |
| `magick` или `convert` (ImageMagick) | `image` | для этого режима |
| `pandoc` | `doc` | для этого режима |
| `soffice` (LibreOffice) | `.doc` (чтение и запись) | опционально |
| Pillow (Python) | `photo`, `topdf`, WebP из PDF | всегда (в requirements) |

Интерфейс сам показывает красный баннер, если для выбранного режима нужной программы нет (`/api/formats` возвращает `engines`).

---

## 3. HTTP API

Все POST принимают `multipart/form-data` с полем `files` (много файлов).

### `GET /api/formats`
Возвращает `{engines: {ffmpeg, imagemagick, pandoc, libreoffice}, categories: {image|audio|video|doc: {inputs, outputs, native, cross}}}`.
`native` — форматы своей категории по убыванию популярности, `cross` — форматы соседней категории (видео→аудио, аудио→видео).

### `POST /api/universal` — 4 новых режима
Поля: `to` (обязательно, формат), `quality` (1–100), `density` (DPI, для pdf/eps→картинка), `bitrate` (`auto`|число кбит/с), `sample_rate` (`auto`|Гц), `keep_metadata` (`1`/`0`), `name_template` (например `VERT_%name%`), `archive_name`.
Ответ: `{job, archive, total, done, failed:[{name,error}], files, single_file?}`.

### `POST /api/convert` — PDF → картинки (poppler)
Поля: `format` (jpg/png/tiff/webp/svg/ps/eps), `dpi` (30–600), `quality`, `all_pages`, `gray`, `a4`, `a4_mode` (`contain`|`cover`), `a4_orient` (`auto`|`portrait`|`landscape`), `archive_name`.

### `POST /api/photos` — готовое фото → A4
Те же поля, что выше, `dpi` до 1200 (по умолчанию 300). Работает через Pillow, poppler не нужен.

### `POST /api/photos2pdf` — фото → PDF на листах A4
Поля: `per_page` (1/2/4/6/8/9), `output` (`single` один PDF | `each` PDF на каждое), `order` (`name` естественная сортировка | `keep`), `margin` (мм, 0–50), `gap` (мм, 0–30), `dpi` (72–600), `quality`, `gray`, `a4_mode`, `a4_orient`, `archive_name`.
Ответ содержит `pages`, `single_pdf` (если файл один).

### `GET /api/download/<job>`
Отдаёт ZIP. `?raw=1` — если в задании ровно один файл, отдаёт его напрямую (любого типа, mimetype по расширению). Имя ZIP берётся из `name.txt` внутри папки задания. `Content-Disposition` содержит **и ASCII-запасное имя (транслитерация), и `filename*=UTF-8''...`** — иначе кириллица в имени ломается.

### Служебное
`GET /api/status` (poppler), `GET /` (страница). Эндпоинта `/api/cleanup` **больше нет** (см. раздел 8, баг №1).

---

## 4. Ключевые решения и логика

### Задания и очистка
Каждая конвертация — папка `%TEMP%/popple_jobs/<12-hex>/{in,out,name.txt}`. Исходники удаляются сразу после конвертации. Старые задания (>1 часа) удаляет `purge_old_jobs()` при каждой новой конвертации. **Не удалять результат по `beforeunload`** — это ломало скачивание.

### Имя архива
Фронтенд запоминает имя папки при drag&drop (`entry.isDirectory`, `webkitGetAsEntry`) или через кнопку «Выбрать папку» (`webkitRelativePath`) в `file._dir`. Правило: одна папка → её имя; несколько → `Первая (+N)`; файлы без папки → `converted`. Санитайзинг в `safe_archive_name()` (убирает `/\:*?"<>|`, зарезервированные имена Windows `CON`, `NUL`… → `converted`).

### A4 (`fit_to_a4`, `build_pages`)
- A4 = 210×297 мм; пиксели = `мм / 25.4 * dpi` (150 DPI → 1240×1754; 300 DPI → 2480×3508).
- `contain` — целиком с белыми полями; `cover` — заполнить и обрезать по центру.
- Ориентация `auto` — по исходной картинке (для сетки на листе — портрет).
- Применяется **EXIF-поворот** (`ImageOps.exif_transpose`) — иначе фото с телефона ложатся боком.
- Прозрачность заливается белым через `alpha_composite`.
- Для SVG/EPS размер листа не задаётся (poppler отвергает `-paper` для eps). Для PS: `-paper A4 -expand`.

### Универсальные конвертеры (`converters.py`)
Логика выбора параметров портирована из VERT (`ffmpeg.svelte.ts`):
- Битрейт `auto`: lossless→lossy = 128k; иначе берётся битрейт источника через `ffprobe`.
- Частота `auto`: lossless→lossy = 44100 (48000 для opus); иначе берётся у источника (для opus 44100 → 48000).
- Видео→аудио: `-map 0:a:0`, без видеопотока.
- Аудио→видео: обложка альбома (если есть) как картинка, иначе чёрный фон 512×512, `-r 1`, `yuv420p`.
- `alac` пишется в `.m4a` (`OUT_EXT`), как в VERT.
- Таблица кодеков `VIDEO_CODECS`/`AUDIO_CODECS` — из VERT `getCodecs()`.
- Один файл не должен валить пачку: любая ошибка ловится и попадает в `failed`.
- Для видео используется до 2 потоков (тяжёлое), для остального до 8.

### Движки: что читает и что пишет
Таблицы форматов — `IMAGE`, `AUDIO`, `VIDEO`, `DOC` в `converters.py`, значение `(можно_читать, можно_писать)`. Только чтение: RAW-камеры (nef, cr2, arw, dng…), `svg`, `heif`, `rm`, `rmvb`, `mogg`, `caf`, `dsd` и др.

---

## 5. Фронтенд (`templates/index.html`)

Один файл: Jinja + CSS + JS. Вставки Jinja: цикл по `formats` (радио формата PDF-режима) и `POPPLER_OK`. Ничего не собирается, внешних зависимостей нет (ни CDN, ни шрифтов).

### Material You (Material 3)
- **Токены цвета** — CSS-переменные `--md-*` (primary, secondary-container, surface-container-low…high и т.д.).
- **Динамический цвет:** функция `applyPalette(seedHex)` строит палитру в **OKLCH** (не настоящий HCT, но даёт то же ощущение) для светлой и тёмной схем, следит за `prefers-color-scheme`. Пользователь выбирает один из 8 seed-цветов кнопкой-палитрой в шапке; выбор хранится в `localStorage['seed']`.
- **Elevation передаётся тоном поверхности, а не тенью** (как в M3). Никаких box-shadow.
- **Навигация:** navigation rail слева (>720px), navigation bar снизу (≤720px). Иконки — Material Symbols, зашиты как SVG-path в Jinja-словаре `icons`.
- **Компоненты:** filled/tonal/text-кнопки, filter-chips (выбор формата), segmented button, слайдеры M3, filled text-field, switch, диалог выбора цвета. Все с state-layer (hover/focus/pressed) и видимым фокусом.
- Формы: extra-large 28dp для карточек, полностью скруглённые кнопки, 8dp для чипов.
- `prefers-reduced-motion` уважается.

### Логика режимов в JS
- `MODES` — карта режимов; `kind`: `uni` | `pdf` | `photo` | `topdf`.
- `syncSettings()` — единая точка, показывает/скрывает контролы под режим и формат (например, «Качество» скрыто для PNG/TIFF/векторных).
- `applyMode()` — заголовок, подсказки, значения по умолчанию (DPI: pdf=150, photo=300, topdf=200; качество: pdf=90, photo=95, topdf=85), очистка списка файлов, баннеры движков.
- `accepts(file)` — фильтрует файлы по режиму. В режиме `audio` принимаются **только аудиоформаты** (видеофайл нужно бросать в режим «Видео» и выбирать аудиоформат-цель).
- `renderUniChips()` — рисует чипы форматов по данным `/api/formats`, с группой «Достать звук»/«Сделать видео» для cross-конвертаций.

---

## 6. Что проверено (и как)

Проверялось **на реальных данных**, не «на глаз»:

- **Все аудио-кодеки** (mp3→wav/flac/ogg/opus/aac/m4a/alac/wma/ac3/aiff/mp2/au/voc/weba/m4b), **все видео-контейнеры** (mp4→20 форматов, с проверкой `ffprobe`, что в результате есть видео и аудио потоки), **18 форматов картинок**, **10 форматов документов**, `.doc` ↔ `docx/md/html/txt/doc` через LibreOffice.
- **Пиксели** результатов (не только размеры): белые поля действительно `(255,255,255)`.
- **HTTP-слой** через curl: смешанные пачки, шаблон имени, битый файл, злые имена (`../../etc/passwd`), кириллица в `Content-Disposition`.
- **UI в реальном Chromium** (Playwright): скриншоты светлой/тёмной темы, мобильная версия, диалог палитры, отсутствие JS-ошибок.
- **jsdom**-тесты: переключение всех 7 режимов, видимость каждого контрола, состав отправляемой формы (поля каждого эндпоинта), смена палитры.

**Не проверено:** реальный drag&drop папки в браузере (в песочнице нет мыши; логика проверена отдельно), работа на самой Windows пользователя.

---

## 7. Известные ограничения (честно)

- **AMR** не кодируется, если в сборке ffmpeg нет `libopencore_amrnb`. Ошибка понятная («Поставь полную сборку»). На Windows помогает сборка gyan.dev **full**.
- **AVIF, JXL** как *выход* зависят от версии ImageMagick. В песочнице ImageMagick 6 не умел писать AVIF; в нём же нет `SVG`/`JXL`. Нужен **ImageMagick 7** с делегатами.
- **HEIC** — надёжность как у VERT «нестабильна»; читать удаётся, писать зависит от сборки.
- Видео: параметры фиксированные (`libx264 -crf 23 -preset medium`, libvpx `-b:v 2M -crf 10`). Ползунка качества видео нет.
- Фото→A4 с `contain` не искажает пропорции, поэтому у снимков 4:3 по краям будут белые поля.
- Файлы до 2 ГБ на запрос (`MAX_CONTENT_LENGTH`).
- Многостраничные PDF в режиме `image`: берётся только первая страница (`[0]`).
- Сервер слушает только `127.0.0.1`. Для доступа из сети поменять `host` в конце `app.py` на `0.0.0.0` (и понимать риски: авторизации нет).

---

## 8. Баги, которые уже ловили (не повторять)

1. **404 при скачивании.** Причина: автоудаление результата по `beforeunload` + очистка предыдущего задания при новой конвертации. Решение: чистка только по возрасту (`purge_old_jobs`).
2. **Красные поля вместо белых.** `Image.new("RGB", size, 255)` даёт `(255,0,0)`, потому что число читается как красный канал. Всегда использовать `white_canvas()` (кортеж `(255,255,255)` для RGB, `255` для `L`). Баг проявлялся и в «Фото в A4», и в «Фото в PDF».
3. **Сервер не стартовал** после правки: при регенерации кода потерялась строка `def poppler_ok()`. После любых больших правок делать `python -c "import ast; ast.parse(...)"` и проверку «вызываемые функции определены».
4. **EPS + `-paper A4`** — poppler отвергает ключ (`page size options may not be used with eps output`). Для EPS/SVG размер листа не задаётся.
5. **MPEG-2 (mpg/mpeg/vob)** падает при нестандартном fps (15, 23.976): нужен `-r 25`. **MXF** ещё капризнее: `-vf fps=25 -b:v 8M -ac 2 -ar 48000`.
6. **Строгие битрейты:** mp2, mp3, ac3, wma принимают только сетку значений. Функция `snap_bitrate()` подгоняет к ближайшему допустимому (иначе ffmpeg молча пишет 0 байт).
7. **Opus/WebA** работают только на 8/12/16/24/48 кГц: остальные принудительно заменяются на 48000.
8. **DivX** — это AVI с mpeg4: писать через `-f avi`, расширение `.divx`.
9. **Кириллица в имени скачиваемого файла:** одного `download_name=` мало, нужен ручной `Content-Disposition` с ASCII-запасным именем и `filename*=UTF-8''`.
10. **Bash-массивы под `sh`:** тестовые скрипты с массивами (`FILES=(...)`) падали с `Syntax error: "(" unexpected`. Запускать через `bash script.sh`.
11. **Порядок форматов:** алфавитный список ставил `3G2, 3GP` первыми в режиме «Аудио». Теперь `POPULAR` в `converters.py` задаёт приоритет (mp3, wav, flac… первыми).

---

## 9. Что осталось / идеи для продолжения

Приоритет по мнению автора (сверху важнее):

1. **Проверить на Windows** реального пользователя: установка poppler/ffmpeg/ImageMagick/pandoc, `run.bat`. Возможно, нужен раздел «как поставить всё одной командой» (`choco install poppler ffmpeg imagemagick pandoc libreoffice-fresh python`).
2. **Dockerfile + docker-compose** — пользователь упоминал домашний сервер. Один контейнер со всеми движками, порт 5000. Тогда ничего ставить на хосте не надо. Не забыть про `host=0.0.0.0` и, возможно, базовую авторизацию.
3. **Прогресс по файлам** — сейчас общий индикатор «Конвертирую…» без процентов. Для видео это ощутимо. Варианты: SSE/WebSocket или опрос `/api/progress/<job>`, парсинг `-progress pipe:1` у ffmpeg.
4. **Отмена конвертации** (в VERT есть).
5. **Настройки видео:** качество/разрешение/fps/кодек (VERT использует `vertd` для этого; здесь можно расширить через ffmpeg).
6. **Слияние PDF, разделение, поворот** — просили только фото→PDF, но напрашивается.
7. **Другие форматы листа** (A3, A5, Letter) — сейчас константа `A4_MM`.
8. **Перенос конвертеров в отдельные потоки/очередь**, чтобы большая пачка видео не блокировала сервер (сейчас `ThreadPoolExecutor` внутри запроса).
9. **Тесты:** сейчас только ручные скрипты. Стоит сложить проверки из раздела 6 в `pytest`.
10. ~~README.md~~ — обновлён, описывает все 7 режимов.
11. Локализация (интерфейс только русский, у VERT есть paraglide-i18n).

### Что можно ещё взять из VERT
VERT (`VERT-main.zip`) — Svelte-приложение. Использовано: наборы форматов и параметры ffmpeg. **Не использовано:** `vertd` (серверное видео с GPU), настройки `vertdSpeed`, метаданные через `music-metadata`, парсеры `.ani`/`.icns` (`src/lib/util/parse`), Stripe/донаты (не нужно), i18n (`messages/`). Лицензия AGPL-3.0: если публиковать проект, код VERT-происхождения (таблицы кодеков, логика битрейтов) обязывает к AGPL.

---

## 10. Как тестировать после изменений

```bash
# 1. синтаксис + «все ли функции определены»
python -c "import ast;ast.parse(open('app.py',encoding='utf-8').read())"
python -c "import ast;ast.parse(open('converters.py',encoding='utf-8').read())"

# 2. JS из шаблона (Jinja-вставку заменить литералом)
python - <<'EOF'
import re
t=open('templates/index.html',encoding='utf-8').read()
js=re.search(r"<script>(.*)</script>",t,re.S).group(1).replace("{{ 'true' if (poppler.pdftoppm and poppler.pdftocairo) else 'false' }}","true")
open('/tmp/ui.js','w',encoding='utf-8').write(js)
EOF
node --check /tmp/ui.js

# 3. движки напрямую, без Flask
python - <<'EOF'
from pathlib import Path; import converters as C
print(C.engines())
# C.convert_any(Path('in.mp3'), Path('out'), 'name', 'flac', {})
EOF

# 4. запуск и проверка HTTP (сервер и curl — в ОДНОЙ команде/скрипте)
python app.py &  sleep 2
curl -s -F files=@tone.mp3 -F to=flac http://127.0.0.1:5000/api/universal
```

Проверять **реальные пиксели/потоки**, а не «файл создался»: `ffprobe -show_entries stream=codec_type`, `PIL.Image.getpixel`, `pdfinfo`, `pdfimages -list`. Скриншоты UI: Playwright (`chromium.launch()`), эмуляция телефона `isMobile:true`, обе цветовые схемы через `colorScheme`.

**Подводные камни песочницы:** фоновый сервер не переживает отдельный вызов инструмента — запускай сервер и клиента в одной команде. Не использовать `localStorage` в артефактах Claude.ai, но в самом приложении он используется нормально (палитра).

---

## 11. Приложение: сводка форматов

**Картинки — читает / пишет:** png, jpg, jpeg, webp, gif, avif, heic, ico, bmp, tiff, tif, psd, jfif, jpe, pbm, pgm, ppm, pnm, hdr, cur, pdf, eps. **Только читает:** svg, heif, ani, icns, RAW-камеры (nef, cr2, arw, dng, xcf, rw2, raf, orf, pef, mos, raw, dcr, crw, cr3, 3fr, erf, mrw, mef, nrw, srw, sr2, srf).

**Аудио — читает / пишет:** mp3, wav, flac, ogg, oga, opus, aac, alac, m4a, wma, amr, ac3, aiff, aifc, aif, mp2, au, m4b, voc, weba. **Только читает:** mogg, caf, mp1, mpc, dsd, dsf, dff, mqa.

**Видео — читает / пишет:** mkv, mp4, avi, mov, webm, ts, mts, m2ts, wmv, mpg, mpeg, flv, f4v, vob, m4v, 3gp, 3g2, mxf, ogv, divx. **Только читает:** rm, rmvb.

**Документы — читает / пишет:** docx, doc, md, html, rtf, csv, tsv, json, rst, epub, odt, docbook, txt, tex, org, fb2.

**PDF → картинки:** jpg, png, tiff, webp, svg, ps, eps.

---

## 12. Итог состояния на момент передачи

- Все 7 режимов работают, регрессионно проверены через HTTP.
- Интерфейс: Material You, светлая/тёмная тема, 8 seed-цветов, адаптив (rail ↔ bottom bar), в Chromium без JS-ошибок.
- Последнее исправление: порядок форматов по популярности (MP3/WAV/FLAC… первыми, а не алфавит).
- Актуальный архив: `popple.zip` (содержит всё описанное выше, включая Material You, VERT-форматы и этот документ).

---

## 13. Universal: что добавлено поверх popple

Проект переименован в **Universal**, все старые 7 режимов не тронуты. Добавлены три режима и фоновые задачи.

| Режим | Эндпоинт | Модуль |
|---|---|---|
| `edit` (Редактор) | `POST /api/edit` → задача | `editor.py` (ffmpeg, `-filter_complex`, `-progress pipe:1`) |
| `dl` (Скачать видео) | `POST /api/dl/info` (предпросмотр), `POST /api/dl` → задача | `downloader.py` (yt-dlp как библиотека) |
| `qr` (QR-коды) | `POST /api/qr` (один, картинка; `?download=1`), `POST /api/qr/bulk` | `qrtools.py` (порт `tools/bulk_qr.py`) |

- **Задачи** (`tasks.py`): id задачи = id папки задания. `GET /api/task/<id>` → `status/progress/message/done/failed/names/single_file`, `POST /api/task/<id>/cancel` убивает ffmpeg или прерывает yt-dlp через хук. `purge_old_jobs` не трогает работающие задачи.
- **Скачивание результата**: ZIP теперь собирается на диске (`bundle.zip`), а не в памяти: видео бывают большими. `GET /api/file/<job>/<name>` отдаёт один файл из многофайлового результата.
- **Редактор**: время понимает `90`, `1:30`, `1:02:03.5`, `1м30с`. Сжатие до N МБ считает битрейт из длительности (3% на контейнер, звук 96k). Склейка нормализует кадр к первому файлу (scale+pad), fps, звук 48 кГц стерео, для файлов без звука подставляет тишину. «Быстро» = `-c copy`, режет по ключевым кадрам.
- **Загрузчик**: ошибки yt-dlp переводятся в человеческие (`_human_error`). cookies.txt одноразовый, удаляется. run.bat/run.sh обновляют yt-dlp при каждом запуске.
- **QR**: рисуем сами по матрице, поэтому цвета/прозрачность одинаковы в PNG/JPG/SVG. В пачке рядом с кодами кладётся `index.csv`.
- **Окружение**: `HOST`, `PORT`, `APP_PASSWORD` (HTTP Basic), `WORK_DIR`, `TASK_WORKERS`. Есть `Dockerfile` и `docker-compose.yml`.
- **Тесты**: `tests/test_app.py` (pytest), 30+ проверок на сгенерированных ffmpeg роликах с ffprobe; загрузчик тестируется через локальный HTTP-сервер (generic-экстрактор yt-dlp), настоящий YouTube только с `NET=1`.

Что не проверено: реальные YouTube/TikTok из песочницы (сеть закрыта прокси), работа на Windows.

### 13.1 Вторая итерация
- Режим **`pdft` (Правка PDF)**: `POST /api/pdf` → задача, `pdftools.py` на pypdf. Операции `merge/split/extract/rotate/compress/protect/unlock`, страницы задаются как `1-3, 5, 8-`. Сжатие пережимает картинки в JPEG и уменьшает до `max_px`; если файл не стал меньше, отдаётся оригинал. Шифрование AES-256 требует `cryptography`.
- **`/api/universal` теперь тоже задача** (прогресс по числу готовых файлов, отмена снимает ещё не начатые). Ответ: `{task, ...}`, результат через `/api/task/<id>`.
- Картинки: поле `resize` (px по длинной стороне, только уменьшение, `-resize NxN>`).
- run.bat/run.sh при каждом запуске делают `pip install -r requirements.txt`, чтобы у старых установок появились новые зависимости.

### 13.2 Windows-приложение (v1.1.0)
- `desktop.py`: werkzeug-сервер на случайном порту 127.0.0.1 в потоке + окно pywebview (WebView2), `ALLOW_DOWNLOADS`, `private_mode=False` (localStorage с цветом живёт), логи в `%LOCALAPPDATA%\Universal\universal.log`, если нет консоли. На Windows подменён `subprocess.Popen` с `CREATE_NO_WINDOW`, иначе ffmpeg мигает консолью. `--smoke` проверяет собранный exe (код выхода).
- `engines.py`: каталог движков (BtbN ffmpeg gpl, pandoc, poppler-windows, ImageMagick portable, zipapp yt-dlp), установка в фоне через `POST /api/engines/install/<name>`, `paths.json` с папками для PATH, `activate()` до импорта app. Свежий yt-dlp подключается через `sys.path` при следующем запуске. Автоустановка только на Windows (`UNIVERSAL_ENGINES_ANYOS=1` для тестов).
- Интерфейс: если `engines.can_install`, вместо «установи и перезапусти» показывает кнопку «Скачать <движок>» с прогрессом и перезагружает страницу.
- Сборка: `packaging/universal.spec` (onedir, без консоли, иконка), `packaging/installer.iss` (per-user, докачка WebView2 при отсутствии), job `windows` в release.yml; job `windows` в tests.yml ставит движки через engines.py и гоняет pytest на Windows.

### 13.3 Linux и macOS (v1.2.0)
- Linux: `packaging/linux/build.sh <v> <out>` собирает дерево `/usr/lib/universal` (код + `vendor/` с чистыми Python-библиотеками из `vendor.txt`; ускорители markupsafe удаляются, другие `.so` в vendor считаются ошибкой), лаунчер `/usr/bin/universal` (PYTHONPATH на vendor), `.desktop`, иконки hicolor, и `.deb`. `build-rpm.sh` и `build-arch.sh` упаковывают то же дерево в контейнерах `fedora:latest` и `archlinux:base-devel`. Системные зависимости: python3 ≥ 3.10, PyGObject + WebKit2GTK 4.1/4.0, Pillow, cryptography, ffmpeg, ImageMagick, pandoc, poppler.
- `desktop.py --smoke` на Linux ещё импортирует `webview.platforms.gtk`. `smoke.sh` проверяет и движки из зависимостей. Проверено локально: `.deb` ставится в Ubuntu 22.04 и 24.04, окно открывается под Xvfb.
- macOS: `universal.spec` на darwin добавляет `BUNDLE` (`Universal.app`, `universal.icns`), job `macos` (arm64 + `macos-15-intel`) делает `.dmg`. Из Finder нет PATH Homebrew, поэтому `desktop.py` добавляет `/opt/homebrew/bin` и `/usr/local/bin`. Бинарные движки на macOS и Linux не качаются (`engines.can_install`), интерфейс показывает команду `brew install` / `apt install`; свежий yt-dlp качается везде.

### 13.4 QR в документе (v1.3.0)
- `qrdoc.py`: `scan()` возвращает уникальные ссылки (`url`, `text`, `count`), `apply()` заменяет выбранные QR-кодами (`mode=replace`) или ставит QR после них (`after`). Endpoint-ы `POST /api/qrdoc/scan` и `/api/qrdoc/apply` (поле `urls` — JSON-список), вкладка «Документ» в режиме QR.
- DOCX правится строками XML, а не через ElementTree: ET переименовывает префиксы и теряет объявления из `mc:Ignorable`, после чего Word считает файл повреждённым. Обрабатываются `w:hyperlink` (через rels), поля `HYPERLINK` (fldChar begin…end) и URL в `w:t` (run с одним `w:t` режется на «текст / QR / текст» с тем же `w:rPr`). Картинки — `word/media/universal_qr_N.png`, rels `rIdUniversalQrN`, `Default png` в `[Content_Types].xml`, `wp:inline` объявляет свои пространства имён сам. Колонтитулы и надписи не трогаются.
- Markdown: `[текст](url)`, `<url>`, голые URL; блоки кода, инлайн-код и картинки пропускаются; QR пишутся как `<img src="имя_qr_001.png" width height>` рядом с .md (результат — ZIP). HTML: `<a href>` → `<img src="data:image/png;base64,…">`.
- ODT/RTF/DOC/EPUB сначала в DOCX через `converters.convert_document`.
- Проверено: docx из pandoc и LibreOffice, ODT → docx; результат открывается в LibreOffice (PDF с 4 картинками QR), текст вокруг ссылок на месте.

### 13.5 Загрузчик в отдельном процессе (v1.3.1)
- `downloader.run_download_task` запускает `worker_cmd()`: из исходников `python downloader.py --worker args.json`, из exe/.app сам exe с `--dl-worker` (развилка в начале `desktop.main`). Воркер пишет события JSON-строками (ASCII) в `dl_events.jsonl`, родитель читает файл раз в 0,3 с; stdout не используется, потому что в exe без консоли его может не быть.
- «Отмена»: `tasks.kill_tree()` — на Windows `taskkill /T /F`, на POSIX `killpg` своей группы (воркер стартует с `start_new_session`; если группа общая с приложением, убивается только сам процесс, иначе погибло бы приложение). После отмены `_cleanup()` удаляет `.part`/фрагменты.
- `build_opts`: `concurrent_fragment_downloads=4`, `js_runtimes` deno/node/bun. В каталоге движков `deno` (ссылка по платформе, `chmod +x` вне Windows), `yt-dlp-ejs` в requirements, vendor.txt и PyInstaller (`collect_data_files`). Баннер в режиме «Скачать видео», если deno нет.
- `desktop.py --smoke` проверяет и воркер (`dl_worker`).

### 13.6 cobalt (v1.4.0)
- `downloader._cobalt_download`: `POST /` на сервер cobalt (JSON, `Authorization: Api-Key …`), ответы `tunnel`/`redirect` (один файл) и `picker` (несколько; одинаковые имена получают « (2)»), `local-processing` не запрашиваем. Коды ошибок приходят как `error.api.<код>` (в доке без префикса), переводятся через `COBALT_ERRORS`. При любой ошибке ссылка уходит в yt-dlp, в итоговой ошибке видны обе причины. Плейлисты сразу через yt-dlp.
- Настройки: поля в интерфейсе (localStorage) или `COBALT_URL`/`COBALT_API_KEY`. `POST /api/dl/cobalt-check` — `GET /` сервера (версия, сервисы).
- Проверено на настоящем cobalt 11.7.1 (собран из исходников): проверка сервера, ошибка `link.invalid` → yt-dlp скачал. Тесты на поддельный сервер: tunnel, picker, ключ, ошибки, опции.

### 13.7 Свой cobalt в Docker (v1.5.0)
- `cobaltlocal.py`: по инструкции cobalt «how to run an instance» пишет `docker-compose.yml` в `engines.root()/cobalt` (рядом с движками), `docker compose -p universal-cobalt pull`, затем `up -d`, и ждёт `GET /` через `downloader.cobalt_info`. Порт `127.0.0.1:9000…9010` (первый свободный, при перезапуске остаётся прежний, хранится в `port.txt`), `API_URL=http://localhost:<порт>/`. watchtower из примера не ставим (не работает с Docker 29+), вместо этого кнопка повторно делает pull. Если pull не удался, но образ уже есть, сервер всё равно поднимается.
- Docker ищется в PATH и в стандартных местах Docker Desktop; если демон не отвечает, на Windows/macOS запускается Docker Desktop и до 3 минут ждём. На Linux — подсказка `systemctl start docker`.
- API: `GET /api/cobalt/local` (статус), `POST` (задача создания, адрес в `result` задачи — новое поле `Task.result`), `DELETE` (`docker compose down`).
- Тест — поддельный `docker`-скрипт. В песочнице настоящий образ с ghcr.io не скачивается (прокси), проверены сообщение об этой ошибке и `docker compose config` для сгенерированного файла.

### 13.8 Сеть и VPN (v1.5.0)
- `downloader.read_net(form)` → `{"proxy": "auto"|"none"|URL, "ipv4": bool}` (форма или `UNIVERSAL_PROXY`), входит в `read_opts`. `net_opts()` даёт yt-dlp `source_address=0.0.0.0` (только IPv4) и `proxy`: для `auto` ничего не задаём (yt-dlp сам берёт системный прокси вместе с исключениями), кроме socks4 из реестра Windows — его переписываем в `socks5h://`.
- `_net_attempts`: если прокси системный и ошибка сетевая (`_is_net_error`), ссылка повторяется напрямую (`probe_url` и цикл в `_download_all`). Явно указанный прокси не обходится.
- `_urlopen` для cobalt: loopback всегда без прокси (раньше системный прокси VPN перехватывал и localhost:9000).
- `POST /api/dl/netcheck` → `net_check()`: системный прокси, итоговый прокси, IPv6 (TCP до youtube.com:443 за 4 с), 4 сайта через `YoutubeDL.urlopen` с теми же настройками; причины коротко через `_short_net_error`.
- Настоящий VPN в песочнице не проверить; тест — мёртвый системный прокси (`http://127.0.0.1:9`) с откатом напрямую, плюс подделанный `YoutubeDL` для netcheck.

### 13.9 cobalt через VPN (v1.5.2)
- `cobaltlocal.container_proxy(net)`: `downloader.effective_proxy` → socks заменяется на http (undici в cobalt умеет только HTTP-прокси, у VPN-клиентов порт обычно смешанный), loopback → `host.docker.internal`. В compose: `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY=localhost,127.0.0.1,::1`, `extra_hosts: host.docker.internal:host-gateway`, `RATELIMIT_MAX`/`TUNNEL_RATELIMIT_MAX`/`SESSION_RATELIMIT_MAX=10000`. Прокси пишется в `proxy.txt`, его показывает статус.
- `POST /api/cobalt/local` принимает поля сети (`proxy`, `ipv4`) как `/api/dl`.
- Проверено на cobalt 11.7.1 из исходников: с `HTTP_PROXY` запросы к www.youtube.com и api.vimeo.com идут через прокси (CONNECT в журнале тестового прокси), с `RATELIMIT_MAX=10000` 25 запросов подряд без `rate_exceeded`; наш JSON-запрос принят во всех 64 сочетаниях настроек (нет `invalid_body`). Сгенерированный compose проходит `docker compose config`.
- `COBALT_ERRORS` дополнен кодами из исходников cobalt 11.7.1; неизвестный код показывается как есть. Для своего сервера (`_is_loopback`) к `fetch.*`/`youtube.login` добавляется подсказка про VPN.

### 13.10 Пустые файлы от cobalt (v1.5.3)
- Источник бага (cobalt 11.7.1): `stream/internal.js` `handleChunkedStream` при не-200 или ошибке HEAD к googlevideo делает `res.end()` — туннель отвечает 200 и пустым телом; склейка `ffmpeg.js` при пустых входах тоже закрывает ответ пустым. Кроме того, `stream/proxy.js` качает прямые файлы через `new Agent()`, то есть **мимо** `HTTP_PROXY` (заблокированные в РФ сайты через свой cobalt в режиме прокси VPN не скачаются, в режиме TUN — да).
- `_fetch_file`: `got == 0` → `CobaltFail(EMPTY_TUNNEL)`; `Content-Length` и меньше байт → «оборвался»; `_media_ok()` — `ffprobe -read_intervals %+#5 -count_packets`, годен, если хоть в одном потоке есть пакеты (проверено на mp4, фрагментированном mp4, webm из пайпа, mp3, jpg; пустой, только заголовок, обрезанный, мусор — отбраковываются). Нет ffprobe — проверка только по размеру. `.part` удаляется, CobaltFail → `_download_all` качает ту же ссылку yt-dlp.
- `_cobalt_download` удаляет уже скачанные файлы карусели при ошибке. `_cd_filename` берёт `filename*=UTF-8''…` (cobalt шлёт `filename="????"` для старых клиентов).
- Тесты: `broken_cobalt` (пустое, мусор, обрезанное тело) — на старом коде задача «готово» с `клип.mp4` 0 байт, на новом файл скачан yt-dlp.

### 13.11 Android и iOS (v1.6.0)
- `mobile/` — Capacitor 8. Нативные проекты `android/` и `ios/` в репозитории. `www/` не коммитится: его собирает `node build-www.mjs`.
- `build-www.mjs` склеивает свой каркас и куски сайта:
  - из каркаса: `src/index.html`, `src/mobile.js`;
  - из `site/index.html`: `<style>`, `<section id="try">` и последний inline-`<script>`.
  - Если на сайте поменять эти куски, сборка упадёт с понятной ошибкой.
- Сайт сохраняет через `window.universalSave`, если он есть. На телефоне это запись в `Directory.Documents/Universal` через Filesystem. Service worker в Capacitor не регистрируется.
- Сеть идёт через `CapacitorHttp`, а не fetch: так нет CORS у Flask, и http:// к домашнему серверу не блокируется.
- Файлы качает `Filesystem.downloadFile`, не в память. Basic-auth собирается в UTF-8.
- Скачивание только через свой сервер: `POST /api/dl` (urlencoded) → опрос `/api/task/<id>` → `/api/file/<job>/<name>` для каждого имени из `names`. Файл 0 байт удаляется.
- Android:
  - `ShareInPlugin` (ACTION_SEND text/plain) шлёт событие `shared` с retain;
  - cleartext разрешён, `requestLegacyExternalStorage`, права на хранилище до API 29;
  - `versionName`/`versionCode` задаются через `-P`;
  - ключ подписи `android/app/universal.jks` (пароль `universal-public`) лежит в репозитории, чтобы обновления вставали поверх. Его можно заменить секретами `ANDROID_KEYSTORE_BASE64` и `ANDROID_KEYSTORE_PASSWORD`, но тогда старое приложение придётся переустановить.
- iOS:
  - сборка без подписи, `CODE_SIGNING_ALLOWED=NO`, `Payload/App.app` → .ipa для AltStore и Sideloadly;
  - в Info.plist: `UIFileSharingEnabled`, ATS `NSAllowsArbitraryLoads`, описание доступа к локальной сети.
- CI: `.github/workflows/mobile.yml`. На PR и main собирает и кладёт в артефакты; из `release.yml` (job `mobile`) выкладывает в релиз.
- Иконки и заставки: `python mobile/icons.py` из `packaging/icon.png`.
- (до v2.6.0) На телефоне не было yt-dlp и ffmpeg, полная версия работала только через свой сервер. С v2.6.0 на Android она встроена, см. 13.18.

### 13.12 cobalt убран (v1.6.0)
- По решению владельца cobalt удалён целиком, видео качает только yt-dlp. Разделы 13.6–13.10 остались для истории.
- Удалены:
  - `cobaltlocal.py`;
  - маршруты `/api/dl/cobalt-check` и `/api/cobalt/local`;
  - в `downloader.py`: `COBALT_*`, `CobaltFail`, `_cobalt_*`, `_fetch_file`, `_media_ok`, `_cd_filename`, `EMPTY_TUNNEL`, `_urlopen`, `_is_loopback`, а также `cobalt_url` и `cobalt_key` в `read_opts`;
  - блок в шаблоне, сервис в `docker-compose.yml`, cobalt-тесты, вариант cobalt в телефоне.
- Блок «Сеть и VPN» (прокси, IPv4, «Проверить сеть») остался: он для yt-dlp.

### 13.13 OCR и сервер для телефона (v1.7.0)
- `ocr.py`: OCRmyPDF запускается **отдельным процессом**, поэтому «Отмена» его убивает (`task.proc`). Как запускается:
  - из исходников: `python -m ocrmypdf`;
  - из exe: тем же exe с ключом `--ocrmypdf` (`desktop.main` → `ocrmypdf.__main__.run`);
  - всегда с `--use-threads`, без multiprocessing внутри exe.
- Ghostscript не нужен. С 17.13 PDF/A собирается через `--pdfa-backend internal`, если gs нет; при коде 10 файл пересобирается обычным PDF.
- Старый системный OCRmyPDF (Linux) не знает `--pdfa-backend`, поэтому ключ ставится только для версий ≥ 17.13.
- Режим «если уже есть текст»: skip → `--skip-text`, redo → `--redo-ocr` (без deskew и rotate, они несовместимы), force → `--force-ocr`. Коды выхода переведены в `EXIT_TEXT`.
- Список языков берётся из `tesseract --list-langs` (`/api/formats` → `engines.ocr_info`).
- `engines.py`, Tesseract для Windows:
  - установщик NSIS ищется в нескольких релизах `tesseract-ocr/tesseract` (не в каждом он есть), запасной источник — индекс UB Mannheim;
  - распаковывает полный 7-Zip (`_seven_zip_full`: установщик 7-Zip из `ip7z/7zip` открывается через 7zr.exe, запасной путь — MSI через `msiexec /a`);
  - языки rus, eng, osd докачиваются из `tesseract-ocr/tessdata`;
  - проверяется в CI на Windows-раннере (`tests.yml`).
- PyInstaller: `collect_submodules`, `collect_data_files` и `copy_metadata("ocrmypdf")`. Самопроверка exe запускает `--ocrmypdf --version`.
- `phoneserver.py`: второй werkzeug-сервер на 0.0.0.0, порт 5000–5020 (прежний по возможности), с тем же `app` и общими `TASKS`.
  - WSGI-обёртка ставит `environ["universal.phone"]`; `require_password` для таких запросов требует пароль телефона.
  - `/api/phone*` доступны только с 127.0.0.1 и не через сервер для телефона.
  - Настройки лежат в `<UNIVERSAL_HOME>/phone.json`; `autostart` вызывается из `desktop.main` и из `app.py __main__`. В Docker сервер не нужен (`UNIVERSAL_DOCKER=1`).
  - QR содержит `universal://connect?url=…&password=…`.
- Телефон:
  - `@capacitor/app` (`appUrlOpen`, `getLaunchUrl`), intent-filter VIEW `universal://connect`, `CFBundleURLTypes` на iOS;
  - сканер — getUserMedia плюс BarcodeDetector (Android) или jsQR (iPhone), с разрешениями CAMERA и NSCameraUsageDescription;
  - «Вставить ссылку» читает буфер обмена.
- Баг масштаба на телефоне: grid-элементы с длинным именем имели `min-width: auto`. Теперь `minmax(0, 1fr)` и `min-width: 0` (то же на сайте).

### 13.14 Обновления из приложения (v1.8.0)
- `version.py` хранит `VERSION`. В репозитории лежит номер следующего релиза, а `release.yml` в шагах «Версия» перезаписывает файл из входного `version`.
  - zip собирается так: `git archive --add-file=version.py` с исключением закоммиченного.
  - Linux: `build.sh` пишет `version.py` в `/usr/lib/universal`.
- `updater.py`:
  - последний релиз берётся через API, при лимите — через страницу релиза (`engines._assets_from_html`); кэш 6 ч в `<UNIVERSAL_HOME>/update.json`;
  - `kind()` определяет способ установки: installer (frozen win), dmg (frozen mac, по `platform.machine`), deb/rpm/arch (пакет в `/usr/lib/universal` и `/etc/os-release`), source, docker.
- Установка:
  - Windows: `Universal-Setup-X.exe /SILENT /SP- /NOCANCEL /CLOSEAPPLICATIONS`; приложение закрывает себя через `app.config["QUIT_APP"]`, его задаёт `desktop.main`. `installer.iss` в тихом режиме запускает новую версию (`Check: WizardSilent`).
  - macOS и Linux: файл открывается (`open` или `xdg-open`), интерфейс показывает команду для терминала.
- `/api/update` (GET проверка, POST установка) доступен только локально (`_local_only`).
- Интерфейс: `#updateBanner`, строка версии в окне палитры, «Позже» сохраняет версию в `localStorage.skipVersion`.
- Телефон:
  - `mobile.js` раз в 6 ч запрашивает `api.github.com/.../releases/latest` (CapacitorHttp);
  - Android: APK качается в `Directory.Cache`, затем `ApkInstallPlugin.install` вызывает FileProvider и `ACTION_VIEW`; без разрешения открывает `ACTION_MANAGE_UNKNOWN_APP_SOURCES`. В манифесте `REQUEST_INSTALL_PACKAGES`;
  - iOS: ссылка на релиз.

### 13.15 Автомерж
- `.github/workflows/automerge.yml` срабатывает на `workflow_run` (завершение «Тестов» или «Телефонов», событие pull_request, ветка `claude/…`, тот же репозиторий).
- Порядок работы:
  1. ищет открытый не-черновик PR на этом `head_sha`;
  2. до 20 мин ждёт, пока доедут остальные check-runs коммита;
  3. если что-то красное или есть конфликт, ничего не делает;
  4. иначе выполняет `gh pr merge --merge`.
- Слияние от `GITHUB_TOKEN` не запускает workflow на push в main, поэтому после него вызывается `gh workflow run pages.yml`.
- Работает только после того, как сам файл попал в main: `workflow_run` берётся из ветки по умолчанию.

### 13.16 Вкладка «Информация» (v1.9.0)
- `about.py`: версии движков через `--version` (параллельно, кэш до перезапуска, `?refresh=1` пересчитывает), версия приложения, способ установки (`updater.kind`), ОС, пути `engines.root()` и `WORK_DIR`.
- `/api/about`: через сервер для телефона и с чужих адресов пути не отдаются. `/api/about/open` открывает папку данных, работает только локально.
- Компьютер: режим `info` (kind `info`, в `isNew`); `#infoView` показывается вместо `.grid`; карточки режимов строятся из `MODES` и `SUBS`, по клику вызывается `goMode`.
- Телефон: `pageInfo` (пять кнопок в нижнем меню). `loadInfo()` берёт `/api/about` подключённого сервера. Ссылки открываются через `Browser.open`.
- «Для зануд»:
  - `about.nerd(TASKS, host, WORK_ROOT)` → `/api/about/nerd` (только локально), разделы `[{title, rows: [[k, v]]}]`;
  - `version.py` получил `COMMIT` и `BUILT`, их пишут `release.yml` и `build.sh`; без них коммит берётся через `git rev-parse` (из исходников);
  - в окне добавляется раздел «Окно» (UA, экран, тема); копирование в Markdown;
  - на телефоне `collectNerd()` собирает всё на месте, а данные компьютера берёт из `/api/about`; пароль сервера в отчёт не пишется.

### 13.17 Редактор документов, фоторедактор, архивы (v2.0.0)
- `static/` теперь отдаётся Flask (`/static/...`). Он попадает в PyInstaller (`datas`) и в Linux-пакет (`build.sh` копирует `static`). Jodit 4.18.4 (MIT) лежит в `static/vendor/jodit` (сборка fat, с русским) и подгружается лениво при первом открытии редактора.
- Режимы `docedit` и `photoedit` — на всю ширину (`FULL`, вместо `.grid`), их код в `static/docedit.js` и `static/photoedit.js`; наружу каждый отдаёт `window.Universal*Edit.show()`.
- `docedit.py`:
  - открытие через LibreOffice `html:HTML (StarWriter):EmbedImages`, запасной путь — pandoc `--extract-media` и вшивание картинок в data: URI;
  - скрипты и `on*` из чужого HTML вырезаются;
  - сохранение — LibreOffice `--infilter="HTML (StarWriter)"` (иначе откроется как веб-страница) или pandoc;
  - **картинки остаются data: URI**: файл рядом с HTML LibreOffice вставил бы в DOCX внешней ссылкой на временную папку.
- `/api/doc/open|save|caps`, `/api/photo/load` (Pillow, иначе `magick ...[0]` → PNG для браузера).
- `filetools.py` и `/api/files`:
  - имена и `relpath` (webkitRelativePath / `entry.fullPath` при перетаскивании) сохраняются как у пользователя;
  - zip — stdlib, 7z — py7zr (AES-256, `header_encryption`);
  - распаковка через `_safe_target` (без zip-slip), cp866 для имён без флага UTF-8;
  - суммы пишутся файлами `*SUMS.txt`, переименование — копиями.
- Вложенные результаты: `tasks.result_files()` (rglob, без скрытых), `/api/download` пакует с относительными путями, `/api/file/<job>/<path>` пускает вложенные пути, но не выше `out`.
- Фоторедактор считает цвет попиксельно (ImageData), без `ctx.filter`: в WKWebView и WebKitGTK его нет. Размытие идёт через `ctx.filter`, если он есть, иначе уменьшением и растяжением.
- Редактор PDF (`static/pdfedit.js`) работает целиком в браузере: pdf.js 4.10.38 legacy (`static/vendor/pdfjs`, Apache-2.0, файлы `.mjs` переименованы в `.js`, плюс `cmaps` и `standard_fonts`) рисует страницы, pdf-lib 1.17.1 (`static/vendor/pdflib`, MIT) собирает файл. `app.py` регистрирует `text/javascript` для `.js`: ES-модулю нужен JS-тип, а реестр Windows иногда отдаёт `text/plain`.
- Объекты хранятся в пунктах в «базовом виде» страницы (с поворотом из файла, без нашего). При сохранении «замазать» рисуется векторным белым прямоугольником, остальное — одной PNG на занятую область (4 px на пункт), с поворотом по `/Rotate` исходной страницы; потом ставится итоговый `/Rotate`. Зашифрованные или битые для pdf-lib PDF сохраняются только «сплющенными» (страницы картинками JPEG 200 DPI).
- «Что нового» (`changelog.py`, `/api/whatsnew?since=`): окно после обновления. На компьютере последняя показанная версия хранится в localStorage `seenVersion`, CHANGELOG.md кладут в сборку spec и build.sh. В телефоне `build-www.mjs` вшивает разделы CHANGELOG в `APP_CHANGES`, показ по `u.seenVersion`; первый запуск после установки (нет ни `u.seenVersion`, ни `u.updChecked`) окно не показывает.
- Вывод внешних программ читаем с `encoding="utf-8", errors="replace"` (в Windows `text=True` = cp1252: поток чтения падал и stdout становился None — так ломались ffprobe в видеоредакторе и pandoc).
- Окно приложения слушает постоянный порт из `desktop.PORTS` (47615–47624), иначе localStorage (origin) терялся при каждом запуске. «Что нового» помнит показанную версию в `<UNIVERSAL_HOME>/seen_version.txt` (`/api/whatsnew?auto=1`).
- `static/hints.js`: карточки форматов, быстрый выбор, «Настройки под формат», «?» у настроек (`HELP` по id элементов). Прогресс ffmpeg в конвертере: `converters._run_progress` + `progress=` в `convert_any`.
- HEIC/AVIF: если ImageMagick не умеет писать формат, `converters._pillow_encode` (Pillow + pillow-heif, необязательный).
- Голос в текст (`stt.py`, `/api/stt`): faster-whisper на CPU (int8). Модели качаются сами в `<UNIVERSAL_HOME>/whisper/<fast|normal|best>` с huggingface.co, при ошибке с hf-mirror.com (свой загрузчик через API `siblings`, без huggingface_hub). В PyInstaller: `collect_dynamic_libs("ctranslate2")`, onnxruntime исключён (VAD не включаем). Пакеты Linux его не содержат (в vendor только чистый Python): баннер предлагает `pip install --user faster-whisper`. Тест с настоящей моделью `test_stt_real_voice_message` идёт в CI (STT_REAL=1, espeak-ng, кэш модели).
- Телефон: карточка «Голос в текст» отправляет файл на `/api/stt` через CapacitorHttp (`dataType: 'formData'`, `base64File`). Android: `ShareInPlugin` принимает SEND с audio/*, video/*, копирует файл в кэш и шлёт `shared` с `file`.
- Первый запуск (`engines.auto_setup`, `app.start_setup`, `/api/setup`): настольная версия сама ставит `engines.AUTO`, которых нет ни в `paths.json`, ни в PATH (`engines.missing`). На macOS ffmpeg (Martin Riedl, ffmpeg и ffprobe двумя архивами, `urls`) и pandoc (`{arch}-macOS.zip`) качаются сами (`CATALOG[...]["mac"]`, `_spec`), остальное — `brew install`, если Homebrew есть. Загрузки повторяются до 3 раз.
- `desktop.py`: на Windows проверяет WebView2 по реестру и при отсутствии ставит официальный bootstrapper (`/silent /install`); `webview.start(gui="edgechromium")`, чтобы не было отката на MSHTML. Любая ошибка окна → `_browser_mode` (обычный браузер, выход через 2 минуты после последнего `/api/ping`). `--browser` запускает так принудительно. Падение при старте → `_crash`: MessageBox/osascript с причиной и путём к `universal.log`.
- PyInstaller на Windows кладёт msvcp140/vcruntime140/vcomp140/concrt140 из System32 рядом с exe (app-local VC++). macOS: `codesign --force --deep --sign -` после сборки, `packaging/macos-first-run.txt` в .dmg.
- Android: `YtdlPlugin.java` (Capacitor-плагин `Ytdl`) — `init`, `update` (свежий yt-dlp с GitHub, JS вызывает раз в сутки), `download` (событие `progress`), `cancel`. Библиотека `io.github.junkfood02.youtubedl-android:library` и `:ffmpeg` 0.18.1 (Maven Central), `abiFilters` (с v2.6.0 только arm64-v8a, см. 13.18), `jniLibs.useLegacyPackaging = true` (Python и ffmpeg распаковываются из jniLibs). Файлы пишутся в public Documents/Universal; какие появились — по снимку папки до и после (`--no-mtime`). В `mobile.js` `viaPhone()` рядом с `viaServer()`, выбор «На телефоне / Через компьютер» (`dl.where`).

### 13.18 Полная версия внутри Android-приложения (v2.6.0)
- Встроенный Python: Chaquopy 17.1.0 (`com.chaquo.python`), Python 3.12, только 64-бит (`-Pabis=`, по умолчанию `arm64-v8a`; у 3.12 нет armeabi-v7a). Пакеты: flask, pillow, qrcode, pypdf, yt-dlp и `requirements-android.txt` (mammoth, python-docx, markdown, markdownify, beautifulsoup4, fpdf2, lxml). `CHAQUOPY_BUILD_PYTHON` в CI — путь к python 3.12 из setup-python.
- `mobile/build-www.mjs` копирует `*.py`, `templates/`, `static/`, `CHANGELOG.md` в `android/app/src/main/python/universal_srv/` (в .gitignore), `extractPackages("universal_srv")` — файлы лежат на диске, Flask читает шаблоны как обычно.
- `PyServerPlugin.java` (`PyServer.start`): `YoutubeDL.init`, `FFmpeg.init`, `Python.start`, затем `android_main.start(...)`: werkzeug на 127.0.0.1:47700 (порт постоянный — localStorage), ссылки `files/bin/ffmpeg|ffprobe` на `nativeLibraryDir/lib*.so` (исполнять можно только оттуда), `LD_LIBRARY_PATH` на библиотеки ffmpeg и python из youtubedl-android, `UNIVERSAL_ANDROID=1`, `UNIVERSAL_HOME=files/universal`.
- Оболочка (`mobile.js`): вкладка «Инструменты» на Android — iframe `#fullFrame` с полной версией. Сохранение: шаблон при `android` перехватывает `a[download]`, `/api/download|file/`, blob:/data: и шлёт родителю `postMessage({type: 'universal-save'})`, оболочка пишет файл в Documents/Universal (`Filesystem.downloadFile`/`writeFile`). Кнопки телефона, обновления и «Открыть папку» в полной версии на Android скрыты, `/api/update` → 403, авто-окно «Что нового» не показывается (его показывает оболочка).
- Замены программ на Android (ветки по `UNIVERSAL_ANDROID`):
  - картинки — Pillow (`converters.convert_image_pillow`); в Pillow от Chaquopy нет libwebp, WebP кодирует Android (`android_pdf.save_webp`, `Bitmap.compress`);
  - PDF в картинки — `android.graphics.pdf.PdfRenderer` (`android_pdf.py`);
  - документы и редактор документов — `pydocs.py` (вместо pandoc/LibreOffice): читает docx/md/html/txt/csv/epub, пишет html/md/txt/docx/pdf;
  - OCR — Tesseract4Android 4.8.0 с JitPack (`android_ocr.py`, словари tessdata_fast качаются в `UNIVERSAL_HOME/tesseract`). 4.9.0 на JitPack собран с ошибкой и отдаёт 404; у JitPack-метаданных модуля неверное имя, поэтому репозиторий с `metadataSources { mavenPom(); artifact() }`;
  - голос в текст — Vosk (`com.alphacephei:vosk-android:0.3.75`, `android_stt.py`), малые модели ru/en/uk качаются с alphacephei.com;
  - скачивание — yt-dlp внутри процесса (`downloader._run_inprocess`).
- Мост Java (Chaquopy): перегрузки с примитивами надо указывать явно — `eraseColor(jint(...))`, `acceptWaveForm(jarray(jbyte)(data), n)`, иначе TypeError «ambiguous».
- Проверка: `.github/workflows/android-e2e.yml` («Android полная версия») собирает x86_64-APK, ставит в эмулятор API 34 и гоняет `mobile/e2e/e2e.py` (17 режимов с настоящими файлами через `adb forward tcp:47700`) и `mobile/e2e/ui.mjs` (снимки через CDP WebView: Playwright к WebView не подключается). На раннере без apt: ffmpeg из `imageio-ffmpeg`, речь — `test.wav` из vosk-api. Итог в Summary, файлы и снимки в артефакте `android-e2e`.
- Локально без KVM эмулятор не запустить, но APK собирается: Android SDK (cmdline-tools, platform 36, build-tools 35), `sdk.dir` в `mobile/android/local.properties`, `CHAQUOPY_BUILD_PYTHON=python3.12 ./gradlew assembleDebug -Pabis=x86_64`.

### 13.19 HTTPS на любом компьютере (v2.6.1)
- Все загрузки из интернета (`engines._get`, `engines._download_once`, `stt._get`, WebView2 в `desktop.py`, модели на Android) идут через `net.urlopen`. Способы проверки по очереди: `truststore` (проверка ОС: Windows сам докачивает корневые сертификаты, проходят антивирусы с подменой HTTPS), затем стандартный контекст + `certifi`. Сработавший запоминается (`net._good`). Проверка сертификатов не отключается; если не прошёл ни один способ — `net.CertError` с подсказкой.
- `truststore` и `certifi` — в `requirements.txt` и `packaging/linux/vendor.txt`. yt-dlp ходит в сеть сам (certifi), его это не касается.
- Тест: локальный HTTPS с самоподписанным сертификатом (`test_net_falls_back_to_next_cert_check`).

### 13.20 Без повторных загрузок (v2.6.2)
- `engines._save_paths`: paths.json пишется через `.tmp` + `os.replace`. `_find_installed(name)` ищет уже распакованный движок в `engines/<имя>` по exe из CATALOG; `available()` при пустом/битом paths.json находит его и перезаписывает (`_register`). `install()` при найденном движке не качает, а только регистрирует и докачивает словари.
- Tesseract: путь регистрируется до `_tessdata`; `available("tesseract")` требует все `langs` в `tessdata` (`_langs_ok`), поэтому недокачанные словари ставятся при следующем запуске без повторной загрузки движка.
- `net.download(url, dest, ...)`: докачка. `dest.part` + `dest.part.json` (url, ETag, Last-Modified), запрос `Range` + `If-Range` (сменился файл — сервер отдаёт 200, начинаем с нуля; слабый ETag не используется), 416 — уже скачано. Отмена и обрыв оставляют `.part`. Архивы движков лежат в `engines/downloads` до успешной установки, битый архив удаляется. Модели Whisper (`stt.ensure_model`) тоже через `net.download`. Проверено вживую на релизе deno: 206, zip цел.

### 13.21 Сохранить страницу сайта (v2.7.0)
- Режим `page` внутри `/api/dl` (третий вариант в «Что качать», блок `#dlPage`). `app.dl` при `mode=page` не требует yt-dlp: `webpage.run_page_task(task, urls, opts, cookie)`, задача того же вида `dl`, поэтому результат и интерфейс прогресса те же. До 50 адресов за раз.
- `webpage.py`: сначала monolith (`monolith_exe()` = `shutil.which`, флаги: `-j -n` без скриптов, `-a -v` без видео и звука, `-i`, `-F`, `-e -q -t 40 -C cookies -u UA`, прокси через переменные окружения), при ошибке или на Android — встроенный (`_save_builtin`, BeautifulSoup `html.parser`): `_Fetcher` (cookies Netscape, прокси, gzip/deflate, лимиты 20 МБ на файл, 150 МБ и 800 файлов на страницу), `_Inliner` (стили с `@import`, `url()` в стилях и `style=""`, картинки/srcset/picture, значок, шрифты, видео и звук, ссылки становятся полными, пометка и `<meta charset>`).
- Ссылка не на страницу, а на файл (по Content-Type): сохраняется как есть.
- Отличия monolith: `@import "файл.css"` не вшивает (только `url()`), внешние стили кладёт как `<link href="data:text/css;base64,…">`, оставляет пустой `<script></script>`.
- `engines.py`: движок `monolith` (`"single": True`, один исполняемый файл без архива, `_install_single`, имя файла по системе `_monolith_asset`: `monolith-cli.exe`, `monolith-macos-*-apple-darwin`, `monolith-cli-linux-*`), в `AUTO`, ставится и на Linux (`can_install`).
- `net.open_with(req, *handlers)`: urlopen с прокси и cookies и тем же перебором проверок HTTPS.
- Тесты: локальный сайт (`site`), встроенный способ, cookies+переадресация+gzip, настоящий monolith (пропускается без него; в CI ставится `engines.py install monolith`), установка одного файла. Android e2e: проверка «страница сайта».

### 13.22 Читалка: TextFold внутри (v2.8.0)
- `third_party/textfold/` — пакет `textfold` без изменений (Apache-2.0, версия и коммит в `VENDORED.md`, лицензия и NOTICE рядом). Обновлять заменой папки.
- `reader.py`: `start()` запускает `textfold.server.serve(port)` потоком внутри Universal (порт 3057, если занят, любой свободный; `TEXTFOLD_DATA` = `<UNIVERSAL_HOME>/textfold` до импорта), `import_text`, `import_file` (md/txt как есть; Word, Excel, EPUB, HTML и PDF с текстом через `textfold.office`/`pdftext`; сканам и картинкам нужен MinerU), `available()`, `missing()`, `pip_command()`, `reachable()` (читалка на 127.0.0.1: только если браузер на этом же компьютере; нет в Docker, на телефоне и через сервер для телефона).
- `app.py`: `/api/reader`, `/api/reader/start`, `/api/reader/open` (text или files), `engines.reader` в `/api/formats` (`usable`, `local`, `missing`, `pip`). Все закрыты `_local_only`.
- Интерфейс: пункт «Читалка» (`#readerView`, iframe `#readerFrame` на адрес читалки), виден, когда `reader.local`; если нет библиотек, вместо iframe команда `pip install`. Кнопка «В читалку» у результатов голоса в текст (`openInReader`).
- Сборки: `packaging/universal.spec` (pathex, модули `textfold.*`, данные `web` и `skills`, данные trafilatura/justext/courlan/htmldate/tld/latex2mathml/dateparser, библиотека pypdfium2_raw); `desktop.py --smoke` проверяет, что читалка запускается и открывает импортированный текст в собранном приложении. Linux-пакеты копируют `third_party`, а библиотеки читалки (lxml и др.) туда не вшиваются (бинарные): `reader.available()` False, пока их не поставили.
- Зависимости читалки в `requirements.txt`; на Android не нужны (`UNIVERSAL_ANDROID` → `available()` False).
- Модель сворачивания подключает человек в самой читалке (её настройки лежат в `~/.textfold`). `TEXTFOLD_NO_MODELS=1` отключает поиск моделей (тесты).
- Тесты `-k reader`: запуск, импорт текста и Word, маршруты, закрытость от телефона, подсказка при нехватке библиотек.

### 13.23 Документы в Markdown: MinerU по запросу (v2.9.0)
- `mdconv.py` ставит и запускает MinerU через `textfold.engine` (вшитый TextFold): `uv` + Python 3.12 + `mineru==4.0.10` в `<данные приложений>/TextFold/engine` (общая папка с читалкой). Уровни: **basic** (пакет, ≈ 0,9 ГБ, маркер `installed-basic.txt`, быстрый режим = `--tier flash`) и **full** (плюс `mineru-kit models download --tier standard`, ≈ 3 ГБ, маркер `installed.txt` textfold; точный = `--tier advanced --disable-image-analysis`).
- Проверено на настоящем MinerU: flash читает PDF с текстом, Word (таблицы в Markdown), сканы и картинки (OCR докачивает ≈ 230 МБ при первом использовании), advanced точнее на сканах («4.2 million dollars» вместо «milliondollars»), 157 с на картинку и PDF на процессоре.
- Результат `mineru-kit parse --format markdown`: `<имя>.md`; вшитые в Markdown картинки (base64) выносятся в `<имя>_images/` (`images=files`, по умолчанию), остаются (`inline`) или удаляются (`none`).
- Маршруты: `/api/md/engine`, `/api/md/install` (задача `mdinstall`, только `_local_only`), `/api/md/uninstall`, `/api/md` (задача `md`, архив). `engines.mineru` в `/api/formats` (`supported`, `basic`, `full`, `local`). Нет на Android и в Docker.
- Интерфейс: пункт «В Markdown» (`#mdBox`: режим, картинки, карточка движка с прогрессом и «Остановить»); кнопка конвертации включается, когда установлен нужный уровень.
- Тесты `-k md_` на поддельных `uv` и `mineru-kit` (Linux и macOS), без скачивания гигабайтов.
- Читалка: запросы к ней идут через opener без прокси (`reader._local`), старт ждёт `START_TIMEOUT` = 90 с, при неудаче сообщение с этапом, состоянием порта и потока. `.github/workflows/mac-smoke.yml` собирает macOS-приложение на обеих архитектурах и запускает `--smoke` (быстрая диагностика, срабатывает и при правке самого файла).

### 13.24 Разделение по голосам (v2.9.0)
- `diarize.py`: sherpa-onnx, сегментация pyannote-segmentation-3.0 (int8) + отпечаток 3D-Speaker ERes2Net (VoxCeleb), кластеризация FastClustering (порог 0,8; `num_clusters=-1` — само, число — вручную). Модели (≈ 28 МБ) в `<данные>/diarization`, качаются через `net.download` один раз.
- `stt.py`: поле формы `speakers` (`auto`/число/пусто), `transcribe(..., audio=)`, звук грузится один раз; подписи «Спикер N» в тексте и .srt, `speakers` в результате; caps `diarize` для интерфейса.
- Нет на Android (`UNIVERSAL_ANDROID`) и там, где не стоит `sherpa-onnx` (в `packaging/linux/vendor.txt` его нет: не чистый Python).
- Настоящий тест: `DIARIZE_REAL=1 pytest -k diarize_real` (CI, job pytest). Проверено вручную на записи с четырьмя говорящими: найдено 4.

### 13.25 Офлайн-сборка для Windows (v2.9.0)
- `packaging/make_offline.py <папка>` скачивает те же движки (`engines.install` для `AUTO`) и модели (Whisper fast/normal, голоса) в папку формата папки данных. Workflow «Релиз» (job windows) копирует её в `dist\Universal\offline`, проверяет `--smoke` с мёртвым прокси (`checks.offline`), собирает `Universal-Offline-Setup-X.exe` (`iscc /DOffline=1`, lzma2/fast, без solid) и `Universal-Offline-X-windows-portable.zip`. Больше 2 ГиБ GitHub не примет: шаг это проверяет.
- `offline.py`: `seed()` при старте (desktop.main, до `engines.activate`) раскладывает недостающие части в папку данных. Поставлено установщиком (рядом `unins*.exe`) → перенос (`os.replace`, мгновенно, места второй раз не занимает); portable → копия. Существующее не перезаписывается.
- Имена `Universal-Offline-*` не совпадают с `ASSET_RE` в updater.py: обновление из приложения по-прежнему берёт обычный установщик.
- `offline-check.yml` проверяет набор на Windows без релиза.

### 13.26 ClamAV, колесо форматов, DBeaver (v2.10.0)
- `av.py`: ClamAV. `clamscan`/`freshclam` из PATH (на Windows `engines.install("clamav")`, каталог `engines.CATALOG["clamav"]`, не в `AUTO`; asset `.win.x64.zip`, в песочнице имена файлов релиза проверить нельзя, CI-job windows ставит его по-настоящему). Базы в `<данные>/clamav-db`, `update_db` пишет временный freshclam.conf (mirror database.clamav.net, `DatabaseOwner` только на posix), системные базы (`/var/lib/clamav`…) годятся. `run_av_task`: один процесс clamscan на все файлы (загрузка баз 10–20 с), файлы под ASCII-именами `f0001.ext` (русские имена на Windows), результат в `task.result["av"]`. Ничего не удаляет. Маршруты `/api/av`, `/api/av/engine`, `/api/av/install`. Настоящий тест `AV_REAL=1 -k av_real` (ubuntu и windows в tests.yml): freshclam качает ≈ 300 МБ и находит EICAR. Остальные тесты на тестовой сигнатуре (md5 EICAR в .hdb). Linux-пакеты: `Suggests: clamav`.
- Колесо форматов (Filee): только `templates/index.html`, блок «колесо форматов». dragover с Ctrl (Mac Option) и `Files` показывает `#wheel`; виды по MIME при перетаскивании, по расширению при броске; список форматов `WHEEL` пересекается с `caps.categories[*].outputs`; PDF идёт через `/api/convert` (все страницы), остальное через `/api/universal` с настройками по умолчанию.
- `dbtools.py`: просмотр SQLite только на чтение (`mode=ro`), `/api/db`, `/api/db/rows`, `/api/db/open`. DBeaver: `engines.CATALOG["dbeaver"]` (Windows, портативный zip с Java), на macOS и Linux ищется в системе. Запуск `dbeaver -con "name=…|driver=sqlite_jdbc|database=<путь>|connect=true|openConsole=false"`: параметры и id драйвера сверены с исходниками DBeaver (DataSourceUtils, ApplicationInstanceServer), обратные слэши путей удваиваются. Сам DBeaver в песочнице не запускался.

### 13.27 ClamAV убран (v2.10.1)
По просьбе владельца проверка на вирусы удалена целиком: `av.py`, маршруты `/api/av*`, вкладка, запись `clamav` в `engines.CATALOG`, тесты, шаги CI (`AV_REAL`, установка clamav) и `Suggests` в пакетах Linux. Раздел 13.26 выше про ClamAV больше не действует; колесо форматов и `dbtools.py` остаются как описано. Хелпер `installUi` в `templates/index.html` остался (его использует DBeaver).

### 13.28 Скорость на слабых ПК (v2.11.0)
- Замеры (Linux, 4 ядра): `import app` 0,29 → 0,17 с; конвертация сама почти вся в ImageMagick/ffmpeg, накладные расходы Python ≈ 0,05 с на файл; приём 40 файлов ≈ 0,45 с. Перепись на Rust ради скорости не нужна: основное время в внешних программах.
- `lazy.py`: `lazy.Module("yt_dlp")` грузит библиотеку при первом обращении (`downloader.yt_dlp`, `pdftools.pypdf`). Тесты подменяют атрибут на обёртке (`monkeypatch.setattr(downloader.yt_dlp, ...)`).
- `desktop.py`: `_boot()` (набор офлайн, движки, импорт app, сервер) и `_run_window()`: окно с `SPLASH` создаётся первым, `boot()` идёт в потоке `webview.start`, потом `window.load_url`. Ошибка загрузки закрывает заставку и доходит до `_crash`. `--smoke` и `--browser` грузят всё синхронно. Класс `Api` даёт странице `pywebview.api.pick_files()`.
- `perf.py`: профиль машины (`slow`: ≤ 2 ядра или RAM < 6000 МБ; `UNIVERSAL_SLOW=0/1`), `default_preset` (veryfast/medium), `whisper_beam` (1/5), `heavy_workers`, доля потоков на процесс (`set_threads` в потоке, `child_env` → `MAGICK_THREAD_LIMIT`, `OMP_NUM_THREADS`, подхватывается `converters._run`), видеокарта: `detect()` пробует рецепты `RECIPES` (nvenc/qsv/amf/videotoolbox) пробным кодированием `testsrc2` и запоминает в `<данные>/hwenc.json`; `UNIVERSAL_HW=0` выключает. `hw_args(codec, quality|kbps)`. В песочнице видеокарт нет: ветки проверены подменой «видеокарты» на libx264 и заведомо неверным кодировщиком (откат на процессор), реальные NVENC/QSV/AMF/VideoToolbox не запускались.
- Откат на процессор: `converters._hw_variant` + повтор в `convert_media`; `editor._run_with_hw`.
- Файлы по пути: `app._uploaded()` (используют `/api/convert`, `/api/universal`, `/api/ocr`, `/api/stt`, `/api/md`; маршруты, где файл правится на месте, не трогать) принимает поле `paths` только при `UNIVERSAL_DESKTOP`, с loopback и заголовком `X-Universal-Token` = `app.LOCAL_TOKEN` (токен кладётся в страницу только окну приложения). `LocalFile.save` делает жёсткую ссылку или копию. Интерфейс: `pickNative`/`appendFiles` в `templates/index.html`. Перетаскивание (drag and drop) по-прежнему загружает файл: путь файла у pywebview приходит только в Python-обработчике события, это не проверялось.

