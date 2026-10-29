"""
HTTPS для всех загрузок Universal (программы, модели распознавания, обновления).

Обычный Python сверяет сертификаты только с корневыми, которые уже лежат в хранилище Windows.
Windows докачивает недостающие корневые сертификаты по требованию, но только своей проверкой,
поэтому на свежей или редко обновляемой системе всё падало с CERTIFICATE_VERIFY_FAILED.
Порядок проверки:
1. truststore — проверка самой системой (Windows, macOS): докачка корневых, сертификаты
   антивирусов и корпоративных прокси, которые подменяют HTTPS;
2. стандартная проверка Python плюс набор корневых certifi из сборки — если системное хранилище
   устарело или испорчено.
Проверка сертификатов не отключается никогда.
"""
from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path

_lock = threading.Lock()
_contexts: list[tuple[str, ssl.SSLContext]] | None = None
_good = 0            # номер способа, который сработал последним: с него и начинаем


def _build() -> list[tuple[str, ssl.SSLContext]]:
    out = []
    try:
        import truststore
        out.append(("system", truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))
    except Exception:          # нет пакета или система не поддерживается
        pass
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    out.append(("bundled", ctx))
    return out


class CertError(urllib.error.URLError):
    """Ни один способ не проверил сертификат: текст для человека, что делать."""


def contexts() -> list[tuple[str, ssl.SSLContext]]:
    global _contexts
    with _lock:
        if _contexts is None:
            _contexts = _build()
        return _contexts


def is_cert_error(e: BaseException) -> bool:
    if isinstance(e, urllib.error.URLError) and isinstance(e.reason, BaseException):
        e = e.reason
    return isinstance(e, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)


def urlopen(req, timeout: float = 30):
    """Как urllib.request.urlopen, но перебирает способы проверки HTTPS (см. выше)."""
    return open_with(req, timeout=timeout)


def open_with(req, *handlers, timeout: float = 30):
    """urlopen с дополнительными обработчиками urllib (прокси, cookies) и тем же перебором проверок HTTPS."""
    global _good
    ctxs = contexts()
    order = [_good] + [i for i in range(len(ctxs)) if i != _good]
    last = None
    for i in order:
        opener = urllib.request.build_opener(*handlers, urllib.request.HTTPSHandler(context=ctxs[i][1]))
        try:
            r = opener.open(req, timeout=timeout)
            _good = i
            return r
        except Exception as e:
            if not is_cert_error(e):
                raise
            last = e
    raise CertError(
        "не удалось проверить сертификат HTTPS: на компьютере устарели корневые сертификаты "
        "или антивирус подменяет HTTPS. Обнови Windows или выключи в антивирусе проверку HTTPS "
        f"({str(getattr(last, 'reason', last))[:120]})")


def reason(e: BaseException) -> str:
    """Коротко, почему не скачалось: для людей, а не только имя исключения."""
    r = getattr(e, "reason", None)
    text = str(r if r is not None else e).strip() or e.__class__.__name__
    if isinstance(e, CertError):
        return text
    if is_cert_error(e):
        return "не прошла проверка сертификата HTTPS"
    return f"{e.__class__.__name__}: {text[:120]}"


def download(url: str, dest: Path, *, headers: dict | None = None, timeout: float = 60,
             progress=None, check_cancel=None) -> Path:
    """Скачать в dest с докачкой. Недокачанное лежит рядом (dest.part, а в dest.part.json — адрес и
    ETag/Last-Modified): после обрыва или закрытия приложения продолжаем с того же места
    (Range + If-Range: если файл на сервере успел смениться, сервер отдаст его целиком, и начнём заново).
    progress(получено, всего или 0); check_cancel() бросает исключение, если задачу отменили."""
    part = dest.with_name(dest.name + ".part")
    meta = dest.with_name(dest.name + ".part.json")
    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    have = part.stat().st_size if part.is_file() else 0
    validator = info.get("etag") or info.get("last_modified")
    h = dict(headers or {})
    if have and info.get("url") == url and validator and not str(validator).startswith("W/"):
        h["Range"] = f"bytes={have}-"
        h["If-Range"] = validator
    else:
        have = 0
    try:
        r = urlopen(urllib.request.Request(url, headers=h), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:          # всё уже скачано, осталось переименовать
            part.replace(dest)
            meta.unlink(missing_ok=True)
            return dest
        raise
    with r:
        resumed = r.status == 206
        if not resumed:
            have = 0
            meta.write_text(json.dumps({"url": url, "etag": r.headers.get("ETag"),
                                        "last_modified": r.headers.get("Last-Modified")}), encoding="utf-8")
        length = int(r.headers.get("Content-Length") or 0)
        total = have + length if length else 0
        got = have
        with part.open("ab" if resumed else "wb") as f:
            while True:
                if check_cancel:
                    check_cancel()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    if total and got < total:              # оборвалось: недокачанное остаётся до следующей попытки
        raise urllib.error.URLError(f"соединение оборвалось на {got >> 20} из {total >> 20} МБ")
    part.replace(dest)
    meta.unlink(missing_ok=True)
    return dest
