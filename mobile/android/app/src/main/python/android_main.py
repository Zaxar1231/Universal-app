"""
Полная версия Universal на телефоне: тот же сервер Flask, что на компьютере, на встроенном Python (Chaquopy).

Код сервера, шаблоны и static лежат в пакете universal_srv (его собирает mobile/build-www.mjs);
Chaquopy распаковывает пакет в обычные файлы, поэтому Flask читает шаблоны с диска.
ffmpeg и ffprobe берутся из youtubedl-android: исполняемые файлы лежат в nativeLibraryDir
(libffmpeg.so, libffprobe.so), их библиотеки распакованы FFmpeg.init() в packages/ffmpeg/usr/lib.
Запускать файлы можно только из nativeLibraryDir, поэтому в PATH кладём ссылки на них.
"""
from __future__ import annotations

import os
import sys
import threading

PORT = 47700          # постоянный: localStorage страницы привязан к адресу
_server = None


def _link(bin_dir: str, name: str, target: str) -> None:
    link = os.path.join(bin_dir, name)
    try:
        if os.path.islink(link) or os.path.exists(link):
            if os.path.realpath(link) == target:
                return
            os.remove(link)
        os.symlink(target, link)
    except OSError as e:
        print("не удалось сделать ссылку", name, e, file=sys.stderr)


def start(native_dir: str, ffmpeg_lib: str, python_lib: str, files_dir: str, cache_dir: str) -> str:
    """Запускает сервер (один раз) и возвращает его адрес."""
    global _server
    if _server is not None:
        return f"http://127.0.0.1:{PORT}/"
    import universal_srv
    root = os.path.dirname(universal_srv.__file__)
    if root not in sys.path:
        sys.path.insert(0, root)

    bin_dir = os.path.join(files_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    for name, lib in (("ffmpeg", "libffmpeg.so"), ("ffprobe", "libffprobe.so")):
        target = os.path.join(native_dir, lib)
        if os.path.exists(target):
            _link(bin_dir, name, target)
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "/system/bin")
    # библиотеки ffmpeg (сборка Termux) для дочерних процессов
    libs = [p for p in (ffmpeg_lib, python_lib) if p and os.path.isdir(p)]
    if libs:
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(libs + [os.environ.get("LD_LIBRARY_PATH", "")]).rstrip(os.pathsep)
    os.environ["UNIVERSAL_ANDROID"] = "1"
    os.environ["UNIVERSAL_HOME"] = os.path.join(files_dir, "universal")
    os.environ.setdefault("WORK_DIR", os.path.join(cache_dir, "jobs"))
    os.environ["TMPDIR"] = cache_dir
    os.environ.setdefault("HOME", files_dir)
    os.makedirs(os.environ["UNIVERSAL_HOME"], exist_ok=True)

    import app as webapp
    from werkzeug.serving import make_server
    _server = make_server("127.0.0.1", PORT, webapp.app, threaded=True)
    threading.Thread(target=_server.serve_forever, daemon=True, name="universal-server").start()
    return f"http://127.0.0.1:{PORT}/"
