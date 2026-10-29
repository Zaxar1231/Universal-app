"""
Документы в Markdown: PDF, сканы и картинки, Word, PowerPoint, Excel, EPUB, HTML. Читает MinerU
(https://github.com/opendatalab/MinerU), которого ставит и запускает код TextFold из third_party.

MinerU большой, поэтому в установщик он не входит: ставится по запросу в собственную папку
(<папка данных приложений>/TextFold/engine, её же использует читалка: один раз скачал, пользуются оба):
  «базовый»  ≈ 1 ГБ: пакет MinerU без больших моделей. Быстрый режим: PDF с текстом, Word, PowerPoint, Excel,
             EPUB, HTML; сканы и картинки тоже (при первом таком файле сам докачает ≈ 230 МБ распознавания).
             Секунды на файл, заголовки, списки и таблицы по разметке документа.
  «полный»   ≈ 3 ГБ: плюс модели. Точный режим: сложные сканы и страницы, формулы в LaTeX, таблицы со слитыми
             ячейками. Несколько секунд на страницу.
Все вычисления на этом компьютере, файлы никуда не отправляются (удалённый режим MinerU не используется).
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import sys
import time
from pathlib import Path

import reader
from tasks import Cancelled, Task

BASIC_MARK = "installed-basic.txt"          # пакет MinerU поставлен (моделей может не быть)
# что читает MinerU
INPUT_EXT = ("pdf", "docx", "doc", "rtf", "pptx", "ppt", "xlsx", "xls", "odt", "ods", "odp", "epub", "html", "htm",
             "mhtml", "mht", "csv", "tsv", "png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif", "jp2")
TIERS = {"fast": "flash", "accurate": "advanced"}
IMAGES = ("files", "inline", "none")     # картинки: файлами рядом / внутри Markdown (base64) / не нужны
DATA_IMG = re.compile(r"!\[([^\]]*)\]\(data:image/([\w.+-]+);base64,([A-Za-z0-9+/=\s]+)\)")
ANY_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)[ \t]*\n?")


class MdError(Exception):
    pass


def _engine():
    """Модуль textfold.engine (установка и запуск MinerU): после подготовки пути и папки данных."""
    reader.prepare()
    try:
        from textfold import engine
    except ImportError as e:
        raise MdError("Не нашёл читалку TextFold (third_party/textfold): " + str(e))
    return engine


def supported() -> bool:
    """MinerU ставится на Windows, macOS и Linux; на телефоне и в Docker не нужен."""
    if os.environ.get("UNIVERSAL_ANDROID") or os.environ.get("UNIVERSAL_DOCKER"):
        return False
    return sys.platform in ("win32", "darwin", "linux") and (reader.THIRD / "textfold").is_dir()


def status() -> dict:
    out = {"supported": supported(), "basic": False, "full": False, "path": "", "version": "", "bytes": 0}
    if not out["supported"]:
        return out
    eng = _engine()
    e = eng.where()                       # полный: пакет и модели
    out["full"] = bool(e)
    if not e:
        home = eng.home()
        if eng._bin(home, "mineru-kit").exists() and (home / BASIC_MARK).exists():
            e = home
    out["basic"] = bool(e)
    out["path"] = str(e or eng.home())
    out["version"] = eng.VERSION
    if e:
        if out["full"]:
            try:
                out["bytes"] = eng.status().get("bytes", 0)      # считается один раз: папка большая
            except OSError:
                pass
    return out


def can_read(name: str) -> bool:
    return Path(name).suffix.lower().lstrip(".") in INPUT_EXT


# --------------------------------------------------------------------- установка

def install(level: str, task: Task) -> None:
    """level: basic (пакет, ≈ 1 ГБ) или full (пакет и модели, ≈ 3,4 ГБ). Шаги можно прервать и начать снова."""
    if level not in ("basic", "full"):
        raise MdError("Неизвестный уровень установки")
    if not supported():
        raise MdError("MinerU здесь не ставится")
    eng = _engine()
    e = eng.home()
    e.mkdir(parents=True, exist_ok=True)
    env = eng._env(e)
    stop = task.cancel_event
    steps = ["Готовлю установщик (uv)", "Ставлю Python для MinerU", "Ставлю MinerU"] + (["Скачиваю модели"] if level == "full" else [])
    n = len(steps)
    task.total = n
    tail: list[str] = []

    def log(line: str) -> None:
        tail.append(line)
        del tail[:-6]

    def go(i: int, work) -> None:
        task.update(i / n, f"{steps[i]} ({i + 1} из {n})")

        def on_line(line: str) -> None:
            m = re.search(r"(\d{1,3})%\|", line)
            if m:
                task.update((i + min(int(m.group(1)), 100) / 100) / n, f"{steps[i]}: {m.group(1)}% ({i + 1} из {n})")
        try:
            work(on_line)
        except eng.Stopped:
            raise Cancelled()
        except RuntimeError as ex:
            raise MdError(f"{steps[i]}: не получилось ({ex}). " + (" / ".join(tail[-2:])[:300] if tail else ""))
        task.check_cancel()

    uv_holder: list[str] = []
    go(0, lambda on: uv_holder.append(eng._uv(e, log, stop)))
    uv = uv_holder[0]
    if not eng._bin(e, "python").exists():
        go(1, lambda on: eng._run([uv, "venv", e / "venv", "--python", "3.12", "--python-preference", "only-managed"], env, log, stop, on))
    else:
        task.update(2 / n, f"{steps[1]}: уже есть")
    go(2, lambda on: eng._run([uv, "pip", "install", "--python", eng._bin(e, "python"), f"mineru=={eng.VERSION}"], env, log, stop, on))
    (e / BASIC_MARK).write_text(f"mineru=={eng.VERSION}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    if level == "full":
        go(3, lambda on: eng._run([eng._bin(e, "mineru-kit"), "models", "download", "--tier", "standard"], env, log, stop, on))
        (e / eng.MARK).write_text(f"mineru=={eng.VERSION}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    shutil.rmtree(e / ".uv-cache", ignore_errors=True)
    eng._size.clear()
    task.update(1, "Готово")
    task.done = 1


def uninstall() -> dict:
    eng = _engine()
    e = eng.home()
    if e.exists():
        shutil.rmtree(e, ignore_errors=True)
    eng._size.clear()
    return status()


# --------------------------------------------------------------------- конвертация

def read_opts(form) -> dict:
    tier = form.get("tier", "fast")
    images = form.get("images", "files")
    return {"tier": tier if tier in TIERS else "fast", "images": images if images in IMAGES else "files"}


def _convert_one(eng, e: Path, src: Path, dst: Path, o: dict, task: Task, base: float, span: float) -> list[Path]:
    """-> файлы результата в dst: <имя>.md и, если в документе были картинки, рядом папка <имя>_images."""
    tier = TIERS[o["tier"]]
    work = dst / f".work_{src.stem}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    cmd = [eng._bin(e, "mineru-kit"), "parse", src, "-o", work, "--format", "markdown", "--tier", tier]
    if tier == "advanced":
        cmd.append("--disable-image-analysis")      # картинка → таблица данных или код схемы: не нужно, тянет время
    stop = task.cancel_event

    def on_line(line: str) -> None:
        m = eng.BAR.match(line)
        if m and int(m.group("total")):
            task.update(base + span * int(m.group("done")) / int(m.group("total")))
    try:
        eng._run(cmd, eng._env(e), lambda line: None, stop, on_line)
    except eng.Stopped:
        shutil.rmtree(work, ignore_errors=True)
        raise Cancelled()
    except RuntimeError as ex:
        shutil.rmtree(work, ignore_errors=True)
        raise MdError(f"MinerU не справился с файлом ({ex})")
    mds = sorted(work.rglob("*.md"))
    if not mds:
        shutil.rmtree(work, ignore_errors=True)
        raise MdError("MinerU ничего не вернул")
    md = mds[0]
    text = md.read_text(encoding="utf-8", errors="replace")
    files = [p for p in md.parent.rglob("*") if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")]
    out: list[Path] = []
    if o["images"] == "none":
        text = ANY_IMG.sub("", text)
    else:
        folder = dst / f"{src.stem}_images"
        count = [0]

        def put(name: str, data: bytes) -> str:
            folder.mkdir(exist_ok=True)
            (folder / name).write_bytes(data)
            out.append(folder / name)
            return f"{folder.name}/{name}"
        for p in files:                              # картинки, которые MinerU положил файлами
            rel = str(p.relative_to(md.parent)).replace(os.sep, "/")
            new = put(p.name, p.read_bytes())
            text = text.replace(rel, new)
        if o["images"] == "files":                   # и вшитые в текст (base64) выносим в файлы
            def extract(m):
                count[0] += 1
                ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(m.group(2).lower(), m.group(2).lower())
                try:
                    data = base64.b64decode(re.sub(r"\s+", "", m.group(3)))
                except ValueError:
                    return m.group(0)
                return f"![{m.group(1)}]({put(f'image{count[0]}.{ext}', data)})"
            text = DATA_IMG.sub(extract, text)
    target = dst / f"{src.stem}.md"
    target.write_text(text, encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return [target] + out


def run_md_task(task: Task, items: list[tuple[Path, str]], o: dict) -> None:
    eng = _engine()
    st = status()
    if o["tier"] == "accurate" and not st["full"]:
        raise MdError("Точному режиму нужны модели: поставь «полный» движок (≈ 3,4 ГБ) или выбери быстрый режим")
    e = eng.where() or (eng.home() if st["basic"] else None)
    if e is None:
        raise MdError("Движок MinerU не установлен: поставь его ниже")
    out, n = task.out_dir, len(items)
    task.total = n
    for i, (src, stem) in enumerate(items):
        task.check_cancel()
        name = src.name
        base, span = i / n, 1 / n
        task.update(base, f"Читаю {i + 1} из {n}: {name}")
        try:
            files = _convert_one(eng, e, src, out, o, task, base, span)
            text = files[0].read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                for f in files:
                    f.unlink(missing_ok=True)
                task.failed.append({"name": name, "error": "Текста не нашлось" + (": попробуй точный режим" if o["tier"] == "fast" else "")})
                continue
            task.done += 1
        except Cancelled:
            raise
        except MdError as ex:
            if task.cancel_event.is_set():
                raise Cancelled()
            task.failed.append({"name": name, "error": str(ex)[:300]})
    task.update(1, "Готово" if not task.failed else "Готово, но не всё получилось")
    if not task.done and task.failed:
        raise MdError(task.failed[0]["error"])
