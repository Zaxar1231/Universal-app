"""
Универсальные конвертеры (набор форматов и логика взяты из VERT).

VERT гоняет ffmpeg / ImageMagick / pandoc в браузере через WebAssembly.
Здесь те же движки, но нативные: на своём сервере они быстрее, не упираются
в память вкладки и не тянут 80 МБ wasm-файлов.

Движки:
    ffmpeg      аудио и видео, включая видео -> аудио и аудио -> видео
    imagemagick картинки (convert / magick)
    pandoc      документы (docx, md, html, rtf, epub, odt, ...)
    libreoffice старые .doc/.xls/.ppt и офисные форматы (если установлен)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# ----------------------------------------------------------------------------
# Наборы форматов (из VERT)
# ----------------------------------------------------------------------------

# аудио: (можно читать, можно писать)
AUDIO = {
    "mp3": (1, 1), "wav": (1, 1), "flac": (1, 1), "ogg": (1, 1), "oga": (1, 1),
    "opus": (1, 1), "aac": (1, 1), "alac": (1, 1), "m4a": (1, 1), "wma": (1, 1),
    "amr": (1, 1), "ac3": (1, 1), "aiff": (1, 1), "aifc": (1, 1), "aif": (1, 1),
    "mp2": (1, 1), "au": (1, 1), "m4b": (1, 1), "voc": (1, 1), "weba": (1, 1),
    # только чтение
    "mogg": (1, 0), "caf": (1, 0), "mp1": (1, 0), "mpc": (1, 0), "dsd": (1, 0),
    "dsf": (1, 0), "dff": (1, 0), "mqa": (1, 0),
}

VIDEO = {f: (1, 1) for f in (
    "mkv", "mp4", "avi", "mov", "webm", "ts", "mts", "m2ts", "wmv", "mpg", "mpeg",
    "flv", "f4v", "vob", "m4v", "3gp", "3g2", "mxf", "ogv", "divx",
)}
VIDEO["rm"] = (1, 0)
VIDEO["rmvb"] = (1, 0)

IMAGE = {f: (1, 1) for f in (
    "png", "jpg", "jpeg", "webp", "gif", "avif", "heic", "ico", "bmp", "tiff", "tif",
    "psd", "jfif", "jpe", "pbm", "pgm", "ppm", "pnm", "hdr", "cur", "pdf", "eps",
)}
IMAGE["svg"] = (1, 0)      # читаем (растеризуем), писать векторно ImageMagick не умеет
for f in ("heif", "ani", "icns", "nef", "cr2", "arw", "dng", "xcf", "rw2", "raf", "orf",
          "pef", "mos", "raw", "dcr", "crw", "cr3", "3fr", "erf", "mrw", "mef", "nrw",
          "srw", "sr2", "srf"):
    IMAGE[f] = (1, 0)      # RAW и экзотика: только чтение

DOC = {
    "docx": (1, 1), "md": (1, 1), "html": (1, 1), "rtf": (1, 1), "csv": (1, 1),
    "tsv": (1, 1), "json": (1, 1), "rst": (1, 1), "epub": (1, 1), "odt": (1, 1),
    "docbook": (1, 1), "doc": (1, 1),
    # дополнительно, pandoc это умеет
    "txt": (1, 1), "tex": (1, 1), "org": (1, 1), "fb2": (1, 1),
}

# что pandoc называет иначе, чем мы
PANDOC_READ = {"md": "markdown", "txt": "markdown", "tex": "latex", "docbook": "docbook",
               "rst": "rst", "org": "org", "fb2": "fb2", "html": "html", "csv": "csv",
               "tsv": "tsv", "json": "json", "epub": "epub", "odt": "odt", "docx": "docx",
               "rtf": "rtf"}
PANDOC_WRITE = {"md": "markdown", "txt": "plain", "tex": "latex", "docbook": "docbook",
                "rst": "rst", "org": "org", "fb2": "fb2", "html": "html", "csv": "csv",
                "tsv": "tsv", "json": "json", "epub": "epub", "odt": "odt", "docx": "docx",
                "rtf": "rtf"}

CATEGORIES = {"image": IMAGE, "audio": AUDIO, "video": VIDEO, "doc": DOC}

# что во что можно перекладывать между категориями (как в VERT)
CROSS = {"video": ["audio"], "audio": ["video"], "image": [], "doc": []}


def category_of(ext: str) -> str | None:
    ext = ext.lower().lstrip(".")
    for name, table in CATEGORIES.items():
        if ext in table:
            return name
    return None


def can_read(ext: str) -> bool:
    ext = ext.lower().lstrip(".")
    return any(table.get(ext, (0, 0))[0] for table in CATEGORIES.values())


# что показывать первым: популярные форматы выше экзотики
POPULAR = {
    "image": ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "avif", "heic", "pdf", "psd"],
    "audio": ["mp3", "wav", "flac", "ogg", "opus", "aac", "m4a", "wma", "aiff", "alac"],
    "video": ["mp4", "mkv", "webm", "avi", "mov", "wmv", "flv", "mpg", "3gp", "ogv"],
    "doc":   ["docx", "pdf", "md", "html", "txt", "rtf", "epub", "odt", "doc", "csv"],
}


def _ordered(names, category: str) -> list[str]:
    pop = POPULAR.get(category, [])
    first = [n for n in pop if n in names]
    return first + sorted(n for n in names if n not in first)


def targets_for(category: str) -> dict:
    """Форматы для конвертации: свои (native) и из другой категории (cross), уже по порядку."""
    native = [f for f, (_, w) in CATEGORIES[category].items() if w]
    cross = []
    for other in CROSS.get(category, []):
        cross += [f for f, (_, w) in CATEGORIES[other].items() if w and f not in native]
    return {"native": _ordered(set(native), category),
            "cross": _ordered(set(cross), CROSS[category][0]) if cross else []}


# ----------------------------------------------------------------------------
# Доступность движков
# ----------------------------------------------------------------------------

def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def engines() -> dict:
    return {
        "ffmpeg": bool(_which("ffmpeg")),
        "imagemagick": bool(_which("magick", "convert")),
        "pandoc": bool(_which("pandoc")),
        "libreoffice": bool(_which("soffice", "libreoffice")),
    }


class ConvertError(Exception):
    pass


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ConvertError("Слишком долго конвертируется (таймаут)")
    except FileNotFoundError:
        raise ConvertError(f"Не найдена программа {cmd[0]}")


def _last_error(res: subprocess.CompletedProcess, fallback: str) -> str:
    lines = [l.strip() for l in (res.stderr or "").splitlines() if l.strip()]
    return lines[-1][:300] if lines else fallback


# ----------------------------------------------------------------------------
# Кодеки (таблица из VERT, getCodecs)
# ----------------------------------------------------------------------------

VIDEO_CODECS = {
    "mp4": ("libx264", "aac"), "mkv": ("libx264", "aac"), "mov": ("libx264", "aac"),
    "mts": ("libx264", "aac"), "ts": ("libx264", "aac"), "m2ts": ("libx264", "aac"),
    "flv": ("libx264", "aac"), "f4v": ("libx264", "aac"), "m4v": ("libx264", "aac"),
    "3gp": ("libx264", "aac"), "3g2": ("libx264", "aac"),
    "wmv": ("wmv2", "wmav2"),
    "webm": ("libvpx", "libvorbis"),
    "ogv": ("libtheora", "libvorbis"),
    "avi": ("mpeg4", "libmp3lame"), "divx": ("mpeg4", "libmp3lame"),
    "mpg": ("mpeg2video", "mp2"), "mpeg": ("mpeg2video", "mp2"), "vob": ("mpeg2video", "mp2"),
    "mxf": ("mpeg2video", "pcm_s16le"),
}

AUDIO_CODECS = {
    "mp3": "libmp3lame", "flac": "flac", "wav": "pcm_s16le", "ogg": "libvorbis",
    "oga": "libvorbis", "opus": "libopus", "aac": "aac", "m4a": "aac", "m4b": "aac",
    "alac": "alac", "wma": "wmav2", "ac3": "ac3", "mp2": "mp2", "amr": "libopencore_amrnb",
    "aiff": "pcm_s16be", "aifc": "pcm_s16be", "aif": "pcm_s16be", "au": "pcm_s16be",
    "voc": "pcm_u8", "weba": "libopus",
}

# контейнер, если по расширению ffmpeg не угадает
AUDIO_MUXER = {"alac": "ipod", "m4b": "ipod", "weba": "webm", "aifc": "aiff",
               "aac": "adts", "oga": "ogg", "opus": "ogg"}
VIDEO_MUXER = {"divx": "avi", "m4v": "mp4", "mts": "mpegts", "m2ts": "mpegts", "f4v": "mp4"}
OUT_EXT = {"alac": "m4a", "aifc": "aiff"}   # ALAC пишется в m4a, как и в VERT

# кодеки, которые принимают только стандартные битрейты (кбит/с)
STRICT_BITRATES = {
    "mp2": (32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    "mp3": (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    "ac3": (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 448, 512, 576, 640),
    "wma": (32, 40, 48, 64, 80, 96, 128, 160, 192, 256, 320),
}


def snap_bitrate(codec_key: str, kbps: int) -> int:
    """Подгоняет битрейт под ближайший допустимый для кодека."""
    grid = STRICT_BITRATES.get(codec_key)
    if not grid:
        return kbps
    return min(grid, key=lambda g: abs(g - kbps))


LOSSLESS = {"flac", "m4a", "caf", "alac", "wav", "dsd", "dsf", "dff", "aiff", "aif", "aifc"}


def _probe(path: Path, entry: str) -> int | None:
    """Битрейт/частота первой аудиодорожки через ffprobe (как detectAudioBitrate в VERT)."""
    if not _which("ffprobe"):
        return None
    res = _run(["ffprobe", "-v", "quiet", "-select_streams", "a:0", "-show_entries",
                f"stream={entry}", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], 60)
    try:
        return int(res.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _has_video_stream(path: Path) -> bool:
    if not _which("ffprobe"):
        return False
    res = _run(["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-show_entries",
                "stream=codec_type", "-of", "csv=p=0", str(path)], 60)
    return "video" in res.stdout


# ----------------------------------------------------------------------------
# Аудио и видео (ffmpeg)
# ----------------------------------------------------------------------------

def convert_media(src: Path, dst_dir: Path, stem: str, to: str, *,
                  bitrate: str = "auto", sample_rate: str = "auto",
                  keep_metadata: bool = True) -> Path:
    """Аудио/видео -> аудио/видео. Логика выбора параметров повторяет VERT."""
    if not _which("ffmpeg"):
        raise ConvertError("ffmpeg не найден в PATH")
    src_ext = src.suffix.lower().lstrip(".")
    to = to.lower()
    src_is_video = src_ext in VIDEO
    dst_is_video = to in VIDEO
    if not VIDEO.get(to, AUDIO.get(to, (0, 0)))[1]:
        raise ConvertError(f"Формат {to} нельзя создавать")

    out_ext = OUT_EXT.get(to, to)
    out = dst_dir / f"{stem}.{out_ext}"
    meta_args = [] if keep_metadata else ["-map_metadata", "-1", "-map_chapters", "-1"]

    # --- параметры звука: как в VERT ---
    lossless_to_lossy = src_ext in LOSSLESS and to not in LOSSLESS
    a_args: list[str] = []
    if bitrate != "auto":
        a_args += ["-b:a", f"{int(bitrate)}k"]
    elif lossless_to_lossy:
        a_args += ["-b:a", "128k"]
    else:
        br = _probe(src, "bit_rate")
        if br:
            a_args += ["-b:a", f"{round(br / 1000)}k"]

    if sample_rate != "auto":
        a_args += ["-ar", str(int(sample_rate))]
    elif lossless_to_lossy:
        a_args += ["-ar", "48000" if to == "opus" else "44100"]
    else:
        sr = _probe(src, "sample_rate")
        if sr:
            a_args += ["-ar", str(48000 if (to == "opus" and sr == 44100) else sr)]
    # кодек контейнера: у видео это звуковая дорожка (mp2 в mpg, mp3 в avi), у аудио сам формат
    acodec_key = {"libmp3lame": "mp3", "mp2": "mp2", "ac3": "ac3", "wmav2": "wma"}.get(
        VIDEO_CODECS[to][1] if dst_is_video else AUDIO_CODECS.get(to, ""), "")
    if "-b:a" in a_args and acodec_key:
        i = a_args.index("-b:a")
        a_args[i + 1] = f"{snap_bitrate(acodec_key, int(a_args[i + 1].rstrip('k')))}k"
    if to in ("weba", "opus") and "-ar" in a_args:
        i = a_args.index("-ar")
        if a_args[i + 1] not in ("8000", "12000", "16000", "24000", "48000"):
            a_args[i + 1] = "48000"
    if to == "amr":
        a_args = [x for i, x in enumerate(a_args)
                  if x not in ("-ar", "-b:a") and not (i and a_args[i - 1] in ("-ar", "-b:a"))]
        a_args += ["-ar", "8000", "-ac", "1", "-b:a", "12.2k"]
    # у lossless-кодеков битрейт не задаётся
    if to in ("flac", "wav", "alac", "aiff", "aifc", "aif", "au", "voc"):
        a_args = [a for i, a in enumerate(a_args)
                  if a not in ("-b:a",) and not (i and a_args[i - 1] == "-b:a")]

    base = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]

    if src_is_video and not dst_is_video:
        # видео -> аудио: вытаскиваем первую звуковую дорожку
        codec = AUDIO_CODECS.get(to)
        cmd = base + ["-i", str(src), "-vn", "-map", "0:a:0"]
        if codec:
            cmd += ["-c:a", codec]
        cmd += meta_args + a_args
    elif dst_is_video and not src_is_video:
        # аудио -> видео: обложка альбома как картинка, иначе чёрный фон 512x512 (как в VERT)
        vcodec, acodec = VIDEO_CODECS.get(to, ("libx264", "aac"))
        cover = dst_dir / f".{stem}_cover.jpg"
        has_art = False
        if keep_metadata and _has_video_stream(src):
            r = _run(base + ["-i", str(src), "-an", "-c:v", "copy", "-update", "1", str(cover)], 120)
            has_art = r.returncode == 0 and cover.exists() and cover.stat().st_size > 0
        if has_art:
            cmd = base + ["-loop", "1", "-i", str(cover), "-i", str(src),
                          "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        else:
            cmd = base + ["-f", "lavfi", "-i", "color=c=black:s=512x512:rate=1", "-i", str(src)]
        cmd += ["-shortest", "-pix_fmt", "yuv420p", "-r", "1", "-c:v", vcodec, "-c:a", acodec]
        cmd += meta_args + a_args
    elif dst_is_video:
        # видео -> видео
        vcodec, acodec = VIDEO_CODECS.get(to, ("libx264", "aac"))
        cmd = base + ["-i", str(src), "-c:v", vcodec, "-c:a", acodec]
        if vcodec == "libx264" or vcodec == "libx265":
            cmd += ["-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "23"]
        if vcodec == "libvpx":
            cmd += ["-b:v", "2M", "-crf", "10"]
        if vcodec == "mpeg2video" and to == "mxf":
            # MXF привередлив: fps фильтром, 48 кГц стерео и явный видеобитрейт
            cmd += ["-vf", "fps=25", "-b:v", "8M", "-ac", "2"]
            a_args = [x for i, x in enumerate(a_args)
                      if x not in ("-ar", "-b:a") and not (i and a_args[i - 1] in ("-ar", "-b:a"))]
            a_args += ["-ar", "48000"]
        elif vcodec == "mpeg2video":
            cmd += ["-r", "25", "-b:v", "5M"]   # mpeg2 не принимает нестандартный fps (15, 23.9 ...)
        cmd += meta_args + a_args
    else:
        # аудио -> аудио
        codec = AUDIO_CODECS.get(to)
        cmd = base + ["-i", str(src)]
        if to in ("m4a", "alac", "m4b") and keep_metadata:
            cmd += ["-c:v", "copy"]  # сохраняем обложку
        if codec:
            cmd += ["-c:a", codec]
        cmd += meta_args + a_args
        if to in AUDIO_MUXER:
            cmd += ["-f", AUDIO_MUXER[to]]
        if to == "weba":
            cmd += ["-vn"]

    if to in VIDEO_MUXER:
        cmd += ["-f", VIDEO_MUXER[to]]
    cmd.append(str(out))
    res = _run(cmd)
    if res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        err = _last_error(res, "ffmpeg завершился с ошибкой")
        low = (res.stderr or "").lower()
        if "matches no streams" in low or "does not contain any stream" in low:
            err = "В файле нет звуковой дорожки"
        elif "encoder not found" in low or "unknown encoder" in low:
            err = f"Эта сборка ffmpeg не умеет кодировать .{to}. Поставь полную сборку (например, gyan.dev full)"
        elif "invalid data found" in low or "error opening input" in low or "invalid argument" in low and "input" in low:
            err = "Файл повреждён или это не тот формат, что указан в расширении"
        raise ConvertError(err)
    return out


# ----------------------------------------------------------------------------
# Картинки (ImageMagick)
# ----------------------------------------------------------------------------

def convert_image(src: Path, dst_dir: Path, stem: str, to: str, *,
                  quality: int = 90, keep_metadata: bool = True,
                  density: int = 150) -> Path:
    exe = _which("magick") or _which("convert")
    if not exe:
        raise ConvertError("ImageMagick не найден в PATH")
    to = to.lower()
    if not IMAGE.get(to, (0, 0))[1]:
        raise ConvertError(f"Формат {to} нельзя создавать")
    out_ext = {"jpeg": "jpg", "tiff": "tif"}.get(to, to)
    out = dst_dir / f"{stem}.{out_ext}"
    fmt_prefix = {"jfif": "jpg", "jpe": "jpg"}.get(to, to)

    src_ext = src.suffix.lower().lstrip(".")
    src_arg = str(src)
    cmd = [exe]
    if src_ext in ("pdf", "eps", "svg"):
        cmd += ["-density", str(density)]
    # первая страница/кадр: многостраничные форматы не должны плодить файлы
    if src_ext in ("pdf", "tiff", "tif", "gif", "psd", "ico", "cur", "heic", "webp", "avif"):
        src_arg += "[0]"
    cmd.append(src_arg)
    if not keep_metadata:
        cmd.append("-strip")
    # прозрачность в форматы без альфа-канала заливаем белым, иначе будет чёрный фон
    if to in ("jpg", "jpeg", "jfif", "jpe", "bmp", "pbm", "pgm", "ppm", "pnm", "pdf", "eps"):
        cmd += ["-background", "white", "-alpha", "remove", "-alpha", "off"]
    if to in ("jpg", "jpeg", "jfif", "jpe", "webp", "avif", "heic", "jxl", "tiff", "tif"):
        cmd += ["-quality", str(max(1, min(100, quality)))]
    if to == "ico":
        cmd += ["-resize", "256x256>"]  # ico не больше 256 px
    cmd += ["-auto-orient", f"{fmt_prefix}:{out}"]
    res = _run(cmd, 600)
    if res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        err = _last_error(res, "ImageMagick завершился с ошибкой")
        if "no decode delegate" in err.lower():
            err = f"ImageMagick на этом компьютере не умеет читать .{src_ext}"
        if "no encode delegate" in (res.stderr or "").lower():
            err = f"Эта версия ImageMagick не умеет создавать .{to}. Поставь ImageMagick 7 с поддержкой {to.upper()}"
        if "not allowed by the security policy" in err.lower():
            err = "ImageMagick запрещает этот формат политикой безопасности (policy.xml)"
        raise ConvertError(err)
    return out


# ----------------------------------------------------------------------------
# Документы (pandoc + LibreOffice)
# ----------------------------------------------------------------------------

def _libreoffice_convert(src: Path, dst_dir: Path, target: str) -> Path | None:
    soffice = _which("soffice", "libreoffice")
    if not soffice:
        return None
    profile = dst_dir / ".lo_profile"
    res = _run([soffice, f"-env:UserInstallation=file://{profile}", "--headless",
                "--convert-to", target, "--outdir", str(dst_dir), str(src)], 300)
    shutil.rmtree(profile, ignore_errors=True)
    out = dst_dir / f"{src.stem}.{target}"
    return out if res.returncode == 0 and out.exists() else None


def convert_document(src: Path, dst_dir: Path, stem: str, to: str) -> Path:
    to = to.lower()
    src_ext = src.suffix.lower().lstrip(".")
    if not DOC.get(to, (0, 0))[1]:
        raise ConvertError(f"Формат {to} нельзя создавать")
    out = dst_dir / f"{stem}.{to}"

    # .doc pandoc не читает: сначала LibreOffice превращает его в docx
    tmp_docx = None
    if src_ext == "doc":
        tmp_docx = _libreoffice_convert(src, dst_dir, "docx")
        if not tmp_docx:
            raise ConvertError("Старый .doc читается только через LibreOffice, а он не установлен")
        src = tmp_docx
        src_ext = "docx"
        if to == "docx":
            tmp_docx.rename(out)
            return out
    if to == "doc":
        # .doc pandoc не пишет: делаем docx, а LibreOffice пересохраняет
        mid = dst_dir / f"{stem}.__mid.docx"
        _pandoc(src, src_ext, mid, "docx")
        conv = _libreoffice_convert(mid, dst_dir, "doc")
        mid.unlink(missing_ok=True)
        if not conv:
            raise ConvertError("Запись .doc возможна только через LibreOffice, а он не установлен")
        conv.rename(out)
        return out

    try:
        _pandoc(src, src_ext, out, to)
    finally:
        if tmp_docx:
            tmp_docx.unlink(missing_ok=True)
    return out


def _pandoc(src: Path, src_ext: str, out: Path, to: str) -> None:
    exe = _which("pandoc")
    if not exe:
        raise ConvertError("pandoc не найден в PATH")
    rfmt = PANDOC_READ.get(src_ext)
    wfmt = PANDOC_WRITE.get(to)
    if not rfmt:
        raise ConvertError(f"pandoc не умеет читать .{src_ext}")
    if not wfmt:
        raise ConvertError(f"pandoc не умеет писать .{to}")
    cmd = [exe, "-f", rfmt, "-t", wfmt, "-o", str(out), str(src), "--standalone"]
    if to in ("md", "txt", "rst", "org", "html", "csv", "tsv", "json"):
        cmd.append("--wrap=none")
    if to in ("docx", "odt", "epub"):
        cmd += [f"--resource-path={src.parent}"]
    res = _run(cmd, 300)
    if res.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        raise ConvertError(_last_error(res, "pandoc завершился с ошибкой"))


# ----------------------------------------------------------------------------
# Единая точка входа
# ----------------------------------------------------------------------------

def convert_any(src: Path, dst_dir: Path, stem: str, to: str, opts: dict) -> Path:
    """Выбирает движок по типу исходного файла и целевому формату."""
    src_ext = src.suffix.lower().lstrip(".")
    cat = category_of(src_ext)
    if cat is None or not can_read(src_ext):
        raise ConvertError(f"Формат .{src_ext} не поддерживается")
    to = to.lower()
    dst_cat = category_of(to)
    # неоднозначные форматы: pdf/eps есть и у картинок; ts/ico не путаем
    if to in DOC and to in IMAGE:
        dst_cat = cat if cat in ("doc", "image") else "image"

    common_meta = bool(opts.get("keep_metadata", True))
    if cat in ("audio", "video"):
        if dst_cat not in ("audio", "video"):
            raise ConvertError(f"Из {cat} нельзя сделать .{to}")
        return convert_media(src, dst_dir, stem, to,
                             bitrate=str(opts.get("bitrate", "auto")),
                             sample_rate=str(opts.get("sample_rate", "auto")),
                             keep_metadata=common_meta)
    if cat == "image":
        if dst_cat != "image":
            raise ConvertError(f"Из картинки нельзя сделать .{to}")
        return convert_image(src, dst_dir, stem, to, quality=int(opts.get("quality", 90)),
                             keep_metadata=common_meta, density=int(opts.get("density", 150)))
    if cat == "doc":
        if dst_cat != "doc":
            raise ConvertError(f"Из документа нельзя сделать .{to}")
        return convert_document(src, dst_dir, stem, to)
    raise ConvertError("Неизвестный тип файла")
