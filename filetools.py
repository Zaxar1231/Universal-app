"""
Архивы и файлы: упаковать в ZIP или 7z (с паролем), распаковать ZIP/7z/TAR/GZ/BZ2/XZ
с сохранением папок, посчитать контрольные суммы, переименовать пачкой по шаблону.

Имена в ZIP из Windows часто записаны в кодировке cp866 без флага UTF-8:
zipfile читает их как cp437 («кракозябры»), такие имена перекодируются.
"""
from __future__ import annotations

import bz2
import datetime as dt
import gzip
import hashlib
import lzma
import re
import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from tasks import Task

OPS = ("zip", "7z", "extract", "hash", "rename")
ALGOS = ("md5", "sha1", "sha256", "sha512")
ARCHIVE_RE = re.compile(r"\.(zip|7z|tar|tar\.gz|tgz|tar\.bz2|tbz2?|tar\.xz|txz|gz|bz2|xz)$", re.I)
LEVELS = {"fast": 1, "normal": 6, "max": 9}


class FileToolError(Exception):
    pass


def has_7z() -> bool:
    try:
        import py7zr  # noqa: F401
        return True
    except ImportError:
        return False


def clean_rel(name: str) -> str:
    """Безопасный относительный путь из имени файла или webkitRelativePath."""
    parts = [p for p in re.split(r"[\\/]+", name or "") if p not in ("", ".", "..")]
    parts = [re.sub(r'[<>:"|?*\x00-\x1f]', "_", p).strip(" .") or "_" for p in parts]
    return "/".join(parts) or "file"


def read_opts(form) -> dict:
    o = {
        "op": form.get("op", "zip"),
        "name": (form.get("name") or "").strip(),
        "password": form.get("password") or "",
        "level": form.get("level", "normal"),
        "algos": [a for a in (form.get("algos") or "sha256").split(",") if a in ALGOS] or ["sha256"],
        "template": form.get("template") or "{name}",
        "start": form.get("start", "1"),
        "digits": form.get("digits", "2"),
        "case": form.get("case", "keep"),
        "find": form.get("find") or "",
        "replace": form.get("replace") or "",
    }
    if o["op"] not in OPS:
        raise FileToolError("Неизвестное действие")
    if o["level"] not in LEVELS:
        o["level"] = "normal"
    try:
        o["start"] = max(0, min(10 ** 6, int(o["start"])))
        o["digits"] = max(1, min(8, int(o["digits"])))
    except ValueError:
        raise FileToolError("Номер и количество цифр должны быть числами")
    if o["op"] == "7z" and not has_7z():
        raise FileToolError("Для 7z нужен пакет py7zr: pip install py7zr")
    if o["op"] == "rename" and not re.search(r"\{(name|n|date|ext)\}", o["template"]):
        raise FileToolError("В шаблоне нужен хотя бы один из {name}, {n}, {date}: иначе все файлы получат одно имя")
    return o


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    for i in range(2, 10000):
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
    raise FileToolError("слишком много файлов с одинаковым именем")


# --------------------------------------------------------------------- упаковать

def pack_zip(items: list[tuple[Path, str]], out: Path, level: str, task: Task) -> None:
    n = len(items)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=LEVELS[level], allowZip64=True) as zf:
        for i, (src, rel) in enumerate(items):
            task.check_cancel()
            task.update(i / n, f"Упаковываю {i + 1} из {n}: {rel}")
            zf.write(src, rel)


def pack_7z(items: list[tuple[Path, str]], out: Path, password: str, task: Task) -> None:
    import py7zr
    n = len(items)
    with py7zr.SevenZipFile(out, "w", password=password or None, header_encryption=bool(password)) as z:
        for i, (src, rel) in enumerate(items):
            task.check_cancel()
            task.update(i / n, f"Упаковываю {i + 1} из {n}: {rel}")
            z.write(src, rel)


# --------------------------------------------------------------------- распаковать

def _zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")      # архивы из «Проводника» Windows
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _safe_target(root: Path, name: str) -> Path | None:
    rel = clean_rel(name)
    target = (root / rel).resolve()
    return target if root.resolve() in target.parents or target == root.resolve() else None


