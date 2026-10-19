# Конвертер файлов (веб-морда)

Локальный конвертер с интерфейсом Material You. Файлы обрабатываются на твоём компьютере и никуда не отправляются.

## Режимы
| Режим | Что делает | Нужно |
|---|---|---|
| Картинки | PNG, JPG, WebP, GIF, AVIF, HEIC, ICO, TIFF, PSD и другие | ImageMagick |
| Аудио | MP3, WAV, FLAC, OGG, Opus, AAC, M4A и другие, а также аудио в видео | ffmpeg |
| Видео | MP4, MKV, WebM, AVI, MOV и другие, а также звук из видео | ffmpeg |
| Документы | DOCX, DOC, MD, HTML, RTF, EPUB, ODT, CSV и другие | pandoc (+ LibreOffice для .doc) |
| PDF в картинки | JPG, PNG, TIFF, WebP, SVG, PS, EPS | poppler |
| Фото в A4 | фото приводятся к листу A4 с нужным DPI | Pillow |
| Фото в PDF | 1, 2, 4, 6, 8 или 9 фото на лист A4, один PDF или PDF на каждое | Pillow |

Набор форматов и логика параметров звука/видео взяты из проекта VERT (AGPL-3.0).

## Установка
1. Python 3.9 или новее (при установке на Windows поставь галочку Add Python to PATH).
2. Нужные программы (ставь только те, что нужны твоим режимам).
   - Windows: `choco install poppler ffmpeg imagemagick pandoc libreoffice-fresh`
   - Debian/Ubuntu: `sudo apt install poppler-utils ffmpeg imagemagick pandoc libreoffice`
   - macOS: `brew install poppler ffmpeg imagemagick pandoc`
3. Запуск: Windows `run.bat`, Linux/macOS `./run.sh`
4. Открой http://localhost:5000

Ручной запуск: `pip install -r requirements.txt && python app.py`

Если для выбранного режима программы нет в PATH, интерфейс покажет красный баннер.

## Заметки
- Сервер слушает только 127.0.0.1. Для доступа из сети поменяй `host` в конце `app.py` на `0.0.0.0` (авторизации нет).
- Результат скачивается ZIP-ом с именем исходной папки или одним файлом.
- Старые результаты удаляются сами через час.
- Подробное описание устройства проекта: `HANDOVER.md`.
