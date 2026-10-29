"""
Проверка полной версии Universal на настоящем Android (эмулятор в CI, mobile/e2e/run.sh).

Сервер работает внутри приложения (встроенный Python), порт проброшен на компьютер
(adb forward tcp:47700). Каждый режим гоняется по-настоящему: загрузка файла, задача,
скачивание результата и проверка, что внутри то, что нужно.
Файлы для проверки делает этот же скрипт (Pillow, wave, ffmpeg на компьютере CI); речь — образец из vosk-api.
"""
from __future__ import annotations

import io
import json
import math
import os
import struct
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
import wave
import zipfile
from pathlib import Path

BASE = os.environ.get("E2E_URL", "http://127.0.0.1:47700/")
OUT = Path(os.environ.get("E2E_OUT", "e2e-out"))
OUT.mkdir(parents=True, exist_ok=True)
HOST_FILES = os.environ.get("E2E_HOST_FILES", "http://10.0.2.2:8765/")   # компьютер CI глазами эмулятора
results: list[tuple[str, bool, str]] = []


def req(path, fields=None, files=None, method=None, timeout=120):
    url = BASE + path.lstrip("/")
    data, headers = None, {}
    if fields is not None or files:
        b = "----u" + uuid.uuid4().hex
        parts = []
        for k, v in (fields or {}).items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (name, content) in (files or []):
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body, resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def jreq(*a, **k):
    st, body, _ = req(*a, **k)
    try:
        return st, json.loads(body)
    except ValueError:
        return st, {"raw": body[:300].decode("utf-8", "replace")}


def run_task(path, fields, files, timeout=900):
    st, t = jreq(path, fields, files)
    if st == 200 and "task" not in t and "job" in t:
        # старые режимы (PDF в картинки, фото в PDF) отвечают сразу, без фоновой задачи
        assert not t.get("failed"), f"{path}: {t['failed']}"
        st, body, _ = req(f"api/download/{t['job']}")
        assert st == 200, f"{path}: архив {st}"
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            for n in names:
                (OUT / f"{Path(path).name}_{Path(n).name}").write_bytes(z.read(n))
        return {"status": "done", "job": t["job"], "kind": Path(path).name, "names": names}
    assert st == 200 and "task" in t, f"{path}: {st} {t}"
    t0 = time.time()
    while t.get("status") not in ("done", "error", "cancelled"):
        assert time.time() - t0 < timeout, f"{path}: задача не закончилась за {timeout} с ({t.get('message')})"
        time.sleep(1)
        _, t = jreq(f"api/task/{t['task']}")
    assert t["status"] == "done", f"{path}: {t.get('status')} {t.get('error')} {t.get('failed')}"
    return t


def result_bytes(t) -> list[tuple[str, bytes]]:
    out = []
    for name in t.get("names") or []:
        st, body, _ = req(f"api/file/{t['job']}/{urllib.request.quote(name)}")
        assert st == 200, f"файл {name}: {st}"
        (OUT / f"{t['kind']}_{Path(name).name}").write_bytes(body)
        out.append((name, body))
    assert out, f"нет файлов в результате: {t}"
    return out


def check(name):
    def deco(fn):
        t0 = time.time()
        try:
            info = fn() or ""
            results.append((name, True, f"{info} ({time.time() - t0:.0f} с)"))
            print(f"✅ {name}: {info}", flush=True)
        except Exception as e:
            results.append((name, False, f"{e.__class__.__name__}: {e}"))
            print(f"❌ {name}: {e}", flush=True)
            traceback.print_exc()
        return fn
    return deco


# --------------------------------------------------------------------- тестовые файлы

def png(w=320, h=200, text=None) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    if text:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48)
        except OSError:
            font = ImageFont.load_default(size=48)
        d.text((20, h // 2 - 30), text, fill="black", font=font)
    else:
        d.rectangle((40, 40, w - 40, h - 40), fill=(220, 40, 40))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def pdf_from_png(data: bytes, pages=2) -> bytes:
    from PIL import Image
    ims = [Image.open(io.BytesIO(data)).convert("RGB") for _ in range(pages)]
    b = io.BytesIO()
    ims[0].save(b, "PDF", save_all=True, append_images=ims[1:], resolution=100)
    return b.getvalue()


def tone_wav(sec=3.0, sr=44100) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / sr))) for i in range(int(sr * sec))))
    return b.getvalue()


SPEECH_URL = "https://raw.githubusercontent.com/alphacep/vosk-api/master/python/example/test.wav"


