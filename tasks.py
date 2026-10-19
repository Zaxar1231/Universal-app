"""
Фоновые задачи с прогрессом и отменой.

Долгие операции (редактор видео, скачивание) не держат HTTP-запрос:
эндпоинт сохраняет файлы, ставит задачу в очередь и сразу отвечает её id.
Интерфейс опрашивает GET /api/task/<id> и показывает проценты.

id задачи совпадает с id папки задания, поэтому результат скачивается
тем же /api/download/<job>, что и у конвертера.
"""
from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable


class Cancelled(Exception):
    """Пользователь нажал «Отмена»."""


class Task:
    def __init__(self, tid: str, kind: str, job_dir: Path, archive: str):
        self.id = tid
        self.kind = kind
        self.job_dir = job_dir
        self.archive = archive
        self.status = "queued"          # queued | running | done | error | cancelled
        self.progress: float | None = 0.0   # 0..1, None = неизвестно
        self.message = "В очереди…"
        self.total = 0
        self.done = 0
        self.failed: list[dict] = []
        self.error = ""
        self.created = time.time()
        self.cancel_event = threading.Event()
        self.proc: subprocess.Popen | None = None   # текущий ffmpeg, чтобы убить при отмене
        self._lock = threading.Lock()

    @property
    def out_dir(self) -> Path:
        return self.job_dir / "out"

    def update(self, progress: float | None = None, message: str | None = None) -> None:
        with self._lock:
            if progress is not None:
                self.progress = max(0.0, min(1.0, progress))
            if message is not None:
                self.message = message

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def cancel(self) -> None:
        self.cancel_event.set()
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def outputs(self) -> list[str]:
        if not self.out_dir.is_dir():
            return []
        return sorted(p.name for p in self.out_dir.iterdir()
                      if p.is_file() and not p.name.startswith("."))

    def to_dict(self) -> dict:
        with self._lock:
            files = self.outputs() if self.status in ("done", "cancelled") else []
            d = {
                "task": self.id, "job": self.id, "kind": self.kind, "status": self.status,
                "progress": self.progress, "message": self.message, "total": self.total,
                "done": self.done, "failed": list(self.failed), "error": self.error,
                "archive": self.archive, "files": len(files),
            }
            if len(files) == 1:
                d["single_file"] = files[0]
            d["names"] = files[:100]
            return d


class TaskManager:
    """Очередь задач. Тяжёлое (видео) по умолчанию идёт по 2 за раз."""

    def __init__(self, workers: int = 2, keep: int = 3600):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="task")
        self.tasks: dict[str, Task] = {}
        self.keep = keep
        self._lock = threading.Lock()

    def submit(self, task: Task, fn: Callable[[Task], None]) -> Task:
        self._gc()
        with self._lock:
            self.tasks[task.id] = task

        def run():
            if task.cancel_event.is_set():
                task.status, task.message = "cancelled", "Отменено"
                return
            task.status, task.message = "running", "Начинаю…"
            try:
                fn(task)
                task.check_cancel()
                task.status = "done"
                task.update(1.0, "Готово")
            except Cancelled:
                task.status, task.message = "cancelled", "Отменено"
            except Exception as e:  # задача не должна ронять поток пула
                task.status = "error"
                task.error = str(e) or e.__class__.__name__
                task.message = "Ошибка"
            finally:
                task.proc = None

        self.pool.submit(run)
        return task

    def get(self, tid: str) -> Task | None:
        return self.tasks.get(tid)

    def _gc(self) -> None:
        now = time.time()
        with self._lock:
            for tid in [t for t, task in self.tasks.items() if now - task.created > self.keep]:
                del self.tasks[tid]
