"""Generation history store.

Deliberately not a database — a single append-only JSONL file. Each line
is one generation attempt (success or failure). This is enough for a
single-node, single-process deployment with one globally-serial backend;
if that ever changes, this is the module to replace, not the API layer
that calls it.

Delete is implemented as "read everything, drop the one line, rewrite the
file" — fine at the scale a single-operator tool accumulates, not built
for a history of millions of entries.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from videogen.schemas import GenerateRequest, GenerateResponse, HistoryEntry, RequestSummary

_lock = threading.Lock()

# options keys that carry base64 media payloads — stripped before storing
# a request_summary so history.jsonl doesn't balloon with image/video/audio
# bytes on every single generation.
_MEDIA_OPTION_KEYS = ("first_frame", "last_frame", "references")


def _summarize_request(request: GenerateRequest) -> RequestSummary:
    options = dict(request.options or {})
    had_media = any(k in options for k in _MEDIA_OPTION_KEYS)
    for k in _MEDIA_OPTION_KEYS:
        options.pop(k, None)
    return RequestSummary(
        mode=request.mode,
        prompt=request.prompt,
        duration=request.duration,
        width=request.width,
        height=request.height,
        seed=request.seed,
        options=options,
        had_media_inputs=had_media,
    )


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
            request_summary=_summarize_request(request),
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
            request_summary=_summarize_request(request),
        )
        self._append(entry)
        return entry

    def _append(self, entry: HistoryEntry) -> None:
        with _lock, self.path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def _read_all(self) -> list[HistoryEntry]:
        """Oldest first, on-disk order. Corrupt lines are skipped, not fatal."""
        if not self.path.exists():
            return []
        entries: list[HistoryEntry] = []
        with _lock, self.path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data: dict[str, Any] = json.loads(line)
                entries.append(HistoryEntry(**data))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return entries

    def list_recent(self, limit: int = 50) -> list[HistoryEntry]:
        return list(reversed(self._read_all()))[:limit]

    def get(self, entry_id: str) -> HistoryEntry | None:
        for entry in self._read_all():
            if entry.id == entry_id:
                return entry
        return None

    def delete(self, entry_id: str) -> HistoryEntry | None:
        """Removes the entry from the log and returns it (so the caller can
        also clean up the underlying video file), or None if not found."""
        entries = self._read_all()
        remaining: list[HistoryEntry] = []
        deleted: HistoryEntry | None = None
        for entry in entries:
            if entry.id == entry_id and deleted is None:
                deleted = entry
                continue
            remaining.append(entry)
        if deleted is None:
            return None
        with _lock, self.path.open("w", encoding="utf-8") as f:
            for entry in remaining:
                f.write(entry.model_dump_json() + "\n")
        return deleted
