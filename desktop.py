"""
Universal как обычное Windows-приложение: своё окно (WebView2), без консоли и браузера.

Внутри тот же Flask-сервер, что и в веб-версии, только на постоянном порту
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
import net

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


PORTS = range(47615, 47625)   # постоянный порт: localStorage окна привязан к адресу, со случайным портом он терялся


def _listen(app):
    """Сервер на первом свободном порту из PORTS, иначе на любом."""
    from werkzeug.serving import make_server
    for port in PORTS:
        with socket.socket() as probe:          # занят другой копией Universal или чужой программой
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                continue
        try:
            return make_server("127.0.0.1", port, app, threaded=True), port
        except OSError:
            continue
    port = _free_port()
    return make_server("127.0.0.1", port, app, threaded=True), port


WEBVIEW2_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_SETUP = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def _webview2_version() -> str:
    """Версия WebView2 Runtime из реестра ('' — нет). Без него pywebview откатывается на Internet
    Explorer (MSHTML), и современный интерфейс в нём не работает: окно пустое или сломанное."""
    import winreg
    keys = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_ID}")]
    for hive, key in keys:
        try:
            with winreg.OpenKey(hive, key) as k:
                v = winreg.QueryValueEx(k, "pv")[0]
                if v and v != "0.0.0.0":
                    return v
        except OSError:
            continue
    return ""


def _notice(text: str, title: str = "Universal") -> None:
    """Сообщение пользователю без окна приложения (оно ещё не открыто или не откроется)."""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 | 0x40000)   # значок «i», поверх всех окон
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f'display dialog {json_str(text)} with title {json_str(title)} buttons {{"OK"}}'],
                           timeout=600)
        else:
            print(text, file=sys.stderr)
    except Exception:
        pass


def json_str(s: str) -> str:
    import json
    return json.dumps(s, ensure_ascii=False)


def _ensure_webview2() -> bool:
    """Нет WebView2 — тихо ставим его (официальный установщик Microsoft, права администратора не нужны)."""
    if _webview2_version():
        return True
    threading.Thread(target=_notice, args=("Universal докачивает компонент Microsoft WebView2, без него окно не откроется. "
                                           "Это займёт минуту-две, окно приложения появится само.",), daemon=True).start()
    try:
        import tempfile
        exe = os.path.join(tempfile.gettempdir(), "MicrosoftEdgeWebview2Setup.exe")
        with net.urlopen(urllib.request.Request(WEBVIEW2_SETUP, headers={"User-Agent": "Universal"}), timeout=60) as r, \
                open(exe, "wb") as f:
            f.write(r.read())
        subprocess.run([exe, "/silent", "/install"], timeout=600)
    except Exception as e:
        print("WebView2 не поставился:", e, file=sys.stderr)
    return bool(_webview2_version())


def _browser_mode(url: str, why: str, webapp) -> None:
    """Запасной путь: окно не открывается — работаем в обычном браузере. Приложение живёт,
    пока открыта вкладка (страница шлёт /api/ping), и закрывается через пару минут после неё."""
    import webbrowser
    print("Открываю в браузере:", why, file=sys.stderr)
    webbrowser.open(url)
    _notice(f"Окно приложения не открылось ({why}), поэтому Universal открыт в браузере. "
            "Пока вкладка открыта, всё работает как обычно. Закроешь вкладку — приложение закроется само.")
    started = time.time()
    while True:
        time.sleep(15)
        last = webapp.app.config.get("LAST_PING")
        if last is None and time.time() - started > 600:
            break                     # вкладку так и не открыли
        if last is not None and time.time() - last > 120:
            break


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
        # читалка: TextFold запускается внутри и открывает импортированный текст (проверяет, что весь его набор попал в сборку)
        import reader
        if reader.available():
            r = reader.import_text("# Проверка\n\nПервый абзац про дело.\n\n## Раздел\n\nВторой абзац с числом 42.", "smoke.md")
            with urllib.request.urlopen(r["url"], timeout=15) as page:
                checks["reader"] = page.status == 200 and "Проверка" in page.read().decode("utf-8")
        elif getattr(sys, "frozen", False):
            checks["reader"] = False
            checks["reader_missing"] = ", ".join(reader.missing())
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
        # OCRmyPDF попал в сборку и запускается тем же exe (Tesseract может быть ещё не поставлен).
        # В пакетах Linux он необязательная системная зависимость: проверяем, только если стоит
        # распознавание речи: faster-whisper с CTranslate2 и PyAV попали в сборку и грузятся
        if getattr(sys, "frozen", False):
            import ctranslate2  # noqa: F401
            import faster_whisper  # noqa: F401
            import faster_whisper.audio  # noqa: F401  (PyAV)
            checks["stt"] = True
        import ocr
        if getattr(sys, "frozen", False) or ocr.version():
            r = subprocess.run(ocr.command(["--version"]), timeout=180, capture_output=True, text=True, encoding="utf-8", errors="replace")
            checks["ocrmypdf"] = r.returncode == 0 and bool(ocr.version()) and r.stdout.strip() == ocr.version()
            if not checks["ocrmypdf"]:
                checks["ocrmypdf_error"] = (r.stderr or r.stdout or "")[-400:]   # в логе CI сразу видно почему
        if sys.platform.startswith("linux"):
            import webview.platforms.gtk  # noqa: F401  (GTK и WebKit2 из зависимостей пакета)
            checks["gui"] = True
    except Exception as e:
        checks["error"] = repr(e)
    try:
        import offline
        if offline.bundle_dir():
            r = offline.ready()
            checks["offline"] = not r["engines_missing"] and not r["whisper_missing"] and r["voices"]
            if checks["offline"] is not True:
                checks["offline_missing"] = json.dumps(r)
    except Exception as e:
        checks["offline"] = False
        checks["offline_error"] = repr(e)
    out = engines.root() / "smoke.json"
    out.write_text(json.dumps(checks), encoding="utf-8")
    return 0 if checks and all(v is True for v in checks.values()) else 1


def main() -> None:
    if "--ocrmypdf" in sys.argv:
        # тот же exe в роли OCRmyPDF (см. ocr.command): распознавание идёт отдельным процессом
        engines.activate()
        from ocrmypdf.__main__ import run
        sys.exit(run(sys.argv[sys.argv.index("--ocrmypdf") + 1:]))
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
    if "--smoke" in sys.argv or "--browser" in sys.argv:
        webapp, server, url = _boot()
        if "--smoke" in sys.argv:
            code = smoke(url)
            server.shutdown()
            sys.exit(code)
        _browser_mode(url, "запуск с --browser", webapp)
        server.shutdown()
        return
    if sys.platform == "win32" and not _ensure_webview2():
        webapp, server, url = _boot()
        _browser_mode(url, "нет Microsoft WebView2", webapp)
        server.shutdown()
        return
    _run_window(home)


SPLASH = """<!doctype html><meta charset="utf-8"><title>Universal</title>
<style>
html,body{height:100%;margin:0}
body{display:grid;place-items:center;background:#F4FBFA;color:#161d1c;font:15px/1.4 system-ui,Segoe UI,sans-serif}
@media (prefers-color-scheme:dark){body{background:#0e1514;color:#dde4e2}}
main{text-align:center}
h1{font-size:28px;font-weight:600;margin:0 0 18px}
i{display:block;width:34px;height:34px;margin:0 auto 14px;border:3px solid #006a6044;border-top-color:#006a60;border-radius:50%;animation:s 0.9s linear infinite}
@keyframes s{to{transform:rotate(360deg)}}
p{margin:0;opacity:.7}
</style><main><h1>Universal</h1><i></i><p id="m">Запускаю…</p></main>"""


def _boot(progress=None):
    """Всё тяжёлое при запуске: набор офлайн, движки, сервер. -> (webapp, server, url).
    Окно к этому моменту уже может быть открыто (заставка), поэтому на медленном ПК человек сразу видит приложение."""
    say = progress or (lambda _m: None)
    import offline
    offline.seed(lambda i, n, name: say(f"Раскладываю набор для работы без интернета, один раз: {i} из {n}"))   # до проверки «чего не хватает»
    say("Запускаю…")
    engines.activate()            # PATH к движкам и свежий yt-dlp, до импорта app
    import app as webapp

    server, port = _listen(webapp.app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    _wait(url + "api/engines")
    import perf
    perf.warm()                   # есть ли видеокарта для кодирования: пробы идут в фоне, пока человек выбирает файлы

    def quit_app():
        # обновление на Windows: установщик ждёт, пока приложение закроется
        try:
            import webview  # type: ignore
            for w in list(webview.windows):
                w.destroy()
        finally:
            threading.Timer(2, os._exit, [0]).start()
    webapp.app.config["QUIT_APP"] = quit_app
    if "--smoke" not in sys.argv:
        import phoneserver
        phoneserver.autostart(webapp.app)     # «Сервер для телефона» был включён — включаем снова
        webapp.start_setup()                  # первый запуск: недостающие движки докачиваются сами
    return webapp, server, url


class Api:
    """Что страница может попросить у окна приложения. Публичные методы доступны из JavaScript: pywebview.api.pick_files()."""

    def __init__(self):
        self._window = None                # с подчёркиванием: pywebview не должен выставлять окно в страницу

    def pick_files(self):
        """Диалог выбора файлов самой системы. Возвращает пути: файл читается на месте, без копирования через браузер."""
        import webview  # type: ignore
        kind = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        chosen = self._window.create_file_dialog(kind, allow_multiple=True) if self._window else None
        out = []
        for p in chosen or []:
            try:
                out.append({"path": str(p), "name": os.path.basename(str(p)), "size": os.path.getsize(p)})
            except OSError:
                continue
        return out


def _run_window(home) -> None:
    """Окно с заставкой открывается сразу, сервер поднимается рядом и потом подставляет в окно приложение."""
    state: dict = {}
    try:
        import webview  # type: ignore
        webview.settings["ALLOW_DOWNLOADS"] = True
        api = Api()
        window = webview.create_window("Universal", html=SPLASH, width=1280, height=860, min_size=(420, 600),
                                       background_color="#F4FBFA", text_select=True, js_api=api)
        api._window = window

        def say(msg: str) -> None:
            try:
                window.evaluate_js(f"document.getElementById('m').textContent={json_str(msg)}")
            except Exception:
                pass

        def boot() -> None:
            try:
                state["webapp"], state["server"], state["url"] = _boot(say)
                window.load_url(state["url"])
            except BaseException as e:
                state["error"] = e
                try:
                    window.destroy()
                except Exception:
                    pass
        # private_mode=False: localStorage (цвет оформления) переживает перезапуск;
        # edgechromium: никогда не откатываться на Internet Explorer
        gui = "edgechromium" if sys.platform == "win32" else None
        webview.start(boot, gui=gui, private_mode=False, storage_path=str(home / "webview"))
    except Exception as e:
        import traceback
        traceback.print_exc()
        if "error" not in state:
            if "url" not in state:
                state["webapp"], state["server"], state["url"] = _boot()
            _browser_mode(state["url"], f"{e.__class__.__name__}: {str(e)[:150]}", state["webapp"])
    if "server" in state:
        state["server"].shutdown()
    if "error" in state:
        raise state["error"]


def _crash(e: BaseException) -> None:
    """Приложение упало при запуске: показать причину и где лог, а не молча исчезнуть."""
    import traceback
    log = engines.root() / "universal.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            traceback.print_exc(file=f)
    except OSError:
        pass
    _notice(f"Universal не запустился: {e.__class__.__name__}: {str(e)[:300]}\n\n"
            f"Подробности в файле {log}. Пришли его разработчику: "
            "https://github.com/Zaxar1231/Universal-app/issues", "Universal — ошибка")


if __name__ == "__main__":
    try:
        main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as e:
        _crash(e)
        sys.exit(1)
