"""
Распознавание текста на телефоне без OCRmyPDF: Tesseract для Android (Tesseract4Android,
та же библиотека tesseract) через мост Java встроенного Python. Страницы PDF рисует android_pdf.

На выходе, как на компьютере: PDF с невидимым текстовым слоем (TessPdfRenderer) и, по желанию, .txt.
Языки (tessdata_fast, по 1–4 МБ) скачиваются при первом использовании.
"""
from __future__ import annotations

import shutil
import tempfile
import urllib.request
from pathlib import Path

from java import jclass  # type: ignore

import android_pdf
import engines
import net
from tasks import Task

TessBaseAPI = jclass("com.googlecode.tesseract.android.TessBaseAPI")
TessPdfRenderer = jclass("com.googlecode.tesseract.android.TessPdfRenderer")
ReadFile = jclass("com.googlecode.leptonica.android.ReadFile")
File = jclass("java.io.File")

TESSDATA = ("https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata",
            "https://cdn.jsdelivr.net/gh/tesseract-ocr/tessdata_fast@main/{lang}.traineddata")
from ocr import ANDROID_LANGS as LANGS  # noqa: E402
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")


class OcrError(Exception):
    pass


def data_dir() -> Path:
    d = engines.root() / "tesseract"            # внутри должна быть папка tessdata
    (d / "tessdata").mkdir(parents=True, exist_ok=True)
    return d


def have(lang: str) -> bool:
    f = data_dir() / "tessdata" / f"{lang}.traineddata"
    return f.is_file() and f.stat().st_size > 100_000


def ensure_lang(lang: str, task: Task | None = None) -> None:
    if have(lang):
        return
    dst = data_dir() / "tessdata" / f"{lang}.traineddata"
    errors = []
    for tpl in TESSDATA:
        try:
            if task:
                task.update(None, f"Скачиваю язык {lang} для распознавания (один раз)…")
            req = urllib.request.Request(tpl.format(lang=lang), headers={"User-Agent": "Universal"})
            tmp = dst.with_suffix(".part")
            with net.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            tmp.replace(dst)
            if have(lang):
                return
        except Exception as e:
            errors.append(str(e)[:80])
    raise OcrError(f"не скачался язык {lang} ({'; '.join(errors)}). Нужен интернет один раз")


def _pages(src: Path, tmp: Path) -> list[Path]:
    if src.suffix.lower() == ".pdf":
        n = android_pdf.page_count(src)
        return android_pdf.render_pages(src, list(range(n)), 300, lambda i: tmp / f"p{i:04}.png")
    if src.suffix.lower() in IMAGE_EXT:
        return [src]
    raise OcrError("нужен PDF или картинка")


def run_ocr_task(task: Task, items: list[tuple[Path, str]], o: dict) -> None:
    langs = [l for l in o["langs"] if l in LANGS] or ["rus", "eng"]
    task.total = len(items)
    for l in langs:
        ensure_lang(l, task)
    api = TessBaseAPI()
    if not api.init(str(data_dir()), "+".join(langs), TessBaseAPI.OEM_LSTM_ONLY):
        raise OcrError("Tesseract не запустился")
    try:
        for i, (src, stem) in enumerate(items):
            task.check_cancel()
            name = stem + src.suffix
            try:
                with tempfile.TemporaryDirectory(dir=str(task.job_dir)) as tmp:
                    task.update(i / len(items), f"Готовлю страницы: {name}")
                    pages = _pages(src, Path(tmp))
                    out_base = task.out_dir / f"{stem}_ocr"
                    renderer = TessPdfRenderer(api, str(out_base))       # допишет .pdf сам
                    api.beginDocument(renderer, stem)
                    texts = []
                    for k, page in enumerate(pages):
                        task.check_cancel()
                        task.update((i + k / len(pages)) / len(items), f"Распознаю {name}: страница {k + 1} из {len(pages)}")
                        pix = ReadFile.readFile(File(str(page)))
                        if pix is None:
                            raise OcrError(f"страница {k + 1} не читается")
                        api.setImage(pix)
                        texts.append(api.getUTF8Text() or "")
                        api.addPageToDocument(pix, str(page), renderer)
                        pix.recycle()
                    api.endDocument(renderer)
                    renderer.recycle()
                    if o.get("txt"):
                        (task.out_dir / f"{stem}_ocr.txt").write_text("\n\f".join(texts), encoding="utf-8")
                if not (task.out_dir / f"{stem}_ocr.pdf").exists():
                    raise OcrError("PDF не получился")
                task.done += 1
            except OcrError as e:
                task.failed.append({"name": name, "error": str(e)})
            except Exception as e:
                if task.cancel_event.is_set():
                    raise
                task.failed.append({"name": name, "error": f"{e.__class__.__name__}: {str(e)[:150]}"})
    finally:
        try:
            api.recycle()
        except Exception:
            pass
    task.result = {"message": f"Распознано файлов: {task.done}"}
    if not task.done and task.failed:
        raise OcrError(task.failed[0]["error"])
