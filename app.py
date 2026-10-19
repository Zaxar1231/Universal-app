"""
Universal: локальный комбайн для файлов.

    конвертер форматов (как vert.sh): картинки, аудио, видео, документы
    PDF в картинки, фото в A4, фото в PDF
    редактор видео и аудио на ffmpeg (обрезка, склейка, сжатие, GIF ...)
    скачивание видео и музыки с YouTube, TikTok, VK и ~1800 сайтов (yt-dlp)
    QR-коды: один или пачкой из .txt/.csv/.docx

Запуск:
    pip install -r requirements.txt
    python app.py
Открыть: http://localhost:5000

Переменные окружения:
    HOST (127.0.0.1), PORT (5000), APP_PASSWORD (пароль на вход, логин любой),
    WORK_DIR (где хранить задания, по умолчанию временная папка системы)
"""
import hmac
import io
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, jsonify, render_template, request, send_file, abort

import converters as conv
import downloader
import editor
import qrtools
from tasks import Task, TaskManager

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 ГБ на запрос

WORK_ROOT = Path(os.environ.get("WORK_DIR") or Path(tempfile.gettempdir()) / "universal_jobs")
WORK_ROOT.mkdir(parents=True, exist_ok=True)
TASKS = TaskManager(workers=int(os.environ.get("TASK_WORKERS", "2")))
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


@app.before_request
def require_password():
    """Если задан APP_PASSWORD, пускаем только с ним (HTTP Basic, логин любой)."""
    if not APP_PASSWORD:
        return None
    auth = request.authorization
    if auth and hmac.compare_digest((auth.password or "").encode(), APP_PASSWORD.encode()):
        return None
    return Response("Нужен пароль", 401, {"WWW-Authenticate": 'Basic realm="Universal", charset="UTF-8"'})

# формат -> (расширение, аргументы pdftoppm / pdftocairo)
# pdftoppm умеет png, jpeg, tiff. Остальное делаем через pdftocairo.
FORMATS = {
    "jpg":  {"ext": "jpg",  "tool": "pdftoppm",   "flag": "-jpeg", "dpi": True,  "quality": True},
    "png":  {"ext": "png",  "tool": "pdftoppm",   "flag": "-png",  "dpi": True,  "quality": False},
    "tiff": {"ext": "tif",  "tool": "pdftoppm",   "flag": "-tiff", "dpi": True,  "quality": False},
    "svg":  {"ext": "svg",  "tool": "pdftocairo", "flag": "-svg",  "dpi": False, "quality": False},
    "ps":   {"ext": "ps",   "tool": "pdftocairo", "flag": "-ps",   "dpi": False, "quality": False},
    "eps":  {"ext": "eps",  "tool": "pdftocairo", "flag": "-eps",  "dpi": False, "quality": False},
}
# WebP poppler сам не умеет: конвертируем png -> webp через Pillow, если он есть
try:
    from PIL import Image, ImageOps  # type: ignore
    FORMATS["webp"] = {"ext": "webp", "tool": "pdftoppm", "flag": "-png", "dpi": True,
                       "quality": True, "post": "webp"}
except ImportError:
    Image = None
    ImageOps = None

# формат -> имя формата в Pillow (для A4-подгонки растровых картинок)
PIL_FORMAT = {"jpg": "JPEG", "png": "PNG", "tiff": "TIFF", "webp": "WEBP"}
A4_MM = (210, 297)

SAFE_NAME = re.compile(r"[^\w\-. ()\[\]]+", re.UNICODE)


def purge_old_jobs(max_age: int = 3600) -> None:
    """Удаляем задания старше часа, чтобы temp не разрастался."""
    now = time.time()
    for d in WORK_ROOT.iterdir():
        task = TASKS.get(d.name)
        if task and task.status in ("queued", "running"):
            continue   # долгая задача ещё работает
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = SAFE_NAME.sub("_", stem).strip(" .") or "file"
    return stem[:120]


WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_archive_name(raw: str) -> str:
    """Имя папки -> безопасное имя ZIP. Кириллицу и пробелы сохраняем,
    убираем то, что нельзя в именах файлов Windows/Linux."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", raw or "").strip(" .")
    name = re.sub(r"_{2,}", "_", name)
    if name.lower().endswith(".zip"):
        name = name[:-4].rstrip(" .")
    if not name or name.upper() in WIN_RESERVED:
        return "converted"
    return name[:100]


_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a","b","v","g","d","e","yo","zh","z","i","y","k","l","m","n","o","p","r","s","t","u","f",
                "kh","ts","ch","sh","shch","","y","","e","yu","ya"]))


def ascii_fallback(name: str) -> str:
    """Запасное ASCII-имя для старых клиентов: 'Отчёты сентябрь' -> 'Otchyoty sentyabr'."""
    out = []
    for ch in name:
        low = ch.lower()
        if low in _TR:
            t = _TR[low]
            out.append(t.capitalize() if ch != low else t)
        else:
            out.append(ch)
    res = re.sub(r"[^A-Za-z0-9._ ()+\-]+", "_", "".join(out)).strip(" ._")
    return res or "converted"


def remember_archive_name(job_dir: Path, raw: str) -> str:
    name = safe_archive_name(raw)
    (job_dir / "name.txt").write_text(name, encoding="utf-8")
    return name + ".zip"


def poppler_ok() -> dict:
    return {t: bool(shutil.which(t)) for t in ("pdftoppm", "pdftocairo")}


def white_canvas(mode: str, size: tuple):
    """Белый холст. Для RGB нужен кортеж: число 255 Pillow читает как красный канал."""
    return Image.new(mode, size, 255 if mode == "L" else (255, 255, 255))


def fit_to_a4(path: Path, fmt: str, dpi: int, quality: int, mode: str,
              orient: str, gray: bool, dest: "Path | None" = None) -> None:
    """Приводит растровую картинку к листу A4.
    mode:   'contain' (целиком, белые поля) или 'cover' (заполнить и обрезать)
    orient: 'auto' (по исходной картинке), 'portrait' или 'landscape'
    """
    w_mm, h_mm = A4_MM
    long_px = round(h_mm / 25.4 * dpi)
    short_px = round(w_mm / 25.4 * dpi)

    with Image.open(path) as src:
        src.load()
        src = ImageOps.exif_transpose(src)  # фото с телефона: применяем поворот из EXIF
        if src.mode in ("RGBA", "LA", "P"):
            # прозрачность заливаем белым, иначе в JPG получится чёрный фон
            src = src.convert("RGBA")
            bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
            src = Image.alpha_composite(bg, src)
        im = src.convert("L" if gray else "RGB")

    if orient == "auto":
        landscape = im.width > im.height
    else:
        landscape = orient == "landscape"
    size = (long_px, short_px) if landscape else (short_px, long_px)

    scale = (max if mode == "cover" else min)(size[0] / im.width, size[1] / im.height)
    new = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    im = im.resize(new, Image.LANCZOS)

    canvas = white_canvas(im.mode, size)
    canvas.paste(im, ((size[0] - new[0]) // 2, (size[1] - new[1]) // 2))

    kwargs = {}
    if fmt in ("jpg", "webp"):
        kwargs["quality"] = quality
    if fmt == "jpg":
        kwargs["dpi"] = (dpi, dpi)
    elif fmt in ("png", "tiff"):
        kwargs["dpi"] = (dpi, dpi)
    canvas.save(dest or path, PIL_FORMAT[fmt], **kwargs)


def convert_one(pdf: Path, out_dir: Path, fmt: str, dpi: int, quality: int,
                all_pages: bool, gray: bool, stem: str,
                a4: bool = False, a4_mode: str = "contain", a4_orient: str = "auto") -> dict:
    spec = FORMATS[fmt]
    target = out_dir / stem
    cmd = [spec["tool"], spec["flag"]]
    if spec["dpi"]:
        cmd += ["-r", str(dpi)]
    if spec["quality"] and spec["flag"] == "-jpeg":
        cmd += ["-jpegopt", f"quality={quality}"]
    if gray and spec["tool"] == "pdftoppm":
        cmd += ["-gray"]
    if not all_pages:
        cmd += ["-f", "1", "-l", "1"]
        if spec["tool"] == "pdftoppm":
            cmd += ["-singlefile"]
        elif spec["flag"] in ("-svg", "-ps", "-eps"):
            pass
    if a4 and spec["flag"] == "-ps":
        # у poppler есть готовый размер бумаги; -expand растягивает страницу под лист
        # (для eps и svg размер листа не задаётся, poppler такие флаги отвергает)
        cmd += ["-paper", "A4", "-expand"]
    # pdftocairo без -singlefile добавляет номер страницы для растровых форматов,
    # для svg/ps/eps выдаёт один файл на вызов
    if spec["tool"] == "pdftocairo" and spec["flag"] in ("-svg", "-ps", "-eps"):
        cmd += [str(pdf), f"{target}.{spec['ext']}"]
    else:
        cmd += [str(pdf), str(target)]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"name": stem, "ok": False, "error": "Слишком долго конвертируется (таймаут 5 минут)"}

    if res.returncode != 0:
        err = (res.stderr or "").strip().splitlines()
        return {"name": stem, "ok": False, "error": err[-1] if err else "Poppler вернул ошибку"}

    produced = sorted(out_dir.glob(f"{stem}*"))
    produced = [p for p in produced if p.is_file() and p.suffix.lower() != ".pdf"]

    # постобработка webp (poppler отдаёт png, дальше Pillow)
    if spec.get("post") == "webp" and Image is not None:
        converted = []
        for p in produced:
            if p.suffix.lower() == ".png":
                w = p.with_suffix(".webp")
                with Image.open(p) as im:
                    im.save(w, "WEBP", quality=quality)
                p.unlink()
                converted.append(w)
            else:
                converted.append(p)
        produced = converted

    # приведение к A4 для растровых форматов
    if a4 and fmt in PIL_FORMAT and Image is not None:
        for p in produced:
            try:
                fit_to_a4(p, fmt, dpi, quality, a4_mode, a4_orient, gray)
            except Exception as e:  # битая картинка не должна валить всю пачку
                return {"name": stem, "ok": False, "error": f"Не удалось подогнать под A4: {e}"}

    if not produced:
        return {"name": stem, "ok": False, "error": "Poppler ничего не создал (пустой или битый PDF?)"}
    return {"name": stem, "ok": True, "files": [p.name for p in produced]}


@app.route("/")
def index():
    return render_template("index.html", formats=list(FORMATS.keys()), poppler=poppler_ok())


@app.route("/api/status")
def status():
    return jsonify({"poppler": poppler_ok(), "formats": list(FORMATS.keys())})


@app.route("/api/convert", methods=["POST"])
def convert():
    if not all(poppler_ok().values()):
        return jsonify({"error": "Poppler не найден в PATH. Установи poppler-utils и перезапусти сервер."}), 500

    purge_old_jobs()

    fmt = request.form.get("format", "jpg").lower()
    if fmt not in FORMATS:
        return jsonify({"error": f"Формат {fmt} не поддерживается"}), 400

    try:
        dpi = max(30, min(600, int(request.form.get("dpi", 150))))
        quality = max(1, min(100, int(request.form.get("quality", 90))))
    except ValueError:
        return jsonify({"error": "DPI и качество должны быть числами"}), 400
    all_pages = request.form.get("all_pages") == "1"
    gray = request.form.get("gray") == "1"
    a4 = request.form.get("a4") == "1"
    a4_mode = request.form.get("a4_mode", "contain")
    a4_orient = request.form.get("a4_orient", "auto")
    if a4_mode not in ("contain", "cover"):
        a4_mode = "contain"
    if a4_orient not in ("auto", "portrait", "landscape"):
        a4_orient = "auto"

    files = [f for f in request.files.getlist("files") if f.filename]
    if not files:
        return jsonify({"error": "Файлы не выбраны"}), 400

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_ROOT / job_id
    in_dir = job_dir / "in"
    out_dir = job_dir / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    archive = remember_archive_name(job_dir, request.form.get("archive_name", ""))

    # уникальные имена, чтобы одинаковые не перетирали друг друга
    used: dict[str, int] = {}
    tasks = []
    for f in files:
        stem = safe_stem(f.filename)
        n = used.get(stem, 0)
        used[stem] = n + 1
        if n:
            stem = f"{stem}_{n}"
        pdf_path = in_dir / f"{stem}.pdf"
        f.save(pdf_path)
        tasks.append((pdf_path, stem))

    workers = max(1, min(8, os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda t: convert_one(t[0], out_dir, fmt, dpi, quality, all_pages, gray, t[1],
                                  a4, a4_mode, a4_orient),
            tasks,
        ))

    shutil.rmtree(in_dir, ignore_errors=True)  # исходники больше не нужны

    ok = [r for r in results if r["ok"]]
    return jsonify({
        "job": job_id,
        "archive": archive,
        "total": len(results),
        "done": len(ok),
        "failed": [r for r in results if not r["ok"]],
        "files": sum(len(r["files"]) for r in ok),
    })



PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
OUT_EXT = {"jpg": ".jpg", "png": ".png", "tiff": ".tif", "webp": ".webp"}


@app.route("/api/photos", methods=["POST"])
def photos():
    """Готовые фото -> A4 с нужным DPI (Pillow, poppler не нужен)."""
    if Image is None:
        return jsonify({"error": "Не установлен Pillow. Выполни: pip install pillow"}), 500

    purge_old_jobs()

    fmt = request.form.get("format", "jpg").lower()
    if fmt not in PIL_FORMAT:
        return jsonify({"error": f"Формат {fmt} не поддерживается для фото"}), 400
    try:
        dpi = max(30, min(1200, int(request.form.get("dpi", 300))))
        quality = max(1, min(100, int(request.form.get("quality", 92))))
    except ValueError:
        return jsonify({"error": "DPI и качество должны быть числами"}), 400
    gray = request.form.get("gray") == "1"
    a4_mode = request.form.get("a4_mode", "contain")
    a4_orient = request.form.get("a4_orient", "auto")
    if a4_mode not in ("contain", "cover"):
        a4_mode = "contain"
    if a4_orient not in ("auto", "portrait", "landscape"):
        a4_orient = "auto"

    files = [f for f in request.files.getlist("files") if f.filename]
    if not files:
        return jsonify({"error": "Файлы не выбраны"}), 400

    job_id = uuid.uuid4().hex[:12]
    in_dir = WORK_ROOT / job_id / "in"
    out_dir = WORK_ROOT / job_id / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    archive = remember_archive_name(WORK_ROOT / job_id, request.form.get("archive_name", ""))

    used: dict[str, int] = {}
    tasks = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        stem = safe_stem(f.filename)
        n = used.get(stem, 0)
        used[stem] = n + 1
        if n:
            stem = f"{stem}_{n}"
        src = in_dir / f"{stem}{ext if ext in PHOTO_EXT else '.bin'}"
        f.save(src)
        tasks.append((src, stem))

    def work(t):
        src, stem = t
        dest = out_dir / f"{stem}{OUT_EXT[fmt]}"
        try:
            fit_to_a4(src, fmt, dpi, quality, a4_mode, a4_orient, gray, dest=dest)
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else e.__class__.__name__
            return {"name": stem, "ok": False, "error": f"Не открылось как картинка: {msg}"}
        return {"name": stem, "ok": True, "files": [dest.name]}

    workers = max(1, min(8, os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, tasks))

    shutil.rmtree(in_dir, ignore_errors=True)
    ok = [r for r in results if r["ok"]]
    return jsonify({
        "job": job_id,
        "archive": archive,
        "total": len(results),
        "done": len(ok),
        "failed": [r for r in results if not r["ok"]],
        "files": len(ok),
    })



# ---------- Фото -> PDF (A4) ----------

# раскладки: (колонки, строки)
LAYOUTS = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3), 8: (2, 4), 9: (3, 3)}


def load_photo(path: Path, gray: bool):
    """Открывает фото: применяет EXIF-поворот, заливает прозрачность белым."""
    with Image.open(path) as src:
        src.load()
        src = ImageOps.exif_transpose(src)
        if src.mode in ("RGBA", "LA", "P"):
            src = src.convert("RGBA")
            bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
            src = Image.alpha_composite(bg, src)
        return src.convert("L" if gray else "RGB")


def place_in_cell(im, cell_w: int, cell_h: int, mode: str):
    """Масштабирует фото под ячейку. contain: целиком, cover: заполнить и обрезать."""
    scale = (max if mode == "cover" else min)(cell_w / im.width, cell_h / im.height)
    new = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    im = im.resize(new, Image.LANCZOS)
    if mode == "cover":  # обрезаем лишнее по центру
        left, top = (new[0] - cell_w) // 2, (new[1] - cell_h) // 2
        im = im.crop((left, top, left + cell_w, top + cell_h))
    return im


def build_pages(photos: list, per_page: int, dpi: int, margin_mm: int, gap_mm: int,
                mode: str, orient: str, gray: bool) -> list:
    """Раскладывает фото по страницам A4. Возвращает список PIL-страниц."""
    cols, rows = LAYOUTS[per_page]
    long_px = round(A4_MM[1] / 25.4 * dpi)
    short_px = round(A4_MM[0] / 25.4 * dpi)
    mm = lambda v: round(v / 25.4 * dpi)
    margin, gap = mm(margin_mm), mm(gap_mm)

    if orient == "auto":
        # для одного фото на листе смотрим на само фото, для сетки берём по большинству
        land = sum(1 for im in photos if im.width > im.height)
        landscape = land > len(photos) / 2
        if per_page > 1:
            landscape = False if (cols, rows) != (2, 1) else landscape
    else:
        landscape = orient == "landscape"
    W, H = (long_px, short_px) if landscape else (short_px, long_px)
    if per_page > 1 and landscape and cols < rows:
        cols, rows = rows, cols  # в альбомной ориентации сетку тоже поворачиваем

    cell_w = (W - 2 * margin - gap * (cols - 1)) // cols
    cell_h = (H - 2 * margin - gap * (rows - 1)) // rows
    if cell_w < 20 or cell_h < 20:
        raise ValueError("Поля и промежутки слишком большие для такой раскладки")

    pages = []
    for start in range(0, len(photos), per_page):
        page = white_canvas("L" if gray else "RGB", (W, H))
        for i, im in enumerate(photos[start:start + per_page]):
            r, c = divmod(i, cols)
            cell = place_in_cell(im, cell_w, cell_h, mode)
            x = margin + c * (cell_w + gap) + (cell_w - cell.width) // 2
            y = margin + r * (cell_h + gap) + (cell_h - cell.height) // 2
            page.paste(cell, (x, y))
        pages.append(page)
    return pages


def save_pdf(pages: list, dest: Path, dpi: int, quality: int) -> None:
    pages[0].save(dest, "PDF", resolution=dpi, save_all=True,
                  append_images=pages[1:], quality=quality)


@app.route("/api/photos2pdf", methods=["POST"])
def photos2pdf():
    """Фото -> PDF на листах A4: 1 фото на страницу или сетка (2/4/6/8/9 на лист)."""
    if Image is None:
        return jsonify({"error": "Не установлен Pillow. Выполни: pip install pillow"}), 500

    purge_old_jobs()

    def num(key, default, lo, hi):
        return max(lo, min(hi, int(request.form.get(key, default))))

    try:
        per_page = num("per_page", 1, 1, 9)
        dpi = num("dpi", 200, 72, 600)
        quality = num("quality", 90, 10, 100)
        margin_mm = num("margin", 10, 0, 50)
        gap_mm = num("gap", 5, 0, 30)
    except ValueError:
        return jsonify({"error": "Числовые настройки заданы неверно"}), 400
    if per_page not in LAYOUTS:
        return jsonify({"error": f"Раскладка на {per_page} фото не поддерживается"}), 400

    gray = request.form.get("gray") == "1"
    mode = request.form.get("a4_mode", "contain")
    orient = request.form.get("a4_orient", "auto")
    output = request.form.get("output", "single")  # single = один PDF, each = PDF на каждое фото
    if mode not in ("contain", "cover"):
        mode = "contain"
    if orient not in ("auto", "portrait", "landscape"):
        orient = "auto"
    if output not in ("single", "each"):
        output = "single"
    order = request.form.get("order", "name")  # name = по имени, keep = как выбрано

    files = [f for f in request.files.getlist("files") if f.filename]
    if not files:
        return jsonify({"error": "Файлы не выбраны"}), 400

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_ROOT / job_id
    in_dir, out_dir = job_dir / "in", job_dir / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    archive_raw = request.form.get("archive_name", "")

    # сохраняем и читаем фото; битые пропускаем, но сообщаем
    entries, failed, used = [], [], {}
    for f in files:
        ext = Path(f.filename).suffix.lower()
        stem = safe_stem(f.filename)
        n = used.get(stem, 0)
        used[stem] = n + 1
        if n:
            stem = f"{stem}_{n}"
        src = in_dir / f"{stem}{ext if ext in PHOTO_EXT else '.bin'}"
        f.save(src)
        try:
            entries.append((stem, load_photo(src, gray)))
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else e.__class__.__name__
            failed.append({"name": stem, "ok": False, "error": f"Не открылось как картинка: {msg}"})
    shutil.rmtree(in_dir, ignore_errors=True)

    if order == "name":
        entries.sort(key=lambda e: [int(x) if x.isdigit() else x.lower()
                                    for x in re.split(r"(\d+)", e[0])])  # IMG_2 раньше IMG_10

    if not entries:
        return jsonify({"job": job_id, "archive": remember_archive_name(job_dir, archive_raw),
                        "total": len(files), "done": 0, "failed": failed, "files": 0, "pages": 0})

    total_pages = 0
    try:
        if output == "single":
            # имя PDF: по папке, а если её нет - по первому файлу
            base = safe_archive_name(archive_raw) if archive_raw else "photos"
            pages = build_pages([im for _, im in entries], per_page, dpi, margin_mm, gap_mm,
                                mode, orient, gray)
            save_pdf(pages, out_dir / f"{base}.pdf", dpi, quality)
            total_pages, pdf_count = len(pages), 1
        else:
            pdf_count = 0
            for stem, im in entries:
                pages = build_pages([im], 1, dpi, margin_mm, gap_mm, mode, orient, gray)
                save_pdf(pages, out_dir / f"{stem}.pdf", dpi, quality)
                total_pages += len(pages)
                pdf_count += 1
    except ValueError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 400

    resp = {
        "job": job_id,
        "total": len(files),
        "done": len(entries),
        "failed": failed,
        "files": pdf_count,
        "pages": total_pages,
        "archive": remember_archive_name(job_dir, archive_raw),
    }
    # если получился один PDF, отдаём его напрямую, без ZIP
    if pdf_count == 1:
        resp["single_pdf"] = next(out_dir.glob("*.pdf")).name
    return jsonify(resp)



# ---------- Универсальный конвертер (набор форматов VERT: картинки, аудио, видео, документы) ----------

FILENAME_FORMAT_OK = re.compile(r"^[^/\\:*?\"<>|]*$")


def apply_name_template(template: str, name: str) -> str:
    """Шаблон имени как в VERT: %name% заменяется на имя исходного файла."""
    template = (template or "%name%").strip()
    if "%name%" not in template:
        template += "%name%"
    return safe_stem(template.replace("%name%", name)) if FILENAME_FORMAT_OK.match(template) else name


@app.route("/api/formats")
def formats():
    """Какие категории и форматы умеет сервер (нужно интерфейсу для выпадающих списков)."""
    eng = conv.engines()
    eng.update({"ytdlp": downloader.available(), "qrcode": qrtools.available()})
    cats = {}
    for name, table in conv.CATEGORIES.items():
        tg = conv.targets_for(name)
        cats[name] = {
            "inputs": sorted(f for f, (r, _) in table.items() if r),
            "outputs": tg["native"] + tg["cross"],   # плоский список (совместимость)
            "native": tg["native"],
            "cross": tg["cross"],
        }
    cats["edit"] = {"inputs": editor.INPUTS, "outputs": editor.VIDEO_OUT + editor.AUDIO_OUT,
                    "native": editor.VIDEO_OUT, "cross": editor.AUDIO_OUT}
    return jsonify({"engines": eng, "categories": cats})


@app.route("/api/universal", methods=["POST"])
def universal():
    """Любой поддерживаемый файл -> выбранный формат. Тип определяется по расширению."""
    to = request.form.get("to", "").lower().lstrip(".")
    if not to:
        return jsonify({"error": "Не выбран формат, в который конвертировать"}), 400
    if not conv.category_of(to):
        return jsonify({"error": f"Формат .{to} не поддерживается"}), 400

    def num(key, default, lo, hi):
        return max(lo, min(hi, int(request.form.get(key, default))))

    try:
        opts = {
            "quality": num("quality", 90, 1, 100),
            "density": num("density", 150, 30, 600),
            "bitrate": request.form.get("bitrate", "auto"),
            "sample_rate": request.form.get("sample_rate", "auto"),
            "keep_metadata": request.form.get("keep_metadata", "1") == "1",
        }
        if opts["bitrate"] != "auto":
            opts["bitrate"] = str(num("bitrate", 128, 8, 640))
        if opts["sample_rate"] != "auto":
            opts["sample_rate"] = str(num("sample_rate", 44100, 8000, 192000))
    except ValueError:
        return jsonify({"error": "Числовые настройки заданы неверно"}), 400
    template = request.form.get("name_template", "%name%")

    files = [f for f in request.files.getlist("files") if f.filename]
    if not files:
        return jsonify({"error": "Файлы не выбраны"}), 400

    purge_old_jobs()
    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_ROOT / job_id
    in_dir, out_dir = job_dir / "in", job_dir / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    archive = remember_archive_name(job_dir, request.form.get("archive_name", ""))

    used: dict[str, int] = {}
    tasks = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        stem = safe_stem(f.filename)
        n = used.get(stem, 0)
        used[stem] = n + 1
        if n:
            stem = f"{stem}_{n}"
        src = in_dir / f"{stem}{ext}"
        f.save(src)
        tasks.append((src, stem))

    def work(t):
        src, stem = t
        out_stem = apply_name_template(template, stem)
        try:
            out = conv.convert_any(src, out_dir, out_stem, to, opts)
        except conv.ConvertError as e:
            return {"name": src.name, "ok": False, "error": str(e)}
        except Exception as e:  # неожиданная ошибка одного файла не должна валить пачку
            return {"name": src.name, "ok": False, "error": f"Внутренняя ошибка: {e.__class__.__name__}"}
        return {"name": src.name, "ok": True, "files": [out.name]}

    # видео тяжёлое: меньше потоков, чтобы не съесть весь процессор
    heavy = any(conv.category_of(t[0].suffix) in ("video",) for t in tasks)
    workers = max(1, min(2 if heavy else 8, os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, tasks))

    shutil.rmtree(in_dir, ignore_errors=True)
    for hidden in out_dir.glob(".*"):   # временные обложки и профили LibreOffice
        shutil.rmtree(hidden, ignore_errors=True) if hidden.is_dir() else hidden.unlink(missing_ok=True)

    ok = [r for r in results if r["ok"]]
    resp = {
        "job": job_id, "archive": archive, "total": len(results),
        "done": len(ok), "failed": [r for r in results if not r["ok"]],
        "files": sum(len(r["files"]) for r in ok),
    }
    if len(ok) == 1 and not resp["failed"]:
        resp["single_file"] = ok[0]["files"][0]
    return jsonify(resp)


@app.route("/api/download/<job>")
def download(job: str):
    if not re.fullmatch(r"[0-9a-f]{12}", job):
        abort(404)
    out_dir = WORK_ROOT / job / "out"
    if not out_dir.is_dir():
        abort(404)
    items = sorted(p for p in out_dir.iterdir() if p.is_file() and not p.name.startswith("."))
    if request.args.get("raw") == "1" and len(items) == 1:
        one = items[0]
        resp = send_file(one, mimetype=mimetypes.guess_type(one.name)[0] or "application/octet-stream",
                         as_attachment=True, download_name="file")
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{ascii_fallback(one.stem)}{one.suffix}"; '
            f"filename*=UTF-8''{quote(one.name, safe='')}"
        )
        return resp
    # ZIP собираем на диске: скачанные видео бывают по несколько гигабайт
    bundle = WORK_ROOT / job / "bundle.zip"
    if not bundle.exists() or bundle.stat().st_mtime < max((p.stat().st_mtime for p in items), default=0):
        tmp = bundle.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for p in items:
                zf.write(p, p.name)
        tmp.replace(bundle)
    name_file = WORK_ROOT / job / "name.txt"
    try:
        base = safe_archive_name(name_file.read_text(encoding="utf-8"))
    except OSError:
        base = "converted"
    resp = send_file(bundle, mimetype="application/zip", as_attachment=True, download_name="converted.zip")
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="{ascii_fallback(base)}.zip"; '
        f"filename*=UTF-8''{quote(base + '.zip', safe='')}"
    )
    return resp


# ---------- Фоновые задачи (редактор, скачивание) ----------

def new_job(archive_raw: str = "") -> tuple[str, Path, str]:
    purge_old_jobs()
    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_ROOT / job_id
    (job_dir / "in").mkdir(parents=True)
    (job_dir / "out").mkdir(parents=True)
    return job_id, job_dir, remember_archive_name(job_dir, archive_raw)


def save_uploads(files, in_dir: Path) -> list[tuple[Path, str]]:
    """Сохраняет загруженные файлы с уникальными безопасными именами."""
    used: dict[str, int] = {}
    out = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,6}", ext):
            ext = ".bin"
        stem = safe_stem(f.filename)
        n = used.get(stem, 0)
        used[stem] = n + 1
        if n:
            stem = f"{stem}_{n}"
        src = in_dir / f"{stem}{ext}"
        f.save(src)
        out.append((src, stem))
    return out


@app.route("/api/task/<tid>")
def task_status(tid: str):
    task = TASKS.get(tid)
    if not task:
        return jsonify({"error": "Задача не найдена (сервер перезапускался?)"}), 404
    return jsonify(task.to_dict())


@app.route("/api/task/<tid>/cancel", methods=["POST"])
def task_cancel(tid: str):
    task = TASKS.get(tid)
    if not task:
        return jsonify({"error": "Задача не найдена"}), 404
    task.cancel()
    return jsonify(task.to_dict())


# ---------- Редактор видео и аудио (ffmpeg) ----------

@app.route("/api/edit", methods=["POST"])
def edit():
    if not shutil.which("ffmpeg"):
        return jsonify({"error": "ffmpeg не найден в PATH. Установи его и перезапусти сервер."}), 500
    try:
        opts = editor.read_opts(request.form)
    except conv.ConvertError as e:
        return jsonify({"error": str(e)}), 400
    files = [f for f in request.files.getlist("files") if f.filename]
    if not files:
        return jsonify({"error": "Файлы не выбраны"}), 400
    bad = [f.filename for f in files if Path(f.filename).suffix.lower().lstrip(".") not in editor.INPUTS]
    if bad:
        return jsonify({"error": f"Это не аудио и не видео: {', '.join(bad[:3])}"}), 400

    job_id, job_dir, archive = new_job(request.form.get("archive_name", "") or "edited")
    items = save_uploads(files, job_dir / "in")
    template = request.form.get("name_template", "%name%_edit")
    items = [(src, apply_name_template(template, stem)) for src, stem in items]
    merge_name = safe_stem(request.form.get("merge_name") or "") if request.form.get("merge_name") else "merged"
    task = Task(job_id, "edit", job_dir, archive)
    TASKS.submit(task, lambda t: editor.run_edit_task(t, items, opts, merge_name))
    return jsonify(task.to_dict())


@app.route("/api/probe", methods=["POST"])
def probe_media():
    """Длительность и размеры файла (для подсказок в редакторе)."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Нет файла"}), 400
    with tempfile.TemporaryDirectory(dir=WORK_ROOT) as d:
        p = Path(d) / ("probe" + Path(f.filename).suffix.lower()[:7])
        f.save(p)
        try:
            return jsonify(editor.probe(p))
        except conv.ConvertError as e:
            return jsonify({"error": str(e)}), 400


