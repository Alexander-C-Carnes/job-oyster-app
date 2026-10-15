"""The résumé sent with each application, kept as the exact PDF that went out.

One per tracked job (keyed by its tracker id), in data/sent/<id>/ with an index in
data/sent/index.json: {id: {name, source, recorded, title?}}; `title` is the name it downloads under
when that isn't its file's (the app's own, named by the Profile's PDF file name pattern). `source` says where it came from:
"upload" (added in the app), "app" (the app's résumé for the job, frozen when it was recorded),
or "notion" (a copy of the file in the Notion row's Resume Used). Later edits to the job's
résumé never change it.
"""
from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path
from typing import Optional

from ..store import _write, now_iso
from .board import JsonFile

MAX_BYTES = 15 * 1024 * 1024


def safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "-", Path(name or "").stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -.") or "resume"
    return stem[:120] + ".pdf"


class SentResumes:
    def __init__(self, root: Path):
        self.root = root
        self.index = JsonFile(root / "index.json", dict)
        self._lock = threading.Lock()

    def sig(self):
        return self.index.sig()

    def _dir(self, row_id: str) -> Path:
        return self.root / re.sub(r"[^A-Za-z0-9_-]", "_", row_id)

    def get(self, row_id: str) -> Optional[dict]:
        meta = self.index.read().get(row_id)
        if not meta:
            return None
        path = self._dir(row_id) / meta["name"]
        return {**meta, "path": path} if path.exists() else None

    def record(self, row_id: str, name: str, data: bytes, source: str, note: str = "", title: str = "") -> dict:
        if not data.startswith(b"%PDF-"):
            raise ValueError("That isn't a PDF.")
        if len(data) > MAX_BYTES:
            raise ValueError("That PDF is over 15 MB.")
        name = safe_name(name)
        with self._lock:
            d = self._dir(row_id)
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
            (d / name).write_bytes(data)
            index = dict(self.index.read())
            index[row_id] = {"name": name, "source": source, "note": note, "recorded": now_iso(),
                             **({"title": title} if title else {})}
            _write(self.index.path, index)
        return self.get(row_id)

    def remove(self, row_id: str) -> None:
        with self._lock:
            index = dict(self.index.read())
            if index.pop(row_id, None) is not None:
                _write(self.index.path, index)
            shutil.rmtree(self._dir(row_id), ignore_errors=True)
