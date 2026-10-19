"""
Universal как обычное Windows-приложение: своё окно (WebView2), без консоли и браузера.

Внутри тот же Flask-сервер, что и в веб-версии, только на случайном порту
127.0.0.1 и без пароля. Задания хранятся в %LOCALAPPDATA%\\Universal\\jobs,
скачанные движки в %LOCALAPPDATA%\\Universal\\engines.

Сборка: pyinstaller universal.spec (см. .github/workflows/release.yml).
Запуск без сборки: pip install pywebview && python desktop.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import engines

if sys.platform == "win32":
    # без этого каждый вызов ffmpeg/pandoc мигает чёрным окном консоли
    _Popen = subprocess.Popen

    class _QuietPopen(_Popen):
        def __init__(self, *args, **kwargs):
            if not kwargs.get("creationflags"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _QuietPopen


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 20) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except Exception:
            time.sleep(0.1)


def smoke(url: str) -> int:
    """Самопроверка собранного exe в CI: сервер, шаблон, форматы, QR. Код выхода 0 = всё живо."""
    import json
    checks = {}
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            checks["index"] = r.status == 200 and b"Universal" in r.read()
        with urllib.request.urlopen(url + "api/formats", timeout=10) as r:
            checks["formats"] = "edit" in json.load(r)["categories"]
        req = urllib.request.Request(url + "api/qr", data=b"text=hi", method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            checks["qr"] = r.read(8) == b"\x89PNG\r\n\x1a\n"
        import webview  # noqa: F401  (проверяем, что окно попало в сборку)
        import yt_dlp  # noqa: F401
        checks["imports"] = True
        # отдельный процесс скачивания (exe запускает сам себя с --dl-worker) стартует и отвечает
        import subprocess
        import tempfile
        import downloader
        with tempfile.TemporaryDirectory() as d:
            ev, args = os.path.join(d, "ev.jsonl"), os.path.join(d, "args.json")
            with open(args, "w", encoding="utf-8") as f:
                json.dump({"urls": [], "opts": downloader.read_opts({}), "out": d, "events": ev, "cookie": ""}, f)
            subprocess.run(downloader.worker_cmd(args), timeout=120, capture_output=True)
            checks["dl_worker"] = os.path.exists(ev) and '"finished"' in open(ev, encoding="utf-8").read()
        if sys.platform.startswith("linux"):
            import webview.platforms.gtk  # noqa: F401  (GTK и WebKit2 из зависимостей пакета)
            checks["gui"] = True
    except Exception as e:
        checks["error"] = repr(e)
    out = engines.root() / "smoke.json"
    out.write_text(json.dumps(checks), encoding="utf-8")
    return 0 if checks and all(v is True for v in checks.values()) else 1


def main() -> None:
    if "--dl-worker" in sys.argv:
        # тот же exe в роли отдельного процесса скачивания (см. downloader.worker_cmd)
        engines.activate()
        import downloader
        downloader.worker_main(sys.argv[sys.argv.index("--dl-worker") + 1])
        return
    home = engines.root()
    home.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        # exe без консоли: print и логи пишем в файл, иначе им некуда писать
        log = open(home / "universal.log", "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)   # без строки на каждый запрос
    if sys.platform == "darwin":
        # из Finder приложение стартует без PATH из терминала, а Homebrew живёт здесь
        extra = [d for d in ("/opt/homebrew/bin", "/usr/local/bin") if os.path.isdir(d)]
        os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])
    os.environ.setdefault("WORK_DIR", str(home / "jobs"))
    os.environ["UNIVERSAL_DESKTOP"] = "1"
    engines.activate()            # PATH к движкам и свежий yt-dlp, до импорта app

    from werkzeug.serving import make_server

    import app as webapp

    port = _free_port()
    server = make_server("127.0.0.1", port, webapp.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    _wait(url + "api/engines")
    if "--smoke" in sys.argv:
        code = smoke(url)
        server.shutdown()
        sys.exit(code)

    import webview  # type: ignore

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window("Universal", url, width=1280, height=860, min_size=(420, 600),
                          background_color="#F4FBFA", text_select=True)
    # private_mode=False: localStorage (цвет оформления) переживает перезапуск
    webview.start(private_mode=False, storage_path=str(home / "webview"))
    server.shutdown()


if __name__ == "__main__":
    main()
