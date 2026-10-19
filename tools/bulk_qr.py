#!/usr/bin/env python3
"""
bulk_qr.py — массовая генерация QR-кодов из списка ссылок.

Установка:   pip install "qrcode[pil]" tqdm
Примеры:
    python bulk_qr.py links.txt
    python bulk_qr.py document.docx -f svg
    python bulk_qr.py links.csv -c url -o out -f svg
    python bulk_qr.py links.txt -f png -s 12 -e H --workers 8 --dedupe
"""
import argparse
import csv
import re
import sys
from multiprocessing import Pool, cpu_count
from pathlib import Path
from urllib.parse import urlparse

import qrcode
from qrcode.constants import ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q, ERROR_CORRECT_H
from qrcode.image.svg import SvgPathImage

try:
    from tqdm import tqdm
except ImportError:  # прогресс-бар необязателен
    tqdm = None

EC = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}


def read_docx_links(path: Path) -> list[str]:
    """Достаёт ссылки из .docx: гиперссылки (кнопки/текст-ссылки) и голые URL в тексте.
    Порядок — как в документе (включая таблицы)."""
    import zipfile
    import xml.etree.ElementTree as ET

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    with zipfile.ZipFile(path) as z:
        rels_xml = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        rels = {r.get("Id"): r.get("Target") for r in rels_xml
                if r.get("TargetMode") == "External"}
        doc = ET.fromstring(z.read("word/document.xml"))

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
    # убираем дубли, сохраняя порядок
    return [u for u in dict.fromkeys(u.strip() for u in found) if u.startswith(("http://", "https://"))]


def read_links(path: Path, column: str | None) -> list[str]:
    """Читает ссылки из .docx, .csv или .txt (по строке на ссылку)."""
    if path.suffix.lower() == ".docx":
        return read_docx_links(path)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            if column:
                return [r[column].strip() for r in csv.DictReader(f, dialect=dialect)
                        if r.get(column, "").strip()]
            return [row[0].strip() for row in csv.reader(f, dialect=dialect)
                    if row and row[0].strip()]
    with path.open(encoding="utf-8-sig") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def make_name(idx: int, url: str, width: int) -> str:
    host = urlparse(url if "://" in url else "//" + url).netloc or "link"
    host = re.sub(r"[^A-Za-z0-9._-]+", "_", host)[:40]
    return f"{idx:0{width}d}_{host}"


def render(job):
    idx, url, name, outdir, fmt, box, border, ec = job
    try:
        qr = qrcode.QRCode(error_correction=EC[ec], box_size=box, border=border)
        qr.add_data(url)
        qr.make(fit=True)
        path = Path(outdir) / f"{name}.{fmt}"
        if fmt == "svg":
            qr.make_image(image_factory=SvgPathImage).save(str(path))
        else:
            qr.make_image(fill_color="black", back_color="white").save(str(path))
        return idx, url, path.name, ""
    except Exception as e:  # слишком длинная ссылка и т.п.
        return idx, url, "", str(e)


def main():
    p = argparse.ArgumentParser(description="Массовая генерация QR-кодов из ссылок")
    p.add_argument("input", type=Path, help="файл со ссылками (.txt, .csv или .docx)")
    p.add_argument("-o", "--out", type=Path, default=Path("qr_out"), help="папка вывода")
    p.add_argument("-c", "--column", help="имя колонки со ссылками (для CSV с заголовком)")
    p.add_argument("-f", "--format", choices=["png", "svg"], default="png")
    p.add_argument("-s", "--box-size", type=int, default=10, help="размер модуля в пикселях (PNG)")
    p.add_argument("-b", "--border", type=int, default=4, help="рамка в модулях (минимум 4 по стандарту)")
    p.add_argument("-e", "--ec", choices=list(EC), default="M", help="коррекция ошибок L/M/Q/H")
    p.add_argument("-w", "--workers", type=int, default=cpu_count(), help="число процессов")
    p.add_argument("--dedupe", action="store_true", help="убрать дубликаты ссылок")
    args = p.parse_args()

    if not args.input.exists():
        sys.exit(f"Файл не найден: {args.input}")

    links = read_links(args.input, args.column)
    if args.dedupe:
        links = list(dict.fromkeys(links))
    if not links:
        sys.exit("Ссылок не найдено")

    args.out.mkdir(parents=True, exist_ok=True)
    width = max(4, len(str(len(links))))
    jobs = [
        (i, u, make_name(i, u, width), str(args.out), args.format,
         args.box_size, args.border, args.ec)
        for i, u in enumerate(links, 1)
    ]

    results = []
    with Pool(args.workers) as pool:
        it = pool.imap_unordered(render, jobs, chunksize=32)
        if tqdm:
            it = tqdm(it, total=len(jobs), unit="qr")
        for r in it:
            results.append(r)

    results.sort()
    with (args.out / "index.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["n", "url", "file", "error"])
        w.writerows(results)

    bad = [r for r in results if r[3]]
    print(f"Готово: {len(results) - len(bad)} из {len(results)} → {args.out}/")
    if bad:
        print(f"Ошибок: {len(bad)} (см. index.csv)")


if __name__ == "__main__":
    main()
