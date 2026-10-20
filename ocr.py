"""
Распознавание текста в сканах (OCRmyPDF + Tesseract).

Скан PDF или фото документа превращается в PDF, в котором можно искать
и копировать текст: картинка страницы остаётся, под ней ложится невидимый
текстовый слой. По желанию ещё выравнивает перекошенные страницы,
поворачивает перевёрнутые, сохраняет в архивном PDF/A и отдаёт текст .txt.

OCRmyPDF работает отдельным процессом (так его можно прервать «Отменой»):
из exe — тем же exe с ключом --ocrmypdf, иначе python -m ocrmypdf.
Ghostscript не нужен: с OCRmyPDF 17.13 PDF/A собирается встроенным способом.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from tasks import Task

MODES = ("skip", "redo", "force")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif", ".jp2")
LANG_NAMES = {"rus": "русский", "eng": "английский", "ukr": "украинский", "bel": "белорусский", "kaz": "казахский",
              "deu": "немецкий", "fra": "французский", "spa": "испанский", "ita": "итальянский", "pol": "польский",
              "chi_sim": "китайский", "jpn": "японский"}
# коды выхода OCRmyPDF (ocrmypdf.exceptions.ExitCode)
EXIT_TEXT = {
    2: "OCRmyPDF не понял настройки",
    3: "не хватает программы для распознавания (Tesseract)",
    4: "получился неправильный PDF",
    5: "файл не открылся или результат не записался",
    6: "в файле уже есть текст. Выбери «Переделать распознавание» или «Распознать всё заново»",
    7: "Tesseract завершился с ошибкой",
    8: "PDF защищён паролем: сначала сними пароль в «Правке PDF»",
    9: "ошибка настроек OCRmyPDF",
    10: "PDF/A не получился",
    15: "внутренняя ошибка OCRmyPDF",
    130: "отменено",
}


class OcrError(Exception):
    pass


def version() -> str:
    try:
        return metadata.version("ocrmypdf")
    except metadata.PackageNotFoundError:
        return ""


def _ver_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def tesseract() -> str | None:
    return shutil.which("tesseract")


def languages() -> list[str]:
    """Языки, для которых у Tesseract есть данные (rus, eng…)."""
    exe = tesseract()
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.strip() for l in (r.stdout + r.stderr).splitlines()[1:]
            if re.fullmatch(r"[A-Za-z_]+", l.strip()) and l.strip() != "osd"]


def status() -> dict:
    v = version()
    return {"ocrmypdf": bool(v), "version": v, "tesseract": bool(tesseract()), "ghostscript": bool(_ghostscript()),
            "languages": languages(), "names": LANG_NAMES}


def available() -> bool:
    return bool(version()) and bool(tesseract())


def _ghostscript() -> str | None:
    return shutil.which("gswin64c") or shutil.which("gs")


def command(args: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ocrmypdf", *args]
    return [sys.executable, "-m", "ocrmypdf", *args]


def read_opts(form) -> dict:
    o = {
        "langs": [l for l in re.split(r"[+,\s]+", form.get("langs", "rus+eng")) if l],
        "mode": form.get("mode", "skip"),
        "deskew": form.get("deskew", "1") == "1",
        "rotate": form.get("rotate", "1") == "1",
        "pdfa": form.get("pdfa", "0") == "1",
        "txt": form.get("txt", "0") == "1",
        "optimize": form.get("optimize", "1"),
    }
    if not o["langs"] or not all(re.fullmatch(r"[A-Za-z_]{3,10}", l) for l in o["langs"]):
        raise OcrError("Неверно заданы языки")
    if o["mode"] not in MODES:
        o["mode"] = "skip"
    if o["optimize"] not in ("0", "1"):
        o["optimize"] = "1"
    return o


def build_args(src: Path, out: Path, o: dict, sidecar: Path | None, pdfa: bool) -> list[str]:
    v = _ver_tuple(version())
    a = ["-l", "+".join(o["langs"]), "--use-threads", "--optimize", o["optimize"]]
    a.append({"skip": "--skip-text", "redo": "--redo-ocr", "force": "--force-ocr"}[o["mode"]])
    # выравнивание и поворот меняют картинку страницы, а --redo-ocr такое запрещает
    if o["deskew"] and o["mode"] != "redo":
        a.append("--deskew")
    if o["rotate"] and o["mode"] != "redo":
        a.append("--rotate-pages")
    if pdfa:
        a += ["--output-type", "pdfa"]
        if not _ghostscript() and v >= (17, 13):
            a += ["--pdfa-backend", "internal"]
    else:
        a += ["--output-type", "pdf"]
    if src.suffix.lower() in IMAGE_EXT:
        a += ["--image-dpi", "300"]          # используется, только если у фото нет своего DPI
    if sidecar:
        a += ["--sidecar", str(sidecar)]
    return a + [str(src), str(out)]


def _explain(code: int, tail: list[str]) -> str:
    text = EXIT_TEXT.get(code, f"OCRmyPDF завершился с кодом {code}")
    detail = next((l for l in reversed(tail) if re.search(r"error|ошибк|not|cannot|could", l, re.I)), "")
    if code == 3 and re.search(r"language|traineddata", detail, re.I):
        text = "нет данных Tesseract для выбранного языка"
    return f"{text}: {detail[:200]}" if detail and code not in (6, 8, 130) else text


def _run(task: Task, args: list[str]) -> tuple[int, list[str]]:
    proc = subprocess.Popen(command(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", start_new_session=sys.platform != "win32")
    task.proc = proc
    tail: list[str] = []
    for line in proc.stdout:
        line = line.strip()
        if line:
            tail = (tail + [line])[-12:]
    proc.wait()
    task.proc = None
    task.check_cancel()
    return proc.returncode, tail


def run_ocr_task(task: Task, items: list[tuple[Path, str]], o: dict) -> None:
    if not version():
        raise OcrError("Не установлен OCRmyPDF: pip install ocrmypdf")
    if not tesseract():
        raise OcrError("Не найден Tesseract: поставь его кнопкой в баннере или пакетом tesseract-ocr")
    have = set(languages())
    missing = [l for l in o["langs"] if have and l not in have]
    if missing:
        raise OcrError("У Tesseract нет языков: " + ", ".join(LANG_NAMES.get(l, l) for l in missing))
    n = task.total = len(items)
    for i, (src, stem) in enumerate(items):
        task.check_cancel()
        task.update(i / n, f"Распознаю {i + 1} из {n}: {src.name}")
        out = task.out_dir / f"{stem}_ocr.pdf"
        sidecar = task.out_dir / f"{stem}.txt" if o["txt"] else None
        code, tail = _run(task, build_args(src, out, o, sidecar, o["pdfa"]))
        if code == 10 and o["pdfa"]:
            # PDF/A не сошёлся: лучше обычный PDF с текстом, чем ничего
            task.update(None, f"PDF/A не получился, сохраняю обычный PDF: {src.name}")
            code, tail = _run(task, build_args(src, out, o, sidecar, False))
        if code == 0 and out.exists():
            task.done += 1
        else:
            out.unlink(missing_ok=True)
            if sidecar:
                sidecar.unlink(missing_ok=True)
            task.failed.append({"name": src.name, "error": _explain(code, tail)})
    if not task.done and task.failed:
        raise OcrError(task.failed[0]["error"])
