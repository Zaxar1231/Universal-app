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
GITHUB_WEB = "https://github.com"
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
        "repo": "ImageMagick/ImageMagick",
        # zip, если есть; иначе 7z (его распаковывает 7zr.exe: py7zr не умеет фильтр BCJ2)
        "asset": [r"portable-Q16-HDRI-x64\.zip$", r"portable-Q16-HDRI-x64\.7z$"],
        "for": "картинки",
    },
    "deno": {
        # JavaScript-движок, которым yt-dlp решает задачки YouTube; без него YouTube режет скорость
        "title": "deno", "size": "≈ 45 МБ", "exe": ["deno.exe"],
        "url": "https://github.com/denoland/deno/releases/latest/download/{asset}",
        "for": "быстрое скачивание с YouTube",
    },
    "tesseract": {
        # официальный установщик для Windows (NSIS) распаковывается 7-Zip, ставить и права администратора не нужно
        "title": "Tesseract OCR", "size": "≈ 110 МБ", "exe": ["tesseract.exe"],
        "repo": "tesseract-ocr/tesseract", "asset": r"^tesseract-ocr-w64-setup-.*\.exe$", "any_release": True,
        "index": "https://digi.bib.uni-mannheim.de/tesseract/", "nsis": True, "langs": ["rus", "eng", "osd"],
        "for": "распознавание текста в сканах (OCR)",
    },
    "ytdlp": {
        "title": "свежий yt-dlp", "size": "≈ 3 МБ", "exe": ["yt-dlp.zip"],
        "url": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
        "for": "скачивание видео, если какой-то сайт перестал качаться",
    },
}


def _deno_asset() -> str:
    import platform
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    target = {"win32": "pc-windows-msvc", "darwin": "apple-darwin"}.get(sys.platform, "unknown-linux-gnu")
    return f"deno-{arch}-{target}.zip"


def _exe_names(name: str) -> list[str]:
    names = CATALOG[name]["exe"]
    if name == "deno" and sys.platform != "win32":  # deno есть и для Linux/macOS, там без .exe
        names = [n[:-4] if n.endswith(".exe") else n for n in names]
    return names


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


def can_install(name: str = "") -> bool:
    """Бинарные движки качаем только на Windows: на Linux они приходят зависимостями
    пакета, на macOS ставятся через Homebrew. Свежий yt-dlp годится везде."""
    if name in ("ytdlp", "deno"):
        return True
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


def _get(url: str, timeout: int = 30):
    headers = dict(UA)
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(GITHUB_API.split("/repos/")[0]):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def _assets_from_api(repo: str) -> list[tuple[str, str]]:
    with _get(GITHUB_API.format(repo=repo)) as r:
        data = json.load(r)
    return [(a["name"], a["browser_download_url"]) for a in data.get("assets", [])]


def _assets_from_releases(repo: str) -> list[tuple[str, str]]:
    """Файлы нескольких последних релизов подряд: у Tesseract установщик есть не в каждом релизе."""
    with _get(GITHUB_API.format(repo=repo).replace("/latest", "?per_page=15")) as r:
        data = json.load(r)
    return [(a["name"], a["browser_download_url"]) for rel in data if not rel.get("prerelease")
            for a in rel.get("assets", [])]


def _assets_from_index(url: str) -> list[tuple[str, str]]:
    """Страница-список файлов (UB Mannheim для Tesseract): новые версии в конце."""
    with _get(url) as r:
        page = r.read().decode("utf-8", "replace")
    names = list(dict.fromkeys(re.findall(r'href="([^"/?]+\.exe)"', page)))
    names.sort(key=lambda n: [int(x) for x in re.findall(r"\d+", n)], reverse=True)
    return [(n, url.rstrip("/") + "/" + n) for n in names]


def _assets_from_html(repo: str) -> list[tuple[str, str]]:
    """Запасной путь без API (у API лимит 60 запросов в час на IP): страница релиза."""
    with _get(f"{GITHUB_WEB}/{repo}/releases/latest") as r:
        tag = r.geturl().rstrip("/").rsplit("/", 1)[-1]      # редирект на .../releases/tag/<tag>
    with _get(f"{GITHUB_WEB}/{repo}/releases/expanded_assets/{tag}") as r:
        page = r.read().decode("utf-8", "replace")
    links = re.findall(r'href="(/' + re.escape(repo) + r'/releases/download/[^"]+)"', page)
    return [(l.rsplit("/", 1)[-1], GITHUB_WEB + l) for l in dict.fromkeys(links)]


def _resolve_url(spec: dict) -> str:
    if "url" in spec:
        return spec["url"].replace("{asset}", _deno_asset())
    errors, listed = [], False
    sources = [_assets_from_api, _assets_from_html]
    if spec.get("any_release"):
        sources.insert(1, _assets_from_releases)
    if spec.get("index"):
        sources.append(lambda _repo: _assets_from_index(spec["index"]))
    for source in sources:
        try:
            assets = source(spec["repo"])
        except Exception as e:
            errors.append(e.__class__.__name__)
            continue
        listed = True
        patterns = spec["asset"] if isinstance(spec["asset"], list) else [spec["asset"]]
        for pattern in patterns:          # по приоритету: сначала zip, потом 7z
            for name, url in assets:
                if re.search(pattern, name):
                    return url
    if listed:
        raise EngineError(f"В последнем релизе {spec['repo']} нет файла для Windows")
    raise EngineError(f"Не удалось узнать последнюю версию ({', '.join(errors)}). Проверь интернет")


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


