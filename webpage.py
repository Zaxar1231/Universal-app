"""
Сохранить страницу сайта одним HTML-файлом: стили, картинки и шрифты вшиты внутрь,
файл открывается без интернета (как у monolith, https://github.com/Y2Z/monolith, CC0).

Два способа:
1. monolith, если он есть: на Windows, macOS и Linux его скачивает engines.py. Точнее: умеет iframe,
   srcset, @import, MHTML и вшивает видео и звук.
2. Встроенный на Python: работает везде, в том числе на телефоне (там monolith не запустить) и в Docker.
   Если monolith упал на какой-то странице, она пробуется встроенным.

Результат: <заголовок страницы>.html рядом с остальными файлами задания.
"""
from __future__ import annotations

import base64
import html as htmllib
import http.cookiejar
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import net
from tasks import Cancelled, Task, kill_tree

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
MAX_PAGE = 30 << 20         # сама страница
MAX_RES = 20 << 20          # один файл внутри страницы (картинка, шрифт, стиль)
MAX_MEDIA = 15 << 20        # видео и звук, если их не отключили
MAX_TOTAL = 150 << 20       # всё вместе внутри одного HTML
MAX_COUNT = 800             # файлов внутри одной страницы
MONOLITH_TIMEOUT = 300      # секунд на одну страницу
PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"


class PageError(Exception):
    pass


def available() -> bool:
    return True          # встроенный способ есть всегда


def monolith_exe() -> str | None:
    return shutil.which("monolith")


def read_opts(form) -> dict:
    import downloader
    o = {
        "no_js": form.get("no_js", "1") == "1",           # без скриптов: страница читается, не пытаясь ходить в сеть
        "no_media": form.get("no_media", "1") == "1",     # без видео и звука: иначе файл вырастает на сотни МБ
        "no_images": form.get("no_images") == "1",
        "no_fonts": form.get("no_fonts") == "1",
        "builtin": form.get("builtin") == "1",            # принудительно встроенный способ (проверки)
    }
    o.update(downloader.read_net(form))
    return o


# --------------------------------------------------------------------- получение файлов

class _Fetcher:
    """Скачивание с cookies, прокси и общим лимитом на объём страницы."""

    def __init__(self, o: dict, cookiefile: Path | None, task: Task | None):
        self.task = task
        proxy = o.get("proxy", "auto")
        handlers: list = []
        if proxy == "none":
            handlers.append(urllib.request.ProxyHandler({}))
        elif proxy != "auto":
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self.jar = http.cookiejar.MozillaCookieJar()
        if cookiefile and cookiefile.is_file():
            try:
                self.jar.load(str(cookiefile), ignore_discard=True, ignore_expires=True)
            except (OSError, http.cookiejar.LoadError):
                raise PageError("cookies.txt не читается: нужен файл в формате Netscape (его делает расширение «Get cookies.txt LOCALLY»)")
            for c in self.jar:
                if not c.expires:        # 0 в cookies.txt — сессионная cookie; Python 3.12 считает её уже просроченной
                    c.expires = None
                    c.discard = True
        handlers.append(urllib.request.HTTPCookieProcessor(self.jar))
        self.handlers = handlers
        self.total = 0
        self.count = 0
        self.lock = threading.Lock()

    def get(self, url: str, limit: int = MAX_RES, referer: str = "") -> tuple[bytes, str, str, str]:
        """-> (байты, mime, кодировка из заголовка, адрес после переадресации)"""
        if self.task:
            self.task.check_cancel()
        if not re.match(r"https?://", url, re.I):
            raise PageError("поддерживаются только http и https")
        with self.lock:
            if self.count >= MAX_COUNT or self.total >= MAX_TOTAL:
                raise PageError("слишком много файлов внутри страницы")
            self.count += 1
        headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "ru,en;q=0.8",
                   "Accept-Encoding": "gzip, deflate"}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        with net.open_with(req, *self.handlers, timeout=30) as r:
            data = r.read(limit + 1)
            if len(data) > limit:
                raise PageError("файл слишком большой")
            enc = (r.headers.get("Content-Encoding") or "").lower()
            if enc in ("gzip", "x-gzip"):
                data = zlib.decompress(data, 47)
            elif enc == "deflate":
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    data = zlib.decompress(data, -15)
            mime = r.headers.get_content_type() or ""
            return data, mime, r.headers.get_content_charset() or "", r.geturl()


