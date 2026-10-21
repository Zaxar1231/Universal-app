# PyInstaller: pyinstaller packaging/universal.spec --noconfirm
# Windows: папка dist/Universal с Universal.exe (без консоли), её упаковывает installer.iss.
# macOS: dist/Universal.app, его упаковывает в .dmg release.yml.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

ROOT = SPECPATH + "/.."
VERSION = os.environ.get("UNIVERSAL_VERSION", "0.0.0")

a = Analysis(
    [ROOT + "/desktop.py"],
    pathex=[ROOT],
    # CTranslate2 (движок Whisper) держит свои библиотеки рядом с модулем
    binaries=collect_dynamic_libs("ctranslate2"),
    # yt_dlp_ejs: JS-скрипты, которыми deno решает задачки YouTube
    # ocrmypdf: шрифты и данные для текстового слоя, а версию он читает из метаданных пакета
    datas=[(ROOT + "/templates", "templates"), (ROOT + "/static", "static"), (ROOT + "/CHANGELOG.md", ".")] + collect_data_files("yt_dlp_ejs")
    + collect_data_files("ocrmypdf") + copy_metadata("ocrmypdf") + collect_data_files("faster_whisper"),
    # yt-dlp подгружает экстракторы динамически, а app импортирует модули по имени
    hiddenimports=collect_submodules("yt_dlp") + collect_submodules("yt_dlp_ejs") + collect_submodules("ocrmypdf")
    + ["about", "app", "changelog", "converters", "docedit", "downloader", "filetools", "editor", "engines", "ocr", "pdftools", "phoneserver", "qrdoc", "qrtools", "tasks", "updater", "version", "py7zr", "pillow_heif", "stt", "faster_whisper", "ctranslate2"],
    excludes=["tkinter", "pytest", "playwright", "onnxruntime", "torch", "transformers"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Universal",
    icon=ROOT + ("/packaging/universal.icns" if sys.platform == "darwin" else "/packaging/universal.ico"),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Universal")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Universal.app",
        icon=ROOT + "/packaging/universal.icns",
        bundle_identifier="io.github.zaxar1231.universal",
        info_plist={
            "CFBundleName": "Universal",
            "CFBundleDisplayName": "Universal",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "AGPL-3.0",
        },
    )
