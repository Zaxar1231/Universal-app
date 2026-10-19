# Universal

[![Тесты](https://github.com/Zaxar1231/Universal-app/actions/workflows/tests.yml/badge.svg)](https://github.com/Zaxar1231/Universal-app/actions/workflows/tests.yml)
[![Сайт](https://img.shields.io/badge/сайт-открыть-00696b)](https://zaxar1231.github.io/Universal-app/)
[![Релиз](https://img.shields.io/github/v/release/Zaxar1231/Universal-app?label=релиз)](https://github.com/Zaxar1231/Universal-app/releases/latest)
[![Лицензия: AGPL-3.0](https://img.shields.io/badge/лицензия-AGPL--3.0-blue)](LICENSE)

**[Открыть сайт →](https://zaxar1231.github.io/Universal-app/)**: QR-коды, конвертер картинок и правка PDF работают прямо в браузере, в том числе на телефоне. Видео, аудио и скачивание требуют полной версии (ниже).

![Конвертер](site/img/convert.webp)

Локальный комбайн для файлов: конвертер форматов (как vert.sh), редактор видео и аудио на ffmpeg, скачивание видео с любых сайтов и QR-коды. Всё работает на твоём компьютере или домашнем сервере, файлы никуда не уходят (кроме скачивания, понятно).

## Режимы
| Режим | Что делает | Нужно |
|---|---|---|
| Картинки | PNG, JPG, WebP, GIF, AVIF, HEIC, ICO, TIFF, PSD, RAW и другие; можно сразу уменьшить до N px | ImageMagick |
| Аудио | MP3, WAV, FLAC, OGG, Opus, AAC, M4A и другие, а также аудио в видео | ffmpeg |
| Видео | MP4, MKV, WebM, AVI, MOV и другие, а также звук из видео | ffmpeg |
| Документы | DOCX, DOC, MD, HTML, RTF, EPUB, ODT, CSV и другие | pandoc (+ LibreOffice для .doc) |
| PDF в картинки | JPG, PNG, TIFF, WebP, SVG, PS, EPS | poppler |
| Фото в A4 | фото приводятся к листу A4 с нужным DPI | Pillow |
| Фото в PDF | 1, 2, 4, 6, 8 или 9 фото на лист A4 | Pillow |
| **Правка PDF** | склеить, разделить по страницам, вытащить страницы (1-3, 5, 8-), повернуть, сжать сканы, поставить или снять пароль | pypdf (ставится сам) |
| **Редактор** | обрезка, склейка, кадрирование 9:16/1:1/…, поворот, разрешение, fps, скорость 0.25–4×, громкость, нормализация, появление/затухание, без звука, сжатие до N МБ, H.264/H.265/VP9/AV1, видео в GIF, предпросмотр с выбором начала и конца | ffmpeg |
| **Скачать видео** | YouTube, TikTok, VK, Rutube, Instagram, X, Twitch, SoundCloud и ~1800 сайтов; видео до 4K или только звук (MP3/M4A/Opus/FLAC/WAV), плейлисты, субтитры, обложки, cookies для закрытого | yt-dlp, ffmpeg |
| **QR-коды** | текст/ссылка, Wi‑Fi, контакт (vCard) с живым предпросмотром; пачка из списка, .txt, .csv или .docx; PNG/SVG/JPG, цвета, прозрачный фон | qrcode |

Конвертер, редактор, скачивание и правка PDF идут в фоне: видно проценты, их можно отменить.

<p>
<img src="site/img/editor.webp" width="49%" alt="Редактор видео">
<img src="site/img/download.webp" width="49%" alt="Скачивание видео">
</p>
<p>
<img src="site/img/mobile_qr.webp" width="24%" alt="QR на телефоне">
<img src="site/img/mobile_editor.webp" width="24%" alt="Редактор на телефоне">
</p>

## Установка
Все файлы в [последнем релизе](https://github.com/Zaxar1231/Universal-app/releases/latest).

| Система | Файл | Как поставить |
|---|---|---|
| Windows 10/11 | `Universal-Setup-X.Y.Z.exe` | запустить установщик. Движки приложение предложит скачать одной кнопкой |
| Windows без установки | `Universal-X.Y.Z-windows-portable.zip` | распаковать, запустить `Universal.exe` |
| Debian, Ubuntu, Mint | `universal_X.Y.Z_all.deb` | `sudo apt install ./universal_X.Y.Z_all.deb` |
| Fedora | `universal-X.Y.Z-1.noarch.rpm` | `sudo dnf install ./universal-X.Y.Z-1.noarch.rpm` |
| Arch, Manjaro | `universal-X.Y.Z-1-any.pkg.tar.zst` | `sudo pacman -U universal-X.Y.Z-1-any.pkg.tar.zst` |
| macOS (Apple Silicon / Intel) | `Universal-X.Y.Z-macos-arm64.dmg` / `-x86_64.dmg` | открыть, перетащить в «Программы»; движки: `brew install ffmpeg imagemagick pandoc poppler` |

На Linux ffmpeg, ImageMagick, pandoc и poppler приходят зависимостями пакета. В Fedora основной ffmpeg (`ffmpeg-free`) не умеет H.264/H.265: для редактора видео поставь ffmpeg из [RPM Fusion](https://rpmfusion.org/).

Первый запуск на macOS: приложение не подписано сертификатом Apple, поэтому открой его правой кнопкой → «Открыть» (или «Системные настройки → Конфиденциальность и безопасность → Всё равно открыть»). На Windows SmartScreen может попросить «Подробнее → Выполнить в любом случае».

## Установка из исходников (Windows, Linux, macOS)
1. Python 3.10 или новее (на Windows отметь Add Python to PATH).
2. Программы (ставь те, что нужны твоим режимам):
   - Windows: `choco install ffmpeg imagemagick pandoc poppler libreoffice-fresh`
   - Debian/Ubuntu: `sudo apt install ffmpeg imagemagick pandoc poppler-utils libreoffice`
   - macOS: `brew install ffmpeg imagemagick pandoc poppler`
3. [Скачай последний релиз](https://github.com/Zaxar1231/Universal-app/releases/latest) (`universal-X.Y.Z.zip`) и распакуй.
4. Запуск: Windows `run.bat`, Linux/macOS `./run.sh`. Скрипты сами создадут окружение, доставят новые зависимости после обновления и каждый раз обновят yt-dlp.
5. Открой http://localhost:5000

Если нужной программы нет, интерфейс покажет красный баннер в соответствующем режиме.

## Открыть с телефона
Телефон и компьютер в одной Wi‑Fi сети. Запусти сервер открытым в сеть и с паролем:
```bash
HOST=0.0.0.0 APP_PASSWORD=мой-пароль ./run.sh                 # Linux / macOS
set HOST=0.0.0.0&& set APP_PASSWORD=мой-пароль&& run.bat      # Windows (cmd)
```
Потом на телефоне открой `http://IP-компьютера:5000` (IP смотри в `ipconfig` / `ip a`), логин любой. Windows спросит про брандмауэр: разреши для частной сети.

## Docker (домашний сервер)
```bash
docker compose up -d --build
```
Внутри уже есть ffmpeg, ImageMagick, pandoc, poppler и LibreOffice. Поменяй `APP_PASSWORD` в `docker-compose.yml`. Обновить yt-dlp: `docker compose build --no-cache && docker compose up -d`.

## Настройки (переменные окружения)
| Переменная | По умолчанию | Зачем |
|---|---|---|
| `HOST` | `127.0.0.1` | `0.0.0.0`, чтобы пускать из сети |
| `PORT` | `5000` | порт |
| `APP_PASSWORD` | пусто | пароль на вход (браузер спросит, логин любой) |
| `WORK_DIR` | временная папка | где лежат задания; результаты удаляются через час |
| `TASK_WORKERS` | `2` | сколько тяжёлых задач идёт одновременно |

## Тесты
```bash
pip install pytest
python -m pytest -q          # на роликах, которые генерирует ffmpeg
NET=1 python -m pytest -q    # плюс настоящее скачивание с YouTube
```

## Консольный QR
`tools/bulk_qr.py` — исходный скрипт массовой генерации, работает и без сервера: `python tools/bulk_qr.py links.docx -f svg`.

## Сайт
Папка `site/` публикуется на GitHub Pages workflow-ом `.github/workflows/pages.yml` при каждом пуше в `main`. Это статическая страница: QR (qrcode-generator), картинки (canvas) и PDF (pdf-lib) работают в браузере, её можно установить на телефон как приложение и пользоваться офлайн.

## Сборка настольных версий
Linux-пакеты собирает `packaging/linux/build.sh` (дерево `/usr` + `.deb`), `build-rpm.sh` и `build-arch.sh` в контейнерах Fedora и Arch; чистые Python-библиотеки лежат внутри пакета, Pillow, cryptography и WebKitGTK берутся системные. macOS: тот же `packaging/universal.spec` собирает `Universal.app`, `hdiutil` делает `.dmg`. Каждый пакет в workflow «Релиз» ставится в чистый контейнер своего дистрибутива (Debian 12, Ubuntu 22.04/24.04, Fedora, Arch) и проходит `universal --smoke`.

Windows: `desktop.py` запускает тот же сервер внутри окна pywebview, `engines.py` качает движки в `%LOCALAPPDATA%\Universal\engines`. Exe собирает PyInstaller (`packaging/universal.spec`), установщик Inno Setup (`packaging/installer.iss`); всё это делает workflow «Релиз» на Windows-раннере и перед публикацией запускает `Universal.exe --smoke`. Локально: `pip install -r packaging/requirements-desktop.txt && python desktop.py`.

## Релизы
Описание версий в `CHANGELOG.md`. Новый релиз: добавь раздел `## vX.Y.Z` в CHANGELOG и запусти workflow «Релиз» (Actions → Релиз → Run workflow) или запушь тег `vX.Y.Z`. Архив и описание соберутся сами. К релизу прикладываются установщик и портативная версия для Windows, а также архив исходников только с файлами для работы: модули `*.py`, `templates/`, `requirements.txt`, `run.bat`, `run.sh`, README и LICENSE. Тесты, Docker и сайт берутся из репозитория (`git clone`).

## Лицензии
Проект под AGPL-3.0 (см. `LICENSE`): набор форматов и логика параметров звука/видео в конвертере взяты из проекта VERT (AGPL-3.0). yt-dlp — Unlicense. Скачивай только то, на что у тебя есть права.

Подробности устройства: `HANDOVER.md`.
