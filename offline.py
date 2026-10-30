"""
Офлайн-сборка для Windows: движки (ffmpeg, pandoc, ImageMagick, poppler, deno, Tesseract, monolith) и модели
(Whisper «Быстрая» и «Обычная», определение голосов) лежат прямо в папке приложения, в подпапке `offline`.
Устроена она как папка данных Universal: engines/<имя>, whisper/<размер>, diarization.

При запуске seed() раскладывает недостающее в папку данных, после чего всё работает как обычно, без интернета.
  установленное приложение  переносит (на том же диске это мгновенно, места второй раз не занимает);
  портативная папка         копирует, чтобы её можно было унести на флешке дальше.
Уже существующее не трогает: скачанное человеком новее и важнее.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import engines

ROOT = Path(__file__).resolve().parent


def bundle_dir() -> Path | None:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT
    d = base / "offline"
    return d if d.is_dir() else None


def _installed(bundle: Path) -> bool:
    """Приложение поставил установщик (рядом лежит unins000.exe), а не распаковали из zip."""
    return any(bundle.parent.glob("unins*.exe"))


def _units(bundle: Path) -> list[tuple[Path, Path]]:
    """(что, куда): каждый движок, модель Whisper и модели голосов отдельной единицей."""
    root = engines.root()
    out = []
    for sub in ("engines", "whisper"):
        src = bundle / sub
        if src.is_dir():
            out += [(p, root / sub / p.name) for p in sorted(src.iterdir()) if p.is_dir() and p.name != "downloads"]
    if (bundle / "diarization").is_dir():
        out.append((bundle / "diarization", root / "diarization"))
    return out


def pending(bundle: Path | None = None) -> list[tuple[Path, Path]]:
    bundle = bundle or bundle_dir()
    return [(s, d) for s, d in _units(bundle) if not d.exists()] if bundle else []


def seed(progress=None) -> list[str]:
    """Раскладывает офлайн-набор. -> имена того, что разложено. Ошибка одной части не мешает остальным."""
    bundle = bundle_dir()
    if not bundle:
        return []
    move = _installed(bundle)
    done = []
    todo = pending(bundle)
    for n, (src, dst) in enumerate(todo, 1):
        if progress:
            progress(n, len(todo), dst.name)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".seed")
            shutil.rmtree(tmp, ignore_errors=True)
            if move:
                try:
                    os.replace(src, tmp)                 # тот же диск: мгновенно
                except OSError:
                    shutil.copytree(src, tmp)            # другой диск: копия, потом убираем исходник
                    shutil.rmtree(src, ignore_errors=True)
            else:
                shutil.copytree(src, tmp)
            os.replace(tmp, dst)
            done.append(dst.relative_to(engines.root()).as_posix())
        except OSError as e:
            print(f"offline: {src.name}: {e}", file=sys.stderr)
    return done


def ready() -> dict:
    """Всё ли из офлайн-набора на месте (для самопроверки сборки): чего нет, список пуст."""
    import diarize
    import stt
    bundle = bundle_dir()
    shipped = sorted(p.name for p in (bundle / "whisper").iterdir()) if bundle and (bundle / "whisper").is_dir() else []
    return {"engines_missing": [n for n in engines.AUTO if not engines.available(n)],
            "whisper_missing": [m for m in shipped if not stt.downloaded(m)],
            "voices": diarize.downloaded()}
