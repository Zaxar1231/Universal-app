import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="диагностика Windows")


def _try(label, **kw):
    exe = shutil.which("magick")
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, text=True, **kw)
        print(f"DIAG {label}: код {r.returncode}", flush=True)
    except OSError as e:
        print(f"DIAG {label}: OSError {str(e)[:80]}", flush=True)


def test_diag():
    print("DIAG magick:", shutil.which("magick"), "Popen:", subprocess.Popen, flush=True)
    _try("простой")
    _try("env", env={**os.environ})
    _try("env + MAGICK", env={**os.environ, "MAGICK_THREAD_LIMIT": "2", "OMP_NUM_THREADS": "2"})
    _try("флаг", creationflags=subprocess.CREATE_NO_WINDOW)
    _try("env + флаг", env={**os.environ}, creationflags=subprocess.CREATE_NO_WINDOW)
    import desktop  # noqa: F401
    print("DIAG после desktop, Popen:", subprocess.Popen, flush=True)
    _try("desktop: простой")
    _try("desktop: env", env={**os.environ})
    _try("desktop: env + MAGICK", env={**os.environ, "MAGICK_THREAD_LIMIT": "2", "OMP_NUM_THREADS": "2"})
    sys.path.insert(0, "third_party/textfold")
    from textfold import server
    server._quiet_children()
    print("DIAG после textfold, Popen.__init__:", subprocess.Popen.__init__, flush=True)
    _try("textfold: простой")
    _try("textfold: env", env={**os.environ})
    _try("textfold: env + MAGICK", env={**os.environ, "MAGICK_THREAD_LIMIT": "2", "OMP_NUM_THREADS": "2"})
