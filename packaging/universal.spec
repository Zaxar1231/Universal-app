# PyInstaller: pyinstaller packaging/universal.spec --noconfirm
# Windows: папка dist/Universal с Universal.exe (без консоли), её упаковывает installer.iss.
# macOS: dist/Universal.app, его упаковывает в .dmg release.yml.
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH + "/.."
VERSION = os.environ.get("UNIVERSAL_VERSION", "0.0.0")

a = Analysis(
    [ROOT + "/desktop.py"],
    pathex=[ROOT],
    datas=[(ROOT + "/templates", "templates")],
    # yt-dlp подгружает экстракторы динамически, а app импортирует модули по имени
    hiddenimports=collect_submodules("yt_dlp")
    + ["app", "converters", "downloader", "editor", "engines", "pdftools", "qrdoc", "qrtools", "tasks", "py7zr"],
    excludes=["tkinter", "pytest", "playwright"],
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
