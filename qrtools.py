"""
QR-коды: один (текст, ссылка, Wi-Fi, контакт) и массово из списка ссылок.

Массовая генерация портирована из tools/bulk_qr.py: ссылки читаются из .txt,
.csv (с выбором колонки) и .docx (гиперссылки, поля HYPERLINK и голые URL).
Рисуем сами по матрице qrcode, чтобы цвета и прозрачный фон работали
одинаково в PNG, JPG и SVG.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

try:
    import qrcode  # type: ignore
    from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q
    EC = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
except ImportError:
    qrcode = None
    EC = {"L": 1, "M": 0, "Q": 3, "H": 2}

from PIL import Image, ImageDraw

FORMATS = ["png", "svg", "jpg"]
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class QRError(Exception):
    pass


def available() -> bool:
    return qrcode is not None


# ----------------------------------------------------------------------------
# Чтение списков ссылок (из bulk_qr.py)
# ----------------------------------------------------------------------------

def read_docx_links(data: bytes) -> list[str]:
    """Гиперссылки (кнопки/текст-ссылки) и голые URL из .docx, в порядке документа."""
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            rels_xml = ET.fromstring(z.read("word/_rels/document.xml.rels"))
            rels = {r.get("Id"): r.get("Target") for r in rels_xml
                    if r.get("TargetMode") == "External"}
            doc = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise QRError("Файл .docx повреждён или это не Word-документ")

    found: list[str] = []

    def walk(el):
        if el.tag == W + "hyperlink":  # внутрь не лезем: текст ссылки часто обрезан
            if el.get(R + "id") in rels:
                found.append(rels[el.get(R + "id")])
            return
        if el.tag == W + "instrText" and el.text:  # поля HYPERLINK "..."
            found.extend(re.findall(r'HYPERLINK\s+"([^"]+)"', el.text))
        elif el.tag == W + "t" and el.text:  # URL, набранный обычным текстом
            found.extend(re.findall(r"https?://[^\s<>\"')]+", el.text))
        for ch in el:
            walk(ch)

    walk(doc)
    return [u for u in dict.fromkeys(u.strip() for u in found) if u.startswith(("http://", "https://"))]


def read_csv_links(text: str, column: str | None) -> list[str]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    f = io.StringIO(text)
    if column:
        rows = list(csv.DictReader(f, dialect=dialect))
        if rows and column not in rows[0]:
            raise QRError(f"В CSV нет колонки «{column}». Есть: {', '.join(k for k in rows[0] if k)}")
        return [r[column].strip() for r in rows if (r.get(column) or "").strip()]
    return [row[0].strip() for row in csv.reader(f, dialect=dialect) if row and row[0].strip()]


def read_lines(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def read_links(name: str, data: bytes, column: str | None = None) -> list[str]:
    ext = Path(name).suffix.lower()
    if ext == ".docx":
        return read_docx_links(data)
    text = data.decode("utf-8-sig", errors="replace")
    if ext == ".csv":
        return read_csv_links(text, column)
    return read_lines(text)


def make_name(idx: int, data: str, width: int) -> str:
    host = urlparse(data if "://" in data else "//" + data).netloc if re.match(r"^\S+$", data) else ""
    host = re.sub(r"[^A-Za-z0-9._-]+", "_", host or "qr")[:40]
    return f"{idx:0{width}d}_{host}"


# ----------------------------------------------------------------------------
# Шаблоны содержимого
# ----------------------------------------------------------------------------

def _esc_wifi(s: str) -> str:
    return re.sub(r'([\\;,:"])', r"\\\1", s)


def wifi_payload(ssid: str, password: str, security: str = "WPA", hidden: bool = False) -> str:
    if not ssid:
        raise QRError("Укажи имя сети Wi-Fi")
    security = security if security in ("WPA", "WEP", "nopass") else "WPA"
    p = f"WIFI:T:{security};S:{_esc_wifi(ssid)};"
    if security != "nopass":
        p += f"P:{_esc_wifi(password)};"
    if hidden:
        p += "H:true;"
    return p + ";"


def vcard_payload(name: str, phone: str = "", email: str = "", org: str = "", url: str = "") -> str:
    if not (name or phone or email):
        raise QRError("Заполни хотя бы имя, телефон или почту")
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{name}", f"N:{name};;;;"]
    if org:
        lines.append(f"ORG:{org}")
    if phone:
        lines.append(f"TEL;TYPE=CELL:{phone}")
    if email:
        lines.append(f"EMAIL:{email}")
    if url:
        lines.append(f"URL:{url}")
    lines.append("END:VCARD")
    return "\n".join(lines)


def payload_from_form(form) -> str:
    kind = form.get("kind", "text")
    if kind == "wifi":
        return wifi_payload(form.get("ssid", ""), form.get("password", ""),
                            form.get("security", "WPA"), form.get("hidden") == "1")
    if kind == "vcard":
        return vcard_payload(form.get("name", "").strip(), form.get("phone", "").strip(),
                             form.get("email", "").strip(), form.get("org", "").strip(),
                             form.get("url", "").strip())
    text = form.get("text", "")
    if not text.strip():
        raise QRError("Введи текст или ссылку")
    return text


# ----------------------------------------------------------------------------
# Отрисовка
# ----------------------------------------------------------------------------

def read_style(form) -> dict:
    def num(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(form.get(key, default))))
        except (TypeError, ValueError):
            return default

    st = {
        "format": (form.get("format") or "png").lower(),
        "box": num("box_size", 10, 1, 60),
        "border": num("border", 4, 0, 20),
        "ec": (form.get("ec") or "M").upper(),
        "fg": form.get("fg") or "#000000",
        "bg": form.get("bg") or "#ffffff",
        "transparent": form.get("transparent") == "1",
    }
    if st["format"] not in FORMATS:
        st["format"] = "png"
    if st["ec"] not in EC:
        st["ec"] = "M"
    for k in ("fg", "bg"):
        if not HEX.match(st[k]):
            raise QRError("Цвет должен быть в виде #RRGGBB")
    if st["format"] == "jpg":
        st["transparent"] = False   # в JPG нет прозрачности
    return st


def matrix(data: str, ec: str) -> list[list[bool]]:
    if not qrcode:
        raise QRError("Не установлен пакет qrcode: pip install \"qrcode[pil]\"")
    qr = qrcode.QRCode(error_correction=EC[ec], border=0)
    qr.add_data(data)
    try:
        qr.make(fit=True)
    except Exception:
        raise QRError("Слишком много текста для одного QR-кода (максимум около 2900 символов)")
    return qr.get_matrix()


def _rgb(hexcolor: str) -> tuple:
    return tuple(int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))


def render(data: str, st: dict) -> bytes:
    """Готовый файл QR-кода в выбранном формате."""
    m = matrix(data, st["ec"])
    n, box, b = len(m), st["box"], st["border"]
    if st["format"] == "svg":
        size = n + 2 * b
        d = "".join(f"M{x + b},{y + b}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
        bg = "" if st["transparent"] else f'<rect width="100%" height="100%" fill="{st["bg"]}"/>'
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
               f'width="{size * box}" height="{size * box}" shape-rendering="crispEdges">'
               f'{bg}<path fill="{st["fg"]}" d="{d}"/></svg>')
        return svg.encode()
    px = (n + 2 * b) * box
    if st["transparent"]:
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        fg = _rgb(st["fg"]) + (255,)
    else:
        img = Image.new("RGB", (px, px), _rgb(st["bg"]))
        fg = _rgb(st["fg"])
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                x0, y0 = (x + b) * box, (y + b) * box
                draw.rectangle((x0, y0, x0 + box - 1, y0 + box - 1), fill=fg)
    buf = io.BytesIO()
    if st["format"] == "jpg":
        img.save(buf, "JPEG", quality=95)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


MIME = {"png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg"}


def bulk(links: list[str], out_dir: Path, st: dict) -> tuple[int, list[dict]]:
    """Пачка QR в папку + index.csv. Возвращает (сколько удалось, ошибки)."""
    width = max(4, len(str(len(links))))
    rows, failed, ok = [], [], 0
    for i, link in enumerate(links, 1):
        name = f"{make_name(i, link, width)}.{st['format']}"
        try:
            (out_dir / name).write_bytes(render(link, st))
            rows.append([i, link, name, ""])
            ok += 1
        except QRError as e:
            rows.append([i, link, "", str(e)])
            failed.append({"name": f"{i}: {link[:60]}", "error": str(e)})
    with (out_dir / "index.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["n", "data", "file", "error"])
        w.writerows(rows)
    return ok, failed
