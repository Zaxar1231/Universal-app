"""
Движки для настольной версии: скачиваются одной кнопкой в папку приложения.

На Windows ffmpeg, pandoc, poppler и ImageMagick не входят в установщик
(вместе это ~300 МБ). Когда режиму нужен движок, интерфейс предлагает
скачать его; файлы кладутся в %LOCALAPPDATA%\\Universal\\engines, а папки
с exe добавляются в PATH при каждом запуске.

yt-dlp встроен в приложение, но сайты меняются чаще, чем выходят релизы,
поэтому свежую сборку yt-dlp тоже можно докачать: она подключается
поверх встроенной при следующем запуске.

Проверка из консоли: python engines.py install ffmpeg pandoc poppler imagemagick
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from tasks import Task

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
UA = {"User-Agent": "Universal-app"}

# что качать (Windows x64). exe — по какому файлу понять, что движок на месте.
CATALOG = {
    "ffmpeg": {
        "title": "ffmpeg", "size": "≈ 190 МБ", "exe": ["ffmpeg.exe", "ffprobe.exe"],
        "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        "for": "аудио, видео, редактор, скачивание",
    },
    "pandoc": {
        "title": "pandoc", "size": "≈ 40 МБ", "exe": ["pandoc.exe"],
        "repo": "jgm/pandoc", "asset": r"windows-x86_64\.zip$",
        "for": "документы",
    },
    "poppler": {
        "title": "poppler", "size": "≈ 20 МБ", "exe": ["pdftoppm.exe", "pdftocairo.exe"],
        "repo": "oschwartz10612/poppler-windows", "asset": r"^Release-.*\.zip$",
        "for": "PDF в картинки",
    },
    "imagemagick": {
        "title": "ImageMagick", "size": "≈ 50 МБ", "exe": ["magick.exe"],
        "repo": "ImageMagick/ImageMagick", "asset": r"portable-Q16-HDRI-x64\.(zip|7z)$",
        "for": "картинки",
    },
    "ytdlp": {
        "title": "свежий yt-dlp", "size": "≈ 3 МБ", "exe": ["yt-dlp.zip"],
        "url": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
        "for": "скачивание видео, если какой-то сайт перестал качаться",
    },
}


def root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".local", "share")
    return Path(os.environ.get("UNIVERSAL_HOME") or Path(base) / "Universal")


def engines_dir() -> Path:
    d = root() / "engines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paths_file() -> Path:
    return engines_dir() / "paths.json"


def _load_paths() -> dict:
    try:
        return json.loads(_paths_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def activate() -> None:
    """Добавляет скачанные движки в PATH и подключает свежий yt-dlp. Вызывать до импорта app."""
    paths = _load_paths()
    dirs = [d for name, d in paths.items() if name != "ytdlp" and Path(d).is_dir()]
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
    yt = paths.get("ytdlp")
    if yt and Path(yt).is_file() and yt not in sys.path:
        sys.path.insert(0, yt)       # zipapp yt-dlp импортируется как обычный пакет


def can_install() -> bool:
    return sys.platform == "win32" or bool(os.environ.get("UNIVERSAL_ENGINES_ANYOS"))


def status() -> dict:
    paths = _load_paths()
    out = {}
    for name, spec in CATALOG.items():
        d = paths.get(name)
        out[name] = {"title": spec["title"], "size": spec["size"], "for": spec["for"],
                     "installed": bool(d and Path(d).exists())}
    return out


class EngineError(Exception):
    pass


def _resolve_url(spec: dict) -> str:
    if "url" in spec:
        return spec["url"]
    req = urllib.request.Request(GITHUB_API.format(repo=spec["repo"]), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        raise EngineError(f"Не удалось узнать последнюю версию ({e.__class__.__name__}). Проверь интернет")
    for a in data.get("assets", []):
        if re.search(spec["asset"], a["name"]):
            return a["browser_download_url"]
    raise EngineError(f"В последнем релизе {spec['repo']} нет файла для Windows")


def _download(url: str, dest: Path, task: Task | None, base: float, span: float) -> None:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                if task:
                    task.check_cancel()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if task:
                    mb = f"{got / 1048576:.0f}" + (f" из {total / 1048576:.0f}" if total else "")
                    task.update(base + span * (got / total if total else 0), f"Скачиваю: {mb} МБ")
    except EngineError:
        raise
    except Exception as e:
        if task and task.cancel_event.is_set():
            raise
        raise EngineError(f"Загрузка прервалась ({e.__class__.__name__}). Попробуй ещё раз")


def _extract(archive: Path, target: Path) -> None:
    if archive.suffix.lower() == ".7z":
        try:
            import py7zr  # type: ignore
        except ImportError:
            raise EngineError("Для распаковки .7z нужен пакет py7zr")
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(target)
        return
    with zipfile.ZipFile(archive) as z:
        for m in z.infolist():   # защита от путей вида ../../
            p = (target / m.filename).resolve()
            if not str(p).startswith(str(target.resolve())):
                raise EngineError("Архив движка повреждён")
        z.extractall(target)


def install(name: str, task: Task | None = None) -> str:
    """Скачивает и распаковывает движок. Возвращает папку, которая уйдёт в PATH."""
    if name not in CATALOG:
        raise EngineError("Неизвестный движок")
    spec = CATALOG[name]
    edir = engines_dir()
    if task:
        task.total = 1
        task.update(0, f"Ищу последнюю версию {spec['title']}…")
    url = _resolve_url(spec)
    with tempfile.TemporaryDirectory(dir=edir) as tmp:
        tmp = Path(tmp)
        fname = url.rsplit("/", 1)[-1]
        if name == "ytdlp":
            fname = "yt-dlp.zip"
        archive = tmp / fname
        _download(url, archive, task, 0, 0.9)
        if task:
            task.update(0.92, "Распаковываю…")
        if name == "ytdlp":
            if not zipfile.is_zipfile(archive):
                raise EngineError("Скачался не тот файл yt-dlp")
            final = edir / "yt-dlp.zip"
            shutil.move(str(archive), final)
            result = str(final)
        else:
            unpacked = tmp / "x"
            _extract(archive, unpacked)
            first = spec["exe"][0]
            found = next((p for p in unpacked.rglob(first) if p.is_file()), None)
            if not found:
                raise EngineError(f"В архиве нет {first}")
            final = edir / name
            shutil.rmtree(final, ignore_errors=True)
            # вся распакованная папка: рядом с exe лежат нужные ему dll и настройки
            shutil.move(str(unpacked), final)
            result = str(final / found.parent.relative_to(unpacked))
            for exe in spec["exe"][1:]:
                if not (Path(result) / exe).exists():
                    raise EngineError(f"В архиве нет {exe}")
    paths = _load_paths()
    paths[name] = result
    _paths_file().write_text(json.dumps(paths, ensure_ascii=False, indent=1), encoding="utf-8")
    if name != "ytdlp":
        os.environ["PATH"] = result + os.pathsep + os.environ.get("PATH", "")
    if task:
        task.done = 1
    return result


def remove(name: str) -> None:
    paths = _load_paths()
    p = paths.pop(name, None)
    if p:
        target = Path(p)
        if target.is_file():
            target.unlink(missing_ok=True)
        else:
            shutil.rmtree(engines_dir() / name, ignore_errors=True)
    _paths_file().write_text(json.dumps(paths, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    # python engines.py install ffmpeg pandoc ...   (используется в CI на Windows)
    if len(sys.argv) >= 3 and sys.argv[1] == "install":
        for n in sys.argv[2:]:
            print(n, "->", install(n))
        activate()
        print("PATH ok:", {n: bool(shutil.which(CATALOG[n]["exe"][0])) for n in sys.argv[2:] if n != "ytdlp"})
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1))
