"""
Проверки на реальных файлах: ffmpeg генерирует тестовые ролики, дальше
смотрим ffprobe-ом, что получилось именно то, что просили.

Запуск: python -m pytest -q
Тесты, которым нужен интернет (yt-dlp), включаются переменной NET=1.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as webapp  # noqa: E402
import downloader  # noqa: E402
import editor  # noqa: E402
import qrtools  # noqa: E402
from converters import ConvertError  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def streams(path):
    res = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", str(path)], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    if not shutil.which("ffmpeg"):
        return d
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-f", "lavfi", "-i", "sine=f=440:d=6",
       "-c:v", "libx264", "-g", "30", "-c:a", "aac", "-shortest", str(d / "a.mp4"))
    ff("-f", "lavfi", "-i", "testsrc=s=320x240:r=25:d=4", "-c:v", "libx264", str(d / "silent.mp4"))
    ff("-f", "lavfi", "-i", "sine=f=330:d=5", str(d / "tone.mp3"))
    return d


@pytest.fixture()
def client():
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def wait_task(client, tid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/task/{tid}").get_json()
        if d["status"] in ("done", "error", "cancelled"):
            return d
        time.sleep(0.2)
    raise AssertionError("задача не завершилась")


# ---------------------------------------------------------------- редактор

def test_parse_time():
    assert editor.parse_time("90") == 90
    assert editor.parse_time("1:30") == 90
    assert editor.parse_time("01:02:03.5") == 3723.5
    assert editor.parse_time("1м30с") == 90
    assert editor.parse_time("2,5") == 2.5
    assert editor.parse_time("") is None
    with pytest.raises(ConvertError):
        editor.parse_time("abc")


def test_atempo_chain():
    assert editor._atempo_chain(4) == ["atempo=2.0", "atempo=2"]
    assert editor._atempo_chain(0.25) == ["atempo=0.5", "atempo=0.5"]


def opts(**kw):
    base = {k: "" for k in ("start", "end")}
    base.update({k: str(v) for k, v in kw.items()})
    return editor.read_opts(base)


@needs_ffmpeg
def test_trim_crop_scale(media, tmp_path):
    out = editor.edit_file(media / "a.mp4", tmp_path, "r",
                           opts(start="1", end="4", aspect="9:16", height="240", out="mp4"))
    s = streams(out)
    v = next(x for x in s["streams"] if x["codec_type"] == "video")
    assert v["height"] == 240 and v["width"] in (134, 136)          # 9:16 после кропа
    assert abs(float(s["format"]["duration"]) - 3) < 0.25
    assert any(x["codec_type"] == "audio" for x in s["streams"])


@needs_ffmpeg
def test_speed_and_mute(media, tmp_path):
    out = editor.edit_file(media / "a.mp4", tmp_path, "r", opts(speed="2", mute="1", out="webm"))
    s = streams(out)
    assert abs(float(s["format"]["duration"]) - 3) < 0.3
    assert [x["codec_name"] for x in s["streams"]] == ["vp9"]


@needs_ffmpeg
def test_target_size(media, tmp_path):
    out = editor.edit_file(media / "a.mp4", tmp_path, "r", opts(target_mb="0.3", out="mp4"))
    assert out.stat().st_size < 0.3 * 1048576 * 1.15


@needs_ffmpeg
def test_target_size_too_small(media, tmp_path):
    with pytest.raises(ConvertError, match="слишком мало"):
        editor.edit_file(media / "a.mp4", tmp_path, "r", opts(target_mb="0.001", out="mp4"))


@needs_ffmpeg
def test_gif_and_rotate(media, tmp_path):
    out = editor.edit_file(media / "a.mp4", tmp_path, "r", opts(out="gif", rotate="90", gif_width="200", end="2"))
    v = streams(out)["streams"][0]
    assert v["codec_name"] == "gif" and v["width"] == 200 and v["height"] > 200


@needs_ffmpeg
def test_audio_edit(media, tmp_path):
    out = editor.edit_file(media / "tone.mp3", tmp_path, "r",
                           opts(out="same", fade_in="1", fade_out="1", volume="50", normalize="1", end="3"))
    s = streams(out)
    assert out.suffix == ".mp3" and abs(float(s["format"]["duration"]) - 3) < 0.2


@needs_ffmpeg
def test_audio_file_to_video_format_rejected(media, tmp_path):
    with pytest.raises(ConvertError, match="нет видео"):
        editor.edit_file(media / "tone.mp3", tmp_path, "r", opts(out="mp4"))


@needs_ffmpeg
def test_extract_audio(media, tmp_path):
    out = editor.edit_file(media / "a.mp4", tmp_path, "r", opts(out="opus"))
    assert [x["codec_name"] for x in streams(out)["streams"]] == ["opus"]


@needs_ffmpeg
def test_fast_trim_copy(media, tmp_path):
    out = editor.edit_file(media / "a.mp4", tmp_path, "r", opts(start="2", fast="1", out="same"))
    assert out.suffix == ".mp4" and float(streams(out)["format"]["duration"]) < 5


@needs_ffmpeg
def test_merge_mixed_sizes_and_silence(media, tmp_path):
    out = editor.merge_files([media / "a.mp4", media / "silent.mp4"], tmp_path, "m", opts(out="mp4"))
    s = streams(out)
    v = next(x for x in s["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"]) == (640, 360)
    assert any(x["codec_type"] == "audio" for x in s["streams"])
    assert abs(float(s["format"]["duration"]) - 10) < 0.4


@needs_ffmpeg
def test_edit_endpoint_and_cancel(client, media):
    with open(media / "a.mp4", "rb") as f:
        r = client.post("/api/edit", data={"files": (f, "клип.mp4"), "out": "mp4", "height": "240"},
                        content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    d = wait_task(client, r.get_json()["task"])
    assert d["status"] == "done" and d["single_file"] == "клип_edit.mp4"
    dl = client.get(f"/api/download/{d['job']}?raw=1")
    assert dl.status_code == 200 and dl.data[4:8] == b"ftyp"
    assert "UTF-8''%D0%BA%D0%BB%D0%B8%D0%BF_edit.mp4" in dl.headers["Content-Disposition"]
    dl.close()

    # отмена: длинная задача (медленный пресет) убивается
    with open(media / "a.mp4", "rb") as f:
        r = client.post("/api/edit", data={"files": (f, "a.mp4"), "out": "mkv", "vcodec": "h265",
                                           "preset": "veryslow", "speed": "0.25"},
                        content_type="multipart/form-data")
    tid = r.get_json()["task"]
    time.sleep(0.8)
    client.post(f"/api/task/{tid}/cancel")
    assert wait_task(client, tid)["status"] == "cancelled"


def test_edit_endpoint_rejects_non_media(client):
    r = client.post("/api/edit", data={"files": (io.BytesIO(b"x"), "doc.txt")},
                    content_type="multipart/form-data")
    assert r.status_code == 400


# ---------------------------------------------------------------- QR

def test_qr_png_decodes_size(client):
    r = client.post("/api/qr", data={"text": "https://example.com", "box_size": "5", "border": "4"})
    assert r.status_code == 200 and r.mimetype == "image/png"
    from PIL import Image
    im = Image.open(io.BytesIO(r.data))
    n = len(qrtools.matrix("https://example.com", "M"))
    assert im.size == ((n + 8) * 5,) * 2
    assert im.getpixel((0, 0)) == (255, 255, 255)
    assert im.getpixel((20, 20)) == (0, 0, 0)            # угол поискового узора


def test_qr_svg_colors_transparent(client):
    r = client.post("/api/qr", data={"text": "hi", "format": "svg", "fg": "#ff0000", "transparent": "1"})
    body = r.data.decode()
    assert r.mimetype == "image/svg+xml" and 'fill="#ff0000"' in body and "<rect" not in body


def test_qr_wifi_payload():
    assert qrtools.wifi_payload("My;Net", "p:ss") == r"WIFI:T:WPA;S:My\;Net;P:p\:ss;;"
    assert qrtools.wifi_payload("Open", "", "nopass") == "WIFI:T:nopass;S:Open;;"


def test_qr_bad_input(client):
    assert client.post("/api/qr", data={"text": " "}).status_code == 400
    assert client.post("/api/qr", data={"text": "x", "fg": "red"}).status_code == 400
    assert client.post("/api/qr", data={"text": "x" * 5000}).status_code == 400


def make_docx(links):
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rels = "".join(f'<Relationship Id="r{i}" Type="hyperlink" Target="{u}" TargetMode="External"/>'
                   for i, u in enumerate(links))
    body = "".join(f'<w:p><w:hyperlink r:id="r{i}"><w:r><w:t>link</w:t></w:r></w:hyperlink></w:p>'
                   for i in range(len(links)))
    body += '<w:p><w:r><w:t>see https://plain.example/x</w:t></w:r></w:p>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/_rels/document.xml.rels",
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_qr_bulk_sources(client):
    data = {
        "links": "https://a.example\n# комментарий\nhttps://a.example\n",
        "column": "url",
        "files": [(io.BytesIO(b"name;url\nx;https://b.example\ny;https://c.example\n"), "list.csv"),
                  (io.BytesIO(make_docx(["https://d.example/1"])), "doc.docx")],
        "format": "svg",
    }
    r = client.post("/api/qr/bulk", data=data, content_type="multipart/form-data")
    d = r.get_json()
    assert r.status_code == 200, d
    assert d["total"] == 5 and d["done"] == 5                  # дубль убран
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/download/{d['job']}").data))
    names = sorted(z.namelist())
    assert "index.csv" in names and "0001_a.example.svg" in names and "0005_plain.example.svg" in names


def test_qr_bulk_wrong_column(client):
    r = client.post("/api/qr/bulk", data={"column": "link", "files": [(io.BytesIO(b"url\nhttps://x"), "a.csv")]},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "колонки" in r.get_json()["error"]


# ---------------------------------------------------------------- загрузчик

def test_extract_urls():
    text = "смотри https://youtu.be/abc, и вот (https://vk.com/video1_2). https://youtu.be/abc"
    assert downloader.extract_urls(text) == ["https://youtu.be/abc", "https://vk.com/video1_2"]


def test_dl_opts_audio():
    o = downloader.read_opts({"mode": "audio", "audio_format": "mp3", "abitrate": "192"})
    y = downloader.build_opts(o, Path("/tmp"), None)
    pp = y["postprocessors"][0]
    assert y["format"] == "bestaudio/best" and pp["preferredcodec"] == "mp3" and pp["preferredquality"] == "192"
    assert y["noplaylist"] is True


def test_dl_opts_video_quality():
    o = downloader.read_opts({"mode": "video", "quality": "720", "container": "mp4", "playlist": "1"})
    y = downloader.build_opts(o, Path("/tmp"), None)
    assert "[height<=720]" in y["format"] and y["format_sort"][0] == "res:720"
    assert y["merge_output_format"] == "mp4" and y["noplaylist"] is False


def test_dl_rejects_no_urls(client):
    assert client.post("/api/dl", data={"urls": "просто текст"}).status_code == 400


@pytest.fixture()
def http_media(media):
    """Локальный HTTP-сервер: yt-dlp качает прямую ссылку generic-экстрактором."""
    import functools
    import http.server
    import threading
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(media))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@needs_ffmpeg
def test_dl_local_video_and_audio(client, http_media):
    r = client.post("/api/dl", data={"urls": f"{http_media}/a.mp4", "mode": "video"})
    d = wait_task(client, r.get_json()["task"])
    assert d["status"] == "done" and d["single_file"].endswith(".mp4"), d

    r = client.post("/api/dl", data={"urls": f"{http_media}/a.mp4 {http_media}/nope.mp4",
                                     "mode": "audio", "audio_format": "mp3", "abitrate": "128"})
    d = wait_task(client, r.get_json()["task"])
    assert d["status"] == "done" and d["done"] == 1 and len(d["failed"]) == 1, d
    assert d["single_file"].endswith(".mp3")


@needs_ffmpeg
def test_dl_info_local(client, http_media):
    d = client.post("/api/dl/info", data={"urls": f"{http_media}/a.mp4"}).get_json()
    assert d["items"][0]["title"] == "a" and not d["items"][0].get("error")


@pytest.mark.skipif(os.environ.get("NET") != "1", reason="нужен интернет: NET=1")
def test_dl_real(client):
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"   # «Me at the zoo», 19 секунд
    r = client.post("/api/dl", data={"urls": url, "mode": "audio", "audio_format": "mp3"})
    d = wait_task(client, r.get_json()["task"], timeout=300)
    assert d["status"] == "done", d


# ---------------------------------------------------------------- конвертер (регресс)

@needs_ffmpeg
def test_universal_still_works(client, media):
    with open(media / "tone.mp3", "rb") as f:
        r = client.post("/api/universal", data={"files": (f, "tone.mp3"), "to": "flac"},
                        content_type="multipart/form-data")
    d = wait_task(client, r.get_json()["task"])
    assert d["status"] == "done" and d["done"] == 1 and d["single_file"] == "tone.flac"


@pytest.mark.skipif(not shutil.which("convert"), reason="нет ImageMagick")
def test_universal_image_resize(client):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1200, 600), (10, 200, 30)).save(buf, "PNG")
    buf.seek(0)
    r = client.post("/api/universal", data={"files": (buf, "big.png"), "to": "jpg", "resize": "300"},
                    content_type="multipart/form-data")
    d = wait_task(client, r.get_json()["task"])
    im = Image.open(io.BytesIO(client.get(f"/api/download/{d['job']}?raw=1").data))
    assert im.size == (300, 150)


# ---------------------------------------------------------------- PDF

def make_pdf(pages=3, photo=False):
    from PIL import Image
    import random
    imgs = []
    for i in range(pages):
        im = Image.new("RGB", (1600, 2200) if photo else (200, 300), (255, 255, 255))
        if photo:   # шум плохо жмётся без потерь: имитация скана
            im = Image.frombytes("RGB", (1600, 2200), bytes(random.getrandbits(8) for _ in range(1600 * 2200 * 3)))
        imgs.append(im)
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def pdf_task(client, files, **form):
    data = {"files": [(io.BytesIO(b), n) for n, b in files], **form}
    r = client.post("/api/pdf", data=data, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return wait_task(client, r.get_json()["task"])


def pages_of(client, d, name=None):
    from pypdf import PdfReader
    url = f"/api/file/{d['job']}/{name}" if name else f"/api/download/{d['job']}?raw=1"
    return len(PdfReader(io.BytesIO(client.get(url).data)).pages)


def test_pdf_ranges():
    import pdftools
    assert pdftools.parse_ranges("1-3, 5, 8-", 9) == [0, 1, 2, 4, 7, 8]
    assert pdftools.parse_ranges("", 2) == [0, 1]
    with pytest.raises(pdftools.PdfError):
        pdftools.parse_ranges("4", 3)
    with pytest.raises(pdftools.PdfError):
        pdftools.parse_ranges("a-b", 3)


def test_pdf_merge_split_extract_rotate(client):
    a, b = make_pdf(3), make_pdf(2)
    d = pdf_task(client, [("a.pdf", a), ("b.pdf", b)], op="merge", merge_name="all")
    assert d["single_file"] == "all.pdf" and pages_of(client, d) == 5

    d = pdf_task(client, [("a.pdf", a)], op="split")
    assert d["names"] == ["a_p1.pdf", "a_p2.pdf", "a_p3.pdf"]

    d = pdf_task(client, [("a.pdf", a)], op="extract", ranges="3,1")
    assert pages_of(client, d) == 2

    from pypdf import PdfReader
    d = pdf_task(client, [("a.pdf", a)], op="rotate", angle="90", ranges="2")
    r = PdfReader(io.BytesIO(client.get(f"/api/download/{d['job']}?raw=1").data))
    assert [p.rotation for p in r.pages] == [0, 90, 0]


def test_pdf_compress(client):
    src = make_pdf(1, photo=True)
    d = pdf_task(client, [("scan.pdf", src)], op="compress", quality="50", max_px="800")
    out = client.get(f"/api/download/{d['job']}?raw=1").data
    assert d["status"] == "done" and len(out) < len(src) / 3


def test_pdf_password_roundtrip(client):
    d = pdf_task(client, [("a.pdf", make_pdf(2))], op="protect", new_password="тайна1")
    locked = client.get(f"/api/download/{d['job']}?raw=1").data
    from pypdf import PdfReader
    assert PdfReader(io.BytesIO(locked)).is_encrypted

    d = pdf_task(client, [("x.pdf", locked)], op="rotate", angle="180")
    assert d["status"] == "error" and "паролем" in d["failed"][0]["error"]
    d = pdf_task(client, [("x.pdf", locked)], op="unlock", password="не тот")
    assert "Неверный пароль" in d["failed"][0]["error"]
    d = pdf_task(client, [("x.pdf", locked)], op="unlock", password="тайна1")
    assert not PdfReader(io.BytesIO(client.get(f"/api/download/{d['job']}?raw=1").data)).is_encrypted


def test_pdf_bad_requests(client):
    one = [("a.pdf", make_pdf(1))]
    r = client.post("/api/pdf", data={"op": "merge", "files": [(io.BytesIO(b), n) for n, b in one]},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    d = pdf_task(client, [("bad.pdf", b"not a pdf")], op="rotate")
    assert d["status"] == "error"


def test_index_renders(client):
    html = client.get("/").data.decode()
    for mode in ("image", "edit", "dl", "qr", "topdf", "pdft"):
        assert f'value="{mode}"' in html


def test_password(monkeypatch, client):
    monkeypatch.setattr(webapp, "APP_PASSWORD", "secret")
    assert client.get("/api/engines").status_code == 401
    import base64
    ok = {"Authorization": "Basic " + base64.b64encode(b"me:secret").decode()}
    assert client.get("/api/engines", headers=ok).status_code == 200


# ---------------------------------------------------------------- движки (настольная версия)

@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    import engines
    monkeypatch.setenv("UNIVERSAL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("UNIVERSAL_ENGINES_ANYOS", "1")
    srv_dir = tmp_path / "srv"
    srv_dir.mkdir()
    import functools
    import http.server
    import threading
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(srv_dir))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield engines, srv_dir, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_engine_install_and_activate(engine_env, monkeypatch, client):
    engines, srv_dir, base = engine_env
    with zipfile.ZipFile(srv_dir / "ff.zip", "w") as z:
        z.writestr("ffmpeg-build/bin/ffmpeg.exe", "x")
        z.writestr("ffmpeg-build/bin/ffprobe.exe", "x")
        z.writestr("ffmpeg-build/bin/avcodec.dll", "x")
    monkeypatch.setitem(engines.CATALOG, "ffmpeg", {**engines.CATALOG["ffmpeg"], "url": base + "/ff.zip"})
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    r = client.post("/api/engines/install/ffmpeg")
    d = wait_task(client, r.get_json()["task"])
    assert d["status"] == "done", d
    bindir = engines.root() / "engines" / "ffmpeg" / "ffmpeg-build" / "bin"
    assert (bindir / "avcodec.dll").exists()                      # dll рядом с exe
    assert os.environ["PATH"].split(os.pathsep)[0] == str(bindir)
    assert engines.status()["ffmpeg"]["installed"]

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    engines.activate()                                            # повторный запуск приложения
    assert str(bindir) in os.environ["PATH"]


def test_engine_install_errors(engine_env, monkeypatch, client):
    engines, srv_dir, base = engine_env
    with zipfile.ZipFile(srv_dir / "bad.zip", "w") as z:
        z.writestr("readme.txt", "no exe here")
    with zipfile.ZipFile(srv_dir / "evil.zip", "w") as z:
        z.writestr("../../evil/pandoc.exe", "x")
    monkeypatch.setitem(engines.CATALOG, "pandoc", {**engines.CATALOG["pandoc"], "url": base + "/bad.zip"})
    d = wait_task(client, client.post("/api/engines/install/pandoc").get_json()["task"])
    assert d["status"] == "error" and "нет pandoc.exe" in d["error"]

    monkeypatch.setitem(engines.CATALOG, "pandoc", {**engines.CATALOG["pandoc"], "url": base + "/evil.zip"})
    d = wait_task(client, client.post("/api/engines/install/pandoc").get_json()["task"])
    assert d["status"] == "error" and "повреждён" in d["error"]
    assert not (engines.root().parent / "evil").exists()

    monkeypatch.setitem(engines.CATALOG, "pandoc", {**engines.CATALOG["pandoc"], "url": base + "/missing.zip"})
    d = wait_task(client, client.post("/api/engines/install/pandoc").get_json()["task"])
    assert d["status"] == "error" and "прервалась" in d["error"]
    assert client.post("/api/engines/install/nope").status_code == 404


def test_engine_ytdlp_zipapp(engine_env, monkeypatch):
    engines, srv_dir, base = engine_env
    with zipfile.ZipFile(srv_dir / "yt-dlp", "w") as z:
        z.writestr("fake_ytdlp_probe/__init__.py", "VERSION = 'fresh'")
    monkeypatch.setitem(engines.CATALOG, "ytdlp", {**engines.CATALOG["ytdlp"], "url": base + "/yt-dlp"})
    path = engines.install("ytdlp")
    monkeypatch.setattr(sys, "path", list(sys.path))
    engines.activate()
    assert sys.path[0] == path
    import importlib
    assert importlib.import_module("fake_ytdlp_probe").VERSION == "fresh"


def test_engine_install_only_on_windows(monkeypatch, client):
    monkeypatch.delenv("UNIVERSAL_ENGINES_ANYOS", raising=False)
    if sys.platform != "win32":
        assert client.post("/api/engines/install/ffmpeg").status_code == 400


def test_windows_ignores_system_convert(monkeypatch):
    import converters
    monkeypatch.setattr(converters.sys, "platform", "win32")
    monkeypatch.setattr(converters.shutil, "which", lambda n: "C:/Windows/System32/convert.exe" if n == "convert" else None)
    assert converters._magick() is None


def test_engine_resolve_falls_back_to_release_page(monkeypatch):
    """API упёрся в лимит (403) -> ссылка берётся со страницы релиза."""
    import http.server
    import threading
    import engines

    class FakeGitHub(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/api/"):
                self.send_response(403); self.end_headers(); self.wfile.write(b"rate limit exceeded")
            elif self.path == "/jgm/pandoc/releases/latest":
                self.send_response(302); self.send_header("Location", "/jgm/pandoc/releases/tag/3.9"); self.end_headers()
            elif self.path == "/jgm/pandoc/releases/tag/3.9":
                self.send_response(200); self.end_headers()
            elif self.path == "/jgm/pandoc/releases/expanded_assets/3.9":
                self.send_response(200); self.end_headers()
                self.wfile.write(b'<a href="/jgm/pandoc/releases/download/3.9/pandoc-3.9-arm64-macOS.zip">'
                                 b'<a href="/jgm/pandoc/releases/download/3.9/pandoc-3.9-windows-x86_64.zip">')
            else:
                self.send_response(404); self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    monkeypatch.setattr(engines, "GITHUB_API", base + "/api/repos/{repo}/releases/latest")
    monkeypatch.setattr(engines, "GITHUB_WEB", base)
    try:
        assert engines._resolve_url(engines.CATALOG["pandoc"]) == base + "/jgm/pandoc/releases/download/3.9/pandoc-3.9-windows-x86_64.zip"
        with pytest.raises(engines.EngineError, match="нет файла"):
            engines._resolve_url({"repo": "jgm/pandoc", "asset": r"nothing-matches$"})
        with pytest.raises(engines.EngineError, match="Проверь интернет"):
            engines._resolve_url({"repo": "no/such", "asset": "x"})
    finally:
        srv.shutdown()


def test_engine_7z_falls_back_to_7zip(engine_env, monkeypatch, tmp_path):
    """py7zr не осилил архив (BCJ2) -> распаковка внешним 7-Zip."""
    engines, srv_dir, base = engine_env
    py7zr = pytest.importorskip("py7zr")      # есть в настольных зависимостях, в обычных нет
    with py7zr.SevenZipFile(srv_dir / "im.7z", "w") as z:
        z.writestr("x", "ImageMagick-7/magick.exe")
    calls = []

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("BCJ2 filter is not supported by py7zr")

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = Path([c for c in cmd if c.startswith("-o")][0][2:])
        (out / "ImageMagick-7").mkdir(parents=True)
        (out / "ImageMagick-7" / "magick.exe").write_text("x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(py7zr, "SevenZipFile", Broken)
    monkeypatch.setattr(engines, "_seven_zip_exe", lambda: "7zr")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setitem(engines.CATALOG, "imagemagick", {**engines.CATALOG["imagemagick"], "url": base + "/im.7z"})
    path = engines.install("imagemagick")
    assert calls and calls[0][:2] == ["7zr", "x"]
    assert Path(path, "magick.exe").exists()


def test_engine_asset_priority():
    import engines
    spec = engines.CATALOG["imagemagick"]
    assets = [("ImageMagick-7.1.2-portable-Q16-HDRI-x64.7z", "u7z"), ("ImageMagick-7.1.2-portable-Q16-HDRI-x64.zip", "uzip")]
    orig = engines._assets_from_api
    try:
        engines._assets_from_api = lambda repo: assets
        assert engines._resolve_url(spec) == "uzip"
        engines._assets_from_api = lambda repo: assets[:1]
        assert engines._resolve_url(spec) == "u7z"
    finally:
        engines._assets_from_api = orig


# ---------------------------------------------------------------- QR в документе

def make_qr_docx() -> bytes:
    """Word-файл со всеми видами ссылок: гиперссылка, поле HYPERLINK, адрес текстом."""
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    body = (
        '<w:p><w:r><w:t xml:space="preserve">Сайт: </w:t></w:r>'
        '<w:hyperlink r:id="rId5"><w:r><w:t>наш сайт</w:t></w:r></w:hyperlink>'
        '<w:r><w:t>.</w:t></w:r></w:p>'
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> HYPERLINK "https://field.example.com/x" </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>поле</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Текстом https://plain.example.com/a?b=1&amp;c=2, и дальше.</w:t></w:r></w:p>'
        '<w:p><w:hyperlink r:id="rId6"><w:r><w:t>почта</w:t></w:r></w:hyperlink></w:p>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/_rels/document.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://site.example.com/" TargetMode="External"/>'
                   '<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="mailto:a@b.c" TargetMode="External"/>'
                   '</Relationships>')
        z.writestr("word/document.xml", f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_qrdoc_docx_scan_and_replace(tmp_path):
    import qrdoc
    from xml.etree import ElementTree as ET
    src = tmp_path / "doc.docx"
    src.write_bytes(make_qr_docx())
    links = {l["url"]: l for l in qrdoc.scan(src, tmp_path / "w")["links"]}
    assert set(links) == {"https://site.example.com/", "https://field.example.com/x", "https://plain.example.com/a?b=1&c=2"}
    assert links["https://site.example.com/"]["text"] == "наш сайт" and links["https://field.example.com/x"]["text"] == "поле"

    out = tmp_path / "out"; out.mkdir()
    st = qrtools.read_style({})
    dst, n = qrdoc.apply(src, out, "doc", set(links) - {"https://field.example.com/x"}, "replace", 3, st, tmp_path / "w")
    assert n == 2
    z = zipfile.ZipFile(dst)
    xml = z.read("word/document.xml").decode()
    ET.fromstring(xml)                                           # XML не сломан
    assert xml.count("<w:drawing>") == 2
    assert "наш сайт" not in xml and "HYPERLINK" in xml           # гиперссылка заменена, поле оставлено
    import re as _re
    texts = "".join(_re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    assert "Текстом , и дальше." in texts and "plain.example.com" not in texts   # адрес ушёл, текст вокруг остался
    assert '<w:b/>' in xml                                       # форматирование куска текста сохранилось
    assert "mailto" in z.read("word/_rels/document.xml.rels").decode()
    assert 'Extension="png"' in z.read("[Content_Types].xml").decode()
    pngs = [n for n in z.namelist() if n.startswith("word/media/")]
    assert len(pngs) == 2 and all(z.read(p)[:4] == b"\x89PNG" for p in pngs)
    assert [l["url"] for l in qrdoc.scan(dst, tmp_path / "w2")["links"]] == ["https://field.example.com/x"]


def test_qrdoc_docx_after_keeps_links(tmp_path):
    import qrdoc
    src = tmp_path / "doc.docx"
    src.write_bytes(make_qr_docx())
    out = tmp_path / "out"; out.mkdir()
    dst, n = qrdoc.apply(src, out, "doc", {"https://field.example.com/x", "https://plain.example.com/a?b=1&c=2"},
                         "after", 2, qrtools.read_style({}), tmp_path / "w")
    xml = zipfile.ZipFile(dst).read("word/document.xml").decode()
    assert n == 2 and xml.count("<w:drawing>") == 2
    assert "https://plain.example.com/a?b=1&amp;c=2</w:t>" in xml and "поле" in xml
    assert len(qrdoc.scan(dst, tmp_path / "w2")["links"]) == 3   # все ссылки на месте


def test_qrdoc_markdown(tmp_path):
    import qrdoc
    md = ("# Заголовок\n\n[Сайт](https://a.example.com/x \"title\") и <https://b.example.com>.\n\n"
          "Голая https://c.example.com/p, дальше. ![картинка](https://img.example.com/i.png)\n\n"
          "```\nhttps://code.example.com\n```\n\nКод `https://inline.example.com` тоже.\n")
    src = tmp_path / "note.md"
    src.write_text(md, encoding="utf-8")
    urls = [l["url"] for l in qrdoc.scan(src, tmp_path)["links"]]
    assert urls == ["https://a.example.com/x", "https://b.example.com", "https://c.example.com/p"]
    out = tmp_path / "out"; out.mkdir()
    dst, n = qrdoc.apply(src, out, "note", set(urls), "replace", 3, qrtools.read_style({}), tmp_path / "w")
    text = dst.read_text(encoding="utf-8")
    assert n == 3 and text.count('<img src="note_qr_') == 3
    assert "https://code.example.com" in text and "https://inline.example.com" in text
    assert "![картинка](https://img.example.com/i.png)" in text and ", дальше." in text
    assert sorted(p.name for p in out.iterdir()) == ["note_qr.md", "note_qr_001.png", "note_qr_002.png", "note_qr_003.png"]


def test_qrdoc_html(tmp_path):
    import qrdoc
    src = tmp_path / "page.html"
    src.write_text('<p>Смотри <a class="x" href="https://a.example.com/?q=1&amp;r=2">тут</a> и '
                   '<a href="https://b.example.com">там</a>.</p>', encoding="utf-8")
    links = qrdoc.scan(src, tmp_path)["links"]
    assert [(l["url"], l["text"]) for l in links] == [("https://a.example.com/?q=1&r=2", "тут"), ("https://b.example.com", "там")]
    out = tmp_path / "out"; out.mkdir()
    dst, n = qrdoc.apply(src, out, "page", {"https://a.example.com/?q=1&r=2"}, "replace", 3, qrtools.read_style({}), tmp_path / "w")
    html_out = dst.read_text(encoding="utf-8")
    assert n == 1 and html_out.count("data:image/png;base64,") == 1 and ">там</a>" in html_out and ">тут<" not in html_out


def test_qrdoc_endpoints(client):
    import json as _json
    data = make_qr_docx()
    r = client.post("/api/qrdoc/scan", data={"file": (io.BytesIO(data), "Отчёт.docx")}, content_type="multipart/form-data")
    urls = [l["url"] for l in r.get_json()["links"]]
    assert r.status_code == 200 and len(urls) == 3
    r = client.post("/api/qrdoc/apply", data={"file": (io.BytesIO(data), "Отчёт.docx"), "urls": _json.dumps(urls),
                                              "mode": "replace", "size_cm": "2.5"}, content_type="multipart/form-data")
    d = r.get_json()
    assert r.status_code == 200 and d["done"] == 3 and d["single_file"] == "Отчёт_qr.docx"
    got = client.get(f"/api/download/{d['job']}?raw=1")
    assert zipfile.ZipFile(io.BytesIO(got.data)).read("word/document.xml").count(b"<w:drawing>") == 3
    got.close()

    bad = client.post("/api/qrdoc/scan", data={"file": (io.BytesIO(b"x"), "a.pdf")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and "не поддерживается" in bad.get_json()["error"]
    none = client.post("/api/qrdoc/apply", data={"file": (io.BytesIO(data), "a.docx"), "urls": "[]"}, content_type="multipart/form-data")
    assert none.status_code == 400
    broken = client.post("/api/qrdoc/scan", data={"file": (io.BytesIO(b"not zip"), "a.docx")}, content_type="multipart/form-data")
    assert broken.status_code == 400 and "повреждён" in broken.get_json()["error"]


@pytest.mark.skipif(not shutil.which("pandoc"), reason="нет pandoc")
def test_qrdoc_odt_via_docx(tmp_path):
    import qrdoc
    md = tmp_path / "s.md"
    md.write_text("[Ссылка](https://odt.example.com)\n", encoding="utf-8")
    subprocess.run(["pandoc", str(md), "-o", str(tmp_path / "s.odt")], check=True)
    out = tmp_path / "out"; out.mkdir()
    dst, n = qrdoc.apply(tmp_path / "s.odt", out, "s", {"https://odt.example.com"}, "replace", 3, qrtools.read_style({}), tmp_path / "w")
    assert dst.suffix == ".docx" and n == 1


# ---------------------------------------------------------------- загрузчик: отмена и скорость

@pytest.fixture()
def slow_server(media, tmp_path):
    """/big.mp4 отдаётся по ~100 КБ/с, /hang вообще не отвечает (как зависший сайт)."""
    import http.server
    import threading
    big = tmp_path / "big.bin"
    big.write_bytes(os.urandom(4 * 1048576))

    class Slow(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/hang"):
                time.sleep(60)
                return
            data = big.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                for i in range(0, len(data), 8192):
                    self.wfile.write(data[i:i + 8192])
                    time.sleep(0.08)
            except OSError:
                pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _cancel_and_time(client, tid):
    t0 = time.time()
    client.post(f"/api/task/{tid}/cancel")
    d = wait_task(client, tid, timeout=20)
    return d, time.time() - t0


@pytest.mark.parametrize("path, wait_for", [("/big.mp4", "Качаю"), ("/hang/video.mp4", "получаю данные")])
def test_dl_cancel_is_immediate(client, slow_server, path, wait_for):
    r = client.post("/api/dl", data={"urls": slow_server + path, "mode": "video"})
    tid = r.get_json()["task"]
    t0 = time.time()
    while wait_for not in client.get(f"/api/task/{tid}").get_json()["message"] and time.time() - t0 < 20:
        time.sleep(0.2)
    time.sleep(1)
    task = webapp.TASKS.get(tid)
    worker = task.proc
    assert worker is not None and worker.poll() is None            # качает отдельный процесс
    d, took = _cancel_and_time(client, tid)
    assert d["status"] == "cancelled" and took < 3, (d, took)
    assert worker.poll() is not None                              # процесс убит, а не брошен
    assert not any(task.out_dir.iterdir())                        # недокачанные .part убраны


def test_dl_worker_crash_is_reported(client, monkeypatch):
    import downloader
    monkeypatch.setattr(downloader, "worker_cmd", lambda args: [sys.executable, "-c", "import sys; sys.exit('boom')"])
    d = wait_task(client, client.post("/api/dl", data={"urls": "https://example.com/v"}).get_json()["task"])
    assert d["status"] == "error" and "boom" in d["error"]


def test_dl_opts_speed_and_js_runtime():
    y = downloader.build_opts(downloader.read_opts({"mode": "video"}), Path("/tmp"), None)
    assert y["concurrent_fragment_downloads"] == 4 and "deno" in y["js_runtimes"]


def test_engine_deno_install_sets_exec_bit(engine_env, monkeypatch):
    engines, srv_dir, base = engine_env
    exe = "deno.exe" if sys.platform == "win32" else "deno"
    with zipfile.ZipFile(srv_dir / engines._deno_asset(), "w") as z:
        z.writestr(exe, "#!/bin/sh\necho deno 2.0\n")
    monkeypatch.setitem(engines.CATALOG, "deno", {**engines.CATALOG["deno"], "url": base + "/{asset}"})
    path = engines.install("deno")
    assert Path(path, exe).exists() and engines.can_install("deno")
    if sys.platform != "win32":
        assert os.access(Path(path, exe), os.X_OK)


# ---------------------------------------------------------------- cobalt

@pytest.fixture()
def fake_cobalt(media):
    """Поддельный сервер cobalt: /video → tunnel, /carousel → picker, /bad → error, требует ключ."""
    import http.server
    import threading
    seen = {}

    class Cobalt(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                return self._json(200, {"cobalt": {"version": "11.7.1", "services": ["vk", "youtube"]}})
            if self.path.startswith("/file/"):
                data = (media / "a.mp4").read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", 'attachment; filename="pic.mp4"')
                self.end_headers()
                self.wfile.write(data)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.update(body=body, auth=self.headers.get("Authorization"))
            if self.headers.get("Authorization") != "Api-Key secret":
                return self._json(401, {"status": "error", "error": {"code": "error.api.auth.key.missing"}})
            base = f"http://127.0.0.1:{self.server.server_port}"
            if body["url"].endswith("/video"):
                return self._json(200, {"status": "tunnel", "url": base + "/file/1", "filename": "Клип (720p).mp4"})
            if body["url"].endswith("/carousel"):
                return self._json(200, {"status": "picker", "picker": [{"type": "video", "url": base + "/file/2"},
                                                                       {"type": "video", "url": base + "/file/3"}]})
            return self._json(400, {"status": "error", "error": {"code": "error.api.content.video.private"}})

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Cobalt)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/", seen
    srv.shutdown()


def test_cobalt_tunnel_and_options(client, fake_cobalt):
    base, seen = fake_cobalt
    d = wait_task(client, client.post("/api/dl", data={
        "urls": "https://vk.com/video", "cobalt_url": base, "cobalt_key": "secret",
        "quality": "720", "container": "mkv", "compat": "0"}).get_json()["task"])
    assert d["status"] == "done" and d["single_file"] == "Клип (720p).mp4", d
    assert seen["auth"] == "Api-Key secret"
    b = seen["body"]
    assert b["videoQuality"] == "720" and b["youtubeVideoContainer"] == "mkv" and b["youtubeVideoCodec"] == "vp9"


def test_cobalt_picker_and_audio_options(client, fake_cobalt):
    base, seen = fake_cobalt
    d = wait_task(client, client.post("/api/dl", data={
        "urls": "https://instagram.com/carousel", "cobalt_url": base, "cobalt_key": "secret",
        "mode": "audio", "audio_format": "m4a", "abitrate": "192"}).get_json()["task"])
    assert d["status"] == "done" and d["files"] == 2, d
    b = seen["body"]
    assert b["downloadMode"] == "audio" and b["audioFormat"] == "best" and b["audioBitrate"] == "320"


def test_cobalt_error_falls_back_to_ytdlp(client, fake_cobalt, http_media):
    base, _ = fake_cobalt
    d = wait_task(client, client.post("/api/dl", data={
        "urls": f"{http_media}/a.mp4", "cobalt_url": base, "cobalt_key": "secret"}).get_json()["task"])
    assert d["status"] == "done" and d["single_file"].endswith(".mp4")      # yt-dlp подхватил
    d = wait_task(client, client.post("/api/dl", data={
        "urls": "https://example.invalid/x", "cobalt_url": base}).get_json()["task"])
    assert d["status"] == "error", d
    assert d["failed"][0]["error"].startswith("cobalt: серверу cobalt нужен API-ключ; yt-dlp:"), d["failed"]


def test_cobalt_check_endpoint(client, fake_cobalt):
    base, _ = fake_cobalt
    assert client.post("/api/dl/cobalt-check", data={"cobalt_url": base}).get_json() == {"version": "11.7.1", "services": ["vk", "youtube"]}
    assert client.post("/api/dl/cobalt-check", data={"cobalt_url": "ftp://x"}).status_code == 400


def test_cobalt_skipped_for_playlists():
    o = downloader.read_opts({"cobalt_url": "http://c:9000/", "playlist": "1"})
    assert o["cobalt_url"] and o["playlist"]
    assert downloader.read_opts({"cobalt_url": "javascript:alert(1)"})["cobalt_url"] == ""


def test_dl_read_events_windows_newlines_and_partial_tail(tmp_path):
    """Файл событий с \\r\\n (как пишет Windows в текстовом режиме) и недописанной последней строкой."""
    p = tmp_path / "ev.jsonl"
    p.write_bytes(b'{"message": "a"}\r\n{"done": "u"}\r\n\r\n{"progress": 0.5, "mess')
    evs, pos = downloader.read_events(p, 0)
    assert evs == [{"message": "a"}, {"done": "u"}]
    with p.open("ab") as f:
        f.write(b'age": "b"}\n')
    evs, pos2 = downloader.read_events(p, pos)
    assert evs == [{"progress": 0.5, "message": "b"}] and pos2 == p.stat().st_size
    assert downloader.read_events(tmp_path / "none", 7) == ([], 7)


# ---------------------------------------------------------------- свой cobalt в Docker

FAKE_DOCKER = r'''#!/usr/bin/env python3
import os, sys
d = os.path.dirname(os.path.abspath(__file__))
state = os.path.join(d, "state")
a = sys.argv[1:]
open(os.path.join(d, "log"), "a").write(" ".join(a) + "\n")
if a[0] == "info":
    sys.exit(0 if os.path.exists(os.path.join(d, "daemon")) else 1)
if a[:2] == ["compose", "version"]:
    print("Docker Compose version v2"); sys.exit(0)
if a[0] == "ps":
    print(open(state).read() if os.path.exists(state) else ""); sys.exit(0)
if a[0] == "compose" and "pull" in a:
    print("cobalt Pulled"); sys.exit(0)
if a[0] == "compose" and "up" in a:
    assert os.path.exists("docker-compose.yml")
    print("cobalt Pulling"); print("Container universal-cobalt Started")
    open(state, "w").write("running"); sys.exit(0)
if a[0] == "compose" and "down" in a:
    os.remove(state); sys.exit(0)
sys.exit(2)
'''


@pytest.fixture()
def fake_docker(tmp_path, monkeypatch):
    import cobaltlocal
    monkeypatch.setenv("UNIVERSAL_HOME", str(tmp_path / "home"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "docker"
    exe.write_text(FAKE_DOCKER.replace("#!/usr/bin/env python3", "#!" + sys.executable))
    exe.chmod(0o755)
    monkeypatch.setattr(cobaltlocal, "docker_exe", lambda: str(exe))
    monkeypatch.setattr(cobaltlocal, "_desktop_app", lambda: None)
    return cobaltlocal, bindir


@pytest.mark.skipif(sys.platform == "win32", reason="фейковый docker — скрипт с shebang")
def test_cobalt_local_lifecycle(fake_docker, monkeypatch, client):
    cobaltlocal, bindir = fake_docker
    st = client.get("/api/cobalt/local").get_json()
    assert st["docker"] and not st["daemon"] and not st["state"]
    # Docker не запущен, а запустить его некому — понятная ошибка
    d = wait_task(client, client.post("/api/cobalt/local").get_json()["task"])
    assert d["status"] == "error" and "systemctl" in d["error"]

    (bindir / "daemon").touch()
    seen = []
    monkeypatch.setattr(downloader, "cobalt_info", lambda url, key="": seen.append(url) or {"version": "11.5", "services": []})
    d = wait_task(client, client.post("/api/cobalt/local").get_json()["task"])
    assert d["status"] == "done", d
    url = d["result"]["url"]
    assert url.startswith("http://localhost:90") and seen[-1] == url
    port = url.rsplit(":", 1)[1].strip("/")
    yml = (cobaltlocal.folder() / "docker-compose.yml").read_text()
    assert f'"127.0.0.1:{port}:9000/tcp"' in yml and f'API_URL: "http://localhost:{port}/"' in yml
    assert "ghcr.io/imputnet/cobalt" in yml and "cookies" not in yml
    st = client.get("/api/cobalt/local").get_json()
    assert st["state"] == "running" and st["url"] == url

    # повторное нажатие оставляет тот же порт и подхватывает cookies.json
    (cobaltlocal.folder() / "cookies.json").write_text("{}")
    d = wait_task(client, client.post("/api/cobalt/local").get_json()["task"])
    assert d["result"]["url"] == url
    assert "./cookies.json:/cookies.json:ro" in (cobaltlocal.folder() / "docker-compose.yml").read_text()

    st = client.delete("/api/cobalt/local").get_json()
    assert not st["state"]
    assert "compose -p universal-cobalt down" in (bindir / "log").read_text()


def test_cobalt_local_no_docker(monkeypatch, client):
    import cobaltlocal
    monkeypatch.setattr(cobaltlocal, "docker_exe", lambda: None)
    assert client.get("/api/cobalt/local").get_json()["docker"] is False
    d = wait_task(client, client.post("/api/cobalt/local").get_json()["task"])
    assert d["status"] == "error" and "Docker Desktop" in d["error"]


# ---------------------------------------------------------------- сеть и VPN

def test_net_settings(monkeypatch):
    import urllib.request
    rn = downloader.read_net
    assert rn({}) == {"proxy": "auto", "ipv4": True}
    assert rn({"proxy": "127.0.0.1:10809", "ipv4": "0"}) == {"proxy": "http://127.0.0.1:10809", "ipv4": False}
    assert rn({"proxy": "socks5://127.0.0.1:10808"})["proxy"] == "socks5://127.0.0.1:10808"
    assert rn({"proxy": "ftp://x; rm -rf"})["proxy"] == "auto"
    assert downloader.net_opts({"proxy": "none", "ipv4": True}) == {"proxy": "", "source_address": "0.0.0.0"}
    assert downloader.net_opts({"proxy": "http://p:1", "ipv4": False}) == {"proxy": "http://p:1"}
    # Windows отдаёт socks из реестра как socks4, а v2rayN/Clash говорят socks5
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"socks": "socks4://127.0.0.1:10808"})
    assert downloader.system_proxy() == "socks5h://127.0.0.1:10808"
    assert downloader.net_opts({"proxy": "auto", "ipv4": False}) == {"proxy": "socks5h://127.0.0.1:10808"}
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://127.0.0.1:10809"})
    assert downloader.net_opts({"proxy": "auto", "ipv4": False}) == {}   # yt-dlp возьмёт системный сам
    assert [a["proxy"] for a in downloader._net_attempts({"proxy": "auto"})] == ["auto", "none"]
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    assert len(downloader._net_attempts({"proxy": "auto"})) == 1


def test_net_human_errors():
    h = downloader._human_error
    assert "VPN" in h("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies")
    assert "прокси" in h("Unable to connect to proxy: [Errno 111] Connection refused")
    assert "Проверить сеть" in h("<urlopen error timed out>")


@needs_ffmpeg
def test_dl_dead_system_proxy_falls_back_to_direct(client, http_media, monkeypatch):
    """VPN-клиент выключили, а прокси в системе остался: качаем напрямую."""
    dead = "http://127.0.0.1:9"                       # discard-порт, там никого нет
    for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("http_proxy", dead)
    monkeypatch.setenv("https_proxy", dead)
    d = client.post("/api/dl/info", data={"urls": f"{http_media}/a.mp4"}).get_json()
    assert d["items"][0]["title"] == "a", d
    r = client.post("/api/dl", data={"urls": f"{http_media}/a.mp4", "mode": "video"})
    d = wait_task(client, r.get_json()["task"])
    assert d["status"] == "done" and d["single_file"].endswith(".mp4"), d
    # а если прокси задан явно и он мёртвый — понятная ошибка, без тихого обхода
    r = client.post("/api/dl", data={"urls": f"{http_media}/a.mp4", "proxy": dead})
    d = wait_task(client, r.get_json()["task"])
    assert d["status"] == "error" and "прокси" in d["error"], d


def test_dl_netcheck(client, monkeypatch):
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            calls.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def urlopen(self, url):
            if "vk.com" in url:
                raise OSError("<urlopen error timed out>")
            return io.BytesIO(b"x")

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    d = client.post("/api/dl/netcheck", data={"proxy": "none", "ipv4": "1"}).get_json()
    by = {x["name"]: x for x in d["sites"]}
    assert by["YouTube"]["ok"] and not by["VK"]["ok"] and by["VK"]["error"] == "нет ответа"
    assert downloader._short_net_error("<urlopen error Tunnel connection failed: 403 Forbidden>") == "прокси не пустил (403)"
    assert d["proxy"] == "" and d["ipv4"] is True and "ipv6" in d
    assert all(c["proxy"] == "" and c["source_address"] == "0.0.0.0" for c in calls)


def test_cobalt_local_proxy_and_limits(fake_docker, monkeypatch, client):
    """Прокси VPN-клиента уходит в контейнер (иначе cobalt ходит мимо VPN), лимиты сняты."""
    import urllib.request
    cobaltlocal, bindir = fake_docker
    cp = cobaltlocal.container_proxy
    assert cp({"proxy": "none"}) == ""
    assert cp({"proxy": "http://127.0.0.1:10809"}) == "http://host.docker.internal:10809"
    assert cp({"proxy": "socks5://localhost:7890"}) == "http://host.docker.internal:7890"
    assert cp({"proxy": "http://192.168.1.5:3128"}) == "http://192.168.1.5:3128"
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"socks": "socks4://127.0.0.1:10808"})
    assert cp({"proxy": "auto"}) == "http://host.docker.internal:10808"
    yml = cobaltlocal.compose_text(9000, True, "http://host.docker.internal:10809")
    assert 'HTTPS_PROXY: "http://host.docker.internal:10809"' in yml and 'NO_PROXY: "localhost,127.0.0.1,::1"' in yml
    assert 'RATELIMIT_MAX: "10000"' in yml and "host.docker.internal:host-gateway" in yml
    assert yml.index("COOKIE_PATH") < yml.index("volumes:")

    if sys.platform == "win32":
        return
    (bindir / "daemon").touch()
    monkeypatch.setattr(downloader, "cobalt_info", lambda url, key="", net=None: {"version": "11.7", "services": []})
    d = wait_task(client, client.post("/api/cobalt/local", data={"proxy": "127.0.0.1:10809"}).get_json()["task"])
    assert d["status"] == "done", d
    assert 'HTTP_PROXY: "http://host.docker.internal:10809"' in (cobaltlocal.folder() / "docker-compose.yml").read_text()
    assert client.get("/api/cobalt/local").get_json()["proxy"] == "http://host.docker.internal:10809"


def test_cobalt_errors_explained(monkeypatch, tmp_path):
    o = downloader.read_opts({"cobalt_url": "http://localhost:9000/"})
    for code, part in (("error.api.rate_exceeded", "ограничил число запросов"),
                       ("error.api.youtube.no_session_tokens", "сервер сессий"),
                       ("error.api.fetch.fail", "Перезапустить и обновить сервер"),   # свой сервер мимо VPN
                       ("error.api.something.new", "ошибка something.new")):
        reply = json.dumps({"status": "error", "error": {"code": code}}).encode()
        monkeypatch.setattr(downloader, "_urlopen", lambda req, timeout, net=None, r=reply: io.BytesIO(r))
        with pytest.raises(downloader.CobaltFail, match=part):
            downloader._cobalt_download("https://x.test/v", o, tmp_path, lambda e: None, 0, 1)
