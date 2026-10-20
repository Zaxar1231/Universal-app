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
    if "not a bot" in low or "confirm you" in low:
        return ("YouTube не пускает с IP этого VPN («подтвердите, что вы не бот»). Выбери другой сервер VPN, "
                "исключи Universal из VPN (раздельное туннелирование) или добавь cookies.txt")
    if "unable to connect to proxy" in low or "proxyerror" in low or "proxy" in low and "refused" in low:
        return ("Не подключиться к прокси. VPN-клиент выключен, а прокси в системе остался, или порт другой: "
                "в настройках «Сеть и VPN» выбери «Без прокси» или впиши верный")
    if "certificate verify failed" in low or "certificate_verify_failed" in low:
        return "Сертификат сайта не прошёл проверку: VPN или антивирус подменяет HTTPS. Выключи у них проверку HTTPS"
    if "unsupported url" in low:
        return "Этот сайт не поддерживается (или ссылка не на видео)"
    if "private" in low or "login" in low or "sign in" in low or "cookies" in low:
        return "Видео закрыто или нужен вход в аккаунт. Добавь cookies.txt в настройках"
    if "not available" in low or "unavailable" in low:
        return "Видео недоступно (удалено, закрыто или заблокировано в регионе)"
    if "http error 429" in low or "too many requests" in low:
        return "Сайт ограничил частоту запросов (429). Подожди немного или добавь cookies.txt"
    if _is_net_error(low):
        return "Нет связи с сайтом (интернет, VPN, прокси или блокировка провайдера). Нажми «Проверить сеть» в настройках"
    if "requested format is not available" in low:
        return "Такого качества нет. Выбери «Лучшее»"
    return msg.strip()[:300] or "yt-dlp не смог скачать"


NET_ERRORS = ("urlopen error", "tunnel connection failed", "timed out", "name or service not known",
              "getaddrinfo failed", "nodename nor servname", "connection reset", "connection refused",
              "network is unreachable", "no route to host", "remote end closed", "connection aborted",
              "transporterror", "unable to download webpage", "eof occurred", "ssl: ", "winerror 10060",
              "winerror 10054", "winerror 10061")


def _is_net_error(low: str) -> bool:
    return any(k in low for k in NET_ERRORS)


# ----------------------------------------------------------------------------
# сеть: VPN-клиенты (v2rayN, Clash, Hiddify, Happ…) прописывают прокси в систему,
# а VPN в режиме TUN часто ломает IPv6, и тогда каждое соединение висит по 30 с
# ----------------------------------------------------------------------------

PROXY_RE = re.compile(r"^(https?|socks5h?|socks4a?)://[^\s/]+(:\d+)?/?$", re.I)


def system_proxy() -> str:
    """Прокси из настроек системы (реестр Windows, macOS, переменные окружения)."""
    v = _raw_system_proxy().strip()
    if not v:
        return ""
    if "://" not in v:
        v = "http://" + v
    # Windows подписывает socks-прокси как socks4, а VPN-клиенты почти всегда говорят socks5
    return re.sub(r"^socks4a?://", "socks5h://", v, flags=re.I)


def effective_proxy(o: dict | None) -> str:
    p = (o or {}).get("proxy", "auto")
    if p == "none":
        return ""
    if p and p != "auto":
        return p
    return system_proxy()


def _raw_system_proxy() -> str:
    import urllib.request
    try:
        found = urllib.request.getproxies()
    except Exception:
        return ""
    return next((found[k] for k in ("https", "http", "all", "socks") if found.get(k)), "")


def net_opts(o: dict | None) -> dict:
    """Настройки сети для yt-dlp. «Как в системе» оставляем yt-dlp (он учтёт и исключения
    вроде localhost), явно передаём только свой адрес, «напрямую» и исправленный socks4."""
    o = o or {}
    n = {}
    p = o.get("proxy", "auto")
    if p == "none":
        n["proxy"] = ""                          # "" = напрямую
    elif p != "auto":
        n["proxy"] = p
    elif re.match(r"^socks4a?://", _raw_system_proxy(), re.I):
        n["proxy"] = system_proxy()
    if o.get("ipv4", True):
        n["source_address"] = "0.0.0.0"          # только IPv4: сломанный VPN-ом IPv6 не тормозит
    return n


def _base_opts(cookiefile: Path | None, net: dict | None = None) -> dict:
    o = {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "logger": _Quiet(), "socket_timeout": 20, "retries": 5, "fragment_retries": 5,
        "windowsfilenames": True, "trim_file_name": 150,
    }
    o.update(net_opts(net))
    if cookiefile:
        o["cookiefile"] = str(cookiefile)
    if shutil.which("ffmpeg"):
        o["ffmpeg_location"] = str(Path(shutil.which("ffmpeg")).parent)
    return o


def probe_url(url: str, cookiefile: Path | None = None, net: dict | None = None) -> dict:
    """Короткая информация для предпросмотра: название, автор, длительность, обложка."""
    if not yt_dlp:
        raise DownloadFail("yt-dlp не установлен: pip install yt-dlp")
    info = None
    attempts = _net_attempts(net)
    for k, attempt in enumerate(attempts):
        opts = _base_opts(cookiefile, attempt) | {"skip_download": True, "extract_flat": "in_playlist",
                                                  "playlist_items": "1-50"}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            break
        except DownloadError as e:
            err = opts["logger"].last_error or str(e)
            if k + 1 == len(attempts) or not _is_net_error(err.lower()):
                raise DownloadFail(_human_error(err))
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


