"""
Скорость на слабых компьютерах: что за машина, сколько потоков отдать каждой программе и можно ли кодировать видео
видеокартой.

  профиль       ядра и память. «Слабый» ПК (до 2 ядер или меньше ≈ 6 ГБ памяти): быстрые настройки по умолчанию
                (preset veryfast у x264/x265, распознавание речи без перебора вариантов).
  потоки        несколько программ сразу, и у каждой потоки на все ядра: ядра дерутся и всё идёт медленнее. Каждой
                программе достаётся доля ядер (ImageMagick получает её через MAGICK_THREAD_LIMIT).
  видеокарта    NVIDIA NVENC, Intel Quick Sync, AMD AMF, Apple VideoToolbox. Какой кодировщик работает, выясняется
                пробным кодированием на этом компьютере (с теми же параметрами, что пойдут в дело) и запоминается.
                Если на деле кодирование упало, вызывающий код повторяет его процессором.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading

import engines

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SLOW_CORES = 2
SLOW_RAM_MB = 6000          # у ПК с 4 ГБ системе доступно около 3,9 ГБ; с 8 ГБ около 7,9 ГБ

_lock = threading.Lock()
_machine: dict | None = None
_hw: dict | None = None
_local = threading.local()


# --------------------------------------------------------------------- машина

def _ram_mb() -> int:
    try:
        if sys.platform == "win32":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("ptotal", ctypes.c_ulonglong), ("pavail", ctypes.c_ulonglong),
                            ("vtotal", ctypes.c_ulonglong), ("vavail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
            m = MEM()
            m.length = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.total >> 20)
        if sys.platform == "darwin":
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return int(r.stdout.strip()) >> 20
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) >> 10
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        pass
    return 0                    # неизвестно: считаем, что хватает


def machine() -> dict:
    global _machine
    if _machine is None:
        cores, ram = os.cpu_count() or 2, _ram_mb()
        slow = os.environ.get("UNIVERSAL_SLOW") == "1" or (os.environ.get("UNIVERSAL_SLOW") != "0" and (
            cores <= SLOW_CORES or (0 < ram < SLOW_RAM_MB)))
        _machine = {"cores": cores, "ram_mb": ram, "slow": slow}
    return _machine


def slow() -> bool:
    return machine()["slow"]


def default_preset() -> str:
    """Скорость сжатия x264/x265 по умолчанию."""
    return "veryfast" if slow() else "medium"


def whisper_beam() -> int:
    """Перебор вариантов при распознавании речи: 1 (жадный) почти вдвое быстрее, на слабом ПК он и нужен."""
    return 1 if slow() else 5


def heavy_workers() -> int:
    """Сколько видео кодировать одновременно: x264 и сам занимает все ядра, а два сразу на 4 ядрах только мешают."""
    return 1 if machine()["cores"] <= 4 else 2


# --------------------------------------------------------------------- потоки

def split_threads(workers: int) -> int:
    return max(1, machine()["cores"] // max(1, workers))


def set_threads(n: int | None) -> None:
    """Сколько потоков дозволено программам, которые запускает этот поток (None — без ограничения)."""
    _local.threads = n


def child_env() -> dict | None:
    n = getattr(_local, "threads", None)
    if not n:
        return None
    return {**os.environ, "MAGICK_THREAD_LIMIT": str(n), "OMP_NUM_THREADS": str(n)}


# --------------------------------------------------------------------- видеокарта

# {q} — число качества (шкала близка к CRF), {b} — килобиты в секунду, {vt} — шкала VideoToolbox 1..100
RECIPES = {
    "h264": [
        ("NVIDIA NVENC", "h264_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "{q}", "-b:v", "0"], ["-preset", "p4", "-b:v", "{b}k"]),
        ("Intel Quick Sync", "h264_qsv", ["-preset", "veryfast", "-global_quality", "{q}"], ["-preset", "veryfast", "-b:v", "{b}k"]),
        ("AMD AMF", "h264_amf", ["-quality", "speed", "-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}", "-qp_b", "{q}"], ["-quality", "speed", "-b:v", "{b}k"]),
        ("Apple VideoToolbox", "h264_videotoolbox", ["-q:v", "{vt}"], ["-b:v", "{b}k"]),
    ],
    "h265": [
        ("NVIDIA NVENC", "hevc_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "{q}", "-b:v", "0", "-tag:v", "hvc1"], ["-preset", "p4", "-b:v", "{b}k", "-tag:v", "hvc1"]),
        ("Intel Quick Sync", "hevc_qsv", ["-preset", "veryfast", "-global_quality", "{q}", "-tag:v", "hvc1"], ["-preset", "veryfast", "-b:v", "{b}k", "-tag:v", "hvc1"]),
        ("AMD AMF", "hevc_amf", ["-quality", "speed", "-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}", "-qp_b", "{q}", "-tag:v", "hvc1"], ["-quality", "speed", "-b:v", "{b}k", "-tag:v", "hvc1"]),
        ("Apple VideoToolbox", "hevc_videotoolbox", ["-q:v", "{vt}", "-tag:v", "hvc1"], ["-b:v", "{b}k", "-tag:v", "hvc1"]),
    ],
}
PRIORITY = {"win32": ("NVIDIA NVENC", "Intel Quick Sync", "AMD AMF"), "darwin": ("Apple VideoToolbox",),
            "linux": ("NVIDIA NVENC", "Intel Quick Sync")}


def _fill(args: list[str], q: int = 23, kbps: int = 2000) -> list[str]:
    vt = max(1, min(100, round(110 - q * 2.6)))
    return [a.replace("{q}", str(q)).replace("{b}", str(kbps)).replace("{vt}", str(vt)) for a in args]


def _platform() -> str:
    return sys.platform


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _encoders(ffmpeg: str) -> set[str]:
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {m.group(1) for m in re.finditer(r"^\s*V[\w.]{5}\s+(\S+)", r.stdout, re.M)}


def _works(ffmpeg: str, encoder: str, args: list[str]) -> bool:
    """Пробное кодирование полсекунды: кодировщик открывается и принимает эти параметры."""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
           "-frames:v", "12", "-pix_fmt", "yuv420p", "-c:v", encoder, *_fill(args), "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=40, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _cache_file():
    return engines.root() / "hwenc.json"


def detect(force: bool = False) -> dict:
    """{"h264": {"label", "encoder", "q", "b"} | отсутствует, "h265": ...}. Результат запоминается на диске."""
    global _hw
    with _lock:
        if _hw is not None and not force:
            return _hw
        ffmpeg = _ffmpeg()
        found: dict = {}
        if not ffmpeg or os.environ.get("UNIVERSAL_HW") == "0":
            _hw = found
            return found
        try:
            stamp = f"{ffmpeg}|{os.path.getmtime(ffmpeg)}|{_platform()}"
        except OSError:
            stamp = ffmpeg
        try:
            saved = json.loads(_cache_file().read_text(encoding="utf-8"))
            if saved.get("stamp") == stamp and not force:
                _hw = saved.get("found", {})
                return _hw
        except (OSError, ValueError):
            pass
        have = _encoders(ffmpeg)
        order = PRIORITY.get(_platform() if _platform() in PRIORITY else "linux", ())
        for codec, recipes in RECIPES.items():
            for label in order:
                for lab, enc, q_args, b_args in recipes:
                    if lab == label and enc in have and _works(ffmpeg, enc, q_args):
                        found[codec] = {"label": lab, "encoder": enc, "q": q_args, "b": b_args}
                        break
                if codec in found:
                    break
        _hw = found
        try:
            _cache_file().write_text(json.dumps({"stamp": stamp, "found": found}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return found


def hw_args(codec: str, quality: int | None = None, kbps: int | None = None) -> list[str] | None:
    """Аргументы `-c:v ...` для видеокарты или None, если её нет. quality — число вроде CRF; kbps — режим битрейта."""
    h = detect().get(codec)
    if not h:
        return None
    if kbps:
        return ["-c:v", h["encoder"], *_fill(h["b"], kbps=max(50, int(kbps)))]
    return ["-c:v", h["encoder"], *_fill(h["q"], q=int(quality if quality is not None else 23))]


def warm() -> None:
    """Выяснить про видеокарту в фоне, пока человек ещё выбирает файлы (пробы на Windows занимают секунды)."""
    threading.Thread(target=detect, daemon=True, name="perf-detect").start()


def status() -> dict:
    """Для интерфейса. Не ждёт пробного кодирования: пока оно идёт, "checking" истинно."""
    m = machine()
    hw = _hw
    return {**m, "preset": default_preset(), "checking": hw is None, "hw": {c: v["label"] for c, v in (hw or {}).items()}}
