"""
Скачивание видео и музыки с YouTube, TikTok, VK, Rutube, Instagram, X, Twitch,
SoundCloud и ещё ~1800 сайтов через yt-dlp.

yt-dlp часто обновляется, потому что сайты меняются. Если какой-то сайт
перестал качаться, первым делом: pip install -U yt-dlp
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

if __name__ == "__main__" and "--worker" in sys.argv:
    # отдельный процесс скачивания: подключаем свежий yt-dlp до его импорта
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import engines
    engines.activate()

from tasks import Cancelled, Task, kill_tree

try:
    import yt_dlp  # type: ignore
    from yt_dlp.utils import DownloadCancelled, DownloadError  # type: ignore
except ImportError:  # интерфейс покажет баннер
    yt_dlp = None
    DownloadCancelled = DownloadError = Exception

QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360"]
CONTAINERS = ["mp4", "mkv", "webm"]
AUDIO_FORMATS = ["mp3", "m4a", "opus", "flac", "wav"]
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


class DownloadFail(Exception):
    pass


def available() -> bool:
    return yt_dlp is not None


def version() -> str:
    return yt_dlp.version.__version__ if yt_dlp else ""


def extract_urls(text: str) -> list[str]:
    """Достаёт ссылки из любого текста (по одной в строке или вперемешку), без дублей."""
    return list(dict.fromkeys(u.rstrip(".,;)") for u in URL_RE.findall(text or "")))


class _Quiet:
    """Логгер для yt-dlp: молчит, но запоминает последнюю ошибку."""

    def __init__(self):
        self.last_error = ""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        self.last_error = re.sub(r"\x1b\[[0-9;]*m", "", str(msg)).replace("ERROR: ", "")


def _human_error(msg: str) -> str:
    low = msg.lower()
    if "unsupported url" in low:
        return "Этот сайт не поддерживается (или ссылка не на видео)"
    if "private" in low or "login" in low or "sign in" in low or "cookies" in low:
        return "Видео закрыто или нужен вход в аккаунт. Добавь cookies.txt в настройках"
    if "not available" in low or "unavailable" in low:
        return "Видео недоступно (удалено, закрыто или заблокировано в регионе)"
    if "http error 429" in low or "too many requests" in low:
        return "Сайт ограничил частоту запросов (429). Подожди немного или добавь cookies.txt"
    if "urlopen error" in low or "tunnel connection failed" in low or "timed out" in low \
            or "name or service not known" in low or "getaddrinfo failed" in low:
        return "Нет связи с сайтом (интернет, прокси или блокировка провайдера)"
    if "requested format is not available" in low:
        return "Такого качества нет. Выбери «Лучшее»"
    return msg.strip()[:300] or "yt-dlp не смог скачать"


def _base_opts(cookiefile: Path | None) -> dict:
    o = {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "logger": _Quiet(), "socket_timeout": 30, "retries": 5, "fragment_retries": 5,
        "windowsfilenames": True, "trim_file_name": 150,
    }
    if cookiefile:
        o["cookiefile"] = str(cookiefile)
    if shutil.which("ffmpeg"):
        o["ffmpeg_location"] = str(Path(shutil.which("ffmpeg")).parent)
    return o


def probe_url(url: str, cookiefile: Path | None = None) -> dict:
    """Короткая информация для предпросмотра: название, автор, длительность, обложка."""
    if not yt_dlp:
        raise DownloadFail("yt-dlp не установлен: pip install yt-dlp")
    opts = _base_opts(cookiefile) | {"skip_download": True, "extract_flat": "in_playlist",
                                     "playlist_items": "1-50"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        raise DownloadFail(_human_error(opts["logger"].last_error or str(e)))
    if not info:
        raise DownloadFail("Ничего не нашлось по ссылке")
    is_list = info.get("_type") == "playlist"
    heights = sorted({f.get("height") for f in info.get("formats") or [] if f.get("height")}, reverse=True)
    thumb = info.get("thumbnail") or next((t.get("url") for t in reversed(info.get("thumbnails") or [])
                                           if t.get("url")), "")
    return {
        "url": url,
        "title": info.get("title") or info.get("id") or url,
        "uploader": info.get("uploader") or info.get("channel") or info.get("playlist_uploader") or "",
        "duration": info.get("duration") or 0,
        "thumbnail": thumb,
        "site": info.get("extractor_key") or info.get("extractor") or "",
        "playlist": is_list,
        "count": len(info.get("entries") or []) if is_list else 1,
        "heights": heights[:8],
    }


def build_opts(o: dict, out_dir: Path, cookiefile: Path | None) -> dict:
    ydl = _base_opts(cookiefile)
    # видео кусками (HLS/DASH: VK, Rutube, YouTube) качаем в 4 потока, а не по одному
    ydl["concurrent_fragment_downloads"] = 4
    # JavaScript-движок для YouTube: без него сайт режет скорость и прячет форматы
    ydl["js_runtimes"] = {"deno": {}, "node": {}, "bun": {}}
    ydl["outtmpl"] = {"default": str(out_dir / "%(title).120B [%(id)s].%(ext)s")}
    ydl["noplaylist"] = not o["playlist"]
    ydl["overwrites"] = True
    pps: list[dict] = []
    if o["mode"] == "audio":
        ydl["format"] = "bestaudio/best"
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": o["audio_format"]}
        if o["audio_format"] in ("mp3", "m4a", "opus") and o["abitrate"] != "best":
            pp["preferredquality"] = o["abitrate"]
        else:
            pp["preferredquality"] = "0"
        pps.append(pp)
    else:
        h = o["quality"]
        cap = f"[height<={h}]" if h != "best" else ""
        ydl["format"] = f"bv*{cap}+ba/b{cap}/bv*+ba/b"
        sort = []
        if h != "best":
            sort.append(f"res:{h}")
        if o["container"] == "mp4" and o["compat"]:
            sort += ["vcodec:h264", "acodec:aac"]   # откроется везде, даже в старом телевизоре
        elif o["container"] == "mp4":
            sort += ["ext:mp4:m4a"]
        if sort:
            ydl["format_sort"] = sort
        ydl["merge_output_format"] = o["container"]
        if o["subs"]:
            ydl.update({"writesubtitles": True, "writeautomaticsub": False,
                        "subtitleslangs": ["ru.*", "en.*", "uk.*"]})
            pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
    if o["metadata"]:
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
    if o["thumbnail"] and not (o["mode"] == "audio" and o["audio_format"] in ("wav",)):
        ydl["writethumbnail"] = True
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    ydl["postprocessors"] = pps
    return ydl


def read_opts(form) -> dict:
    o = {
        "mode": form.get("mode", "video"),
        "quality": form.get("quality", "best"),
        "container": form.get("container", "mp4"),
        "audio_format": form.get("audio_format", "mp3"),
        "abitrate": form.get("abitrate", "best"),
        "playlist": form.get("playlist") == "1",
        "subs": form.get("subs") == "1",
        "thumbnail": form.get("thumbnail", "1") == "1",
        "metadata": form.get("metadata", "1") == "1",
        "compat": form.get("compat", "1") == "1",
    }
    if o["mode"] not in ("video", "audio"):
        o["mode"] = "video"
    if o["quality"] not in QUALITIES:
        o["quality"] = "best"
    if o["container"] not in CONTAINERS:
        o["container"] = "mp4"
    if o["audio_format"] not in AUDIO_FORMATS:
        o["audio_format"] = "mp3"
    if o["abitrate"] not in ("best", "320", "256", "192", "128", "96"):
        o["abitrate"] = "best"
    return o


def _download_all(urls: list[str], o: dict, out: Path, cookiefile: Path | None, emit) -> None:
    """Основной цикл; emit(dict) сообщает прогресс. Выполняется в отдельном процессе."""
    n = len(urls)
    state = {"i": 0, "title": ""}

    def hook(d):
        i = state["i"]
        info = d.get("info_dict") or {}
        title = info.get("title") or state["title"]
        state["title"] = title
        idx = info.get("playlist_index")
        pl = f" ({idx}/{info.get('n_entries')})" if idx and info.get("n_entries") else ""
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = (d.get("downloaded_bytes") or 0) / total if total else 0
            speed = d.get("speed") or 0
            sp = f" · {speed / 1048576:.1f} МБ/с" if speed else ""
            emit({"progress": (i + frac * 0.95) / n, "message": f"Качаю{pl}: {title}{sp}"})
        elif d["status"] == "finished":
            emit({"progress": (i + 0.95) / n, "message": f"Обрабатываю{pl}: {title}"})

    def pp_hook(d):
        if d.get("status") == "started":
            name = {"Merger": "Склеиваю видео и звук", "ExtractAudio": "Достаю звук",
                    "EmbedThumbnail": "Вставляю обложку", "Metadata": "Записываю теги"}.get(
                (d.get("postprocessor") or "").replace("FFmpeg", ""), "Обрабатываю")
            emit({"message": f"{name}: {state['title']}"})

    ydl_opts = build_opts(o, out, cookiefile)
    ydl_opts["progress_hooks"] = [hook]
    ydl_opts["postprocessor_hooks"] = [pp_hook]
    for i, url in enumerate(urls):
        state["i"], state["title"] = i, url
        emit({"progress": i / n, "message": f"Ссылка {i + 1} из {n}: получаю данные…"})
        logger = _Quiet()
        ydl_opts["logger"] = logger
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            emit({"done": url})
        except DownloadError as e:
            emit({"failed": url, "error": _human_error(logger.last_error or str(e))})
        except Exception as e:  # одна ссылка не должна валить всю пачку
            emit({"failed": url, "error": _human_error(str(e))})


def _cleanup(out: Path, cookiefile: Path | None) -> None:
    """Хвосты: недокачанные куски (.part, фрагменты), а также обложки и субтитры, уже вшитые в файл."""
    side = (".vtt", ".srt", ".ass", ".webp", ".jpg", ".png")
    junk = (".part", ".ytdl", ".temp")
    is_junk = lambda p: p.suffix.lower() in junk or re.search(r"\.part-Frag\d+", p.name)
    files = [q for q in out.iterdir() if q.is_file()] if out.is_dir() else []
    media = [q for q in files if q.suffix.lower() not in side and not is_junk(q)]
    for p in files:
        if is_junk(p) or (p.suffix.lower() in side and any(p.name.startswith(m.stem + ".") for m in media)):
            for _ in range(10):                  # Windows отпускает файл не сразу после kill
                try:
                    p.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.2)
    if cookiefile:
        cookiefile.unlink(missing_ok=True)


def worker_cmd(args_file: Path) -> list[str]:
    """Как запустить отдельный процесс скачивания: из exe самим exe, иначе этим же Python."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--dl-worker", str(args_file)]
    return [sys.executable, str(Path(__file__).resolve()), "--worker", str(args_file)]


