"""
Редактор документов: открыть DOCX/DOC/ODT/RTF/HTML/MD/TXT в визуальном редакторе
(Jodit в интерфейсе) и сохранить в DOCX/ODT/RTF/PDF/HTML/MD/TXT.

Открытие: LibreOffice (лучше держит оформление и сразу встраивает картинки),
без него pandoc. Сохранение: HTML из редактора → LibreOffice (DOCX/ODT/RTF/PDF)
или pandoc. Картинки остаются data: URI: так оба вшивают их в документ
(картинку-файл рядом LibreOffice вставил бы ссылкой на временную папку).
"""
from __future__ import annotations

import base64
import html as htmllib
import mimetypes
import re
import shutil
import tempfile
from pathlib import Path

import converters as conv

OPEN_EXT = ("docx", "doc", "odt", "rtf", "html", "htm", "md", "markdown", "txt", "epub")
SAVE_FMT = {
    # формат: (фильтр LibreOffice, формат pandoc)
    "docx": ("docx:MS Word 2007 XML", "docx"),
    "odt": ("odt:writer8", "odt"),
    "rtf": ("rtf:Rich Text Format", "rtf"),
    "pdf": ("pdf:writer_pdf_Export", None),
    "html": (None, None),
    "md": (None, "gfm"),
    "txt": (None, "plain"),
}
PAGE_CSS = """body { font-family: "Times New Roman", "Liberation Serif", serif; font-size: 12pt; line-height: 1.4; }
table { border-collapse: collapse; } td, th { border: 1px solid #777; padding: 4px 6px; vertical-align: top; }
img { max-width: 100%; }"""


class DocError(Exception):
    pass


def caps() -> dict:
    return {"libreoffice": bool(conv._which("soffice", "libreoffice")), "pandoc": bool(conv._which("pandoc")),
            "open": list(OPEN_EXT), "save": list(SAVE_FMT)}


def _soffice() -> str | None:
    return conv._which("soffice", "libreoffice")


def _lo(src: Path, out_dir: Path, target: str, infilter: str = "") -> Path:
    exe = _soffice()
    if not exe:
        raise DocError("нужен LibreOffice")
    profile = out_dir / ".lo_profile"
    cmd = [exe, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless"]
    if infilter:
        cmd.append(f"--infilter={infilter}")
    cmd += ["--convert-to", target, "--outdir", str(out_dir), str(src)]
    res = conv._run(cmd, 300)
    shutil.rmtree(profile, ignore_errors=True)
    ext = target.split(":", 1)[0]
    out = out_dir / f"{src.stem}.{ext}"
    if res.returncode != 0 or not out.exists():
        raise DocError("LibreOffice не справился: " + conv._last_error(res, "без подробностей"))
    return out


def _pandoc(args: list[str]) -> str:
    exe = conv._which("pandoc")
    if not exe:
        raise DocError("нужен pandoc или LibreOffice")
    res = conv._run([exe, *args], 300)
    if res.returncode != 0:
        raise DocError("pandoc не справился: " + conv._last_error(res, "без подробностей"))
    return res.stdout


def _body(html: str) -> str:
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    body = m.group(1) if m else html
    # скрипты и обработчики событий из чужого HTML в редактор не пускаем
    body = re.sub(r"<script\b.*?</script\s*>", "", body, flags=re.S | re.I)
    body = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", body, flags=re.I)
    return body.strip()


def _inline_images(html: str, base: Path) -> str:
    """Картинки, которые pandoc выложил файлами, вшиваем в HTML как data: URI."""
    def repl(m):
        src = m.group(2)
        if src.startswith(("data:", "http:", "https:")):
            return m.group(0)
        p = (base / src).resolve()
        if not str(p).startswith(str(base.resolve())) or not p.is_file():
            return m.group(0)
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        return f'{m.group(1)}data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}{m.group(3)}'
    return re.sub(r'(<img\b[^>]*?\ssrc=")([^"]+)(")', repl, html, flags=re.I)


def open_doc(src: Path) -> dict:
    ext = src.suffix.lower().lstrip(".")
    if ext not in OPEN_EXT:
        raise DocError(f"редактор не открывает .{ext}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if ext == "txt":
            text = src.read_bytes().decode("utf-8", "replace")
            return {"html": "".join(f"<p>{htmllib.escape(l) or '<br>'}</p>" for l in text.splitlines()), "engine": "текст"}
        if ext in ("html", "htm"):
            return {"html": _body(src.read_bytes().decode("utf-8", "replace")), "engine": "HTML"}
        if ext in ("md", "markdown"):
            media = tmp / "media"
            out = _pandoc(["-f", "gfm", "-t", "html5", f"--extract-media={media}", str(src)])
            return {"html": _inline_images(_body(out), tmp), "engine": "pandoc"}
        if _soffice():
            try:
                # EmbedImages: картинки сразу внутри HTML
                out = _lo(src, tmp, "html:HTML (StarWriter):EmbedImages")
                return {"html": _inline_images(_body(out.read_text(encoding="utf-8", errors="replace")), tmp),
                        "engine": "LibreOffice"}
            except DocError:
                if ext == "doc":
                    raise
        if ext == "doc":
            raise DocError("старый .doc открывается только через LibreOffice, а он не установлен")
        reader = {"docx": "docx", "odt": "odt", "rtf": "rtf", "epub": "epub"}[ext]
        out = _pandoc(["-f", reader, "-t", "html5", f"--extract-media={tmp / 'media'}", str(src)])
        return {"html": _inline_images(_body(out), tmp), "engine": "pandoc",
                "warning": "Открыто через pandoc: шрифты и цвета могут потеряться. С LibreOffice оформление сохраняется лучше."}


def save_doc(body: str, fmt: str, out_dir: Path, stem: str) -> Path:
    if fmt not in SAVE_FMT:
        raise DocError("неизвестный формат")
    lo_filter, pandoc_fmt = SAVE_FMT[fmt]
    title = htmllib.escape(stem)
    page = (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{title}</title>'
            f"<style>{PAGE_CSS}</style></head><body>{_body(body)}</body></html>")
    out = out_dir / f"{stem}.{fmt}"
    if fmt == "html":
        out.write_text(page, encoding="utf-8")
        return out
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        tmp = Path(tmp)
        src = tmp / f"{stem}.html"
        src.write_text(page, encoding="utf-8")
        if lo_filter and _soffice():
            made = _lo(src, tmp, lo_filter, infilter="HTML (StarWriter)")   # как документ Writer, а не веб-страница
            shutil.move(str(made), out)
            return out
        if not pandoc_fmt:
            raise DocError("PDF сохраняется через LibreOffice, а он не установлен. Поставь LibreOffice или сохрани в DOCX")
        args = ["-f", "html", "-t", pandoc_fmt, "-o", str(out), f"--resource-path={tmp}", str(src)]
        if fmt in ("md", "txt"):
            args.append("--wrap=none")
        else:
            args.append("--standalone")
        _pandoc(args)
        if not out.exists():
            raise DocError("файл не получился")
        return out
