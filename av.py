"""
Проверка файлов на вирусы: ClamAV (https://github.com/Cisco-Talos/clamav, GPL-2.0), открытый антивирус.

Работает на этом компьютере, файлы никуда не отправляются. Ничего не удаляет и не лечит: только говорит, что нашёл.
  движок   clamscan и freshclam. На Windows Universal сам скачивает портативную сборку; на macOS и Linux это пакет
           системы (`brew install clamav`, `sudo apt install clamav`, `sudo dnf install clamav`).
  базы     сигнатуры вирусов (≈ 300 МБ), качает и обновляет freshclam в <папка данных Universal>/clamav-db.
           Если у системы уже есть свои базы (/var/lib/clamav и т. п.), годятся и они.
"""
from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import engines
from tasks import Cancelled, Task

DB_MARK = ("*.cvd", "*.cld", "*.hdb", "*.hsb", "*.ndb", "*.ldb", "*.cdb", "*.mdb")
SYSTEM_DBS = ("/var/lib/clamav", "/opt/homebrew/var/lib/clamav", "/usr/local/var/lib/clamav", "/var/db/clamav")
MIRROR = "database.clamav.net"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LINE = re.compile(r"^(?P<name>.+): (?:(?P<ok>OK)|(?P<virus>.+) (?P<kind>FOUND)|(?P<err>.+) (?P<ekind>ERROR))$")


class AvError(Exception):
    pass


def supported() -> bool:
    return not os.environ.get("UNIVERSAL_ANDROID")


def clamscan() -> str | None:
    return shutil.which("clamscan")


def freshclam() -> str | None:
    return shutil.which("freshclam")


def available() -> bool:
    return bool(clamscan() and freshclam())


def install_hint() -> str:
    if sys.platform == "darwin":
        return "brew install clamav"
    if sys.platform.startswith("linux"):
        return "sudo apt install clamav   (или: sudo dnf install clamav, sudo pacman -S clamav)"
    return ""


def db_dir() -> Path:
    return engines.root() / "clamav-db"


def _has_db(d: Path) -> bool:
    try:
        return d.is_dir() and any(next(d.glob(p), None) for p in DB_MARK)
    except OSError:
        return False


def db_path() -> Path | None:
    """Где лежат базы: свои (свежее) или системные."""
    if _has_db(db_dir()):
        return db_dir()
    return next((Path(p) for p in SYSTEM_DBS if _has_db(Path(p))), None)


def db_info() -> dict:
    d = db_path()
    if not d:
        return {"ready": False, "path": str(db_dir()), "mb": 0, "updated": 0}
    files = [f for p in DB_MARK for f in d.glob(p) if f.is_file()]
    return {"ready": True, "path": str(d), "mb": sum(f.stat().st_size for f in files) >> 20,
            "updated": int(max(f.stat().st_mtime for f in files))}


def status() -> dict:
    return {"supported": supported(), "available": available(), "can_install": engines.can_install("clamav"),
            "hint": install_hint(), "db": db_info()}


# --------------------------------------------------------------------- установка и базы

def _run_stream(cmd: list[str], task: Task, on_line) -> int:
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    task.proc = p
    try:
        for raw in p.stdout:
            on_line(raw.decode("utf-8", "replace").rstrip())
    finally:
        p.wait()
        task.proc = None
    task.check_cancel()
    return p.returncode


