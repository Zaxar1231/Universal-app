"""
Разделение по голосам («кто говорит»): к распознанной речи добавляются подписи «Спикер 1», «Спикер 2» и т. д.

Работает прямо на компьютере, без токенов и аккаунтов: sherpa-onnx (ONNX Runtime) с двумя маленькими моделями.
  сегментация  pyannote-segmentation-3.0 (≈ 1,5 МБ): где в записи говорит человек и где молчание;
  отпечаток    3D-Speaker ERes2Net, обучена на VoxCeleb (≈ 26 МБ): голос человека в числах.
Реплики с похожими голосами собираются в одного говорящего. Число говорящих определяется само (порог 0,8) или задаётся.
Модели скачиваются один раз в <папка данных Universal>/diarization.
"""
from __future__ import annotations

import importlib.util
import os
import tarfile
import threading
import urllib.error
from pathlib import Path

import engines
import net
from tasks import Task

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEG_URL = f"{BASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMB_URL = f"{BASE}/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
SEG_FILE = "segmentation.int8.onnx"
EMB_FILE = "embedding.onnx"
MB = 28                       # скачивается всего
THRESHOLD = 0.8               # чем выше, тем меньше говорящих; 0,7–0,9 одинаково верно на проверочной записи
MAX_SPEAKERS = 10
SAMPLE_RATE = 16000


class DiarizeError(Exception):
    pass


def available() -> bool:
    """sherpa-onnx есть (на телефоне и в части Linux-пакетов его нет)."""
    if os.environ.get("UNIVERSAL_ANDROID"):
        return False
    return importlib.util.find_spec("sherpa_onnx") is not None and importlib.util.find_spec("numpy") is not None


def model_dir() -> Path:
    return engines.root() / "diarization"


def downloaded() -> bool:
    d = model_dir()
    return (d / SEG_FILE).is_file() and (d / EMB_FILE).is_file()


def caps() -> dict:
    return {"available": available(), "downloaded": downloaded(), "mb": MB, "max": MAX_SPEAKERS,
            "pip": "" if available() else "pip install --user sherpa-onnx"}


def read_speakers(value: str | None) -> int:
    """Поле формы: пусто или 0 — не делить, auto — сколько найдётся, число — столько говорящих. -> 0 | -1 | n"""
    v = (value or "").strip().lower()
    if v in ("", "0", "off", "no"):
        return 0
    if v == "auto":
        return -1
    if v.isdigit() and 1 <= int(v) <= MAX_SPEAKERS:
        return int(v)
    return -1


# --------------------------------------------------------------------- модели

def ensure_models(task: Task | None = None, base: float = 0.0, span: float = 0.0) -> Path:
    d = model_dir()
    if downloaded():
        return d
    d.mkdir(parents=True, exist_ok=True)

    def part(url: str, dest: Path, lo: float, hi: float, what: str) -> None:
        def progress(got: int, total: int) -> None:
            if task:
                task.update(base + span * (lo + (hi - lo) * (got / total if total else 0)) if span else None,
                            f"Скачиваю модель определения голосов, {what} (один раз): {got >> 20} МБ")
        try:
            net.download(url, dest, headers={"User-Agent": "Universal"}, progress=progress,
                         check_cancel=task.check_cancel if task else None)
        except (urllib.error.URLError, OSError) as e:
            if task and task.cancel_event.is_set():
                raise
            raise DiarizeError(f"Не получилось скачать модель определения голосов ({net.reason(e)}). "
                               "Нужен интернет один раз, дальше работает без него")

    if not (d / SEG_FILE).is_file():
        arc = d / "segmentation.tar.bz2"
        part(SEG_URL, arc, 0.0, 0.2, "сегментация")
        try:
            with tarfile.open(arc, "r:bz2") as t:
                member = next((m for m in t.getmembers() if m.name.endswith("/model.int8.onnx") or m.name == "model.int8.onnx"), None)
                if member is None or not member.isfile():
                    raise DiarizeError("В архиве модели сегментации нет model.int8.onnx")
                src = t.extractfile(member)
                tmp = d / (SEG_FILE + ".part")
                with open(tmp, "wb") as out:
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
                os.replace(tmp, d / SEG_FILE)
        except tarfile.TarError as e:
            arc.unlink(missing_ok=True)
            raise DiarizeError(f"Модель сегментации скачалась неполной ({e}); повтори")
        arc.unlink(missing_ok=True)
    if not (d / EMB_FILE).is_file():
        tmp = d / "embedding.download"
        part(EMB_URL, tmp, 0.2, 1.0, "голос")
        os.replace(tmp, d / EMB_FILE)
    return d


# --------------------------------------------------------------------- работа

_lock = threading.Lock()
_cache: dict = {}


def _model(num_speakers: int):
    """Модель определения голосов: тяжело строится, поэтому держим по одной на число говорящих."""
    import sherpa_onnx
    d = model_dir()
    key = (str(d), num_speakers)
    with _lock:
        if key not in _cache:
            _cache.clear()
            threads = max(1, min(4, (os.cpu_count() or 2) - 1))
            cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(d / SEG_FILE)), num_threads=threads),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(d / EMB_FILE), num_threads=threads),
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers, threshold=THRESHOLD),
                min_duration_on=0.3, min_duration_off=0.5)
            if not cfg.validate():
                raise DiarizeError("Модель определения голосов не открылась (файлы повреждены?). Удали папку " + str(d))
            _cache[key] = sherpa_onnx.OfflineSpeakerDiarization(cfg)
        return _cache[key]


def turns(audio, num_speakers: int = -1) -> list[tuple[float, float, int]]:
    """audio: float32, 16 кГц, моно. -> [(начало, конец, номер говорящего с нуля)] по времени."""
    if not downloaded():
        raise DiarizeError("Нет моделей определения голосов")
    sd = _model(num_speakers)
    if getattr(sd, "sample_rate", SAMPLE_RATE) != SAMPLE_RATE:
        raise DiarizeError("Модель ждёт другую частоту звука")
    result = sd.process(audio).sort_by_start_time()
    return [(float(s.start), float(s.end), int(s.speaker)) for s in result]


def assign(segments: list[tuple[float, float, str]], found: list[tuple[float, float, int]]) -> list[int]:
    """Каждому отрезку текста (начало, конец, текст) подбирает говорящего: у кого больше пересечение по времени,
    а если отрезок целиком в паузе, то ближайшего по времени. Номера по порядку появления: 0, 1, 2…"""
    if not found:
        return [0] * len(segments)
    raw: list[int] = []
    for a, b, _t in segments:
        best, best_ov = None, 0.0
        for s, e, spk in found:
            ov = min(b, e) - max(a, s)
            if ov > best_ov:
                best, best_ov = spk, ov
        if best is None:
            mid = (a + b) / 2
            best = min(found, key=lambda x: 0 if x[0] <= mid <= x[1] else min(abs(mid - x[0]), abs(mid - x[1])))[2]
        raw.append(best)
    order: dict[int, int] = {}
    return [order.setdefault(r, len(order)) for r in raw]


def label(n: int) -> str:
    return f"Спикер {n + 1}"


def join(segments: list[tuple[float, float, str]], who: list[int]) -> str:
    """Текст с подписями: подряд идущие реплики одного говорящего собираются в один абзац."""
    lines: list[tuple[int, list[str]]] = []
    for (a, b, text), spk in zip(segments, who):
        if lines and lines[-1][0] == spk:
            lines[-1][1].append(text)
        else:
            lines.append((spk, [text]))
    return "\n\n".join(f"{label(spk)}: {' '.join(parts)}" for spk, parts in lines)
