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

import net
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
        # macOS: статические сборки Martin Riedl, ffmpeg и ffprobe отдельными архивами
        "mac": {"size": "≈ 80 МБ", "urls": ["https://ffmpeg.martin-riedl.de/redirect/latest/macos/{riedl}/release/ffmpeg.zip",
                                           "https://ffmpeg.martin-riedl.de/redirect/latest/macos/{riedl}/release/ffprobe.zip"]},
    },
    "pandoc": {
        "title": "pandoc", "size": "≈ 40 МБ", "exe": ["pandoc.exe"],
        "repo": "jgm/pandoc", "asset": r"windows-x86_64\.zip$",
        "for": "документы",
        "mac": {"asset": r"{arch}-macOS\.zip$"},
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
    "monolith": {
        # сохраняет страницу сайта одним HTML-файлом (CC0); один исполняемый файл, без архива
        "title": "monolith", "size": "≈ 8 МБ", "exe": ["monolith.exe"], "single": True,
        "url": "https://github.com/Y2Z/monolith/releases/latest/download/{asset}", "asset_fn": "_monolith_asset",
        "for": "сохранить страницу сайта одним файлом",
    },
    "ytdlp": {
        "title": "свежий yt-dlp", "size": "≈ 3 МБ", "exe": ["yt-dlp.zip"],
        "url": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
        "for": "скачивание видео, если какой-то сайт перестал качаться",
    },
}


def _monolith_asset() -> str:
    """Имя файла в релизах Y2Z/monolith для этой системы."""
    arm = _arch() == "arm64"
    if sys.platform == "win32":
        return "monolith-cli.exe"
    if sys.platform == "darwin":
        return f"monolith-macos-{'aarch64' if arm else 'x86_64'}-apple-darwin"
    return f"monolith-cli-linux-{'aarch64' if arm else 'x86_64'}"


def _deno_asset() -> str:
    import platform
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    target = {"win32": "pc-windows-msvc", "darwin": "apple-darwin"}.get(sys.platform, "unknown-linux-gnu")
    return f"deno-{arch}-{target}.zip"


# что ставить само при первом запуске настольной версии, по порядку нужности
AUTO = ("ffmpeg", "pandoc", "imagemagick", "poppler", "deno", "tesseract", "monolith")
# macOS: чего нет в виде готовых архивов, ставим Homebrew (если он есть)
BREW = {"imagemagick": ["imagemagick"], "poppler": ["poppler"], "tesseract": ["tesseract", "tesseract-lang"],
        "ffmpeg": ["ffmpeg"], "pandoc": ["pandoc"], "deno": ["deno"]}


def _arch() -> str:
    import platform
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def _spec(name: str) -> dict:
    """Описание движка для этой системы: на macOS поверх общего — ключ "mac"."""
    spec = dict(CATALOG[name])
    mac = spec.pop("mac", None)
    if sys.platform == "darwin" and mac:
        if "urls" in mac or "asset" in mac:
            spec.pop("url", None)        # у Windows-сборки прямая ссылка, на macOS — своя
        spec.update(mac)
    return spec


def _exe_names(name: str) -> list[str]:
    names = CATALOG[name]["exe"]
    # без .exe: deno для Linux/macOS и сборки для macOS (на Linux качаем только deno и yt-dlp)
    if (name in ("deno", "monolith") and sys.platform != "win32") or sys.platform == "darwin":
        names = [n[:-4] if n.endswith(".exe") else n for n in names]
    return names


def _langs_ok(name: str, d: str) -> bool:
    """У нашего Tesseract должны быть и словари: без русского он почти бесполезен."""
    langs = CATALOG[name].get("langs")
    return not langs or all((Path(d) / "tessdata" / f"{l}.traineddata").is_file() for l in langs)


def _find_installed(name: str) -> str | None:
    """Уже распакованный нами движок в engines/<имя>: на случай, если paths.json потерялся или испорчен
    (приложение закрыли во время записи) — иначе при каждом запуске всё качалось бы заново."""
    if name == "ytdlp":
        f = engines_dir() / "yt-dlp.zip"
        return str(f) if zipfile.is_zipfile(f) else None
    base = engines_dir() / name
    if not base.is_dir():
        return None
    names = _exe_names(name)
    for exe in base.rglob(names[0]):
        if exe.is_file() and all((exe.parent / n).is_file() for n in names[1:]):
            return str(exe.parent)
    return None


def _discard(files) -> None:
    """Удалить скачанные архивы. Windows может ещё держать файл (7-Zip, антивирус) — тогда не страшно:
    уберём при следующей установке, а сама установка из-за этого не падает."""
    for f in files:
        try:
            Path(f).unlink(missing_ok=True)
        except OSError:
            pass


