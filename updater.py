"""
Проверка обновлений и обновление из самого приложения.

Последний релиз берётся с GitHub (API, а если упёрлись в лимит — со страницы
релиза). Ответ кэшируется на несколько часов, чтобы не дёргать GitHub при
каждом открытии окна.

Как ставится новая версия:
- Windows: качается Universal-Setup-X.exe и запускается тихо (/SILENT),
  установщик закрывает приложение, обновляет и запускает его снова;
- macOS: качается .dmg под свой процессор и открывается в Finder;
- Linux (пакет): качается .deb/.rpm/.pkg.tar.zst под дистрибутив и открывается
  «Установкой приложений»; рядом команда для терминала;
- из исходников и в Docker: только ссылка на релиз (обновляются через git pull).
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import engines
from tasks import Task
from version import VERSION

REPO = "Zaxar1231/Universal-app"
RELEASES = f"https://github.com/{REPO}/releases"
CACHE_SECONDS = 6 * 3600


class UpdateError(Exception):
    pass


def parse(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def _cache_file() -> Path:
    return engines.root() / "update.json"


def _fetch() -> dict:
    """{'version', 'notes', 'assets': [(name, url, size)]} последнего релиза."""
    try:
        with engines._get(engines.GITHUB_API.format(repo=REPO), timeout=15) as r:
            d = json.load(r)
        return {"version": d.get("tag_name", "").lstrip("v"), "notes": d.get("body") or "",
                "assets": [(a["name"], a["browser_download_url"], a.get("size", 0)) for a in d.get("assets", [])]}
    except Exception:
        pass
    try:                                    # без API: тег из редиректа и список файлов со страницы
        with engines._get(f"{RELEASES}/latest", timeout=15) as r:
            tag = r.geturl().rstrip("/").rsplit("/", 1)[-1]
        assets = engines._assets_from_html(REPO)
    except Exception as e:
        raise UpdateError(f"не удалось связаться с GitHub ({e.__class__.__name__})")
    return {"version": tag.lstrip("v"), "notes": "", "assets": [(n, u, 0) for n, u in assets]}


def latest(force: bool = False) -> dict:
    f = _cache_file()
    if not force:
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - c.get("checked", 0) < CACHE_SECONDS and c.get("version"):
                return c
        except (OSError, ValueError):
            pass
    d = _fetch()
    d["checked"] = time.time()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return d


def _linux_family() -> str:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8").lower()
    except OSError:
        return ""
    ids = " ".join(re.findall(r"^(?:id|id_like)=\"?([^\"\n]+)", text, re.M))
    for fam, keys in (("deb", ("debian", "ubuntu")), ("rpm", ("fedora", "rhel", "centos", "suse")), ("arch", ("arch",))):
        if any(k in ids.split() for k in keys):
            return fam
    return ""


def kind() -> str:
    """Как здесь ставится обновление: installer | dmg | deb | rpm | arch | source | docker."""
    if Path("/.dockerenv").exists() or os.environ.get("UNIVERSAL_DOCKER"):
        return "docker"
    if getattr(sys, "frozen", False):
        return "installer" if sys.platform == "win32" else "dmg" if sys.platform == "darwin" else "source"
    if sys.platform.startswith("linux") and Path(__file__).resolve().parent == Path("/usr/lib/universal"):
        return _linux_family() or "source"
    return "source"


ASSET_RE = {
    "installer": r"^Universal-Setup-[\d.]+\.exe$",
    "deb": r"^universal_[\d.]+_all\.deb$",
    "rpm": r"^universal-[\d.]+-\d+\.noarch\.rpm$",
    "arch": r"^universal-[\d.]+-\d+-any\.pkg\.tar\.zst$",
}


def _asset(k: str, assets: list) -> tuple | None:
    if k == "dmg":
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
        pattern = rf"^Universal-[\d.]+-macos-{arch}\.dmg$"
    else:
        pattern = ASSET_RE.get(k)
    return next((a for a in assets if pattern and re.match(pattern, a[0])), None)


def check(force: bool = False) -> dict:
    d = latest(force)
    k = kind()
    a = _asset(k, d["assets"])
    newer = parse(d["version"]) > parse(VERSION)
    return {"current": VERSION, "latest": d["version"], "newer": newer, "notes": d["notes"][:4000],
            "url": f"{RELEASES}/tag/v{d['version']}", "kind": k,
            "asset": {"name": a[0], "size": a[2]} if a else None,
            "can_install": bool(a) and k not in ("source", "docker")}


def _updates_dir() -> Path:
    d = engines.root() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _open(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


INSTALL_CMD = {"deb": "sudo apt install {p}", "rpm": "sudo dnf install {p}", "arch": "sudo pacman -U {p}"}


def install(task: Task, quit_app=None) -> None:
    """Скачать и запустить установку новой версии. quit_app — как закрыть приложение (Windows)."""
    info = check()
    if not info["newer"]:
        raise UpdateError(f"уже стоит последняя версия {VERSION}")
    k = info["kind"]
    a = _asset(k, latest()["assets"])
    if not a or not info["can_install"]:
        raise UpdateError("для этой версии приложения обновление ставится вручную: " + info["url"])
    name, url, _ = a
    task.total = 1
    for old in _updates_dir().glob("*"):                  # прошлые скачанные обновления больше не нужны
        if old.name != name:
            old.unlink(missing_ok=True)
    dest = _updates_dir() / name
    part = dest.with_name(dest.name + ".part")
    task.update(0, f"Скачиваю Universal {info['latest']}…")
    engines._download(url, part, task, 0, 0.95)
    part.replace(dest)
    task.done = 1
    if k == "installer":
        task.update(1, "Ставлю обновление, приложение перезапустится…")
        # /SILENT: только полоска прогресса; установщик сам закроет окно и откроет новую версию
        subprocess.Popen([str(dest), "/SILENT", "/SP-", "/NOCANCEL", "/CLOSEAPPLICATIONS", "/NORESTART"],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        task.result = {"action": "restart", "path": str(dest)}
        if quit_app:
            threading.Timer(3, quit_app).start()
        return
    _open(dest)
    task.result = {"action": "open", "path": str(dest), "command": INSTALL_CMD.get(k, "").format(p=dest)}
