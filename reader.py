"""
Читалка: TextFold (https://github.com/chenxiachan/textfold, Apache-2.0), вшитый в Universal.

Любой текст сворачивается по уровням: от карты на один экран до каждого слова. Уровни пишет языковая модель,
которую человек подключает сам прямо в читалке (Claude Code, Codex, свой ключ API, локальная Ollama и т. д.):
сам Universal ничего никуда не отправляет, а читалка не включает облачную модель без выбора человека.

TextFold лежит в third_party/textfold без изменений. Его сервер запускается внутри Universal отдельным потоком
на 127.0.0.1 (постоянный порт, чтобы настройки читалки в браузере не терялись), его данные — в
<папка данных Universal>/textfold. Работает там, где читалку можно открыть: на компьютере (приложение или
`python app.py` на этом же компьютере). Не работает на телефоне, в Docker и для телефона через сервер: туда
127.0.0.1 не достаёт.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import engines

ROOT = Path(__file__).resolve().parent
THIRD = ROOT / "third_party" / "textfold"
PREFERRED_PORT = 3057           # не 3017: так читалка не мешает настоящему TextFold, если он тоже запущен
# модуль -> как называется пакет в pip
NEEDS = {"markdown_it": "markdown-it-py", "mdit_py_plugins": "mdit-py-plugins", "latex2mathml": "latex2mathml",
         "trafilatura": "trafilatura>=2.0", "lxml": "lxml", "lxml_html_clean": "lxml_html_clean",
         "bs4": "beautifulsoup4", "mammoth": "mammoth", "openpyxl": "openpyxl", "PIL": "Pillow"}
TEXT_EXT = {".md", ".markdown", ".mdown", ".txt"}

_lock = threading.Lock()
_port = 0
_error = ""
_stage = ""
# к самой читалке ходим напрямую: системный прокси (корпоративный, антивирусный) к 127.0.0.1 отношения не имеет
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
START_TIMEOUT = 90                # первый запуск тяжёлых библиотек в собранном приложении бывает долгим


class ReaderError(Exception):
    pass


def data_dir() -> Path:
    return engines.root() / "textfold"


def _paths() -> None:
    """Папка с пакетом textfold в путь импорта (в собранном приложении она уже там)."""
    p = str(THIRD)
    if THIRD.is_dir() and p not in sys.path:
        sys.path.insert(0, p)


def prepare() -> None:
    """Путь к пакету textfold и папка его данных: делать до первого импорта textfold (store читает её сразу)."""
    os.environ.setdefault("TEXTFOLD_DATA", str(data_dir()))
    _paths()


def missing() -> list[str]:
    """Каких библиотек не хватает для читалки (пакеты Linux их берут из системы или из pip)."""
    _paths()
    out = []
    if not (THIRD / "textfold").is_dir() and importlib.util.find_spec("textfold") is None:
        out.append("textfold")
    out += [m for m in NEEDS if importlib.util.find_spec(m) is None]
    return out


def pip_command() -> str:
    """Что набрать, чтобы поставить недостающее (пакеты Linux, запуск из исходников)."""
    pkgs = [NEEDS[m] for m in missing() if m in NEEDS]
    return ("pip install --user " + " ".join(f'"{p}"' if ">" in p else p for p in pkgs)) if pkgs else ""


def available() -> bool:
    if os.environ.get("UNIVERSAL_ANDROID"):
        return False
    return not missing()


def reachable() -> bool:
    """Браузер человека на том же компьютере, что и Universal: тогда 127.0.0.1 читалки ему доступен."""
    if os.environ.get("UNIVERSAL_DOCKER") or os.environ.get("UNIVERSAL_ANDROID"):
        return False
    if os.environ.get("UNIVERSAL_DESKTOP"):
        return True
    return (os.environ.get("HOST") or "127.0.0.1") in ("127.0.0.1", "localhost", "::1")


def running_url() -> str:
    return f"http://127.0.0.1:{_port}" if _port else ""


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port() -> int:
    if _free(PREFERRED_PORT):
        return PREFERRED_PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(port: int) -> None:
    global _error, _stage
    try:
        _stage = "загрузка библиотек"
        from textfold import server
        _stage = "запуск сервера"
        server.serve(port)
    except Exception as e:           # поток читалки не должен ронять приложение
        _error = f"{e.__class__.__name__}: {str(e)[:200]}"


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), 0.5).close()
        return True
    except OSError:
        return False


def start() -> str:
    """Запускает читалку (один раз за запуск приложения), возвращает её адрес."""
    global _port, _error
    with _lock:
        if _port:
            return running_url()
        miss = missing()
        if miss or os.environ.get("UNIVERSAL_ANDROID"):
            raise ReaderError("Читалка не может запуститься: не хватает " + ", ".join(miss or ["поддержки на этой системе"]))
        data_dir().mkdir(parents=True, exist_ok=True)
        prepare()
        _error = ""
        port = _pick_port()
        th = threading.Thread(target=_serve, args=(port,), daemon=True, name="universal-reader")
        th.start()
        t0 = time.time()
        last = ""
        while time.time() - t0 < START_TIMEOUT:
            if _error:
                raise ReaderError("Читалка не запустилась: " + _error)
            try:
                _local.open(f"http://127.0.0.1:{port}/api/langs", timeout=2).close()
                _port = port
                return running_url()
            except (urllib.error.URLError, OSError) as e:
                last = f"{e.__class__.__name__}: {str(getattr(e, 'reason', e))[:80]}"
                time.sleep(0.3)
        raise ReaderError(f"Читалка не ответила за {START_TIMEOUT} секунд (этап: {_stage or 'не начат'}, "
                          f"порт {port} {'открыт' if _listening(port) else 'закрыт'}, поток {'жив' if th.is_alive() else 'остановился'}, "
                          f"последний ответ: {last or 'нет'})")


def _post(path: str, body: dict, timeout: float = 60) -> dict:
    req = urllib.request.Request(start() + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with _local.open(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error") or e.reason
        except (ValueError, OSError):
            msg = e.reason
        raise ReaderError(f"Читалка отказалась: {msg}")


def open_url(paper: str) -> str:
    return f"{running_url() or start()}/p/{paper}"


def import_text(text: str, name: str = "text.md") -> dict:
    """Текст в библиотеку читалки: -> {"paper": id, "title": ..., "url": адрес читалки}."""
    if not text.strip():
        raise ReaderError("Пустой текст: читать нечего")
    name = name if Path(name).suffix.lower() in TEXT_EXT else (Path(name).stem or "text") + ".md"
    r = _post("/api/import", {"text": text, "name": name[:200]})
    r["url"] = open_url(r["paper"])
    return r


def import_file(path: Path, name: str = "") -> dict:
    """Файл в библиотеку читалки: текст и Markdown как есть; Word, Excel, EPUB, HTML и PDF с текстом читает
    сама читалка; сканам и картинкам нужен PDF-движок (MinerU)."""
    name = name or path.name
    ext = path.suffix.lower()
    if ext in TEXT_EXT:
        return import_text(path.read_text(encoding="utf-8", errors="replace"), name)
    start()
    from textfold import engine, office, pdfdoc, pdftext, store
    kind = engine.kind_of(name)
    if not kind:
        raise ReaderError(f"Файлы .{ext.lstrip('.')} читалка не открывает: попробуй PDF, Word, Excel, EPUB, HTML или текст")
    data = path.read_bytes()
    pid = pdfdoc.pid_of(data, kind)
    if not (store.pdir(pid) / "meta.json").exists():
        if kind != "pdf" and office.reads(kind):
            office.save(pid, path, name)
        elif kind == "pdf" and pdftext.save(pid, path, name):
            pass
        else:
            raise ReaderError("Этому файлу (скан, картинка или сложный PDF) нужен PDF-движок MinerU: "
                              "перетащи его прямо в читалку, она сама предложит поставить движок")
    return {"paper": pid, "title": name, "url": open_url(pid)}


def status() -> dict:
    return {"available": available(), "reachable": reachable(), "missing": missing(), "pip": pip_command(),
            "running": bool(_port), "url": running_url() or f"http://127.0.0.1:{PREFERRED_PORT}",
            "error": _error}
