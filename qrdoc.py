"""
QR-коды прямо в документе: ссылки заменяются картинками QR (или QR встаёт после ссылки).

Форматы:
    .docx  гиперссылки, поля HYPERLINK и адреса, набранные текстом
    .md    [текст](url), <url> и голые адреса
    .html  теги <a href="...">
    .odt .rtf .doc .epub  сначала переводятся в .docx (pandoc / LibreOffice)

Word-файл правится как текст XML, а не через DOM: так сохраняются все
пространства имён и атрибут mc:Ignorable, иначе Word ругается на «нечитаемое
содержимое». Колонтитулы и надписи (текстовые поля) не трогаются.
"""
from __future__ import annotations

import base64
import html
import re
import shutil
import zipfile
from pathlib import Path

import converters
import qrtools

URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]{}]+[^\s<>\"'()\[\]{}.,;:!?»…]", re.I)
NATIVE = {".docx", ".md", ".markdown", ".html", ".htm"}
VIA_DOCX = {".odt", ".rtf", ".doc", ".epub"}
INPUTS = sorted(NATIVE | VIA_DOCX)


class DocError(Exception):
    pass


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".md", ".markdown"):
        return "md"
    if ext in (".html", ".htm"):
        return "html"
    if ext == ".docx":
        return "docx"
    if ext in VIA_DOCX:
        return "convert"
    raise DocError(f"Формат {ext or 'без расширения'} не поддерживается. Подойдут: " + ", ".join(INPUTS))


def _as_docx(src: Path, work: Path) -> Path:
    """ODT, RTF, DOC, EPUB -> DOCX тем же конвертером, что и в режиме «Документы»."""
    work.mkdir(parents=True, exist_ok=True)
    try:
        return converters.convert_document(src, work, src.stem, "docx")
    except converters.ConvertError as e:
        raise DocError(f"Не удалось открыть документ: {e}")


# ----------------------------------------------------------------------------
# Общая модель: список ссылок с текстом и числом вхождений
# ----------------------------------------------------------------------------

class Links:
    def __init__(self):
        self.items: dict[str, dict] = {}

    def add(self, url: str, text: str = "") -> None:
        it = self.items.setdefault(url, {"url": url, "text": "", "count": 0})
        it["count"] += 1
        text = " ".join(text.split())
        if text and text != url and not it["text"]:
            it["text"] = text[:120]

    def as_list(self) -> list[dict]:
        return list(self.items.values())


def _clean_url(u: str) -> str:
    return u.strip().rstrip(".,;:!?)»…")


# ----------------------------------------------------------------------------
# DOCX
# ----------------------------------------------------------------------------

W_HYPERLINK = re.compile(r"<w:hyperlink\b[^>]*>.*?</w:hyperlink>", re.S)
W_RUN = re.compile(r"<w:r\b[^>]*>.*?</w:r>|<w:r\b[^>]*/>", re.S)
W_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
EMU_PER_CM = 360000


def _run_text(xml: str) -> str:
    return html.unescape("".join(W_TEXT.findall(xml)))