def update_db(task: Task, base: float = 0.0, span: float = 1.0) -> None:
    """Скачивает или обновляет базы вирусов (первый раз ≈ 300 МБ)."""
    exe = freshclam()
    if not exe:
        raise AvError("Нет freshclam: поставь ClamAV")
    d = db_dir()
    d.mkdir(parents=True, exist_ok=True)
    conf = [f"DatabaseMirror {MIRROR}", f"DatabaseDirectory {d}"]
    if os.name == "posix":
        conf.append(f"DatabaseOwner {getpass.getuser()}")      # от root freshclam иначе пробует сменить пользователя
    cf = Path(tempfile.mkdtemp(prefix="freshclam-")) / "freshclam.conf"
    cf.write_text("\n".join(conf) + "\n", encoding="utf-8")
    tail: list[str] = []
    task.update(base, "Скачиваю базы вирусов (первый раз около 300 МБ)…")

    def on_line(line: str) -> None:
        if not line.strip():
            return
        tail.append(line)
        del tail[:-6]
        m = re.search(r"(\d{1,3})(?:\.\d)?%", line)
        what = re.match(r"^(?:Downloading\s+)?(main|daily|bytecode)\b", line.strip(), re.I)
        if m:
            task.update(base + span * min(int(m.group(1)), 100) / 100 * 0.95,
                        f"Скачиваю базы вирусов: {m.group(1)}%" + (f" ({what.group(1)})" if what else ""))
        elif what:
            task.update(None, f"Базы вирусов: {what.group(1)}")
    try:
        code = _run_stream([exe, f"--config-file={cf}", f"--datadir={d}", "--stdout", "--no-warnings"], task, on_line)
    finally:
        shutil.rmtree(cf.parent, ignore_errors=True)
    if code != 0 and not _has_db(d):
        raise AvError("Не получилось скачать базы вирусов" + (": " + " / ".join(tail[-2:])[:300] if tail else "")
                      + ". Нужен интернет (database.clamav.net)")
    task.update(base + span, "Базы вирусов на месте")


def install(task: Task) -> None:
    """Движок (на Windows скачиваем сами) и базы."""
    task.total = 2
    if not available():
        if not engines.can_install("clamav"):
            raise AvError("ClamAV здесь ставится системой: " + install_hint())

        class Part:                                     # движок занимает первую половину полосы
            def __getattr__(self, n):
                return getattr(task, n)

            def update(self, p=None, m=None):
                task.update(None if p is None else p * 0.3, m)
        engines.install("clamav", Part())
        engines.activate()
        if not available():
            raise AvError("ClamAV скачался, но не запускается")
    task.done = 1
    update_db(task, 0.3, 0.7)
    task.done = 2
    task.update(1, "Готово")


# --------------------------------------------------------------------- проверка

def read_opts(form) -> dict:
    return {"archives": form.get("archives", "1") != "0"}


def _ascii_links(items: list[tuple[Path, str]], work: Path) -> dict[str, tuple[str, Path]]:
    """clamscan на Windows не всегда понимает русские имена: проверяем по именам f0001.ext, а показываем свои."""
    work.mkdir(parents=True, exist_ok=True)
    out = {}
    for i, (src, stem) in enumerate(items, 1):
        ext = src.suffix.lower()
        name = f"f{i:04d}{ext if re.fullmatch(r'[.][a-z0-9]{1,6}', ext) else ''}"
        dst = work / name
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        out[name] = (src.name, dst)
    return out


def run_av_task(task: Task, items: list[tuple[Path, str]], o: dict) -> None:
    exe = clamscan()
    if not exe:
        raise AvError("ClamAV не установлен")
    db = db_path()
    if not db:
        raise AvError("Нет баз вирусов: обнови их (кнопка ниже)")
    n = len(items)
    task.total = n
    work = task.job_dir / "scan"
    names = _ascii_links(items, work)
    cmd = [exe, "-d", str(db), "--stdout", "--no-summary", "--max-filesize=1900M", "--max-scansize=1900M"]
    if not o.get("archives", True):
        cmd += ["--scan-archive=no"]
    cmd += [str(p) for _, p in names.values()]
    task.update(0, "Загружаю базы вирусов (десять-двадцать секунд)…")
    infected, clean, seen = [], 0, 0
    tail: list[str] = []

    def on_line(line: str) -> None:
        nonlocal clean, seen
        m = LINE.match(line)
        if not m:
            if line.strip():
                tail.append(line.strip())
                del tail[:-4]
            return
        key = Path(m.group("name")).name
        shown = names.get(key, (key, None))[0]
        seen += 1
        if m.group("ok"):
            clean += 1
        elif m.group("kind"):
            infected.append({"name": shown, "virus": m.group("virus")})
        else:
            task.failed.append({"name": shown, "error": m.group("err")[:200]})
        task.done = seen
        task.update(seen / n, f"Проверено {seen} из {n}: {shown}")
    try:
        code = _run_stream(cmd, task, on_line)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if code not in (0, 1) and not seen:
        raise AvError("ClamAV не справился" + (": " + " / ".join(tail)[:300] if tail else ""))
    task.result = {"av": {"scanned": seen, "clean": clean, "infected": infected, "db": db_info(),
                          "checked_at": int(time.time())}}
    task.update(1, "Готово" if not infected else f"Найдено заражённых: {len(infected)}")
