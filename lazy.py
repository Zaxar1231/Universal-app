"""
Ленивый импорт: тяжёлая библиотека загружается при первом обращении, а не при запуске приложения.

yt-dlp и pypdf нужны не каждому (одному скачивать видео, другому клеить PDF), а грузятся секунды на медленном диске
и старом процессоре. Обёртка ведёт себя как модуль: `lazy.Module("yt_dlp").YoutubeDL`, `bool(...)` отвечает, установлена ли
библиотека, и не загружает её.
"""
from __future__ import annotations

import importlib
import importlib.util
import threading


class Module:
    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mod", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _load(self):
        mod = object.__getattribute__(self, "_mod")
        if mod is None:
            with object.__getattribute__(self, "_lock"):
                mod = object.__getattribute__(self, "_mod")
                if mod is None:
                    mod = importlib.import_module(object.__getattribute__(self, "_name"))
                    object.__setattr__(self, "_mod", mod)
        return mod

    def installed(self) -> bool:
        if object.__getattribute__(self, "_mod") is not None:
            return True
        try:
            return importlib.util.find_spec(object.__getattribute__(self, "_name")) is not None
        except (ImportError, ValueError):
            return False

    def __bool__(self) -> bool:
        return self.installed()

    def __getattr__(self, attr: str):            # только для того, чего нет у самой обёртки
        return getattr(self._load(), attr)