def _decode(data: bytes, header_charset: str = "") -> str:
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    cands = [header_charset]
    m = re.search(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", data[:4096], re.I)
    if m:
        cands.append(m.group(1).decode("ascii", "ignore"))
    m = re.match(rb"""\s*@charset\s+["']([\w.:-]+)["']""", data[:200], re.I)
    if m:
        cands.append(m.group(1).decode("ascii", "ignore"))
    cands.append("utf-8")
    for c in cands:
        if not c:
            continue
        try:
            return data.decode(c)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def _sniff(data: bytes, url: str, mime: str) -> str:
    if mime and mime not in ("application/octet-stream", "binary/octet-stream", "text/plain"):
        return mime
    for sig, m in ((b"\x89PNG", "image/png"), (b"\xff\xd8", "image/jpeg"), (b"GIF8", "image/gif"),
                   (b"RIFF", "image/webp"), (b"wOF2", "font/woff2"), (b"wOFF", "font/woff"),
                   (b"\x00\x00\x01\x00", "image/x-icon")):
        if data.startswith(sig):
            return m
    if data[:5] == b"<?xml" or data.lstrip()[:4] == b"<svg":
        return "image/svg+xml"
    guess = mimetypes.guess_type(urllib.parse.urlparse(url).path)[0]
    return guess or mime or "application/octet-stream"


# --------------------------------------------------------------------- встроенный сохранитель

CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.I | re.S)
CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*(['"]?)(.*?)\1\s*\)|(['"])(.*?)\3)\s*([^;{]*);""", re.I)
FONT_RE = re.compile(r"\.(woff2?|ttf|otf|eot)(\?|#|$)", re.I)
DROP_REL = {"preload", "prefetch", "dns-prefetch", "preconnect", "modulepreload", "prerender", "manifest", "alternate"}


def _rels(tag) -> set[str]:
    r = tag.get("rel") or []
    return {x.lower() for x in (r.split() if isinstance(r, str) else r)}


class _Inliner:
    def __init__(self, fetcher: _Fetcher, o: dict, page_url: str):
        self.f, self.o, self.page_url = fetcher, o, page_url
        self.cache: dict[str, str | None] = {}
        self.css_cache: dict[str, str | None] = {}
        self.failed = 0
        self.lock = threading.Lock()

    # ----- файлы
    def data_uri(self, url: str, limit: int = MAX_RES) -> str | None:
        with self.lock:
            if url in self.cache:
                return self.cache[url]
        try:
            data, mime, _cs, final = self.f.get(url, limit, self.page_url)
            with self.f.lock:
                self.f.total += len(data)
            uri = f"data:{_sniff(data, final, mime)};base64,{base64.b64encode(data).decode('ascii')}"
        except Cancelled:
            raise
        except Exception:
            uri = None
            self.failed += 1
        with self.lock:
            self.cache[url] = uri
        return uri

    def prefetch(self, urls: list[str], limit: int = MAX_RES) -> None:
        todo = [u for u in dict.fromkeys(urls) if u not in self.cache]
        if len(todo) > 1:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda u: self.data_uri(u, limit), todo))

    def fetch_css(self, url: str, depth: int) -> str | None:
        with self.lock:
            if url in self.css_cache:
                return self.css_cache[url]
        try:
            data, _mime, cs, final = self.f.get(url, MAX_RES, self.page_url)
            css = self.process_css(_decode(data, cs), final, depth)
        except Cancelled:
            raise
        except Exception:
            css = None
            self.failed += 1
        with self.lock:
            self.css_cache[url] = css
        return css

    # ----- CSS
    def process_css(self, css: str, base: str, depth: int = 0) -> str:
        def imp(m):
            href = (m.group(2) if m.group(2) is not None else m.group(4)) or ""
            media = (m.group(5) or "").strip()
            target = urllib.parse.urljoin(base, href.strip())
            text = self.fetch_css(target, depth + 1) if depth < 3 else None
            if text is None:
                return f'@import url("{target}"){" " + media if media else ""};'
            return f"@media {media} {{\n{text}\n}}" if media else text
        css = CSS_IMPORT.sub(imp, css)

        refs = []
        for m in CSS_URL.finditer(css):
            ref = m.group(2).strip()
            if ref and not ref.startswith(("data:", "#", "about:")):
                refs.append(urllib.parse.urljoin(base, ref))
        self.prefetch([r for r in refs if not (FONT_RE.search(r) and self.o["no_fonts"]) and not (
            not FONT_RE.search(r) and self.o["no_images"])])

        def one(m):
            ref = m.group(2).strip()
            if not ref or ref.startswith(("data:", "#", "about:")):
                return m.group(0)
            target = urllib.parse.urljoin(base, ref)
            is_font = bool(FONT_RE.search(target))
            if (is_font and self.o["no_fonts"]) or (not is_font and self.o["no_images"]):
                return f'url("{PIXEL}")'
            uri = self.data_uri(target)
            return f'url("{uri or target}")'
        return CSS_URL.sub(one, css)

    # ----- страница
    def abs(self, base: str, ref: str | None) -> str | None:
        if not ref:
            return None
        ref = ref.strip()
        if not ref or ref.startswith(("data:", "#", "javascript:", "mailto:", "tel:", "about:", "blob:")):
            return None
        return urllib.parse.urljoin(base, ref)

    def transform(self, soup, now: str):
        from bs4 import Comment
        o = self.o
        base = self.page_url
        base_tag = soup.find("base", href=True)
        if base_tag:
            base = urllib.parse.urljoin(base, base_tag["href"])
            base_tag.decompose()

        # скрипты
        if o["no_js"]:
            for s in soup.find_all("script"):
                s.decompose()
            for t in soup.find_all(True):
                for a in [a for a in t.attrs if a.lower().startswith("on")]:
                    del t[a]
                for a in ("href", "src", "action"):
                    if isinstance(t.get(a), str) and t[a].strip().lower().startswith("javascript:"):
                        t[a] = "#"
            for ns in soup.find_all("noscript"):
                ns.unwrap()                       # без скриптов виден запасной вариант страницы
        else:
            for s in soup.find_all("script", src=True):
                u = self.abs(base, s["src"])
                if u:
                    s["src"] = u
                s.attrs.pop("integrity", None)
        for t in soup.find_all(["meta"], attrs={"http-equiv": re.compile("^refresh$", re.I)}):
            t.decompose()                         # переадресация сломала бы сохранённую копию

        # <style> и style="" (до ссылок на стили: вставленные из них блоки уже готовы)
        for st in soup.find_all("style"):
            text = st.string if st.string is not None else st.get_text()
            if text and ("url(" in text.lower() or "@import" in text.lower()):
                st.string = self.process_css(text, base)
        for t in soup.find_all(style=True):
            if "url(" in t["style"].lower():
                t["style"] = self.process_css(t["style"], base)

        # внешние стили
        sheets = [l for l in soup.find_all("link", href=True) if "stylesheet" in _rels(l)]
        urls = [self.abs(base, l["href"]) for l in sheets]
        with ThreadPoolExecutor(max_workers=6) as pool:
            texts = list(pool.map(lambda u: self.fetch_css(u, 0) if u else None, urls))
        for l, u, text in zip(sheets, urls, texts):
            if text is None:
                if u:
                    l["href"] = u
                l.attrs.pop("integrity", None)
                continue
            st = soup.new_tag("style")
            if l.get("media"):
                st["media"] = l["media"]
            st.string = text
            l.replace_with(st)

        # значки и прочие <link>
        for l in soup.find_all("link", href=True):
            rels = _rels(l)
            if rels & {"icon", "shortcut", "apple-touch-icon", "mask-icon"} and not o["no_images"]:
                u = self.abs(base, l["href"])
                uri = self.data_uri(u) if u else None
                l["href"] = uri or u or l["href"]
            elif rels & DROP_REL:
                l.decompose()
            else:
                u = self.abs(base, l["href"])
                if u:
                    l["href"] = u
                l.attrs.pop("integrity", None)

        # картинки
        imgs = soup.find_all("img")
        for pic in soup.find_all("picture"):
            for s in pic.find_all("source"):
                s.decompose()                     # в сохранённой копии выбор варианта картинки не нужен
        plan = []
        for img in imgs:
            src = img.get("src") or ""
            if (not src or src.startswith("data:")) and img.get("data-src"):
                src = img["data-src"]
            if not src or src.startswith("data:"):
                ss = img.get("srcset") or img.get("data-srcset") or ""
                first = ss.split(",")[0].strip().split(" ")[0] if ss else ""
                src = first or src
            plan.append((img, self.abs(base, src)))
        if not o["no_images"]:
            self.prefetch([u for _, u in plan if u])
        for img, u in plan:
            for a in ("srcset", "data-srcset", "sizes", "data-src", "loading", "integrity"):
                img.attrs.pop(a, None)
            if o["no_images"]:
                img["src"] = PIXEL
            elif u:
                img["src"] = self.data_uri(u) or u
        for tag in soup.find_all(["image"]):          # картинки внутри SVG
            for a in ("href", "xlink:href"):
                u = self.abs(base, tag.get(a))
                if u:
                    tag[a] = PIXEL if o["no_images"] else (self.data_uri(u) or u)
        for tag in soup.find_all(["input"], attrs={"type": re.compile("^image$", re.I)}):
            u = self.abs(base, tag.get("src"))
            if u:
                tag["src"] = PIXEL if o["no_images"] else (self.data_uri(u) or u)

        # видео и звук
        for tag in soup.find_all(["video", "audio"]):
            if o["no_media"]:
                tag.decompose()
                continue
            for a in ("src", "poster"):
                u = self.abs(base, tag.get(a))
                if u:
                    tag[a] = PIXEL if (a == "poster" and o["no_images"]) else (self.data_uri(u, MAX_MEDIA) or u)
            for s in tag.find_all("source"):
                u = self.abs(base, s.get("src"))
                if u:
                    s["src"] = self.data_uri(u, MAX_MEDIA) or u
        for tag in soup.find_all("track"):
            tag.decompose()

        # ссылки остаются рабочими: делаем адреса полными
        for tag, attr in [(t, "href") for t in soup.find_all(["a", "area"], href=True)] + \
                         [(t, "action") for t in soup.find_all("form", action=True)] + \
                         [(t, "src") for t in soup.find_all(["iframe", "embed", "frame"], src=True)] + \
                         [(t, "data") for t in soup.find_all("object", data=True)]:
            u = self.abs(base, tag.get(attr))
            if u:
                tag[attr] = u

        # кодировка и пометка
        for m in soup.find_all("meta"):
            if m.get("charset") is not None or (m.get("http-equiv") or "").lower() == "content-type":
                m.decompose()
        head = soup.find("head")
        if head is None:
            head = soup.new_tag("head")
            (soup.html or soup).insert(0, head)
        meta = soup.new_tag("meta", charset="utf-8")
        head.insert(0, meta)
        soup.insert(0, Comment(f" Сохранено Universal: {self.page_url} · {now} "))
        return soup