class _Docx:
    def __init__(self, path: Path):
        try:
            self.z = zipfile.ZipFile(path)
            self.doc = self.z.read("word/document.xml").decode("utf-8")
            self.rels = self.z.read("word/_rels/document.xml.rels").decode("utf-8")
        except (zipfile.BadZipFile, KeyError):
            raise DocError("Файл .docx повреждён или это не Word-документ")
        self.targets = {}
        for m in re.finditer(r"<Relationship\b[^>]*/>", self.rels):
            tag = m.group(0)
            rid = re.search(r'\bId="([^"]+)"', tag)
            tgt = re.search(r'\bTarget="([^"]+)"', tag)
            if rid and tgt and 'TargetMode="External"' in tag and "/hyperlink" in tag:
                self.targets[rid.group(1)] = html.unescape(tgt.group(1))

    def hyperlink_url(self, xml: str) -> str | None:
        m = re.search(r'\br:id="([^"]+)"', xml[:xml.index(">") + 1])
        url = self.targets.get(m.group(1)) if m else None
        return url if url and url.lower().startswith(("http://", "https://")) else None

    # обход документа: колбэки получают куски XML и решают, чем их заменить
    def walk(self, on_link, on_text_run):
        """on_link(url, text, xml) -> новый xml; on_text_run(run_xml) -> новый xml."""
        out, pos = [], 0
        for m in W_HYPERLINK.finditer(self.doc):
            out.append(self._walk_runs(self.doc[pos:m.start()], on_link, on_text_run))
            xml = m.group(0)
            url = self.hyperlink_url(xml)
            out.append(on_link(url, _run_text(xml), xml) if url else xml)
            pos = m.end()
        out.append(self._walk_runs(self.doc[pos:], on_link, on_text_run))
        return "".join(out)

    @staticmethod
    def _walk_runs(chunk: str, on_link, on_text_run) -> str:
        out, pos = [], 0
        field = None    # {"start": индекс в out, "instr": str, "parts": [...]}
        for m in W_RUN.finditer(chunk):
            if "<w:txbxContent" in m.group(0):      # надписи с вложенными абзацами не трогаем
                continue
            between = chunk[pos:m.start()]
            run = m.group(0)
            pos = m.end()
            if field is not None:
                field["parts"].append(between + run)
                field["instr"] += html.unescape("".join(re.findall(r"<w:instrText[^>]*>(.*?)</w:instrText>", run, re.S)))
                if re.search(r'w:fldCharType="end"', run):
                    xml = "".join(field["parts"])
                    hm = re.search(r'HYPERLINK\s+"([^"]+)"', field["instr"])
                    url = hm.group(1) if hm and hm.group(1).lower().startswith(("http://", "https://")) else None
                    text = _run_text(xml.split('w:fldCharType="separate"', 1)[-1]) if url else ""
                    out.append(on_link(url, text, xml) if url else xml)
                    field = None
                continue
            out.append(between)
            if re.search(r'w:fldCharType="begin"', run):
                field = {"parts": [run], "instr": html.unescape("".join(
                    re.findall(r"<w:instrText[^>]*>(.*?)</w:instrText>", run, re.S)))}
                continue
            out.append(on_text_run(run))
        if field is not None:                        # незакрытое поле: оставляем как было
            out.append("".join(field["parts"]))
        out.append(chunk[pos:])
        return "".join(out)


def _docx_text_urls(run: str) -> list[str]:
    if "<w:drawing" in run or "<w:instrText" in run:
        return []
    return [_clean_url(u) for u in URL_RE.findall(_run_text(run))]


def scan_docx(path: Path) -> list[dict]:
    d = _Docx(path)
    links = Links()

    def on_link(url, text, xml):
        links.add(url, text)
        return xml

    def on_run(run):
        for u in _docx_text_urls(run):
            links.add(u)
        return run

    d.walk(on_link, on_run)
    return links.as_list()


def _drawing(rid: str, pid: int, url: str, size_emu: int) -> str:
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    descr = html.escape(url, quote=True)
    return (
        f'<w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        f'distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{size_emu}" cy="{size_emu}"/>'
        f'<wp:docPr id="{pid}" name="QR {pid}" descr="{descr}"/>'
        f'<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="{a}" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        f'<a:graphic xmlns:a="{a}"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="{pid}" name="qr_{pid}.png" descr="{descr}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{rid}"/>'
        f'<a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{size_emu}" cy="{size_emu}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
        f'</wp:inline></w:drawing></w:r>'
    )


def _split_text_run(run: str, url_xml) -> str:
    """Run с одним <w:t>, где встречаются адреса: режем на куски «текст / QR / текст»."""
    m = W_TEXT.search(run)
    if not m or len(W_TEXT.findall(run)) != 1:
        return run
    head, tail = run[:m.start()], run[m.end():]
    open_tag = run[m.start():m.start(1)]
    if 'xml:space="preserve"' not in open_tag:
        open_tag = open_tag[:-1] + ' xml:space="preserve">'
    text = html.unescape(m.group(1))
    pieces, pos = [], 0
    for um in URL_RE.finditer(text):
        url = _clean_url(um.group(0))
        end = um.start() + len(url)
        repl = url_xml(url)
        if repl is None:
            continue
        pieces.append(("text", text[pos:um.start()]))
        pieces.append(("qr", url, repl))
        pos = end
    if not pieces:
        return run
    pieces.append(("text", text[pos:]))
    out = []
    for p in pieces:
        if p[0] == "text":
            if p[1]:
                out.append(f"{head}{open_tag}{html.escape(p[1], quote=False)}</w:t>{tail}")
        else:
            out.append(p[2])
    return "".join(out)


