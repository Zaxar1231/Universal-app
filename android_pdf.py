"""
PDF в картинки на телефоне без poppler: страницы рисует сам Android (android.graphics.pdf.PdfRenderer)
через мост Java встроенного Python (Chaquopy). Импортируется только на Android.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from java import jclass  # type: ignore  (есть только во встроенном Python на Android)

File = jclass("java.io.File")
FileOutputStream = jclass("java.io.FileOutputStream")
ParcelFileDescriptor = jclass("android.os.ParcelFileDescriptor")
PdfRenderer = jclass("android.graphics.pdf.PdfRenderer")
Page = jclass("android.graphics.pdf.PdfRenderer$Page")
Bitmap = jclass("android.graphics.Bitmap")
Config = jclass("android.graphics.Bitmap$Config")
CompressFormat = jclass("android.graphics.Bitmap$CompressFormat")
Color = jclass("android.graphics.Color")

MAX_SIDE = 7000        # больше Android-битмап может не влезть в память


class _Doc:
    def __init__(self, path: Path):
        try:
            self.fd = ParcelFileDescriptor.open(File(str(path)), ParcelFileDescriptor.MODE_READ_ONLY)
            self.r = PdfRenderer(self.fd)
        except Exception as e:
            raise RuntimeError("PDF не открылся (повреждён или под паролем): " + str(e)[:150])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        try:
            self.r.close()
        finally:
            self.fd.close()


def page_count(path: Path) -> int:
    with _Doc(path) as d:
        return d.r.getPageCount()


def _render(d: _Doc, index: int, dpi: int, out: Path) -> Path:
    page = d.r.openPage(index)
    try:
        k = dpi / 72.0
        w, h = max(1, int(page.getWidth() * k)), max(1, int(page.getHeight() * k))
        if max(w, h) > MAX_SIDE:
            s = MAX_SIDE / max(w, h)
            w, h = max(1, int(w * s)), max(1, int(h * s))
        bmp = Bitmap.createBitmap(w, h, Config.ARGB_8888)
        bmp.eraseColor(Color.WHITE)
        page.render(bmp, None, None, Page.RENDER_MODE_FOR_PRINT)
    finally:
        page.close()
    fos = FileOutputStream(str(out))
    try:
        bmp.compress(CompressFormat.PNG, 100, fos)
    finally:
        fos.close()
        bmp.recycle()
    return out


def render_page(path: Path, index: int, dpi: int, out: Path | None = None) -> Path:
    out = out or Path(tempfile.mkstemp(suffix=".png")[1])
    with _Doc(path) as d:
        return _render(d, index, dpi, out)


def render_pages(path: Path, pages: list[int], dpi: int, out_for) -> list[Path]:
    """out_for(i) -> путь PNG для страницы i (с нуля)."""
    with _Doc(path) as d:
        return [_render(d, i, dpi, out_for(i)) for i in pages]