def _title_of(text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", text[:300000], re.I | re.S)
    t = htmllib.unescape(m.group(1)) if m else ""
    return re.sub(r"\s+", " ", t).strip()


def _safe_name(title: str, url: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" ._")
    if not name:
        u = urllib.parse.urlparse(url)
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (u.netloc + u.path).strip("/").replace("/", "_")) or "page"
    return name[:80].rstrip(" ._") or "page"


def _unique(out_dir: Path, stem: str, ext: str) -> Path:
    p = out_dir / f"{stem}{ext}"
    n = 1
    while p.exists():
        n += 1
        p = out_dir / f"{stem} ({n}){ext}"
    return p


def _save_builtin(url: str, out_dir: Path, o: dict, cookiefile: Path | None, task: Task | None) -> Path:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise PageError("Не установлен beautifulsoup4: pip install beautifulsoup4")
    f = _Fetcher(o, cookiefile, task)
    try:
        data, mime, cs, final = f.get(url, MAX_PAGE)
    except Cancelled:
        raise
    except urllib.error.HTTPError as e:
        raise PageError(f"Сайт ответил ошибкой {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise PageError("Страница не открылась: " + net.reason(e))
    if mime and mime not in ("text/html", "application/xhtml+xml", "text/plain", "application/octet-stream") \
            and not data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        # по ссылке не страница, а файл (картинка, PDF): сохраняем как есть
        ext = mimetypes.guess_extension(mime) or Path(urllib.parse.urlparse(final).path).suffix or ".bin"
        name = _safe_name(Path(urllib.parse.unquote(urllib.parse.urlparse(final).path)).stem, final)
        dst = _unique(out_dir, name, ext)
        dst.write_bytes(data)
        return dst
    text = _decode(data, cs)
    soup = BeautifulSoup(text, "html.parser")
    inl = _Inliner(f, o, final)
    inl.transform(soup, datetime.now().strftime("%Y-%m-%d %H:%M"))
    title = (soup.title.get_text() if soup.title else "") or _title_of(text)
    dst = _unique(out_dir, _safe_name(re.sub(r"\s+", " ", title).strip(), final), ".html")
    dst.write_text(str(soup), encoding="utf-8")
    return dst


# --------------------------------------------------------------------- monolith

def _save_monolith(exe: str, url: str, out_dir: Path, o: dict, cookiefile: Path | None,
                   task: Task | None) -> Path:
    tmp = out_dir / f".page_{os.getpid()}_{int(time.time() * 1000) % 10**8}.html"
    cmd = [exe, url, "-o", str(tmp), "-e", "-q", "-t", "40", "-u", UA]
    if o["no_js"]:
        cmd += ["-j", "-n"]
    if o["no_images"]:
        cmd.append("-i")
    if o["no_fonts"]:
        cmd.append("-F")
    if o["no_media"]:
        cmd += ["-a", "-v"]
    if cookiefile and cookiefile.is_file():
        cmd += ["-C", str(cookiefile)]
    env = os.environ.copy()
    proxy = o.get("proxy", "auto")
    if proxy == "none":
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"
    elif proxy != "auto":
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[k] = proxy
    log = out_dir / (tmp.name + ".log")
    extra = {} if os.name == "nt" else {"start_new_session": True}
    with log.open("w", encoding="utf-8") as lf:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, env=env,
                                cwd=str(out_dir), **extra)
        if task:
            task.proc = proc
        deadline = time.monotonic() + MONOLITH_TIMEOUT
        try:
            while proc.poll() is None:
                if task and task.cancel_event.is_set():
                    kill_tree(proc)
                    raise Cancelled()
                if time.monotonic() > deadline:
                    kill_tree(proc)
                    raise PageError("страница сохранялась слишком долго")
                time.sleep(0.2)
        finally:
            if task:
                task.proc = None
    tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] if log.exists() else []
    log.unlink(missing_ok=True)
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise PageError("monolith не справился" + (": " + tail[0][:200] if tail else ""))
    text = tmp.read_bytes()[:300000].decode("utf-8", "replace")
    dst = _unique(out_dir, _safe_name(_title_of(text), url), ".html")
    os.replace(tmp, dst)
    return dst


