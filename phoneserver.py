"""
Сервер для телефона: та же программа открывается в домашнюю сеть одной кнопкой.

Окно приложения работает на 127.0.0.1 (снаружи не видно). Когда пользователь
включает «Сервер для телефона», рядом поднимается второй сервер на 0.0.0.0
с тем же Flask-приложением и общими задачами. Все запросы через него требуют
пароль (HTTP Basic, логин любой), пароль придумывается сам. Настройка
запоминается: при следующем запуске сервер включится снова.

Телефон получает адрес и пароль ссылкой universal://connect?... — её же
содержит QR-код в блоке подключения.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from pathlib import Path
from urllib.parse import quote

import engines

PORTS = range(5000, 5021)
MARK = "universal.phone"           # ключ в WSGI environ: запрос пришёл через сервер для телефона
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # без 0/o и 1/l: пароль диктуют и перепечатывают

_lock = threading.Lock()
_server = None
_thread: threading.Thread | None = None
_error = ""
_password = ""                     # копия из phone.json, чтобы не читать файл на каждый запрос


def _file() -> Path:
    return engines.root() / "phone.json"


def settings() -> dict:
    try:
        s = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s.setdefault("enabled", False)
    s.setdefault("port", 0)
    s.setdefault("password", "")
    return s


def _save(s: dict) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def new_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def password() -> str:
    """Пароль для запросов через сервер для телефона."""
    return _password


def in_docker() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("UNIVERSAL_DOCKER"))


def addresses() -> list[str]:
    """IPv4 этого компьютера в локальной сети. Первым идёт тот, через который ходит интернет."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))        # пакет не уходит: так система лишь выбирает маршрут
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip.startswith(("127.", "169.254.", "0.")) or ip in out:
            continue
        out.append(ip)
    return out


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


class _Marked:
    """WSGI-обёртка: помечает запросы, пришедшие через сервер для телефона."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        environ[MARK] = True
        return self.app(environ, start_response)


def running() -> bool:
    return _server is not None


def start(app) -> None:
    """Включает сервер (и запоминает это). Порт по возможности прежний, чтобы телефон не перенастраивать."""
    global _server, _thread, _error, _password
    from werkzeug.serving import make_server
    with _lock:
        s = settings()
        if not s["password"]:
            s["password"] = new_password()
        if _server is None:
            ports = ([s["port"]] if s["port"] else []) + [p for p in PORTS if p != s["port"]]
            port = next((p for p in ports if _free(p)), None)
            if port is None:
                _error = f"порты {PORTS.start}–{PORTS.stop - 1} заняты"
                raise RuntimeError(_error)
            try:
                _server = make_server("0.0.0.0", port, _Marked(app), threaded=True)
            except OSError as e:
                _error = str(e)
                raise RuntimeError(f"не удалось открыть порт {port}: {e}")
            s["port"] = port
            _thread = threading.Thread(target=_server.serve_forever, daemon=True, name="phone-server")
            _thread.start()
        _error = ""
        _password = s["password"]
        s["enabled"] = True
        _save(s)


def stop(remember: bool = True) -> None:
    global _server, _thread
    with _lock:
        srv, _server, _thread = _server, None, None
        if remember:
            s = settings()
            s["enabled"] = False
            _save(s)
    if srv:
        srv.shutdown()
        srv.server_close()


def regenerate() -> None:
    """Новый пароль: старый перестаёт работать сразу."""
    global _password
    s = settings()
    s["password"] = _password = new_password()
    _save(s)


def autostart(app) -> None:
    """При запуске программы: если сервер был включён, включаем снова (ошибку покажет блок подключения)."""
    if settings()["enabled"] and not in_docker():
        try:
            start(app)
        except RuntimeError:
            pass


def link(url: str, pw: str) -> str:
    return f"universal://connect?url={quote(url, safe='')}&password={quote(pw, safe='')}"


def info() -> dict:
    s = settings()
    d = {"enabled": s["enabled"], "running": running(), "error": _error, "docker": in_docker(),
         "port": s["port"], "password": s["password"] if running() else "", "addresses": [], "urls": [], "link": ""}
    if running():
        d["addresses"] = addresses()
        d["urls"] = [f"http://{ip}:{s['port']}" for ip in d["addresses"]]
        if d["urls"]:
            d["link"] = link(d["urls"][0], s["password"])
    return d
