"""
Собирает офлайн-набор для Windows: скачивает все движки и модели в папку, устроенную как папка данных Universal.
    python packaging/make_offline.py <папка>      (получится <папка>/engines, whisper, diarization)
Скачивает то же самое и теми же функциями, что приложение при первом запуске, поэтому заодно проверяет ссылки.
"""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WHISPER = ("fast", "normal")          # «Точная» (≈ 1,6 ГБ) в набор не входит: докачивается, когда есть интернет


def main(target: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # консоль Windows: cp1252
    out = Path(target).resolve()
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    os.environ["UNIVERSAL_HOME"] = str(out)
    import diarize
    import engines
    import stt
    for name in engines.AUTO:
        print(f"== {name}", flush=True)
        print("  ->", engines.install(name), flush=True)
    for key in WHISPER:
        print(f"== whisper {key}", flush=True)
        stt.ensure_model(key)
    print("== голоса", flush=True)
    diarize.ensure_models()
    shutil.rmtree(out / "engines" / "downloads", ignore_errors=True)
    (out / "engines" / "paths.json").unlink(missing_ok=True)       # пути абсолютные: на другом компьютере они бессмысленны
    missing = [n for n in engines.AUTO if not engines.available(n)]
    if missing or not all(stt.downloaded(k) for k in WHISPER) or not diarize.downloaded():
        sys.exit(f"Набор неполный: движки {missing}")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Готово: {size >> 20} МБ в {out}")


if __name__ == "__main__":
    main(sys.argv[1])