def _net_attempts(o: dict | None) -> list[dict]:
    """Как пробовать сеть: как настроено, а если прокси взят из системы и не сработал — ещё напрямую."""
    o = dict(o or {})
    tries = [o]
    if o.get("proxy", "auto") == "auto" and system_proxy():
        tries.append(o | {"proxy": "none"})
    return tries


def build_opts(o: dict, out_dir: Path, cookiefile: Path | None) -> dict:
    ydl = _base_opts(cookiefile, o)
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
    o.update(read_net(form))
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


def read_net(form) -> dict:
    """Прокси: auto (как в системе) | none | свой адрес; плюс «только IPv4»."""
    p = (form.get("proxy") or os.environ.get("UNIVERSAL_PROXY") or "auto").strip()
    if p not in ("auto", "none"):
        if "://" not in p:
            p = "http://" + p                    # 127.0.0.1:10809 → http://127.0.0.1:10809
        if not PROXY_RE.match(p):
            p = "auto"
    return {"proxy": p, "ipv4": form.get("ipv4", "1") == "1"}


def _short_net_error(msg: str) -> str:
    low = msg.lower()
    m = re.search(r"tunnel connection failed: (\d+)", low)
    if m:
        return f"прокси не пустил ({m.group(1)})"
    for keys, text in ((("timed out", "10060"), "нет ответа"),
                       (("name or service not known", "getaddrinfo", "nodename nor servname", "11001"), "адрес не найден (DNS)"),
                       (("refused", "10061"), "соединение отклонено"),
                       (("reset", "10054", "remote end closed", "eof occurred"), "соединение оборвали (похоже на блокировку)"),
                       (("certificate",), "подменён сертификат HTTPS"),
                       (("unreachable", "no route"), "сеть недоступна")):
        if any(k in low for k in keys):
            return text
    return msg[:160]


NET_TARGETS = [("YouTube", "https://www.youtube.com/generate_204"), ("VK", "https://vk.com/"),
               ("Rutube", "https://rutube.ru/"), ("TikTok", "https://www.tiktok.com/")]


def net_check(net: dict) -> dict:
    """Кнопка «Проверить сеть»: открываются ли сайты с текущими настройками и работает ли IPv6."""
    import socket
    from concurrent.futures import ThreadPoolExecutor

    def site(item):
        name, url = item
        t0 = time.time()
        try:
            with yt_dlp.YoutubeDL(_base_opts(None, net) | {"socket_timeout": 10}) as y:
                y.urlopen(url).read(1)
            return {"name": name, "ok": True, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:
            return {"name": name, "ok": False, "error": _short_net_error(str(e))}

    def ipv6():
        try:
            addr = socket.getaddrinfo("www.youtube.com", 443, socket.AF_INET6, socket.SOCK_STREAM)[0][4]
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
                sock.settimeout(4)
                sock.connect(addr)
            return True
        except Exception:
            return False

    if not yt_dlp:
        raise DownloadFail("yt-dlp не установлен")
    with ThreadPoolExecutor(max_workers=len(NET_TARGETS) + 1) as pool:
        v6 = pool.submit(ipv6)
        sites = list(pool.map(site, NET_TARGETS))
    return {"system_proxy": system_proxy(), "proxy": effective_proxy(net), "ipv4": net.get("ipv4", True),
            "ipv6": v6.result(), "sites": sites}



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
        attempts = _net_attempts(o)
        for k, attempt in enumerate(attempts):
            logger = _Quiet()
            ydl_opts["logger"] = logger
            ydl_opts.update(net_opts(attempt))
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                emit({"done": url})
                break
            except Exception as e:  # одна ссылка не должна валить всю пачку
                err = (logger.last_error or str(e)) if isinstance(e, DownloadError) else str(e)
                if k + 1 < len(attempts) and _is_net_error(err.lower()):
                    emit({"message": f"Через системный прокси не вышло, пробую напрямую: {state['title']}"})
                    continue
                emit({"failed": url, "error": _human_error(err)})
                break


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
        # двоичный режим: в текстовом Windows пишет \r\n, и позиция чтения у родителя уезжает
        with events.open("ab") as f:
            f.write(json.dumps(ev, ensure_ascii=True).encode("ascii") + b"\n")

    _download_all(a["urls"], a["opts"], Path(a["out"]), Path(a["cookie"]) if a["cookie"] else None, emit)
    emit({"finished": True})


def read_events(path: Path, pos: int) -> tuple[list[dict], int]:
    """Новые целые строки событий начиная с байта pos; недописанный хвост дочитаем в следующий раз."""
    if not path.exists():
        return [], pos
    with path.open("rb") as f:
        f.seek(pos)
        chunk = f.read()
    cut = chunk.rfind(b"\n") + 1
    events = []
    for line in chunk[:cut].splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue                          # битая строка не должна ронять всё скачивание
    return events, pos + cut


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
            evs, pos = read_events(events, pos)
            for ev in evs:
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
