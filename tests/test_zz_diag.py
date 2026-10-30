import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="диагностика Windows")


OUT = []


def _try(label, **kw):
    exe = shutil.which("magick")
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, text=True, **kw)
        OUT.append(f"{label}: код {r.returncode}")
    except OSError as e:
        OUT.append(f"{label}: OSError {str(e)[:60]}")


def test_diag():
    OUT.append(f"magick={shutil.which('magick')} Popen={subprocess.Popen}")
    _try("простой")
    _try("env", env={**os.environ})
    _try("env + MAGICK", env={**os.environ, "MAGICK_THREAD_LIMIT": "2", "OMP_NUM_THREADS": "2"})
    _try("флаг", creationflags=subprocess.CREATE_NO_WINDOW)
    _try("env + флаг", env={**os.environ}, creationflags=subprocess.CREATE_NO_WINDOW)
    import desktop  # noqa: F401
    OUT.append(f"после desktop Popen={subprocess.Popen}")
    _try("desktop: простой")
    _try("desktop: env", env={**os.environ})
    _try("desktop: env + MAGICK", env={**os.environ, "MAGICK_THREAD_LIMIT": "2", "OMP_NUM_THREADS": "2"})
    sys.path.insert(0, "third_party/textfold")
    from textfold import server
    server._quiet_children()
    OUT.append("после textfold")
    _try("textfold: простой")
    _try("textfold: env", env={**os.environ})
    _try("textfold: env + MAGICK", env={**os.environ, "MAGICK_THREAD_LIMIT": "2", "OMP_NUM_THREADS": "2"})

    assert False, "DIAG\n" + "\n".join(OUT)