# ---------- Скачивание видео (yt-dlp) ----------

def _cookiefile(job_dir: Path | None) -> Path | None:
    f = request.files.get("cookies")
    if not f or not f.filename:
        return None
    target = (job_dir or WORK_ROOT) / f".cookies_{uuid.uuid4().hex[:8]}.txt"
    f.save(target)
    return target


@app.route("/api/dl/info", methods=["POST"])
def dl_info():
    if not downloader.available():
        return jsonify({"error": "yt-dlp не установлен: pip install yt-dlp"}), 500
    urls = downloader.extract_urls(request.form.get("urls", ""))[:20]
    if not urls:
        return jsonify({"error": "Не нашёл ссылок. Вставь адрес, начинающийся с http"}), 400
    cookie = _cookiefile(None)
    items = []
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            def one(u):
                try:
                    return downloader.probe_url(u, cookie)
                except downloader.DownloadFail as e:
                    return {"url": u, "error": str(e)}
            items = list(pool.map(one, urls))
    finally:
        if cookie:
            cookie.unlink(missing_ok=True)
    return jsonify({"items": items})


@app.route("/api/dl", methods=["POST"])
def dl():
    if not downloader.available():
        return jsonify({"error": "yt-dlp не установлен: pip install yt-dlp"}), 500
    urls = downloader.extract_urls(request.form.get("urls", ""))
    if not urls:
        return jsonify({"error": "Не нашёл ссылок. Вставь адрес, начинающийся с http"}), 400
    if len(urls) > 200:
        return jsonify({"error": "Не больше 200 ссылок за раз"}), 400
    opts = downloader.read_opts(request.form)
    job_id, job_dir, archive = new_job(request.form.get("archive_name", "") or "downloads")
    cookie = _cookiefile(job_dir)
    task = Task(job_id, "dl", job_dir, archive)
    TASKS.submit(task, lambda t: downloader.run_download_task(t, urls, opts, cookie))
    return jsonify(task.to_dict())


