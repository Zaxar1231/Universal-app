"""
Инструменты для PDF на pypdf (чистый Python, ничего ставить не нужно):
склеить, разделить по страницам, извлечь страницы, повернуть, сжать,
поставить или снять пароль.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import lazy

pypdf = lazy.Module("pypdf")            # грузится при первой работе с PDF, а не при запуске приложения


def _errors():
    """(PdfReadError, DependencyError) из pypdf."""
    from pypdf.errors import DependencyError, PdfReadError  # type: ignore
    return PdfReadError, DependencyError

from tasks import Task

OPS = ("merge", "split", "extract", "rotate", "compress", "protect", "unlock")


class PdfError(Exception):
    pass


def available() -> bool:
    return bool(pypdf)


def parse_ranges(spec: str, total: int) -> list[int]:
    """'1-3, 5, 8-' -> [0, 1, 2, 4, 7, ...] (индексы с нуля). Пусто -> все страницы."""
    spec = (spec or "").replace(" ", "").replace("—", "-").replace("–", "-")
    if not spec:
        return list(range(total))
    pages: list[int] = []
    for part in spec.split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part)
        if not m:
            raise PdfError(f"Не понял страницы «{part}». Пиши как 1-3, 5, 8-")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a = int(m.group(1) or 1)
            b = int(m.group(2) or total)
        if a < 1 or b < a:
            raise PdfError(f"Неверный диапазон «{part}»")
        if a > total:
            raise PdfError(f"Страницы {a} нет: в документе их {total}")
        pages += range(a - 1, min(b, total))
    if not pages:
        raise PdfError("Не выбрано ни одной страницы")
    return pages


def _open(path: Path, password: str = "") -> "pypdf.PdfReader":
    try:
        r = pypdf.PdfReader(str(path))
    except (_errors()[0], ValueError, OSError) as e:
        raise PdfError(f"Не открылся как PDF: {str(e)[:120]}")
    if r.is_encrypted:
        if not password:
            raise PdfError("PDF защищён паролем. Введи пароль в режиме «Снять пароль»")
        try:
            ok = r.decrypt(password)
        except Exception:
            ok = 0
        if not ok:
            raise PdfError("Неверный пароль")
    return r


def _save(w: "pypdf.PdfWriter", out: Path) -> Path:
    w.compress_identical_objects()
    with out.open("wb") as f:
        w.write(f)
    return out


def merge(srcs: list[Path], out: Path, password: str = "") -> Path:
    w = pypdf.PdfWriter()
    for p in srcs:
        w.append(_open(p, password))
    return _save(w, out)


def split(src: Path, out_dir: Path, stem: str, password: str = "", ranges: str = "") -> list[Path]:
    r = _open(src, password)
    pages = parse_ranges(ranges, len(r.pages))
    width = len(str(len(r.pages)))
    outs = []
    for i in pages:
        w = pypdf.PdfWriter()
        w.add_page(r.pages[i])
        outs.append(_save(w, out_dir / f"{stem}_p{i + 1:0{width}d}.pdf"))
    return outs


def extract(src: Path, out: Path, ranges: str, password: str = "") -> Path:
    r = _open(src, password)
    w = pypdf.PdfWriter()
    for i in parse_ranges(ranges, len(r.pages)):
        w.add_page(r.pages[i])
    return _save(w, out)


def rotate(src: Path, out: Path, angle: int, ranges: str = "", password: str = "") -> Path:
    if angle not in (90, 180, 270):
        raise PdfError("Поворот бывает 90, 180 или 270 градусов")
    r = _open(src, password)
    chosen = set(parse_ranges(ranges, len(r.pages)))
    w = pypdf.PdfWriter()
    for i, page in enumerate(r.pages):
        w.add_page(page)
        if i in chosen:
            w.pages[-1].rotate(angle)
    return _save(w, out)


def compress(src: Path, out: Path, quality: int = 70, max_px: int = 1600, password: str = "") -> Path:
    """Пережимает картинки внутри PDF в JPEG и уменьшает их, сжимает потоки."""
    from PIL import Image

    r = _open(src, password)
    w = pypdf.PdfWriter(clone_from=r)
    for page in w.pages:
        page.compress_content_streams()
        for img in page.images:
            try:
                im = img.image
                if im is None:
                    continue
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                if max_px and max(im.size) > max_px:
                    im.thumbnail((max_px, max_px), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=quality, optimize=True)
                if buf.tell() < len(img.data):      # заменяем, только если стало меньше
                    img.replace(im, quality=quality)
            except Exception:
                continue   # экзотические картинки (маски, CMYK-JBIG2) оставляем как есть
    _save(w, out)
    if out.stat().st_size >= src.stat().st_size:
        out.write_bytes(src.read_bytes())          # сжать не вышло: отдаём оригинал
    return out


def protect(src: Path, out: Path, new_password: str, password: str = "") -> Path:
    if not new_password:
        raise PdfError("Придумай пароль")
    r = _open(src, password)
    w = pypdf.PdfWriter(clone_from=r)
    w.encrypt(user_password=new_password, owner_password=new_password, algorithm="AES-256")
    with out.open("wb") as f:
        w.write(f)
    return out


def unlock(src: Path, out: Path, password: str) -> Path:
    r = _open(src, password)
    w = pypdf.PdfWriter(clone_from=r)
    return _save(w, out)


def read_opts(form) -> dict:
    o = {
        "op": form.get("op", "merge"),
        "ranges": form.get("ranges", ""),
        "angle": form.get("angle", "90"),
        "quality": form.get("quality", "70"),
        "max_px": form.get("max_px", "1600"),
        "password": form.get("password", ""),
        "new_password": form.get("new_password", ""),
    }
    if o["op"] not in OPS:
        raise PdfError("Неизвестная операция")
    try:
        o["angle"] = int(o["angle"])
        o["quality"] = max(10, min(95, int(o["quality"])))
        o["max_px"] = max(0, min(10000, int(o["max_px"] or 0)))
    except ValueError:
        raise PdfError("Числовые настройки заданы неверно")
    if o["op"] == "protect" and not o["new_password"]:
        raise PdfError("Придумай пароль")
    if o["op"] == "unlock" and not o["password"]:
        raise PdfError("Введи текущий пароль")
    if o["op"] in ("extract", "split"):
        parse_ranges(o["ranges"], 10 ** 6)        # проверяем синтаксис заранее
    return o


SUFFIX = {"extract": "pages", "rotate": "rotated", "compress": "compressed",
          "protect": "protected", "unlock": "unlocked"}


def run_pdf_task(task: Task, items: list[tuple[Path, str]], o: dict, merge_name: str) -> None:
    out_dir = task.out_dir
    op = o["op"]
    if op == "merge":
        task.total = 1
        task.update(0.1, f"Склеиваю {len(items)} PDF…")
        try:
            merge([p for p, _ in items], out_dir / f"{merge_name}.pdf", o["password"])
            task.done = 1
        except PdfError as e:
            task.failed.append({"name": "склейка", "error": str(e)})
    else:
        n = task.total = len(items)
        for i, (src, stem) in enumerate(items):
            task.check_cancel()
            task.update(i / n, f"Файл {i + 1} из {n}: {src.name}")
            out = out_dir / f"{stem}_{SUFFIX.get(op, op)}.pdf"
            try:
                if op == "split":
                    split(src, out_dir, stem, o["password"], o["ranges"])
                elif op == "extract":
                    extract(src, out, o["ranges"], o["password"])
                elif op == "rotate":
                    rotate(src, out, o["angle"], o["ranges"], o["password"])
                elif op == "compress":
                    compress(src, out, o["quality"], o["max_px"], o["password"])
                elif op == "protect":
                    protect(src, out, o["new_password"], o["password"])
                elif op == "unlock":
                    unlock(src, out, o["password"])
                task.done += 1
            except PdfError as e:
                task.failed.append({"name": src.name, "error": str(e)})
            except _errors()[1]:
                task.failed.append({"name": src.name, "error": "Для шифрованных PDF нужен пакет cryptography: pip install cryptography"})
            except Exception as e:  # битый PDF не должен валить пачку
                task.failed.append({"name": src.name, "error": f"Не удалось обработать: {str(e)[:150]}"})
    if not task.done and task.failed:
        raise PdfError(task.failed[0]["error"])
