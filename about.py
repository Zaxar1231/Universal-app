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


# ---------------------------------------------------------------------------
# «Для зануд»: всё, что пригодится при разборе ошибки
# ---------------------------------------------------------------------------
import json  # noqa: E402
import locale  # noqa: E402
import time  # noqa: E402
from importlib import metadata  # noqa: E402

STARTED = time.time()
PACKAGES = ["flask", "werkzeug", "jinja2", "pillow", "pypdf", "cryptography", "qrcode", "yt-dlp", "yt-dlp-ejs",
            "ocrmypdf", "pikepdf", "pypdfium2", "pywebview", "py7zr"]
ENV_KEYS = ["UNIVERSAL_HOME", "WORK_DIR", "HOST", "PORT", "TASK_WORKERS", "UNIVERSAL_DESKTOP", "UNIVERSAL_PROXY",
            "UNIVERSAL_DOCKER", "TESSDATA_PREFIX"]


def _git_commit() -> str:
    from version import COMMIT
    if COMMIT:
        return COMMIT
    root = Path(__file__).resolve().parent
    if (root / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=7", "HEAD"], capture_output=True,
                               text=True, timeout=5)
            return r.stdout.strip() + " (из исходников)" if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    return ""


def _uptime(sec: float) -> str:
    sec = int(sec)
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return (f"{d} д " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def _disk(path: str) -> str:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return "—"
    return f"свободно {u.free / 1073741824:.1f} из {u.total / 1073741824:.1f} ГБ"


def _folder_stats(path: str) -> str:
    p = Path(path)
    if not path or not p.is_dir():
        return "—"
    n = size = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                n += 1
                size += f.stat().st_size
        except OSError:
            continue
    return f"{n} файлов, {size / 1048576:.1f} МБ"


def nerd(tasks, host: str, work_root) -> dict:
    """Разделы с парами «название — значение». tasks — TaskManager приложения, work_root — папка заданий."""
    import phoneserver
    from version import BUILT
    base = info()
    base["jobs"] = str(work_root)
    sections = []

    def section(title, rows):
        sections.append({"title": title, "rows": [[k, "" if v is None else str(v)] for k, v in rows]})

    section("Приложение", [
        ("Версия", base["version"]), ("Коммит", _git_commit() or "—"), ("Собрано", BUILT or "—"),
        ("Установка", base["install"]), ("Окно приложения", "да" if base["desktop"] else "нет, браузер"),
        ("Собран в exe/.app", "да" if getattr(sys, "frozen", False) else "нет"),
        ("Исполняемый файл", sys.executable), ("PID", os.getpid()), ("Работает", _uptime(time.time() - STARTED)),
        ("Рабочая папка процесса", os.getcwd()),
    ])
    section("Система", [
        ("ОС", platform.platform()), ("Процессор", f"{platform.machine()} · ядер: {os.cpu_count()}"),
        ("Python", f"{platform.python_implementation()} {platform.python_version()} ({platform.python_build()[1]})"),
        ("Кодировка файлов", sys.getfilesystemencoding()), ("Локаль", ".".join(filter(None, locale.getlocale())) or "—"),
        ("Часовой пояс", time.strftime("%Z (UTC%z)")),
        ("Диск с настройками", _disk(base["home"])),
    ])
    ph = phoneserver.info()
    section("Сеть", [
        ("Адрес окна", host), ("Сервер для телефона", f"порт {ph['port']}, " + ", ".join(ph["urls"]) if ph["running"] else "выключен"),
        ("Системный прокси", downloader.system_proxy() or "нет"), ("Пароль APP_PASSWORD", "задан" if os.environ.get("APP_PASSWORD") else "нет"),
    ])
    running = [t for t in tasks.tasks.values() if t.status in ("queued", "running")]
    section("Задания", [
        ("Одновременно тяжёлых задач", tasks.pool._max_workers), ("В памяти", len(tasks.tasks)),
        ("Сейчас идут", ", ".join(f"{t.kind} {int((t.progress or 0) * 100)}%" for t in running) or "нет"),
        ("Временные файлы", f"{base['jobs'] or '—'} · {_folder_stats(base['jobs'])}"),
        ("Диск с временными файлами", _disk(base["jobs"]) if base["jobs"] else "—"),
    ])
    pk = []
    for name in PACKAGES:
        try:
            pk.append((name, metadata.version(name)))
        except metadata.PackageNotFoundError:
            pk.append((name, "нет"))
    section("Python-пакеты", pk)
    section("Движки", [(t["name"], f"{t['version'] or ('есть' if t['installed'] else 'нет')}" + (f" · {t['path']}" if t["path"] else ""))
                       for t in base["tools"]] + [("Языки Tesseract", ", ".join(base["languages"]) or "—")])
    try:
        upd = json.loads((engines.root() / "update.json").read_text(encoding="utf-8"))
        checked = time.strftime("%Y-%m-%d %H:%M", time.localtime(upd.get("checked", 0)))
        section("Обновления", [("Последний релиз", upd.get("version", "—")), ("Проверено", checked),
                               ("Файлов в релизе", len(upd.get("assets", [])))])
    except (OSError, ValueError):
        section("Обновления", [("Последний релиз", "ещё не проверялось")])
    section("Папки и окружение", [("Настройки и движки", base["home"]), ("Состав папки", _folder_stats(base["home"]))]
            + [(k, os.environ[k]) for k in ENV_KEYS if os.environ.get(k)])
    return {"sections": sections}