def extract(archive: Path, dest: Path, password: str, task: Task) -> int:
    """Распаковывает в dest, возвращает число файлов."""
    name = archive.name.lower()
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    if name.endswith(".rar"):
        raise FileToolError("RAR не поддерживается: формат закрытый. Открой его 7-Zip или WinRAR")
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            for i, m in enumerate(members):
                task.check_cancel()
                target = _safe_target(dest, _zip_name(m))
                if not target:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(m, pwd=password.encode() if password else None) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out, 1 << 20)
                except RuntimeError as e:
                    if "password" in str(e).lower() or "encrypted" in str(e).lower():
                        raise FileToolError("архив защищён паролем: введи пароль" if not password else "неверный пароль")
                    raise
                except NotImplementedError:
                    raise FileToolError("этот ZIP зашифрован AES: открой его через 7z (поставь py7zr) или 7-Zip")
                count += 1
                if i % 20 == 0:
                    task.update(None, f"{archive.name}: {i + 1} из {len(members)}")
        return count
    if name.endswith(".7z"):
        if not has_7z():
            raise FileToolError("для 7z нужен пакет py7zr: pip install py7zr")
        import py7zr
        try:
            with py7zr.SevenZipFile(archive, "r", password=password or None) as z:
                bad = [n for n in z.getnames() if not _safe_target(dest, n)]
                if bad:
                    raise FileToolError("в архиве опасные пути, распаковывать не стану")
                z.extractall(dest)
        except py7zr.exceptions.PasswordRequired:
            raise FileToolError("архив защищён паролем: введи пароль")
        except (py7zr.exceptions.Bad7zFile, lzma.LZMAError) as e:
            raise FileToolError("неверный пароль или повреждённый архив" if password else f"архив повреждён ({e})")
        return sum(1 for p in dest.rglob("*") if p.is_file())
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            members = [m for m in tf.getmembers() if m.isfile() and _safe_target(dest, m.name)]
            for m in members:
                task.check_cancel()
                src = tf.extractfile(m)
                if not src:
                    continue
                target = _safe_target(dest, m.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                count += 1
        return count
    opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}.get(archive.suffix.lower())
    if opener:
        target = dest / archive.stem
        try:
            with opener(archive, "rb") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
        except (OSError, EOFError, lzma.LZMAError):
            raise FileToolError("архив повреждён")
        return 1
    raise FileToolError("это не архив (ZIP, 7z, TAR, GZ, BZ2, XZ)")


def archive_stem(name: str) -> str:
    return re.sub(r"\.(tar\.(gz|bz2|xz)|tgz|tbz2?|txz|zip|7z|gz|bz2|xz|tar)$", "", name, flags=re.I) or "archive"


# --------------------------------------------------------------------- суммы

def checksums(path: Path, algos: list[str]) -> dict:
    hs = {a: hashlib.new(a) for a in algos}
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            for h in hs.values():
                h.update(chunk)
    return {a: h.hexdigest() for a, h in hs.items()}


# --------------------------------------------------------------------- переименовать

def new_name(rel: str, i: int, o: dict) -> str:
    p = PurePosixPath(rel)
    stem, ext = p.stem, p.suffix
    if o["find"]:
        stem = stem.replace(o["find"], o["replace"])
    num = str(o["start"] + i).zfill(o["digits"])
    try:
        name = o["template"].format(name=stem, n=num, date=dt.date.today().isoformat(), ext=ext.lstrip("."))
    except (KeyError, IndexError, ValueError):
        raise FileToolError("Шаблон не понял: можно {name}, {n}, {date}, {ext}")
    if o["case"] == "lower":
        name, ext = name.lower(), ext.lower()
    elif o["case"] == "upper":
        name = name.upper()
    return clean_rel(name).replace("/", "_") + ext


# --------------------------------------------------------------------- задача

def run_files_task(task: Task, items: list[tuple[Path, str]], o: dict, archive: str) -> None:
    """items: (путь к сохранённому файлу, относительное имя как у пользователя)."""
    out_dir, op, n = task.out_dir, o["op"], len(items)
    base = clean_rel(o["name"] or re.sub(r"\.zip$", "", archive or "") or "archive").replace("/", "_")
    if op in ("zip", "7z"):
        task.total = 1
        out = out_dir / f"{base}.{op}"
        (pack_zip(items, out, o["level"], task) if op == "zip" else pack_7z(items, out, o["password"], task))
        task.done = 1
        task.result = {"message": f"Файлов в архиве: {n}"}
        return
    task.total = n
    if op == "extract":
        files = 0
        for i, (src, rel) in enumerate(items):
            task.check_cancel()
            task.update(i / n, f"Распаковываю {i + 1} из {n}: {rel}")
            dest = _unique(out_dir / clean_rel(archive_stem(PurePosixPath(rel).name)))
            try:
                files += extract(src, dest, o["password"], task)
                task.done += 1
            except FileToolError as e:
                shutil.rmtree(dest, ignore_errors=True)
                task.failed.append({"name": rel, "error": str(e)})
        task.result = {"message": f"Распаковано файлов: {files}"}
    elif op == "hash":
        rows, lines = [], []
        for i, (src, rel) in enumerate(items):
            task.check_cancel()
            task.update(i / n, f"Считаю суммы {i + 1} из {n}: {rel}")
            h = checksums(src, o["algos"])
            rows.append({"name": rel, "size": src.stat().st_size, **h})
            task.done += 1
        for a in o["algos"]:
            # формат как у sha256sum/md5sum: проверка командой «sha256sum -c»
            (out_dir / f"{a.upper()}SUMS.txt").write_text("".join(f"{r[a]}  {r['name']}\n" for r in rows), encoding="utf-8")
            lines.append(a)
        task.result = {"hashes": rows[:500], "algos": o["algos"]}
    elif op == "rename":
        for i, (src, rel) in enumerate(items):
            task.check_cancel()
            target = _unique(out_dir / new_name(rel, i, o))
            shutil.copyfile(src, target)
            task.done += 1
        task.result = {"message": f"Переименовано: {n}"}
    if not task.done and task.failed:
        raise FileToolError(task.failed[0]["error"])
