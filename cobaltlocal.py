"""
Свой сервер cobalt на этом компьютере одной кнопкой.

Делает то же, что инструкция cobalt «how to run an instance»: создаёт папку,
кладёт в неё docker-compose.yml и запускает `docker compose up -d`. Папка лежит
рядом с движками (%LOCALAPPDATA%\\Universal\\cobalt, ~/.local/share/Universal/cobalt).

Отличия от примера из инструкции:
- порт открыт только для этого компьютера (127.0.0.1), снаружи сервер не виден,
  поэтому ни ключи, ни turnstile не нужны;
- вместо watchtower образ обновляется при каждом нажатии «Создать / обновить»
  (docker compose pull): watchtower не дружит с новыми версиями Docker;
- если рядом с docker-compose.yml лежит cookies.json, он подключается сам.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import downloader
import engines
from tasks import Task

PROJECT = "universal-cobalt"
CONTAINER = "universal-cobalt"
IMAGE = "ghcr.io/imputnet/cobalt:11"
PORTS = range(9000, 9011)

COMPOSE = """\
# создано приложением Universal по инструкции cobalt «how to run an instance»
services:
  cobalt:
    image: {image}
    init: true
    read_only: true
    restart: unless-stopped
    container_name: {container}
    ports:
      - "127.0.0.1:{port}:9000/tcp"
    # host.docker.internal — этот компьютер (там слушает прокси VPN-клиента); в Docker Desktop есть и так
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      API_URL: "http://localhost:{port}/"
      # сервер только для себя: лимиты публичного инстанса (20 запросов в минуту) мешают пачкам ссылок
      RATELIMIT_MAX: "10000"
      TUNNEL_RATELIMIT_MAX: "10000"
      SESSION_RATELIMIT_MAX: "10000"
{extra}"""


def folder() -> Path:
    return engines.root() / "cobalt"


def docker_exe() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    if sys.platform == "win32":
        cands = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker/Docker/resources/bin/docker.exe"]
    elif sys.platform == "darwin":
        cands = [Path("/usr/local/bin/docker"), Path("/opt/homebrew/bin/docker"),
                 Path("/Applications/Docker.app/Contents/Resources/bin/docker")]
    else:
        cands = [Path("/usr/bin/docker"), Path("/usr/local/bin/docker")]
    return next((str(c) for c in cands if c.is_file()), None)


def _desktop_app() -> list[str] | None:
    """Как запустить Docker Desktop, если он установлен, но не запущен."""
    if sys.platform == "win32":
        exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker/Docker/Docker Desktop.exe"
        return [str(exe)] if exe.is_file() else None
    if sys.platform == "darwin" and Path("/Applications/Docker.app").is_dir():
        return ["open", "-a", "Docker"]
    return None


def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=folder() if folder().is_dir() else None)


def _daemon_ok(docker: str) -> bool:
    try:
        return _run([docker, "info", "--format", "{{.ServerVersion}}"], 20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _compose_ok(docker: str) -> bool:
    try:
        return _run([docker, "compose", "version"], 20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _port() -> int | None:
    f = folder() / "port.txt"
    try:
        return int(f.read_text().strip())
    except (OSError, ValueError):
        return None


def _state(docker: str) -> str:
    """running | exited | … | '' (контейнера нет)."""
    try:
        r = _run([docker, "ps", "-a", "--filter", f"name=^{CONTAINER}$", "--format", "{{.State}}"], 20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""


def status() -> dict:
    docker = docker_exe()
    st = {"docker": bool(docker), "daemon": False, "compose": False, "state": "",
          "url": "", "folder": str(folder()), "can_start_desktop": bool(_desktop_app())}
    if not docker:
        return st
    st["daemon"] = _daemon_ok(docker)
    if not st["daemon"]:
        return st
    st["compose"] = _compose_ok(docker)
    st["state"] = _state(docker)
    port = _port()
    if st["state"] and port:
        st["url"] = f"http://localhost:{port}/"
        try:
            st["proxy"] = (folder() / "proxy.txt").read_text().strip()
        except OSError:
            st["proxy"] = ""
    return st


def _free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def container_proxy(net: dict | None) -> str:
    """Прокси VPN-клиента для контейнера. Иначе cobalt ходит в интернет мимо VPN,
    и заблокированные сайты (YouTube из России) ему не открываются.
    cobalt (undici) понимает только HTTP-прокси; у VPN-клиентов порт обычно смешанный
    (socks и http на одном), а 127.0.0.1 изнутри контейнера — это сам контейнер."""
    p = downloader.effective_proxy(net)
    if not p:
        return ""
    p = re.sub(r"^socks(4a?|5h?)://", "http://", p, flags=re.I)
    return re.sub(r"//(127\.\d+\.\d+\.\d+|localhost|\[::1\])(?=[:/]|$)", "//host.docker.internal", p, flags=re.I)


def compose_text(port: int, cookies: bool, proxy: str = "") -> str:
    extra = ""
    if proxy:
        extra += (f'      HTTP_PROXY: "{proxy}"\n'
                  f'      HTTPS_PROXY: "{proxy}"\n'
                  '      NO_PROXY: "localhost,127.0.0.1,::1"\n')
    if cookies:
        extra += '      COOKIE_PATH: "/cookies.json"\n'
        extra += "    volumes:\n      - ./cookies.json:/cookies.json:ro\n"
    return COMPOSE.format(image=IMAGE, container=CONTAINER, port=port, extra=extra)


def _wait_daemon(docker: str, task: Task) -> None:
    if _daemon_ok(docker):
        return
    app = _desktop_app()
    if not app:
        raise RuntimeError("Docker не запущен. Запусти службу Docker (sudo systemctl start docker) и попробуй ещё раз")
    task.update(0.05, "Запускаю Docker Desktop…")
    subprocess.Popen(app, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(90):                       # Docker Desktop просыпается до пары минут
        task.check_cancel()
        time.sleep(2)
        if _daemon_ok(docker):
            return
    raise RuntimeError("Docker Desktop не запустился за 3 минуты. Открой его вручную и попробуй ещё раз")


def _compose(docker: str, task: Task, *args: str) -> tuple[int, list[str]]:
    """docker compose с выводом строк в сообщение задачи; отмена убивает процесс."""
    proc = subprocess.Popen([docker, "compose", "-p", PROJECT, *args], cwd=folder(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    task.proc = proc
    tail: list[str] = []
    for line in proc.stdout:
        line = line.strip()
        if line:
            tail = (tail + [line])[-6:]
            task.update(None, "Docker: " + line[:120])
    proc.wait()
    task.proc = None
    task.check_cancel()
    return proc.returncode, tail


def create(task: Task, net: dict | None = None) -> None:
    docker = docker_exe()
    if not docker:
        raise RuntimeError("Не найден Docker. Установи Docker Desktop (docker.com) и попробуй ещё раз")
    task.update(0.02, "Проверяю Docker…")
    _wait_daemon(docker, task)
    if not _compose_ok(docker):
        raise RuntimeError("Нет docker compose. Обнови Docker или поставь плагин docker-compose-plugin")

    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    port = _port() if _state(docker) else None      # свой контейнер уже держит порт: оставляем его
    if not port:
        port = next((p for p in PORTS if _free(p)), None)
        if not port:
            raise RuntimeError("Порты 9000–9010 заняты, сервер некуда повесить")
    proxy = container_proxy(net)
    (d / "docker-compose.yml").write_text(compose_text(port, (d / "cookies.json").is_file(), proxy), encoding="utf-8")
    (d / "port.txt").write_text(str(port))
    (d / "proxy.txt").write_text(proxy)

    task.update(0.1, "Скачиваю свежий образ cobalt (первый раз ≈ 100 МБ)…")
    code, tail = _compose(docker, task, "pull")
    if code != 0 and _run([docker, "image", "inspect", IMAGE], 20).returncode != 0:
        raise RuntimeError("Не удалось скачать образ cobalt: " + " / ".join(dict.fromkeys(t[:200] for t in tail[-2:])))
    task.update(0.7, "Запускаю контейнер…")         # без интернета, но со старым образом тоже запустится
    code, tail = _compose(docker, task, "up", "-d", "--remove-orphans")
    if code != 0:
        raise RuntimeError("docker compose не смог запустить сервер: " + " / ".join(dict.fromkeys(t[:200] for t in tail[-2:])))

    url = f"http://localhost:{port}/"
    task.update(0.9, "Жду, пока сервер ответит…")
    for _ in range(60):
        task.check_cancel()
        try:
            info = downloader.cobalt_info(url)
            task.update(1.0, f"Сервер cobalt {info['version']} работает: {url}")
            task.result = {"url": url, "version": info["version"]}
            return
        except downloader.CobaltFail:
            time.sleep(1)
    raise RuntimeError("Контейнер запустился, но сервер не отвечает. Посмотри журнал: docker logs " + CONTAINER)


def remove() -> None:
    docker = docker_exe()
    if not docker or not (folder() / "docker-compose.yml").is_file():
        return
    r = _run([docker, "compose", "-p", PROJECT, "down"], 120)
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr).strip()[-300:] or "docker compose down не сработал")