def speech_wav() -> bytes:
    """Английская речь (цифры: «one zero zero zero one, nine oh two one oh, zero one eight zero three»)."""
    with urllib.request.urlopen(SPEECH_URL, timeout=60) as r:
        data = r.read()
    (OUT / "speech.wav").write_bytes(data)
    return data


def video_mp4() -> bytes:
    p = OUT / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=500:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(p)], check=True)
    return p.read_bytes()


# --------------------------------------------------------------------- проверки

@check("сервер и движки")
def _():
    st, d = jreq("api/formats")
    assert st == 200, st
    e = d["engines"]
    need = {k: e.get(k) for k in ("ffmpeg", "imagemagick", "pandoc", "poppler", "ytdlp", "qrcode", "pypdf", "ocr")}
    assert all(need.values()), need
    assert e["stt"]["available"], e["stt"]
    return f"версия {e['version']}, " + ", ".join(k for k in need)


@check("главная страница")
def _():
    st, body, _ = req("")
    assert st == 200 and b"Universal" in body and b"class=\"android\"" not in body
    assert b"universal-save" in body, "нет моста сохранения для Android"


@check("картинки: PNG → JPG, WebP, ICO, PDF")
def _():
    sizes = []
    for to, magic in (("jpg", b"\xff\xd8"), ("webp", b"RIFF"), ("ico", b"\x00\x00\x01\x00"), ("pdf", b"%PDF")):
        t = run_task("api/universal", {"to": to, "quality": "85"}, [("files", ("pic.png", png()))])
        (name, data), = result_bytes(t)
        assert data[:len(magic)] == magic, f"{to}: {data[:8]}"
        sizes.append(f"{to} {len(data)} Б")
    return ", ".join(sizes)


@check("аудио: WAV → MP3 (ffmpeg на телефоне)")
def _():
    t = run_task("api/universal", {"to": "mp3", "bitrate": "128"}, [("files", ("tone.wav", tone_wav()))])
    (name, data), = result_bytes(t)
    assert data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"), data[:4]
    return f"{name} {len(data)} Б"


@check("видео: MP4 → WebM")
def _():
    t = run_task("api/universal", {"to": "webm"}, [("files", ("clip.mp4", video_mp4()))])
    (name, data), = result_bytes(t)
    assert data[:4] == b"\x1aE\xdf\xa3", data[:4]
    return f"{name} {len(data)} Б"


@check("видеоредактор: обрезка 1 с и поворот")
def _():
    t = run_task("api/edit", {"start": "0", "end": "1", "out": "mp4", "rotate": "90"}, [("files", ("clip.mp4", video_mp4()))])
    (name, data), = result_bytes(t)
    assert data[4:8] == b"ftyp", data[:8]
    return f"{name} {len(data)} Б"


@check("PDF в картинки (Android PdfRenderer)")
def _():
    t = run_task("api/convert", {"format": "png", "dpi": "100", "all_pages": "1"}, [("files", ("doc.pdf", pdf_from_png(png(), 2)))])
    files = result_bytes(t)
    assert len(files) == 2 and all(d[:8] == b"\x89PNG\r\n\x1a\n" for _, d in files), [n for n, _ in files]
    return ", ".join(n for n, _ in files)


@check("правка PDF: поворот")
def _():
    t = run_task("api/pdf", {"op": "rotate", "angle": "90"}, [("files", ("doc.pdf", pdf_from_png(png(), 2)))])
    (name, data), = result_bytes(t)
    assert data[:4] == b"%PDF" and b"/Rotate 90" in data
    return name


@check("фото в PDF")
def _():
    t = run_task("api/photos2pdf", {"per_page": "1"}, [("files", ("a.png", png())), ("files", ("b.png", png()))])
    (name, data), = result_bytes(t)
    assert data[:4] == b"%PDF"
    return f"{name} {len(data)} Б"


@check("документы: Markdown → DOCX → Markdown (встроенный конвертер)")
def _():
    md = "# Заголовок\n\nТекст **жирный**\n\n- раз\n- два\n".encode()
    t = run_task("api/universal", {"to": "docx"}, [("files", ("note.md", md))])
    (name, docx), = result_bytes(t)
    assert docx[:2] == b"PK" and b"word/document.xml" in docx
    t = run_task("api/universal", {"to": "md"}, [("files", ("note.docx", docx))])
    (name, back), = result_bytes(t)
    text = back.decode("utf-8")
    assert "Заголовок" in text and "**жирный**" in text, text
    return "туда и обратно"


