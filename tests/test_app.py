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
    d = r.get_json()
    assert d["done"] == 1 and d["single_file"] == "tone.flac"


def test_index_renders(client):
    html = client.get("/").data.decode()
    for mode in ("image", "edit", "dl", "qr", "topdf"):
        assert f'value="{mode}"' in html


def test_password(monkeypatch, client):
    monkeypatch.setattr(webapp, "APP_PASSWORD", "secret")
    assert client.get("/api/engines").status_code == 401
    import base64
    ok = {"Authorization": "Basic " + base64.b64encode(b"me:secret").decode()}
    assert client.get("/api/engines", headers=ok).status_code == 200