SEVEN_ZIP_URL = "https://www.7-zip.org/a/7zr.exe"   # официальный консольный 7-Zip, только формат 7z


def _seven_zip_exe() -> str:
    """7zr.exe рядом с движками (скачивается один раз), на других ОС системный 7z."""
    found = shutil.which("7zr") or shutil.which("7z") or shutil.which("7za")
    if found:
        return found
    if sys.platform != "win32":
        raise EngineError("Для распаковки этого архива нужен 7-Zip (пакет p7zip)")
    exe = engines_dir() / "tools" / "7zr.exe"
    if not exe.exists():
        exe.parent.mkdir(parents=True, exist_ok=True)
        _download(SEVEN_ZIP_URL, exe, None, 0, 0)
    return str(exe)


def _extract_7z(archive: Path, target: Path) -> None:
    try:
        import py7zr  # type: ignore
        with py7zr.SevenZipFile(archive) as z:
            z.extractall(target)
        return
    except ImportError:
        pass
    except Exception:                       # BCJ2 и прочие фильтры, которых py7zr не знает
        shutil.rmtree(target, ignore_errors=True)
    import subprocess
    res = subprocess.run([_seven_zip_exe(), "x", str(archive), f"-o{target}", "-y"],
                         capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        raise EngineError("Не удалось распаковать архив движка: " + (res.stderr or res.stdout).strip()[-200:])


SEVEN_ZIP_FULL = {"repo": "ip7z/7zip", "asset": [r"^7z\d+-x64\.exe$", r"^7z\d+-x64\.msi$"]}


def _seven_zip_full() -> str:
    """Полный 7-Zip (7z.exe + 7z.dll): только он открывает установщики NSIS. Свой или установленный."""
    for c in (shutil.which("7z"), os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "7-Zip", "7z.exe")):
        if c and os.path.isfile(c):
            return c
    if sys.platform != "win32":
        raise EngineError("Для распаковки установщика нужен 7-Zip (пакет p7zip-full)")
    home = engines_dir() / "tools" / "7zip"
    found = next(home.rglob("7z.exe"), None) if home.is_dir() else None
    if found:
        return str(found)
    import subprocess
    url = _resolve_url(SEVEN_ZIP_FULL)
    with tempfile.TemporaryDirectory(dir=engines_dir(), ignore_cleanup_errors=True) as tmp:
        inst = Path(tmp) / url.rsplit("/", 1)[-1]
        _download(url, inst, None, 0, 0)
        shutil.rmtree(home, ignore_errors=True)
        if inst.suffix.lower() == ".msi":
            # административная установка: просто распаковка MSI в папку, без прав администратора
            subprocess.run(["msiexec", "/a", str(inst), "/qn", f"TARGETDIR={home}"], capture_output=True, timeout=300)
        else:
            # установщик 7-Zip — самораспаковывающийся архив 7z, его открывает даже 7zr.exe
            subprocess.run([_seven_zip_exe(), "x", str(inst), f"-o{home}", "-y"], capture_output=True, timeout=300)
    found = next(home.rglob("7z.exe"), None) if home.is_dir() else None
    if not found:
        raise EngineError("Не удалось подготовить 7-Zip для распаковки установщика")
    return str(found)


def _extract_nsis(installer: Path, target: Path) -> None:
    import subprocess
    res = subprocess.run([_seven_zip_full(), "x", str(installer), f"-o{target}", "-y"],
                         capture_output=True, text=True, errors="replace", timeout=600)
    if res.returncode != 0:
        raise EngineError("Не удалось распаковать установщик: " + (res.stderr or res.stdout).strip()[-200:])


TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata"


def _tessdata(exe_dir: Path, langs: list[str], task: Task | None) -> None:
    """Языки для Tesseract: установщик кладёт только английский, русский докачиваем."""
    data = exe_dir / "tessdata"
    data.mkdir(exist_ok=True)
    for i, lang in enumerate(langs):
        f = data / f"{lang}.traineddata"
        if f.exists() and f.stat().st_size > 1000:
            continue
        if task:
            task.update(0.93 + 0.06 * i / len(langs), f"Скачиваю язык {lang}…")
        _download(TESSDATA_URL.format(lang=lang), f, task, 0.93, 0)


def _extract(archive: Path, target: Path) -> None:
    if archive.suffix.lower() == ".7z":
        _extract_7z(archive, target)
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
    # ignore_cleanup_errors: Windows держит файл открытым, и ошибка уборки скрыла бы настоящую
    with tempfile.TemporaryDirectory(dir=edir, ignore_cleanup_errors=True) as tmp:
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
            if spec.get("nsis"):
                _extract_nsis(archive, unpacked)
            else:
                _extract(archive, unpacked)
            first = _exe_names(name)[0]
            found = next((p for p in unpacked.rglob(first) if p.is_file()), None)
            if not found:
                raise EngineError(f"В архиве нет {first}")
            final = edir / name
            shutil.rmtree(final, ignore_errors=True)
            # вся распакованная папка: рядом с exe лежат нужные ему dll и настройки
            shutil.move(str(unpacked), final)
            result = str(final / found.parent.relative_to(unpacked))
            for exe in _exe_names(name)[1:]:
                if not (Path(result) / exe).exists():
                    raise EngineError(f"В архиве нет {exe}")
            if spec.get("langs"):
                _tessdata(Path(result), spec["langs"], task)
            if sys.platform != "win32":              # zip не хранит права на запуск
                for exe in _exe_names(name):
                    p = Path(result) / exe
                    if p.exists():
                        p.chmod(0o755)
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
