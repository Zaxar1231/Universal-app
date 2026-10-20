"""
Вкладка «Информация»: версия, движки с их версиями, где лежат данные.

Версии движков узнаются запуском программ с --version (это пара секунд на всё),
поэтому ответ кэшируется до перезапуска приложения; «Обновить» пересчитывает.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import converters
import downloader
import engines
import ocr
import updater

# имя для интерфейса, как найти программу, ключ для версии, регулярка версии
TOOLS = [
    ("ffmpeg", lambda: shutil.which("ffmpeg"), ["-version"], r"ffmpeg version (\S+)", "аудио, видео, редактор, скачивание"),
    ("ImageMagick", converters._magick, ["-version"], r"ImageMagick (\S+)", "картинки"),
    ("pandoc", lambda: shutil.which("pandoc"), ["--version"], r"pandoc(?:\.exe)? (\S+)", "документы"),
    ("poppler", lambda: shutil.which("pdftoppm"), ["-v"], r"version (\S+)", "PDF в картинки"),
    ("LibreOffice", lambda: shutil.which("soffice") or shutil.which("libreoffice"), ["--version"],
     r"LibreOffice (\S+)", "старые .doc"),
    ("Tesseract", lambda: shutil.which("tesseract"), ["--version"], r"tesseract v?(\S+)", "распознавание текста"),
    ("deno", lambda: shutil.which("deno"), ["--version"], r"deno (\S+)", "быстрое скачивание с YouTube"),
]

_cache: dict | None = None
_lock = threading.Lock()


def _version(exe: str, args: list[str], pattern: str) -> str:
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(pattern, (r.stdout or "") + (r.stderr or ""), re.I)
    return m.group(1).strip(",") if m else ""


def _tool(t) -> dict:
    name, find, args, pattern, used_for = t
    exe = find()
    return {"name": name, "for": used_for, "installed": bool(exe),
            "version": _version(exe, args, pattern) if exe else "", "path": exe or ""}


def info(refresh: bool = False) -> dict:
    global _cache
    with _lock:
        if _cache is None or refresh:
            with ThreadPoolExecutor(max_workers=len(TOOLS)) as pool:
                tools = list(pool.map(_tool, TOOLS))
            tools += [
                {"name": "yt-dlp", "for": "скачивание видео", "installed": downloader.available(),
                 "version": downloader.version(), "path": ""},
                {"name": "OCRmyPDF", "for": "распознавание текста", "installed": bool(ocr.version()),
                 "version": ocr.version(), "path": ""},
            ]
            _cache = {"tools": tools}
        d = dict(_cache)
    d.update({
        "version": updater.VERSION,
        "install": updater.kind(),
        "desktop": bool(os.environ.get("UNIVERSAL_DESKTOP")),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
        "home": str(engines.root()),
        "jobs": os.environ.get("WORK_DIR", ""),
        "languages": ocr.languages(),
    })
    return d


def open_folder() -> str:
    """Открыть папку с данными приложения в файловом менеджере."""
    d = engines.root()
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(d))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(d)])
    else:
        subprocess.Popen(["xdg-open", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(Path(d))
