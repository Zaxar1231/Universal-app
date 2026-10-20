"""
Иконки и заставки для Android и iOS из packaging/icon.png (та же папка, что у приложения на компьютере).
Запуск: python icons.py (нужен Pillow). Результат кладётся прямо в android/ и ios/.
"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
SRC = Image.open(HERE.parent / "packaging/icon.png").convert("RGBA")
RES = HERE / "android/app/src/main/res"
IOS = HERE / "ios/App/App/Assets.xcassets"


def solid() -> Image.Image:
    """Квадрат без прозрачных углов: iOS требует непрозрачную иконку, Android обрезает по своей маске."""
    w = SRC.width
    box = SRC.getbbox()
    pad = round(w * 0.06)                       # срезаем скруглённые углы самой картинки
    crop = SRC.crop((box[0] + pad, box[1] + pad, box[2] - pad, box[3] - pad))
    bg = Image.new("RGBA", crop.size, BG)
    bg.alpha_composite(crop)
    return bg.convert("RGB")


BG = SRC.getpixel((round(SRC.width * 0.08), round(SRC.height * 0.08)))[:3]   # тёмный фон иконки у угла
SOLID = solid()
HEX = "#%02X%02X%02X" % BG

# Android: обычная, круглая и адаптивная (передний план 108dp, видимая часть 72dp в центре)
for dpi, k in {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}.items():
    d = RES / f"mipmap-{dpi}"
    n = round(48 * k)
    SRC.resize((n, n), Image.LANCZOS).save(d / "ic_launcher.png")
    r = SOLID.resize((n, n), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (n * 4, n * 4))
    ImageDraw.Draw(mask).ellipse((0, 0, n * 4 - 1, n * 4 - 1), fill=255)
    r.putalpha(mask.resize((n, n), Image.LANCZOS))
    r.save(d / "ic_launcher_round.png")
    f = round(108 * k)
    fg = Image.new("RGBA", (f, f), (0, 0, 0, 0))
    inner = round(f * 0.80)
    fg.paste(SOLID.resize((inner, inner), Image.LANCZOS), ((f - inner) // 2, (f - inner) // 2))
    fg.save(d / "ic_launcher_foreground.png")
(RES / "values/ic_launcher_background.xml").write_text(
    f'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">{HEX}</color>\n</resources>\n')


def splash(w: int, h: int) -> Image.Image:
    im = Image.new("RGB", (w, h), BG)
    s = round(min(w, h) * 0.28)
    icon = SRC.resize((s, s), Image.LANCZOS)
    im.paste(icon, ((w - s) // 2, (h - s) // 2), icon)
    return im


for p in RES.glob("drawable*/splash.png"):
    with Image.open(p) as old:
        size = old.size
    splash(*size).save(p)

# iOS: одна иконка 1024 и заставка 2732
SOLID.resize((1024, 1024), Image.LANCZOS).save(IOS / "AppIcon.appiconset/AppIcon-512@2x.png")
for p in (IOS / "Splash.imageset").glob("*.png"):
    splash(2732, 2732).save(p)
print("иконки готовы, фон", HEX)