# ---------- QR-коды ----------

@app.route("/api/qr", methods=["POST"])
def qr_one():
    """Один QR. ?download=1 отдаёт файлом, иначе картинкой для предпросмотра."""
    try:
        st = qrtools.read_style(request.form)
        data = qrtools.payload_from_form(request.form)
        body = qrtools.render(data, st)
    except qrtools.QRError as e:
        return jsonify({"error": str(e)}), 400
    resp = Response(body, mimetype=qrtools.MIME[st["format"]])
    if request.args.get("download") == "1":
        resp.headers["Content-Disposition"] = f'attachment; filename="qr.{st["format"]}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/qr/bulk", methods=["POST"])
def qr_bulk():
    try:
        st = qrtools.read_style(request.form)
        column = (request.form.get("column") or "").strip() or None
        links = qrtools.read_lines(request.form.get("links", ""))
        for f in request.files.getlist("files"):
            if f.filename:
                links += qrtools.read_links(f.filename, f.read(), column)
    except qrtools.QRError as e:
        return jsonify({"error": str(e)}), 400
    if request.form.get("dedupe", "1") == "1":
        links = list(dict.fromkeys(links))
    if not links:
        return jsonify({"error": "Ссылок не найдено"}), 400
    if len(links) > 20000:
        return jsonify({"error": "Не больше 20 000 кодов за раз"}), 400

    dirs = request.form.get("archive_name", "")
    job_id, job_dir, archive = new_job(dirs or "qr_codes")
    ok, failed = qrtools.bulk(links, job_dir / "out", st)
    shutil.rmtree(job_dir / "in", ignore_errors=True)
    return jsonify({"job": job_id, "archive": archive, "total": len(links), "done": ok,
                    "failed": failed, "files": ok + 1})


@app.route("/api/engines")
def engines_status():
    eng = conv.engines()
    eng.update({"poppler": all(poppler_ok().values()), "ytdlp": downloader.available(),
                "ytdlp_version": downloader.version(), "qrcode": qrtools.available()})
    return jsonify(eng)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print("Движки:", {**conv.engines(), "poppler": all(poppler_ok().values()),
                      "yt-dlp": downloader.version() or False, "qrcode": qrtools.available()})
    if host not in ("127.0.0.1", "localhost") and not APP_PASSWORD:
        print("Внимание: сервер открыт в сеть без пароля. Задай APP_PASSWORD.")
    print(f"Открой http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