# --------------------------------------------------------------------- задача

def _short(url: str) -> str:
    return url if len(url) <= 70 else url[:67] + "…"


def run_page_task(task: Task, urls: list[str], o: dict, cookiefile: Path | None) -> None:
    out = task.out_dir
    n = len(urls)
    task.total = n
    exe = None if (o.get("builtin") or os.environ.get("UNIVERSAL_ANDROID")) else monolith_exe()
    try:
        for i, url in enumerate(urls):
            task.check_cancel()
            task.update(i / n, f"Сохраняю {i + 1} из {n}: {_short(url)}")
            try:
                dst = None
                if exe:
                    try:
                        dst = _save_monolith(exe, url, out, o, cookiefile, task)
                    except (PageError, OSError):
                        dst = None                       # запасной путь: встроенный
                if dst is None:
                    dst = _save_builtin(url, out, o, cookiefile, task)
                task.done += 1
            except Cancelled:
                raise
            except Exception as e:
                if task.cancel_event.is_set():
                    raise Cancelled()
                msg = str(e) if isinstance(e, PageError) else f"{e.__class__.__name__}: {str(e)[:150]}"
                task.failed.append({"name": url, "error": msg})
    finally:
        if cookiefile:
            cookiefile.unlink(missing_ok=True)
    task.update(1, "Готово" if not task.failed else "Готово, но не всё сохранилось")
    if not task.done and task.failed:
        raise PageError(task.failed[0]["error"])