def worker_main(args_file: str) -> None:
    a = json.loads(Path(args_file).read_text(encoding="utf-8"))
    events = Path(a["events"])

    def emit(ev):
        with events.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=True) + "\n")

    _download_all(a["urls"], a["opts"], Path(a["out"]), Path(a["cookie"]) if a["cookie"] else None, emit)
    emit({"finished": True})


def run_download_task(task: Task, urls: list[str], o: dict, cookiefile: Path | None) -> None:
    """Скачивание в отдельном процессе: «Отмена» убивает его сразу вместе с ffmpeg,
    даже если yt-dlp завис на разборе страницы или склейке."""
    if not yt_dlp:
        raise DownloadFail("yt-dlp не установлен: pip install yt-dlp")
    out = task.out_dir
    task.total = len(urls)
    events = task.job_dir / "dl_events.jsonl"
    args = task.job_dir / "dl_args.json"
    args.write_text(json.dumps({"urls": urls, "opts": o, "out": str(out), "events": str(events),
                                "cookie": str(cookiefile) if cookiefile else ""}), encoding="utf-8")
    log = (task.job_dir / "dl_worker.log").open("w", encoding="utf-8")
    extra = {} if sys.platform == "win32" else {"start_new_session": True}   # своя группа: убить всё дерево
    proc = subprocess.Popen(worker_cmd(args), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            cwd=str(task.job_dir), **extra)
    task.proc = proc
    pos, finished = 0, False
    try:
        while True:
            alive = proc.poll() is None
            if task.cancel_event.is_set():
                kill_tree(proc)
                raise Cancelled()
            if events.exists():
                with events.open("r", encoding="utf-8") as f:
                    f.seek(pos)
                    chunk = f.read()
                    # читаем только целые строки, хвост дочитаем в следующий раз
                    cut = chunk.rfind("\n") + 1
                    pos += cut                     # в файле только ASCII (ensure_ascii), байты = символы
                for line in chunk[:cut].splitlines():
                    ev = json.loads(line)
                    if "progress" in ev or "message" in ev:
                        task.update(ev.get("progress"), ev.get("message"))
                    if "done" in ev:
                        task.done += 1
                    if "failed" in ev:
                        task.failed.append({"name": ev["failed"], "error": ev["error"]})
                    finished = finished or ev.get("finished", False)
            if not alive:
                break
            time.sleep(0.3)
    finally:
        task.proc = None
        log.close()
        if task.cancel_event.is_set():
            _cleanup(out, cookiefile)
    if not finished and not task.cancel_event.is_set():
        tail = (task.job_dir / "dl_worker.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        task.failed.append({"name": "загрузчик", "error": "Процесс скачивания упал: " + " / ".join(tail)[-300:]})
    _cleanup(out, cookiefile)
    if not task.done and task.failed:
        raise DownloadFail(task.failed[0]["error"])


if __name__ == "__main__" and "--worker" in sys.argv:
    worker_main(sys.argv[sys.argv.index("--worker") + 1])