def _register(name: str, d: str) -> None:
    paths = _load_paths()
    if paths.get(name) != d:
        paths[name] = d
        _save_paths(paths)
    if name != "ytdlp" and d not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def available(name: str) -> bool:
    """Движок уже есть: скачан нами или стоит в системе (в PATH)."""
    d = _load_paths().get(name)
    if not (d and Path(d).exists()):
        d = _find_installed(name)
        if d:
            _register(name, d)
    if d and Path(d).exists():
        return _langs_ok(name, d)
    if name == "ytdlp":
        return True                  # встроен в приложение
    if name == "imagemagick" and sys.platform != "win32" and shutil.which("convert"):
        return True                  # ImageMagick 6 в Linux: convert вместо magick
    names = [n[:-4] if n.endswith(".exe") and sys.platform != "win32" else n for n in _exe_names(name)]
    return all(shutil.which(exe) for exe in names)


def missing() -> list[str]:
    return [n for n in AUTO if not available(n)]


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
        d = json.loads(_paths_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_paths(paths: dict) -> None:
    """Через временный файл: если приложение закроют посреди записи, старый paths.json останется целым."""
    f = _paths_file()
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(paths, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


YTDLP_MIN_PYTHON = (3, 11)


def activate() -> None:
    """Добавляет скачанные движки в PATH и подключает свежий yt-dlp. Вызывать до импорта app."""
    paths = _load_paths()
    dirs = [d for name, d in paths.items() if name != "ytdlp" and Path(d).is_dir()]
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
    yt = paths.get("ytdlp")
    # yt-dlp с 2026.10.26 требует Python 3.11: на 3.10 (Ubuntu 22.04) скачанный свежий не подключаем, он не запустится
    if yt and Path(yt).is_file() and yt not in sys.path and sys.version_info >= YTDLP_MIN_PYTHON:
        sys.path.insert(0, yt)       # zipapp yt-dlp импортируется как обычный пакет


def can_install(name: str = "") -> bool:
    """Скачиваем сами: на Windows всё; на macOS ffmpeg, pandoc и deno (остальное — Homebrew);
    на Linux движки приходят зависимостями пакета. Свежий yt-dlp и deno годятся везде."""
    if name == "ytdlp":
        return sys.version_info >= YTDLP_MIN_PYTHON
    if name in ("deno", "monolith"):
        return True
    if sys.platform == "win32" or os.environ.get("UNIVERSAL_ENGINES_ANYOS"):
        return True
    if sys.platform == "darwin":
        return not name or "mac" in CATALOG.get(name, {})
    return False


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
    return net.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


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
        asset = globals()[spec["asset_fn"]]() if spec.get("asset_fn") else _deno_asset()
        return spec["url"].replace("{asset}", asset)
    spec = dict(spec)
    if isinstance(spec.get("asset"), str):
        spec["asset"] = spec["asset"].replace("{arch}", _arch())
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
    for attempt in range(3):         # сервера сборок иногда отвечают 404/обрывом, со второго раза отдают
        try:
            return _download_once(url, dest, task, base, span)
        except EngineError:
            if attempt == 2 or (task and task.cancel_event.is_set()):
                raise
            import time
            time.sleep(2 + attempt * 3)


def _download_once(url: str, dest: Path, task: Task | None, base: float, span: float) -> None:
    def progress(got: int, total: int) -> None:
        if task:
            mb = f"{got / 1048576:.0f}" + (f" из {total / 1048576:.0f}" if total else "")
            task.update(base + span * (got / total if total else 0), f"Скачиваю: {mb} МБ")
    try:
        net.download(url, dest, headers=UA, progress=progress, check_cancel=task.check_cancel if task else None)
    except EngineError:
        raise
    except Exception as e:
        if task and task.cancel_event.is_set():
            raise
        raise EngineError(f"Загрузка прервалась ({net.reason(e)}). Попробуй ещё раз")


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
    spec = _spec(name)
    edir = engines_dir()
    if task:
        task.total = 1
    have = _find_installed(name)
    if have and name != "ytdlp":            # уже распакован (например, не докачались только словари)
        _register(name, have)
        _discard((edir / "downloads").glob(f"{name}-*"))    # движок на месте: старые архивы и куски не нужны
        if spec.get("langs"):
            _tessdata(Path(have), spec["langs"], task)
        if task:
            task.done = 1
        return have
    if task:
        task.update(0, f"Ищу последнюю версию {spec['title']}…")
    riedl = "arm64" if _arch() == "arm64" else "amd64"
    urls = [u.replace("{riedl}", riedl) for u in spec["urls"]] if "urls" in spec else [_resolve_url(spec)]
    url = urls[0]
    if spec.get("single"):
        return _install_single(name, spec, url, task)
    # ignore_cleanup_errors: Windows держит файл открытым, и ошибка уборки скрыла бы настоящую
    with tempfile.TemporaryDirectory(dir=edir, ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        fname = url.rsplit("/", 1)[-1]
        if name == "ytdlp":
            fname = "yt-dlp.zip"
        # архивы качаются в engines/downloads с докачкой: закрыли приложение — продолжим с того же места
        cache = edir / "downloads"
        cache.mkdir(exist_ok=True)
        archive = cache / f"{name}-{fname}"
        _download(url, archive, task, 0, 0.9 / len(urls))
        extra = []
        for i, u in enumerate(urls[1:], 1):          # macOS ffmpeg: ffprobe отдельным архивом
            a = cache / f"{name}-part{i}.zip"
            _download(u, a, task, 0.9 * i / len(urls), 0.9 / len(urls))
            extra.append(a)
        downloaded = [archive, *extra]
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
            try:
                if spec.get("nsis"):
                    _extract_nsis(archive, unpacked)
                else:
                    _extract(archive, unpacked)
                for a in extra:
                    _extract(a, unpacked)
            except Exception:
                _discard(downloaded)                 # битый архив: в следующий раз скачать заново
                raise
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
            if sys.platform != "win32":              # zip не хранит права на запуск
                for exe in _exe_names(name):
                    p = Path(result) / exe
                    if p.exists():
                        p.chmod(0o755)
        _discard(downloaded)
    # запоминаем сразу: если дальше не докачаются словари, сам движок качать снова не придётся
    _register(name, result)
    if spec.get("langs"):
        _tessdata(Path(result), spec["langs"], task)
    if task:
        task.done = 1
    return result


def _install_single(name: str, spec: dict, url: str, task: Task | None) -> str:
    """Движок из одного исполняемого файла (monolith): без распаковки, сразу в engines/<имя>."""
    edir = engines_dir()
    cache = edir / "downloads"
    cache.mkdir(exist_ok=True)
    tmp = cache / f"{name}-{url.rsplit('/', 1)[-1]}"
    _download(url, tmp, task, 0, 0.95)
    if tmp.stat().st_size < 100_000:
        _discard([tmp])
        raise EngineError(f"Скачался не тот файл {name}")
    target = edir / name
    target.mkdir(exist_ok=True)
    exe = target / _exe_names(name)[0]
    os.replace(tmp, exe)
    if sys.platform != "win32":
        exe.chmod(0o755)
    _register(name, str(target))
    if task:
        task.done = 1
    return str(target)


def remove(name: str) -> None:
    paths = _load_paths()
    p = paths.pop(name, None)
    if p:
        target = Path(p)
        if target.is_file():
            target.unlink(missing_ok=True)
        else:
            shutil.rmtree(engines_dir() / name, ignore_errors=True)
    _save_paths(paths)


if __name__ == "__main__":
    # python engines.py install ffmpeg pandoc ...   (используется в CI на Windows)
    if len(sys.argv) >= 3 and sys.argv[1] == "install":
        for n in sys.argv[2:]:
            print(n, "->", install(n))
        activate()
        print("PATH ok:", {n: bool(shutil.which(CATALOG[n]["exe"][0])) for n in sys.argv[2:] if n != "ytdlp"})
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1))


# --------------------------------------------------------------------- автоустановка

class _Part:
    """Задача одного движка внутри общей: прогресс i-го из n кладётся в общую полоску."""
    def __init__(self, outer: Task, i: int, n: int, title: str):
        self.outer, self.i, self.n, self.title = outer, i, n, title
        self.cancel_event = outer.cancel_event
        self.total = self.done = 0

    def update(self, progress=None, message=None):
        p = (self.i + (progress or 0)) / self.n
        self.outer.update(p, f"{self.title} ({self.i + 1} из {self.n})" + (f": {message}" if message else ""))

    def check_cancel(self):
        self.outer.check_cancel()


def _brew() -> str | None:
    return shutil.which("brew") or next((p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(p)), None)


def setup_plan() -> list[str]:
    """Чего не хватает и что мы можем поставить сами на этой системе."""
    brew = sys.platform == "darwin" and _brew()
    return [n for n in missing() if can_install(n) or (brew and n in BREW)]


def auto_setup(task: Task) -> None:
    """Первый запуск: докачать все недостающие движки по очереди. Ошибки собираются, а не обрывают остальное."""
    import subprocess
    plan = setup_plan()
    task.total = len(plan)
    for i, name in enumerate(plan):
        task.check_cancel()
        title = CATALOG[name]["title"]
        part = _Part(task, i, len(plan), title)
        try:
            if can_install(name):
                install(name, part)
            else:
                part.update(0, "ставлю через Homebrew…")
                r = subprocess.run([_brew(), "install", *BREW[name]], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=3600,
                                   env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"})
                if r.returncode != 0:
                    raise EngineError((r.stderr or r.stdout).strip().splitlines()[-1][:200] if (r.stderr or r.stdout).strip() else "brew не справился")
            task.done += 1
        except Exception as e:
            if task.cancel_event.is_set():
                raise
            task.failed.append({"name": title, "error": str(e)})
    task.update(1, "Готово" if not task.failed else "Готово, но не всё скачалось")
    task.result = {"installed": task.done, "missing": missing()}
