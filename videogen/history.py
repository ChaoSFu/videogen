"""Generation history store.

Deliberately not a database — a single append-only JSONL file. Each line
is one generation attempt (success or failure). This is enough for a
single-node, single-process deployment with one globally-serial backend;
if that ever changes, this is the module to replace, not the API layer
that calls it.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from videogen.schemas import GenerateRequest, GenerateResponse, HistoryEntry

_lock = threading.Lock()


class HistoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_success(self, request: GenerateRequest, response: GenerateResponse) -> HistoryEntry:
        entry = HistoryEntry(
            id=uuid.uuid4().hex[:12],
            created_at=datetime.now(UTC).isoformat(),
            backend=request.backend,
            mode=request.mode,
            prompt=request.prompt,
            status="succeeded",
            video_url=response.output.video_url if response.output else None,
            video_path=response.output.video_path if response.output else None,
            duration=response.output.duration if response.output else None,
            width=response.output.width if response.output else None,
            height=response.output.height if response.output else None,
            error=None,
        )
        self._append(entry)
        return entry

    def record_failure(self, request: GenerateRequest, error: str) -> HistoryEntry:
        entry = HistoryEntry(
            id=uuid.uuid4().hex[:12],
            created_at=datetime.now(UTC).isoformat(),
            backend=request.backend,
            mode=request.mode,
            prompt=request.prompt,
            status="failed",
            video_url=None,
            video_path=None,
            duration=None,
            width=request.width,
            height=request.height,
            error=error,
        )
        self._append(entry)
        return entry

    def _append(self, entry: HistoryEntry) -> None:
        with _lock, self.path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def list_recent(self, limit: int = 50) -> list[HistoryEntry]:
        if not self.path.exists():
            return []
        lines: list[str] = []
        with _lock, self.path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        entries: list[HistoryEntry] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                data: dict[str, Any] = json.loads(line)
                entries.append(HistoryEntry(**data))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if len(entries) >= limit:
                break
        return entries
