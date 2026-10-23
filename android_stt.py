"""
Голос в текст на телефоне: Whisper (CTranslate2) на Android не собирается, поэтому распознаёт Vosk
(Kaldi, офлайн) через мост Java встроенного Python. Звук в 16 кГц моно делает ffmpeg.
Модель для языка (≈ 40–130 МБ) скачивается при первом распознавании.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.request
import zipfile
from pathlib import Path

from java import jarray, jbyte, jclass  # type: ignore

import engines
from tasks import Task

Model = jclass("org.vosk.Model")
Recognizer = jclass("org.vosk.Recognizer")

URL = "https://alphacephei.com/vosk/models/{name}.zip"
MODELS = {   # язык: (модель, примерный размер, МБ)
    "ru": ("vosk-model-small-ru-0.22", 45),
    "en": ("vosk-model-small-en-us-0.15", 40),
    "uk": ("vosk-model-small-uk-v3-small", 133),
}
LANGS = {"ru": "Русский", "en": "Английский", "uk": "Украинский"}
SR = 16000


class SttError(Exception):
    pass


def model_dir(lang: str) -> Path:
    return engines.root() / "vosk" / MODELS[lang][0]


def downloaded(lang: str) -> bool:
    return (model_dir(lang) / "am").is_dir() or (model_dir(lang) / "conf").is_dir()


def ensure_model(lang: str, task: Task | None = None) -> Path:
    if lang not in MODELS:
        raise SttError(f"на телефоне язык {lang} пока не распознаётся: выбери русский, английский или украинский")
    d = model_dir(lang)
    if downloaded(lang):
        return d
    name, mb = MODELS[lang]
    d.parent.mkdir(parents=True, exist_ok=True)
    zpath = d.parent / f"{name}.zip.part"
    try:
        req = urllib.request.Request(URL.format(name=name), headers={"User-Agent": "Universal"})
        with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or mb * 1048576)
            got = 0
            while True:
                if task:
                    task.check_cancel()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if task:
                    task.update(min(0.99, got / total) * 0.3, f"Скачиваю модель распознавания (один раз): {got >> 20} из {total >> 20} МБ")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(d.parent)         # внутри архива папка с именем модели
    except SttError:
        raise
    except Exception as e:
        if task and task.cancel_event.is_set():
            raise
        raise SttError(f"не скачалась модель распознавания ({str(e)[:120]}). Нужен интернет один раз")
    finally:
        zpath.unlink(missing_ok=True)
    if not downloaded(lang):
        raise SttError("модель распознавания распаковалась не так")
    return d


_models: dict = {}
_lock = threading.Lock()


def _model(lang: str):
    with _lock:
        if lang not in _models:
            _models.clear()
            _models[lang] = Model(str(ensure_model(lang)))
        return _models[lang]


def _duration(src: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    r = subprocess.run([ffprobe, "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def _segment(res: dict):
    words = res.get("result") or []
    text = (res.get("text") or "").strip()
    if not text:
        return None
    if words:
        return (float(words[0]["start"]), float(words[-1]["end"]), text)
    return (0.0, 0.0, text)


def transcribe(src: Path, lang: str, task: Task | None = None, base: float = 0.0, span: float = 1.0):
    """-> (сегменты [(начало, конец, текст)], язык, длительность)"""
    lang = lang or "ru"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SttError("нет ffmpeg")
    model = _model(lang)
    rec = Recognizer(model, float(SR))
    rec.setWords(True)
    dur = _duration(src)
    proc = subprocess.Popen([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
                             "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    segs, got = [], 0
    try:
        while True:
            chunk = proc.stdout.read(SR * 2 // 2)      # полсекунды звука
            if not chunk:
                break
            got += len(chunk)
            if task:
                task.check_cancel()
                if dur:
                    task.update(base + span * min(1.0, got / (dur * SR * 2)))
            if rec.acceptWaveForm(jarray(jbyte)(chunk), len(chunk)):   # явный byte[]: есть ещё short[] и float[]
                s = _segment(json.loads(rec.getResult()))
                if s:
                    segs.append(s)
        s = _segment(json.loads(rec.getFinalResult()))
        if s:
            segs.append(s)
    finally:
        proc.stdout.close()
        proc.wait()
        rec.close()
    if got == 0:
        err = proc.stderr.read().decode("utf-8", "replace").strip().splitlines()
        raise SttError("в файле нет звука" if not err else err[-1][:200])
    return segs, lang, dur or got / (SR * 2)
