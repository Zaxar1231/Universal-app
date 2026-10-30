"""
Голос в текст: голосовые из Telegram и WhatsApp (OGG/OGA/Opus), MP3, M4A, WAV и звук из видео
распознаются прямо на компьютере моделью Whisper (faster-whisper, CTranslate2, на процессоре).

Модель скачивается один раз при первом распознавании в <UNIVERSAL_HOME>/whisper/<размер>:
сначала с huggingface.co, при ошибке с зеркала hf-mirror.com. Дальше всё работает без интернета.
Результат: текст в интерфейсе, .txt и, по желанию, субтитры .srt с таймкодами.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import diarize
import perf
import engines
import net
from tasks import Task

MODELS = {
    # ключ: (репозиторий, примерный размер, подпись)
    "fast": ("Systran/faster-whisper-base", 145, "Быстрая"),
    "normal": ("Systran/faster-whisper-small", 485, "Обычная"),
    "best": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1620, "Точная"),
}
ENDPOINTS = ("https://huggingface.co", "https://hf-mirror.com")
LANGS = {"": "Определить сам", "ru": "Русский", "uk": "Украинский", "be": "Белорусский", "en": "Английский",
         "kk": "Казахский", "uz": "Узбекский", "de": "Немецкий", "fr": "Французский", "es": "Испанский",
         "it": "Итальянский", "pl": "Польский", "tr": "Турецкий", "zh": "Китайский", "ja": "Японский"}
INPUT_EXT = ("ogg", "oga", "opus", "mp3", "m4a", "aac", "wav", "flac", "wma", "amr", "weba", "webm", "mp4",
             "mkv", "mov", "avi", "3gp", "m4b", "aiff", "aif", "caf")


class SttError(Exception):
    pass


def _android() -> bool:
    """Телефон (встроенный Python): Whisper там не собирается, распознаёт Vosk (android_stt)."""
    return bool(os.environ.get("UNIVERSAL_ANDROID"))


def available() -> bool:
    if _android():
        try:
            import android_stt  # noqa: F401
            return True
        except Exception:
            return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def model_dir(key: str) -> Path:
    return engines.root() / "whisper" / key


def downloaded(key: str) -> bool:
    d = model_dir(key)
    return (d / "model.bin").is_file() and (d / "config.json").is_file()


def caps() -> dict:
    if _android() and available():
        import android_stt as a
        return {"available": True, "android": True, "langs": a.LANGS, "diarize": diarize.caps(),
                "models": [{"key": "normal", "label": "Встроенная (Vosk)", "mb": a.MODELS["ru"][1],
                            "downloaded": a.downloaded("ru")}]}
    return {"available": available(), "langs": LANGS, "diarize": diarize.caps(),
            "models": [{"key": k, "label": lbl, "mb": mb, "downloaded": downloaded(k)} for k, (_, mb, lbl) in MODELS.items()]}


def read_opts(form) -> dict:
    o = {"model": form.get("model", "normal"), "lang": form.get("lang", ""), "srt": form.get("srt") == "1",
         "speakers": diarize.read_speakers(form.get("speakers"))}
    if o["model"] not in MODELS:
        raise SttError("Неизвестная модель")
    if o["lang"] not in LANGS:
        o["lang"] = ""
    if not available():
        raise SttError("Не установлен faster-whisper: pip install faster-whisper")
    if o["speakers"] and not diarize.available():
        raise SttError("На телефоне разделения по голосам пока нет" if _android()
                       else "Разделить по голосам нельзя: не установлен sherpa-onnx (pip install sherpa-onnx)")
    return o


# --------------------------------------------------------------------- скачать модель

def _get(url: str, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "Universal"})
    return net.urlopen(req, timeout=timeout)


def _files(endpoint: str, repo: str) -> list[tuple[str, int]]:
    with _get(f"{endpoint}/api/models/{repo}?blobs=true") as r:
        info = json.load(r)
    out = []
    for s in info.get("siblings", []):
        name = s.get("rfilename", "")
        if name and not name.startswith(".") and not name.lower().endswith((".md", ".gitattributes")):
            out.append((name, int(s.get("size") or 0)))
    if not any(n == "model.bin" for n, _ in out):
        raise SttError("в репозитории модели нет model.bin")
    return out


def ensure_model(key: str, task: Task | None = None) -> Path:
    """Скачивает модель, если её ещё нет. Прогресс задачи — доля скачанных байт."""
    d = model_dir(key)
    if downloaded(key):
        return d
    repo, mb, label = MODELS[key]
    errors = []
    for ep in ENDPOINTS:
        try:
            files = _files(ep, repo)
            total = sum(s for _, s in files) or mb * 1024 * 1024
            tmp = d.with_name(d.name + ".part")
            tmp.mkdir(parents=True, exist_ok=True)
            got = 0
            for name, size in files:
                dst = tmp / name
                if dst.is_file() and size and dst.stat().st_size == size:
                    got += size
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                done_before, last = got, [0.0]

                def progress(n: int, _size: int, done_before=done_before, last=last) -> None:
                    if task and time.monotonic() - last[0] > 0.3:
                        last[0] = time.monotonic()
                        now = done_before + n
                        task.update(min(0.99, now / total) * 0.3,
                                    f"Скачиваю модель «{label}» (один раз): {now >> 20} из {total >> 20} МБ")
                # с докачкой: закрыли приложение посреди 1,6 ГБ — продолжим с того же места
                net.download(f"{ep}/{repo}/resolve/main/{name}", dst, headers={"User-Agent": "Universal"},
                             progress=progress, check_cancel=task.check_cancel if task else None)
                got += dst.stat().st_size
            if d.exists():
                import shutil
                shutil.rmtree(d, ignore_errors=True)
            tmp.rename(d)
            return d
        except SttError:
            raise
        except Exception as e:  # сеть, 403, обрыв — пробуем зеркало
            if task and task.cancel_event.is_set():
                raise
            errors.append(f"{ep.split('//')[1]}: {e}")
    raise SttError("Не получилось скачать модель распознавания (" + "; ".join(errors)[:300] +
                   "). Нужен интернет один раз, дальше работает без него")


# --------------------------------------------------------------------- распознать

_models: dict[str, object] = {}
_lock = threading.Lock()


def _model(key: str):
    with _lock:
        if key not in _models:
            from faster_whisper import WhisperModel
            _models.clear()      # одна модель в памяти: большая занимает больше гигабайта
            _models[key] = WhisperModel(str(model_dir(key)), device="cpu", compute_type="int8",
                                        cpu_threads=max(1, (os.cpu_count() or 2) - 1))
        return _models[key]


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


SR = 16000   # Whisper слушает 16 кГц моно


def load_audio(src: Path):
    """Звук -> numpy float32, 16 кГц, моно. Сами, а не через faster-whisper: его чтение через PyAV
    ломается на новых версиях PyAV (у av.open больше нет metadata_errors). ffmpeg понимает всё,
    без него — PyAV напрямую."""
    import shutil
    import subprocess
    import numpy as np
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        r = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
                            "-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"], capture_output=True, timeout=3600)
        if r.returncode == 0 and r.stdout:
            return np.frombuffer(r.stdout, np.float32).copy()
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        if not err or "does not contain any stream" in err[-1] or "matches no streams" in err[-1]:
            raise SttError("в файле нет звука")
    try:
        import av
        chunks = []
        with av.open(str(src)) as box:
            stream = next((s for s in box.streams if s.type == "audio"), None)
            if stream is None:
                raise SttError("в файле нет звука")
            rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
            for frame in box.decode(stream):
                for out in rs.resample(frame) or []:
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in rs.resample(None) or []:
                chunks.append(out.to_ndarray().reshape(-1))
        return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)
    except SttError:
        raise
    except Exception as e:
        raise SttError(f"не получилось прочитать звук ({e.__class__.__name__}: {str(e)[:120]})")


def transcribe(src: Path, key: str, lang: str, task: Task | None = None, base: float = 0.0, span: float = 1.0, audio=None):
    """-> (сегменты [(начало, конец, текст)], язык, длительность). audio: уже прочитанный звук (его же берёт и разделение по голосам)"""
    if audio is None:
        audio = load_audio(src)
    if audio.size < SR // 10:
        raise SttError("в файле нет звука или он короче 0,1 секунды")
    model = _model(key)
    try:
        segments, info = model.transcribe(audio, language=lang or None, beam_size=perf.whisper_beam(),
                                          condition_on_previous_text=False)
    except Exception as e:
        raise SttError(f"распознавание не удалось ({e.__class__.__name__}: {str(e)[:120]})")
    out = []
    dur = info.duration or audio.size / SR
    for seg in segments:            # генератор: распознаётся по мере перебора
        if task:
            task.check_cancel()
            if dur:
                task.update(base + span * min(1.0, seg.end / dur))
        text = seg.text.strip()
        if text:
            out.append((seg.start, seg.end, text))
    return out, info.language, dur


def run_stt_task(task: Task, items: list[tuple[Path, str]], o: dict) -> None:
    """items: (сохранённый файл, уникальное имя без расширения) из app.save_uploads."""
    out_dir, n = task.out_dir, len(items)
    task.total = n
    task.update(0, "Готовлю модель распознавания…")
    if o.get("speakers") and not _android():
        try:
            diarize.ensure_models(task)                    # маленькие модели голосов (≈ 28 МБ), один раз
        except diarize.DiarizeError as e:
            raise SttError(str(e))
    if _android():
        import android_stt
        lang = o["lang"] if o["lang"] in android_stt.LANGS else "ru"
        try:
            android_stt.ensure_model(lang, task)
        except android_stt.SttError as e:
            raise SttError(str(e))
        recognize = lambda src, task, base, span, audio=None: android_stt.transcribe(src, lang, task, base, span)  # noqa: E731
        langs = android_stt.LANGS
    else:
        ensure_model(o["model"], task)
        task.update(0.3, "Загружаю модель в память…")
        _model(o["model"])
        recognize = lambda src, task, base, span, audio=None: transcribe(src, o["model"], o["lang"], task, base, span, audio)  # noqa: E731
        langs = LANGS
    results = []
    for i, (src, stem) in enumerate(items):
        task.check_cancel()
        name = stem + src.suffix
        base, span = 0.3 + 0.7 * i / n, 0.7 / n
        task.update(base, f"Распознаю {i + 1} из {n}: {name}")
        try:
            audio = load_audio(src) if o.get("speakers") else None      # звук читаем один раз: для текста и для голосов
            segs, lang, dur = recognize(src, task, base, span, audio)
        except Exception as e:
            if task.cancel_event.is_set():
                raise
            # SttError свой или из android_stt — текст для человека; прочее — с именем исключения
            msg = str(e) if e.__class__.__name__ == "SttError" else f"{e.__class__.__name__}: {str(e)[:150]}"
            task.failed.append({"name": name, "error": msg})
            continue
        who: list[int] = []
        if o.get("speakers") and segs:
            task.update(base + span * 0.9, f"Определяю голоса {i + 1} из {n}: {name}")
            try:
                who = diarize.assign(segs, diarize.turns(audio, o["speakers"]))
            except Exception as e:                                       # без голосов текст всё равно получен
                if task.cancel_event.is_set():
                    raise
                task.failed.append({"name": name, "error": "Голоса разделить не удалось: " + (
                    str(e) if isinstance(e, diarize.DiarizeError) else f"{e.__class__.__name__}: {str(e)[:120]}")})
                who = []
        many = len(set(who)) > 1                                         # один говорящий: подписи только мешали бы
        text = diarize.join(segs, who) if many else " ".join(t for _, _, t in segs)
        (out_dir / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
        if o["srt"]:
            pre = [f"[{diarize.label(w)}] " for w in who] if many else [""] * len(segs)
            srt = "".join(f"{k}\n{_ts(a)} --> {_ts(b)}\n{p}{t}\n\n" for k, ((a, b, t), p) in enumerate(zip(segs, pre), 1))
            (out_dir / f"{stem}.srt").write_text(srt, encoding="utf-8")
        results.append({"name": name, "text": text, "language": langs.get(lang, lang), "duration": round(dur, 1),
                        **({"speakers": len(set(who))} if who else {})})
        task.done += 1
    task.result = {"transcripts": results[:200]}
    if not task.done and task.failed:
        raise SttError(task.failed[0]["error"])
