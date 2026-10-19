# PyInstaller: pyinstaller packaging/universal.spec --noconfirm
# Папка dist/Universal с Universal.exe (без консоли), её упаковывает installer.iss.
from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH + "/.."

a = Analysis(
    [ROOT + "/desktop.py"],
    pathex=[ROOT],
    datas=[(ROOT + "/templates", "templates")],
    # yt-dlp подгружает экстракторы динамически, а app импортирует модули по имени
    hiddenimports=collect_submodules("yt_dlp")
    + ["app", "converters", "downloader", "editor", "engines", "pdftools", "qrtools", "tasks", "py7zr"],
    excludes=["tkinter", "pytest", "playwright"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Universal",
    icon=ROOT + "/packaging/universal.ico",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Universal")
