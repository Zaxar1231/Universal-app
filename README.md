# Universal

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

## Установка
1. Python 3.10 или новее (на Windows отметь Add Python to PATH).
2. Программы (ставь те, что нужны твоим режимам):
   - Windows: `choco install ffmpeg imagemagick pandoc poppler libreoffice-fresh`
   - Debian/Ubuntu: `sudo apt install ffmpeg imagemagick pandoc poppler-utils libreoffice`
   - macOS: `brew install ffmpeg imagemagick pandoc poppler`
3. Запуск: Windows `run.bat`, Linux/macOS `./run.sh`. Скрипты сами создадут окружение, доставят новые зависимости после обновления и каждый раз обновят yt-dlp.
4. Открой http://localhost:5000

Если нужной программы нет, интерфейс покажет красный баннер в соответствующем режиме.

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

## Лицензии
Набор форматов и логика параметров звука/видео в конвертере взяты из проекта VERT (AGPL-3.0). yt-dlp — Unlicense. Скачивай только то, на что у тебя есть права.

Подробности устройства: `HANDOVER.md`.
