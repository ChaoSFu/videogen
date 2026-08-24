"""Backend protocol and error taxonomy.

A backend is an HTTP client to some already-running video generation
runtime (its own process, its own GPU memory, its own dependency stack).
It never loads a model itself — see MiniMaxH3Backend for why.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from videogen.schemas import GenerateRequest, GenerateResponse


class BackendError(Exception):
    """Base class for all backend-related errors.

    `backend` is set by whoever raises it so the API layer's exception
    handlers can report which backend failed without re-parsing the
    request body.
    """

    def __init__(self, message: str, backend: str | None = None):
        super().__init__(message)
        self.backend = backend


class BackendUnavailableError(BackendError):
    """The backend runtime process could not be reached."""


class BackendBusyError(BackendError):
    """The backend is already processing another generation request."""


class InvalidRequestError(BackendError):
    """The request was rejected as malformed or unsupported."""


class GenerationError(BackendError):
    """The backend accepted the request but generation itself failed."""


class BackendTimeoutError(BackendError):
    """The backend did not respond within the configured timeout."""


class VideoBackend(ABC):
    name: str

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Reachability/busy check. Raises BackendUnavailableError if unreachable.

        Uses a short timeout — this is a liveness probe, not a request
        that should wait behind an in-progress generation.
        """

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Static description of supported modes/params. No network call."""

    @abstractmethod
    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Run one generation. May block for a long time (real inference)."""