def apply_docx(src: Path, dst: Path, selected: set[str], mode: str, size_cm: float, st: dict) -> int:
    d = _Docx(src)
    size = int(size_cm * EMU_PER_CM)
    media: dict[str, tuple[str, str]] = {}      # url -> (rId, имя файла)
    counter = {"n": 0}

    def rid_for(url: str) -> str:
        if url not in media:
            i = len(media) + 1
            media[url] = (f"rIdUniversalQr{i}", f"universal_qr_{i}.png")
        return media[url][0]

    def drawing(url: str) -> str:
        counter["n"] += 1
        return _drawing(rid_for(url), 90000 + counter["n"], url, size)

    def on_link(url, text, xml):
        if url not in selected:
            return xml
        return drawing(url) if mode == "replace" else xml + drawing(url)

    def on_run(run):
        if not _docx_text_urls(run):
            return run
        if mode == "replace":
            return _split_text_run(run, lambda u: drawing(u) if u in selected else None)
        # «после ссылки»: оставляем адрес текстом и добавляем QR сразу за ним
        return _split_text_run(run, lambda u: (
            _split_plain(run, u) + drawing(u)) if u in selected else None)

    def _split_plain(run, u):
        m = W_TEXT.search(run)
        open_tag = run[m.start():m.start(1)]
        if 'xml:space="preserve"' not in open_tag:
            open_tag = open_tag[:-1] + ' xml:space="preserve">'
        return f"{run[:m.start()]}{open_tag}{html.escape(u, quote=False)}</w:t>{run[m.end():]}"

    new_doc = d.walk(on_link, on_run)
    if not media:
        raise DocError("Ни одна из выбранных ссылок не нашлась в документе")

    rels = d.rels.replace("</Relationships>", "".join(
        f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="media/{name}"/>' for rid, name in media.values()) + "</Relationships>")
    png_style = dict(st, format="png", transparent=st.get("transparent", False))
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for item in d.z.infolist():
            data = d.z.read(item.filename)
            if item.filename == "word/document.xml":
                data = new_doc.encode("utf-8")
            elif item.filename == "word/_rels/document.xml.rels":
                data = rels.encode("utf-8")
            elif item.filename == "[Content_Types].xml":
                ct = data.decode("utf-8")
                if not re.search(r'Extension="png"', ct, re.I):
                    ct = ct.replace("<Default ", '<Default Extension="png" ContentType="image/png"/><Default ', 1)
                data = ct.encode("utf-8")
            out.writestr(item, data)
        for url, (_, name) in media.items():
            out.writestr(f"word/media/{name}", qrtools.render(url, png_style))
    d.z.close()
    return counter["n"]


# ----------------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------------

MD_TOKEN = re.compile(
    r"(?P<img>!\[[^\]]*\]\([^)]*\))"                                  # картинки не трогаем
    r"|(?P<link>\[(?P<ltext>[^\]]*)\]\((?P<lurl>https?://[^)\s]+)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<auto><(?P<aurl>https?://[^>\s]+)>)"
    r"|(?P<bare>" + URL_RE.pattern + ")", re.I)


def _md_blocks(text: str):
    """Делит текст на (обычный, кусок) и (код, кусок): в блоках кода ссылки не трогаем."""
    parts = re.split(r"(^```.*?^```[^\n]*$|^~~~.*?^~~~[^\n]*$)", text, flags=re.S | re.M)
    for i, p in enumerate(parts):
        yield (i % 2 == 1), p


def _md_inline(chunk: str, fn) -> str:
    # инлайн-код `...` тоже пропускаем
    out = []
    for i, p in enumerate(re.split(r"(`[^`\n]+`)", chunk)):
        out.append(p if i % 2 else MD_TOKEN.sub(fn, p))
    return "".join(out)


def scan_md(text: str) -> list[dict]:
    links = Links()

    def fn(m):
        if m.group("link"):
            links.add(m.group("lurl"), m.group("ltext"))
        elif m.group("auto"):
            links.add(m.group("aurl"))
        elif m.group("bare"):
            links.add(_clean_url(m.group("bare")))
        return m.group(0)

    for code, chunk in _md_blocks(text):
        if not code:
            _md_inline(chunk, fn)
    return links.as_list()


def apply_md(text: str, stem: str, out_dir: Path, selected: set[str], mode: str,
             size_cm: float, st: dict) -> tuple[str, int]:
    files: dict[str, str] = {}
    count = {"n": 0}
    px = round(size_cm / 2.54 * 96)

    def image(url: str) -> str:
        if url not in files:
            name = f"{stem}_qr_{len(files) + 1:03d}.png"
            (out_dir / name).write_bytes(qrtools.render(url, dict(st, format="png")))
            files[url] = name
        count["n"] += 1
        return f'<img src="{files[url]}" alt="QR-код" width="{px}" height="{px}">'

    def fn(m):
        if m.group("img"):
            return m.group(0)
        if m.group("link"):
            url, whole, tail = m.group("lurl"), m.group(0), ""
        elif m.group("auto"):
            url, whole, tail = m.group("aurl"), m.group(0), ""
        else:
            raw = m.group("bare")
            url = _clean_url(raw)
            whole, tail = url, raw[len(url):]
        if url not in selected:
            return m.group(0)
        return (image(url) if mode == "replace" else f"{whole} {image(url)}") + tail

    out = "".join(chunk if code else _md_inline(chunk, fn) for code, chunk in _md_blocks(text))
    return out, count["n"]


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

A_TAG = re.compile(r"<a\b[^>]*?\bhref\s*=\s*([\"'])(https?://[^\"']+)\1[^>]*>(.*?)</a\s*>", re.S | re.I)


def scan_html(text: str) -> list[dict]:
    links = Links()
    for m in A_TAG.finditer(text):
        links.add(html.unescape(m.group(2)), re.sub(r"<[^>]+>", "", html.unescape(m.group(3))))
    return links.as_list()


def apply_html(text: str, selected: set[str], mode: str, size_cm: float, st: dict) -> tuple[str, int]:
    cache: dict[str, str] = {}
    count = {"n": 0}
    px = round(size_cm / 2.54 * 96)

    def img(url):
        if url not in cache:
            cache[url] = base64.b64encode(qrtools.render(url, dict(st, format="png"))).decode()
        count["n"] += 1
        return (f'<img src="data:image/png;base64,{cache[url]}" alt="QR-код" '
                f'width="{px}" height="{px}" style="vertical-align:middle">')

    def fn(m):
        url = html.unescape(m.group(2))
        if url not in selected:
            return m.group(0)
        return img(url) if mode == "replace" else m.group(0) + " " + img(url)

    return A_TAG.sub(fn, text), count["n"]


# ----------------------------------------------------------------------------
# Точки входа
# ----------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def scan(path: Path, work: Path) -> dict:
    kind = kind_of(path)
    if kind == "convert":
        path, kind = _as_docx(path, work), "docx"
    if kind == "docx":
        links = scan_docx(path)
    elif kind == "md":
        links = scan_md(_read_text(path))
    else:
        links = scan_html(_read_text(path))
    return {"kind": kind, "links": links}


def apply(path: Path, out_dir: Path, stem: str, selected: set[str], mode: str,
          size_cm: float, st: dict, work: Path) -> tuple[Path, int]:
    if mode not in ("replace", "after"):
        raise DocError("Неизвестный режим")
    if not selected:
        raise DocError("Не выбрано ни одной ссылки")
    size_cm = max(1.0, min(15.0, size_cm))
    kind = kind_of(path)
    if kind == "convert":
        path, kind = _as_docx(path, work), "docx"
    if kind == "docx":
        dst = out_dir / f"{stem}_qr.docx"
        n = apply_docx(path, dst, selected, mode, size_cm, st)
    elif kind == "md":
        body, n = apply_md(_read_text(path), stem, out_dir, selected, mode, size_cm, st)
        dst = out_dir / f"{stem}_qr.md"
        dst.write_text(body, encoding="utf-8")
    else:
        body, n = apply_html(_read_text(path), selected, mode, size_cm, st)
        dst = out_dir / f"{stem}_qr{path.suffix.lower()}"
        dst.write_text(body, encoding="utf-8")
    if not n:
        dst.unlink(missing_ok=True)
        for p in out_dir.glob(f"{stem}_qr_*.png"):
            p.unlink()
        raise DocError("Ни одна из выбранных ссылок не нашлась в документе")
    shutil.rmtree(work, ignore_errors=True)
    return dst, n
