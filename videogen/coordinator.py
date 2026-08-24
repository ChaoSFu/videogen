"""Per-backend generation queueing.

H3 is globally serial: it holds one lock internally and rejects a second
concurrent request with 409. Rather than surface that 409 to the caller
and make them poll/retry, each backend gets an `asyncio.Lock` here so a
second concurrent call to the SAME backend simply waits its turn — no
external queue, no polling for the result, the HTTP request just doesn't
return until it's this caller's turn and the generation is done.

This is intentionally not a job system: there is no ticket/job id, no
persistence, no cross-process coordination. If videogen is ever run with
multiple worker processes this stops working (each worker would have its
own lock) — that's a real limitation, not an oversight; H3 itself is
single-process too, so a multi-worker videogen wouldn't help throughput
anyway.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class CurrentJob:
    backend: str
    mode: str
    prompt: str
    started_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "mode": self.mode,
            "prompt": self.prompt,
            "started_at": self.started_at,
            "elapsed_s": round(time.time() - self.started_at, 1),
        }


class BackendCoordinator:
    """One of these per backend name. Not shared across backends, so a
    slow t2va backend never blocks a request to a different, independent
    backend."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._waiting = 0
        self._current: CurrentJob | None = None

    @property
    def queue_depth(self) -> int:
        """Requests waiting for their turn, not counting the one running now."""
        return self._waiting

    @property
    def current(self) -> CurrentJob | None:
        return self._current

    async def run(self, backend: str, mode: str, prompt: str, fn: Callable[[], Awaitable[T]]) -> T:
        self._waiting += 1
        acquired = False
        try:
            await self._lock.acquire()
            acquired = True
            self._waiting -= 1
            self._current = CurrentJob(backend=backend, mode=mode, prompt=prompt, started_at=time.time())
            try:
                return await fn()
            finally:
                self._current = None
        finally:
            if acquired:
                self._lock.release()
            else:
                # Cancelled/errored while still queued (never acquired) —
                # still need to undo the queue-depth bump from above.
                self._waiting -= 1
