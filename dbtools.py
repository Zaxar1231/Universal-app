"""
Базы данных: SQLite-файл можно посмотреть прямо в приложении (таблицы и первые строки) и открыть в DBeaver.

DBeaver Community (https://github.com/dbeaver/dbeaver, Apache-2.0) тяжёлый, поэтому не встроен, а запускается по требованию:
  Windows   Universal сам скачивает портативную сборку (≈ 130 МБ, с Java внутри) в папку движков;
  macOS и Linux  берётся поставленный в систему (Applications, `dbeaver` в PATH, Flatpak).
Открывается копия файла, загруженная в Universal: оригинал не меняется. Готовую копию можно скачать обратно.
Просмотр работает только на чтение и без DBeaver.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import engines

SQLITE_EXT = (".db", ".sqlite", ".sqlite3", ".db3", ".s3db", ".sl3")
MAGIC = b"SQLite format 3\x00"
PREVIEW_ROWS = 50
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DbError(Exception):
    pass


def can_read(name: str) -> bool:
    return name.lower().endswith(SQLITE_EXT)


def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == MAGIC
    except OSError:
        return False


def _connect(path: Path) -> sqlite3.Connection:
    """Только чтение: файл не изменится, даже если в нём есть журнал."""
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    return con


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def inspect(path: Path) -> dict:
    """-> {tables: [{name, rows, columns: [..]}], views: n, size}"""
    if not is_sqlite(path):
        raise DbError("Это не файл SQLite")
    try:
        con = _connect(path)
        try:
            tables = []
            for name, kind in con.execute("select name, type from sqlite_master where type in ('table','view') and name not like 'sqlite_%' order by type, name"):
                cols = [r[1] for r in con.execute(f"pragma table_info({_q(name)})")]
                rows = con.execute(f"select count(*) from {_q(name)}").fetchone()[0] if kind == "table" else None
                tables.append({"name": name, "view": kind == "view", "rows": rows, "columns": cols})
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise DbError(f"Файл не открывается как база SQLite ({e})")
    return {"tables": tables, "size": path.stat().st_size}


def rows(path: Path, table: str, limit: int = PREVIEW_ROWS) -> dict:
    info = inspect(path)
    t = next((x for x in info["tables"] if x["name"] == table), None)
    if not t:
        raise DbError("Такой таблицы нет")
    con = _connect(path)
    try:
        data = [[None if v is None else (v if isinstance(v, (int, float, str)) else f"<{len(v)} байт>") for v in r]
                for r in con.execute(f"select * from {_q(table)} limit {int(limit)}")]
    finally:
        con.close()
    return {"table": table, "columns": t["columns"], "rows": data, "total": t["rows"]}


# --------------------------------------------------------------------- DBeaver

def dbeaver_cmd() -> list[str] | None:
    """Команда запуска DBeaver или None."""
    d = engines._load_paths().get("dbeaver")
    if d:
        exe = Path(d) / "dbeaver.exe"
        if exe.is_file():
            return [str(exe)]
    found = engines._find_installed("dbeaver")
    if found and (Path(found) / "dbeaver.exe").is_file():
        engines._register("dbeaver", found)
        return [str(Path(found) / "dbeaver.exe")]
    for name in ("dbeaver", "dbeaver-ce"):
        p = shutil.which(name)
        if p:
            return [p]
    if sys.platform == "darwin":
        mac = Path("/Applications/DBeaver.app/Contents/MacOS/dbeaver")
        if mac.is_file():
            return [str(mac)]
    if sys.platform.startswith("linux") and shutil.which("flatpak"):
        try:
            r = subprocess.run(["flatpak", "info", "io.dbeaver.DBeaverCommunity"], capture_output=True, timeout=10)
            if r.returncode == 0:
                return ["flatpak", "run", "io.dbeaver.DBeaverCommunity"]
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def install_hint() -> str:
    if sys.platform == "darwin":
        return "brew install --cask dbeaver-community"
    if sys.platform.startswith("linux"):
        return "flatpak install flathub io.dbeaver.DBeaverCommunity   (или snap install dbeaver-ce)"
    return ""


def supported() -> bool:
    return not (os.environ.get("UNIVERSAL_ANDROID") or os.environ.get("UNIVERSAL_DOCKER"))


def status() -> dict:
    return {"supported": supported(), "available": bool(dbeaver_cmd()), "can_install": engines.can_install("dbeaver"),
            "hint": install_hint()}


def connection_arg(path, title: str) -> str:
    """Параметр -con для DBeaver (DataSourceUtils и ApplicationInstanceServer в его исходниках): подключение к файлу
    SQLite с автоматическим входом. Параметры делит `|`; обратный слэш пути пишется как `\\\\`, иначе `\\|` и `\\\\` читаются как служебные."""
    p = str(path)
    if "|" in p or "|" in title:
        raise DbError("В пути или названии есть символ |")
    return f"name=Universal: {title}|driver=sqlite_jdbc|database={p.replace(chr(92), chr(92) * 2)}|connect=true|openConsole=false"


def open_in_dbeaver(path: Path, title: str) -> None:
    cmd = dbeaver_cmd()
    if not cmd:
        raise DbError("DBeaver не установлен")
    if not is_sqlite(path):
        raise DbError("Это не файл SQLite")
    args = [*cmd, "-con", connection_arg(path.resolve(), title)]
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW, start_new_session=(os.name == "posix"))
    except OSError as e:
        raise DbError(f"DBeaver не запустился: {e}")
