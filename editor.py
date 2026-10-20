"""
Редактор аудио и видео на ffmpeg.

Что умеет (всё комбинируется в одном проходе):
    обрезка по времени, склейка нескольких файлов, кадрирование под пропорции
    (9:16 для шортсов, 1:1 и т.д.), поворот и отражение, смена разрешения и fps,
    скорость 0.25x-4x, громкость, нормализация, плавное появление/затухание,
    удаление звука, сжатие до нужного размера в МБ, смена кодека
    (H.264 / H.265 / VP9 / AV1) и формата, видео -> GIF.

Прогресс берётся из `ffmpeg -progress pipe:1`, отмена убивает процесс.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from converters import AUDIO, AUDIO_CODECS, AUDIO_MUXER, OUT_EXT, VIDEO, ConvertError
from tasks import Cancelled, Task

VIDEO_OUT = ["mp4", "mkv", "webm", "mov", "gif"]
AUDIO_OUT = ["mp3", "m4a", "wav", "flac", "ogg", "opus"]
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "4:5": (4, 5), "21:9": (21, 9)}

# какие видеокодеки можно класть в какой контейнер
CONTAINER_VCODECS = {
    "mp4": ["h264", "h265", "av1"], "mov": ["h264", "h265"],
    "mkv": ["h264", "h265", "vp9", "av1"], "webm": ["vp9", "av1"],
}
CONTAINER_ACODEC = {"mp4": "aac", "mov": "aac", "mkv": "aac", "webm": "libopus"}


# ----------------------------------------------------------------------------
# Утилиты
# ----------------------------------------------------------------------------

def parse_time(value: str | None) -> float | None:
    """'90', '1:30', '01:02:03.5', '1м30с' -> секунды. Пусто -> None."""
    if value is None:
        return None
    s = str(value).strip().replace(",", ".").lower()
    if not s:
        return None
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*(?:ч|h))?\s*(?:(\d+(?:\.\d+)?)\s*(?:м|мин|m))?\s*(?:(\d+(?:\.\d+)?)\s*(?:с|сек|s))?", s)
    if m and any(m.groups()):
        h, mi, se = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + se
    parts = s.split(":")
    if len(parts) > 3:
        raise ConvertError(f"Не понял время «{value}». Пиши как 90, 1:30 или 01:02:03")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ConvertError(f"Не понял время «{value}». Пиши как 90, 1:30 или 01:02:03")
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def fmt_time(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@lru_cache(maxsize=1)
def encoders() -> frozenset:
    if not shutil.which("ffmpeg"):
        return frozenset()
    res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return frozenset(m.group(1) for m in re.finditer(r"^\s*[VAS][\w.]{5}\s+(\S+)", res.stdout, re.M))


def probe(path: Path) -> dict:
    """Длительность, размеры, fps и наличие дорожек."""
    if not shutil.which("ffprobe"):
        raise ConvertError("ffprobe не найден в PATH (ставится вместе с ffmpeg)")
    res = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if res.returncode != 0:
        raise ConvertError("Файл повреждён или это не аудио/видео")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        duration = float(data.get("format", {}).get("duration") or (v or a or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    fps = 0.0
    if v:
        num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").partition("/")
        try:
            fps = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            fps = 0.0
    rot = 0
    if v:
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
    w, h = (int(v.get("width", 0)), int(v.get("height", 0))) if v else (0, 0)
    if abs(rot) % 180 == 90:   # ffmpeg сам повернёт по метаданным, считаем размеры после поворота
        w, h = h, w
    return {"duration": duration, "has_video": bool(v), "has_audio": bool(a),
            "width": w, "height": h, "fps": fps}


def run_ffmpeg(cmd: list[str], duration: float, task: Task | None,
               base: float = 0.0, span: float = 1.0) -> None:
    """Запускает ffmpeg, двигает прогресс задачи от base до base+span."""
    cmd = cmd[:1] + ["-hide_banner", "-nostats", "-progress", "pipe:1"] + cmd[1:]
    err_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace")
    if task:
        task.proc = proc
    try:
        for line in proc.stdout:
            if task and task.cancel_event.is_set():
                proc.kill()
                break
            if line.startswith("out_time_us=") and duration > 0 and task:
                try:
                    t = int(line.split("=", 1)[1]) / 1e6
                except ValueError:
                    continue
                task.update(base + span * min(1.0, t / duration))
        proc.wait()
    finally:
        if task:
            task.proc = None
    if task and task.cancel_event.is_set():
        raise Cancelled()
    if proc.returncode != 0:
        err_file.seek(0)
        lines = [l.strip() for l in err_file.read().splitlines() if l.strip()]
        err_file.close()
        raise ConvertError(_explain(lines))
    err_file.close()


def _explain(lines: list[str]) -> str:
    text = "\n".join(lines).lower()
    if "encoder not found" in text or "unknown encoder" in text:
        return "Эта сборка ffmpeg не умеет нужный кодек. Поставь полную сборку ffmpeg"
    if "invalid data found" in text:
        return "Файл повреждён или это не тот формат, что указан в расширении"
    if "matches no streams" in text:
        return "В файле нет нужной дорожки (видео или звука)"
    useful = [l for l in lines if not l.startswith(("frame=", "size=", "Press"))]
    return (useful[-1] if useful else "ffmpeg завершился с ошибкой")[:300]


def _atempo_chain(speed: float) -> list[str]:
    """atempo принимает 0.5..2 за раз, поэтому дробим."""
    out = []
    while speed > 2.0:
        out.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        out.append("atempo=0.5")
        speed /= 0.5
    out.append(f"atempo={speed:.6g}")
    return out


# ----------------------------------------------------------------------------
# Параметры
# ----------------------------------------------------------------------------

def read_opts(form) -> dict:
    """Параметры редактора из формы (значения проверяются здесь, а не в ffmpeg)."""
    def num(key, default, lo, hi, cast=float):
        raw = (form.get(key) or "").strip()
        if raw == "":
            return default
        try:
            return max(lo, min(hi, cast(raw.replace(",", "."))))
        except ValueError:
            raise ConvertError(f"Параметр «{key}» должен быть числом")

    o = {
        "start": parse_time(form.get("start")),
        "end": parse_time(form.get("end")),
        "out": (form.get("out") or "same").lower(),
        "vcodec": (form.get("vcodec") or "auto").lower(),
        "crf": num("crf", 23, 0, 51, int),
        "preset": form.get("preset") or "medium",
        "target_mb": num("target_mb", 0, 0, 100000),
        "height": num("height", 0, 0, 4320, int),
        "fps": num("fps", 0, 0, 240),
        "aspect": form.get("aspect") or "",
        "rotate": num("rotate", 0, 0, 270, int),
        "hflip": form.get("hflip") == "1",
        "vflip": form.get("vflip") == "1",
        "speed": num("speed", 1.0, 0.25, 4.0),
        "volume": num("volume", 100, 0, 1000),
        "normalize": form.get("normalize") == "1",
        "mute": form.get("mute") == "1",
        "fade_in": num("fade_in", 0, 0, 600),
        "fade_out": num("fade_out", 0, 0, 600),
        "abitrate": form.get("abitrate") or "auto",
        "gif_fps": num("gif_fps", 12, 1, 50, int),
        "gif_width": num("gif_width", 480, 64, 1920, int),
        "merge": form.get("merge") == "1",
        "fast": form.get("fast") == "1",
    }
    if o["out"] not in VIDEO_OUT + AUDIO_OUT + ["same"]:
        raise ConvertError(f"Формат {o['out']} редактор не делает")
    if o["vcodec"] not in ("auto", "h264", "h265", "vp9", "av1"):
        raise ConvertError("Неизвестный видеокодек")
    if o["aspect"] and o["aspect"] not in ASPECTS:
        raise ConvertError("Неизвестные пропорции кадра")
    if o["rotate"] not in (0, 90, 180, 270):
        raise ConvertError("Поворот бывает 0, 90, 180 или 270 градусов")
    if o["preset"] not in ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"):
        o["preset"] = "medium"
    if o["abitrate"] != "auto":
        o["abitrate"] = str(int(num("abitrate", 128, 8, 640, int)))
    if o["start"] is not None and o["end"] is not None and o["end"] <= o["start"]:
        raise ConvertError("Конец обрезки должен быть позже начала")
    return o


def _out_format(src: Path, o: dict, has_video: bool) -> str:
    to = o["out"]
    if to == "same":
        ext = src.suffix.lower().lstrip(".")
        if has_video:
            to = ext if ext in CONTAINER_VCODECS else "mp4"
        else:
            to = ext if ext in AUDIO_OUT else ("m4a" if ext in ("aac", "m4b", "alac") else "mp3")
    if to in VIDEO_OUT and not has_video:
        raise ConvertError("В файле нет видео, а выбран видеоформат. Выбери аудиоформат")
    return to


def _pick_vcodec(to: str, wanted: str) -> str:
    allowed = CONTAINER_VCODECS[to]
    codec = wanted if wanted in allowed else allowed[0]
    return codec


def _video_codec_args(codec: str, o: dict, out_dur: float, a_kbps: int) -> list[str]:
    enc = encoders()
    crf = o["crf"]
    if codec == "h264":
        args = ["-c:v", "libx264", "-preset", o["preset"], "-pix_fmt", "yuv420p"]
    elif codec == "h265":
        args = ["-c:v", "libx265", "-preset", o["preset"], "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
                "-x265-params", "log-level=error"]
    elif codec == "vp9":
        args = ["-c:v", "libvpx-vp9", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4",
                "-pix_fmt", "yuv420p"]
        crf = min(63, crf + 8)        # шкала vp9 сдвинута относительно x264
    else:  # av1
        if "libsvtav1" in enc:
            args = ["-c:v", "libsvtav1", "-preset", "8", "-pix_fmt", "yuv420p"]
        elif "libaom-av1" in enc:
            args = ["-c:v", "libaom-av1", "-cpu-used", "6", "-row-mt", "1", "-pix_fmt", "yuv420p"]
        else:
            raise ConvertError("В этой сборке ffmpeg нет кодировщика AV1")
        crf = min(63, crf + 10)

    if o["target_mb"] > 0:
        if out_dur <= 0:
            raise ConvertError("Не удалось узнать длительность, сжать до размера не получится")
        total_kbps = o["target_mb"] * 8192 / out_dur * 0.97   # 3% на контейнер
        v_kbps = int(total_kbps - a_kbps)
        if v_kbps < 50:
            raise ConvertError(f"{o['target_mb']:g} МБ слишком мало для {fmt_time(out_dur)} видео. "
                               f"Нужно хотя бы {round((a_kbps + 50) * out_dur / 8192 / 0.97, 1)} МБ")
        args += ["-b:v", f"{v_kbps}k", "-maxrate", f"{int(v_kbps * 1.4)}k", "-bufsize", f"{v_kbps * 2}k"]
        if codec == "vp9":
            args += ["-crf", str(crf)]   # vp9: crf + b:v = constrained quality
    else:
        args += ["-crf", str(crf)]
        if codec == "vp9":
            args += ["-b:v", "0"]
    return args


def _audio_codec_args(to: str, o: dict, is_video_out: bool) -> tuple[list[str], int]:
    """Аргументы звука и битрейт в кбит/с (для расчёта размера)."""
    if is_video_out:
        codec = CONTAINER_ACODEC[to]
        kbps = int(o["abitrate"]) if o["abitrate"] != "auto" else (128 if codec == "libopus" else 160)
        if o["target_mb"] > 0 and o["abitrate"] == "auto":
            kbps = 96   # при сжатии экономим на звуке
        return ["-c:a", codec, "-b:a", f"{kbps}k", "-ar", "48000"], kbps
    codec = AUDIO_CODECS[to]
    args = ["-c:a", codec]
    if to in ("wav", "flac"):
        return args, 1411
    kbps = int(o["abitrate"]) if o["abitrate"] != "auto" else {"mp3": 192, "opus": 128}.get(to, 160)
    if to == "mp3":
        kbps = min((8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
                   key=lambda g: abs(g - kbps))
    args += ["-b:a", f"{kbps}k"]
    if to == "opus":
        args += ["-ar", "48000"]
    if to in AUDIO_MUXER:
        args += ["-f", AUDIO_MUXER[to]]
    return args, kbps


# ----------------------------------------------------------------------------
# Сборка команды
# ----------------------------------------------------------------------------

def _video_chain(o: dict, out_dur: float, to: str) -> list[str]:
    f: list[str] = []
    if o["aspect"]:
        a, b = ASPECTS[o["aspect"]]
        f.append(f"crop=trunc(min(iw\\,ih*{a}/{b})/2)*2:trunc(min(ih\\,iw*{b}/{a})/2)*2")
    if o["rotate"] == 90:
        f.append("transpose=1")
    elif o["rotate"] == 270:
        f.append("transpose=2")
    elif o["rotate"] == 180:
        f.append("hflip,vflip")
    if o["hflip"]:
        f.append("hflip")
    if o["vflip"]:
        f.append("vflip")
    if o["speed"] != 1:
        f.append(f"setpts=PTS/{o['speed']:.6g}")
    if to == "gif":
        f.append(f"fps={o['gif_fps']}")
        f.append(f"scale={o['gif_width']}:-1:flags=lanczos")
    else:
        if o["height"]:
            f.append(f"scale=-2:min(ih\\,{o['height']})")   # только уменьшаем
        if o["fps"]:
            f.append(f"fps={o['fps']:.6g}")
        f.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")      # x264/x265 хотят чётные размеры
    if o["fade_in"]:
        f.append(f"fade=t=in:st=0:d={o['fade_in']:.6g}")
    if o["fade_out"] and out_dur > o["fade_out"]:
        f.append(f"fade=t=out:st={out_dur - o['fade_out']:.6g}:d={o['fade_out']:.6g}")
    if to == "gif":
        f.append("split[gs0][gs1];[gs0]palettegen=stats_mode=diff[gp];[gs1][gp]paletteuse=dither=bayer:bayer_scale=5")
    return f


def _audio_chain(o: dict, out_dur: float) -> list[str]:
    f: list[str] = []
    if o["speed"] != 1:
        f += _atempo_chain(o["speed"])
    if o["volume"] != 100:
        f.append(f"volume={o['volume'] / 100:.4g}")
    if o["normalize"]:
        f.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if o["fade_in"]:
        f.append(f"afade=t=in:st=0:d={o['fade_in']:.6g}")
    if o["fade_out"] and out_dur > o["fade_out"]:
        f.append(f"afade=t=out:st={out_dur - o['fade_out']:.6g}:d={o['fade_out']:.6g}")
    return f


def _only_trim(o: dict) -> bool:
    return not any((o["aspect"], o["rotate"], o["hflip"], o["vflip"], o["speed"] != 1, o["height"],
                    o["fps"], o["fade_in"], o["fade_out"], o["volume"] != 100, o["normalize"],
                    o["target_mb"]))


def edit_file(src: Path, out_dir: Path, stem: str, o: dict, task: Task | None = None,
              base: float = 0.0, span: float = 1.0) -> Path:
    """Один файл -> один результат."""
    if not shutil.which("ffmpeg"):
        raise ConvertError("ffmpeg не найден в PATH")
    info = probe(src)
    has_video = info["has_video"]
    to = _out_format(src, o, has_video)
    video_out = to in VIDEO_OUT
    if not info["has_audio"] and not has_video:
        raise ConvertError("В файле нет ни видео, ни звука")
    if not video_out and not info["has_audio"]:
        raise ConvertError("В файле нет звука, а выбран аудиоформат")

    start = o["start"] or 0.0
    dur = info["duration"]
    if dur and start >= dur:
        raise ConvertError(f"Начало обрезки ({fmt_time(start)}) дальше конца файла ({fmt_time(dur)})")
    end = min(o["end"], dur) if (o["end"] is not None and dur) else (o["end"] or dur)
    cut = max(0.0, (end or 0) - start)
    out_dur = cut / o["speed"] if cut else 0.0

    out = out_dir / f"{stem}.{OUT_EXT.get(to, to)}"
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if o["start"]:
        cmd += ["-ss", f"{start:.3f}"]
    if o["end"] is not None:
        cmd += ["-t", f"{cut:.3f}"]
    cmd += ["-i", str(src)]

    # быстрый режим: только обрезка в тот же формат, без перекодирования
    same_container = src.suffix.lower().lstrip(".") == to
    if o["fast"] and same_container and _only_trim(o) and o["vcodec"] == "auto" and not o["mute"]:
        cmd += ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)]
        run_ffmpeg(cmd, out_dur, task, base, span)
        return out

    use_audio = info["has_audio"] and not o["mute"] and to != "gif"
    a_args, a_kbps = _audio_codec_args(to, o, video_out) if (use_audio or not video_out) else ([], 0)

    graph = []
    if video_out:
        vchain = _video_chain(o, out_dur, to)
        graph.append(f"[0:v:0]{','.join(vchain)}[vout]")
    if use_audio:
        achain = _audio_chain(o, out_dur) or ["anull"]
        graph.append(f"[0:a:0]{','.join(achain)}[aout]")
    cmd += ["-filter_complex", ";".join(graph)]
    if video_out:
        cmd += ["-map", "[vout]"]
    if use_audio:
        cmd += ["-map", "[aout]"]

    if to == "gif":
        cmd += ["-loop", "0"]
    elif video_out:
        cmd += _video_codec_args(_pick_vcodec(to, o["vcodec"]), o, out_dur, a_kbps if use_audio else 0)
        if to in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
    cmd += a_args
    cmd += ["-map_metadata", "0", str(out)]
    run_ffmpeg(cmd, out_dur, task, base, span)
    if not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise ConvertError("ffmpeg ничего не записал")
    return out


def merge_files(srcs: list[Path], out_dir: Path, stem: str, o: dict, task: Task | None = None) -> Path:
    """Склейка по порядку. Видео приводятся к размеру и fps первого, звук к 48 кГц стерео."""
    if not shutil.which("ffmpeg"):
        raise ConvertError("ffmpeg не найден в PATH")
    infos = [probe(p) for p in srcs]
    any_video = any(i["has_video"] for i in infos)
    if any_video and not all(i["has_video"] for i in infos):
        raise ConvertError("Склеивать можно либо только видео, либо только аудио")
    to = _out_format(srcs[0], o, any_video)
    video_out = to in VIDEO_OUT

    total = sum(i["duration"] for i in infos)
    start = o["start"] or 0.0
    end = min(o["end"], total) if o["end"] is not None else total
    if start >= total:
        raise ConvertError("Начало обрезки дальше конца склеенного файла")
    out_dur = max(0.0, end - start) / o["speed"]

    W = H = 0
    fps = 30.0
    if any_video:
        first = infos[0]
        W, H = first["width"] or 1280, first["height"] or 720
        W, H = W - W % 2, H - H % 2
        fps = first["fps"] if 1 <= first["fps"] <= 120 else 30.0

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for p in srcs:
        cmd += ["-i", str(p)]
    parts, labels = [], []
    for i, info in enumerate(infos):
        if any_video:
            parts.append(f"[{i}:v:0]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                         f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:.6g},format=yuv420p[v{i}]")
        if info["has_audio"]:
            parts.append(f"[{i}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={info['duration']:.3f},"
                         f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        labels.append((f"[v{i}]" if any_video else "") + f"[a{i}]")
    n = len(srcs)
    parts.append("".join(labels) + f"concat=n={n}:v={1 if any_video else 0}:a=1" +
                 ("[mv][ma]" if any_video else "[ma]"))

    trim_v = trim_a = ""
    if o["start"] or o["end"] is not None:
        trim_v = f"trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
        trim_a = f"atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
    use_audio = not o["mute"] and to != "gif"
    if video_out:
        parts.append(f"[mv]{trim_v}{','.join(_video_chain(o, out_dur, to))}[vout]")
    if use_audio or not video_out:
        parts.append(f"[ma]{trim_a}{','.join(_audio_chain(o, out_dur) or ['anull'])}[aout]")
    else:
        parts.append("[ma]anullsink")

    cmd += ["-filter_complex", ";".join(parts)]
    if video_out:
        cmd += ["-map", "[vout]"]
    if use_audio or not video_out:
        cmd += ["-map", "[aout]"]
    a_args, a_kbps = _audio_codec_args(to, o, video_out) if (use_audio or not video_out) else ([], 0)
    if to == "gif":
        cmd += ["-loop", "0"]
    elif video_out:
        cmd += _video_codec_args(_pick_vcodec(to, o["vcodec"]), o, out_dur, a_kbps)
        if to in ("mp4", "mov"):
            cmd += ["-movflags", "+faststart"]
    cmd += a_args
    out = out_dir / f"{stem}.{OUT_EXT.get(to, to)}"
    cmd.append(str(out))
    run_ffmpeg(cmd, out_dur, task)
    return out


def run_edit_task(task: Task, items: list[tuple[Path, str]], o: dict, merge_name: str) -> None:
    """Тело фоновой задачи редактора."""
    out_dir = task.out_dir
    task.total = 1 if o["merge"] and len(items) > 1 else len(items)
    if o["merge"] and len(items) > 1:
        task.update(0, f"Склеиваю {len(items)} файлов…")
        try:
            merge_files([p for p, _ in items], out_dir, merge_name, o, task)
            task.done = 1
        except ConvertError as e:
            task.failed.append({"name": "склейка", "error": str(e)})
    else:
        n = len(items)
        for i, (src, stem) in enumerate(items):
            task.check_cancel()
            task.update(i / n, f"Файл {i + 1} из {n}: {src.name}")
            try:
                edit_file(src, out_dir, stem, o, task, base=i / n, span=1 / n)
                task.done += 1
            except ConvertError as e:
                task.failed.append({"name": src.name, "error": str(e)})
    shutil.rmtree(task.job_dir / "in", ignore_errors=True)
    if not task.done and task.failed:
        raise ConvertError(task.failed[0]["error"])


# видео- и аудиоформаты, которые редактор принимает на вход
INPUTS = sorted(f for f, (r, _) in {**VIDEO, **AUDIO}.items() if r)
