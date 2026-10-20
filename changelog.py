"""
«Что нового»: разделы CHANGELOG.md для окна, которое один раз всплывает после обновления.

CHANGELOG.md лежит рядом с кодом (в сборках его кладут PyInstaller и build.sh).
Разделы начинаются строкой «## vX.Y.Z — дата».
"""
from __future__ import annotations

import re
from pathlib import Path

FILE = Path(__file__).resolve().parent / "CHANGELOG.md"
HEAD = re.compile(r"^## v(\d+(?:\.\d+)*)\s*(?:—|-)?\s*(.*)$", re.M)


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def sections(text: str | None = None) -> list[dict]:
    if text is None:
        try:
            text = FILE.read_text(encoding="utf-8")
        except OSError:
            return []
    heads = list(HEAD.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append({"version": m.group(1), "date": m.group(2).strip(), "md": text[m.end():end].strip()})
    return out


def since(seen: str, current: str, limit: int = 5, text: str | None = None) -> list[dict]:
    """Разделы новее seen и не новее current, сначала свежие. Без seen — только текущая версия."""
    cur = _key(current)
    items = [s for s in sections(text) if _key(s["version"]) <= cur]
    if seen:
        old = _key(seen)
        items = [s for s in items if _key(s["version"]) > old]
    else:
        items = [s for s in items if _key(s["version"]) == cur] or items[:1]
    return items[:limit]


def seen_file() -> Path:
    import engines
    return engines.root() / "seen_version.txt"


def after_update(current: str) -> list[dict]:
    """Для окна после обновления: разделы новее последней показанной версии; запоминает текущую.
    Помним на диске, а не в браузере: localStorage окна теряется, если сменился порт."""
    f = seen_file()
    try:
        seen = f.read_text(encoding="utf-8").strip()
    except OSError:
        seen = ""
    if seen == current:
        return []
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(current, encoding="utf-8")
    except OSError:
        return []          # запомнить негде — лучше не показывать при каждом запуске
    return since(seen, current)