@check("редактор документов: сохранить в PDF и DOCX")
def _():
    for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK")):
        st, d = jreq("api/doc/save", {"html": "<h1>Привет</h1><p>Проверка <b>редактора</b></p>", "format": fmt, "name": "doc"})
        assert st == 200, d
        st, body, _ = req(d["url"].lstrip("/"))
        assert st == 200 and body[:len(magic)] == magic, (fmt, body[:8])
    return "PDF и DOCX"


@check("QR-код")
def _():
    st, body, _ = req("api/qr", {"kind": "text", "text": "https://github.com/Zaxar1231/Universal-app", "format": "png"})
    assert st == 200 and body[:8] == b"\x89PNG\r\n\x1a\n", (st, body[:20])
    (OUT / "qr.png").write_bytes(body)
    return f"{len(body)} Б"


@check("архивы: ZIP и распаковка")
def _():
    t = run_task("api/files", {"op": "zip", "name": "pack"}, [("files", ("a.txt", b"hello")), ("files", ("b.txt", "привет".encode()))])
    (name, data), = result_bytes(t)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["a.txt", "b.txt"], z.namelist()
    t = run_task("api/files", {"op": "extract"}, [("files", ("pack.zip", data))])
    files = result_bytes(t)
    return f"{name}, распаковано {len(files)}"


@check("распознавание текста (Tesseract на телефоне)")
def _():
    t = run_task("api/ocr", {"langs": "eng", "txt": "1"}, [("files", ("scan.png", png(900, 200, "Universal OCR test")))], timeout=600)
    files = dict(result_bytes(t))
    pdf = next(v for k, v in files.items() if k.endswith(".pdf"))
    txt = next(v for k, v in files.items() if k.endswith(".txt")).decode("utf-8", "replace")
    assert pdf[:4] == b"%PDF" and "OCR" in txt.upper(), txt
    return f"текст: {txt.strip()[:60]!r}"


@check("голос в текст (Vosk на телефоне)")
def _():
    t = run_task("api/stt", {"lang": "en", "model": "normal"},
                 [("files", ("voice.wav", speech_wav()))], timeout=900)
    tr = t["result"]["transcripts"][0]
    words = tr["text"].lower().split()
    assert sum(w in words for w in ("one", "zero", "nine", "two", "eight", "three")) >= 3, tr["text"]
    return f"распознано: {tr['text']!r}"


@check("скачивание видео (yt-dlp на телефоне)")
def _():
    t = run_task("api/dl", {"urls": HOST_FILES + "clip.mp4", "mode": "video", "quality": "best"}, None, timeout=600)
    files = result_bytes(t)
    assert any(d[4:8] == b"ftyp" for _, d in files), [n for n, _ in files]
    return ", ".join(n for n, _ in files)


@check("страница сайта одним HTML (встроенный сохранитель)")
def _():
    site = OUT / "host" / "site"          # эту папку отдаёт http.server из run.sh
    site.mkdir(parents=True, exist_ok=True)
    (site / "pic.png").write_bytes(png(40, 30))
    (site / "e.css").write_text(".e{color:red}", encoding="utf-8")
    (site / "s.css").write_text("@import 'e.css'; body{background:url(pic.png)}", encoding="utf-8")
    (site / "index.html").write_text("<html><head><meta charset='utf-8'><title>Проверка страницы</title>"
                                     "<link rel=stylesheet href=s.css></head><body><img src=pic.png><script>1</script></body></html>",
                                     encoding="utf-8")
    t = run_task("api/dl", {"mode": "page", "urls": HOST_FILES + "site/index.html"}, None, timeout=300)
    (name, data), = result_bytes(t)
    h = data.decode("utf-8")
    assert name.endswith(".html") and "data:image/png;base64," in h and ".e{color:red}" in h and "<script" not in h, h[:300]
    return f"{name} {len(data)} Б"


@check("вкладка «Информация»")
def _():
    st, d = jreq("api/about")
    assert st == 200 and d["version"], d
    return f"{d['os']}, Python {d['python']}"


ok = sum(1 for _, good, _ in results if good)
summary = [f"{'✅' if good else '❌'} {name} — {info}" for name, good, info in results]
(OUT / "summary.md").write_text("## Полная версия на Android (эмулятор)\n\n" + "\n".join(f"- {s}" for s in summary) +
                                f"\n\n**{ok} из {len(results)}**\n", encoding="utf-8")
print("\n".join(summary))
print(f"\n{ok} из {len(results)}")
sys.exit(0 if ok == len(results) else 1)
