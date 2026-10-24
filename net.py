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

import ssl
import threading
import urllib.error
import urllib.request

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
    global _good
    ctxs = contexts()
    order = [_good] + [i for i in range(len(ctxs)) if i != _good]
    last = None
    for i in order:
        try:
            r = urllib.request.urlopen(req, timeout=timeout, context=ctxs[i][1])
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
