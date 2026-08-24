"""Unified videogen FastAPI service.

    GET  /health               liveness of this process (cheap, no backend calls)
    GET  /v1/backends          per-backend reachability + capabilities
    POST /v1/videos/generate   dispatch to the requested backend
    GET  /v1/videos            generation history (JSONL-backed, see history.py)
    GET  /ui                   single-page frontend (static, no build step)

This process never loads a video model itself. Each backend is an HTTP
client to a separately-running runtime process (see videogen/backends/).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from videogen import config
from videogen.backends import (
    BackendBusyError,
    BackendError,
    BackendTimeoutError,
    BackendUnavailableError,
    GenerationError,
    InvalidRequestError,
    MiniMaxH3Backend,
    VideoBackend,
)
from videogen.history import HistoryStore
from videogen.schemas import (
    BackendInfo,
    ErrorResponse,
    GenerateRequest,
    GenerateResponse,
    HistoryEntry,
)

logger = logging.getLogger("videogen.api")
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _default_backends() -> dict[str, VideoBackend]:
    return {
        "minimax-h3": MiniMaxH3Backend(
            base_url=config.H3_BASE_URL,
            request_timeout=config.H3_REQUEST_TIMEOUT,
            health_timeout=config.H3_HEALTH_TIMEOUT,
        ),
    }


def create_app(
    backends: dict[str, VideoBackend] | None = None,
    history_path: Path | None = None,
) -> FastAPI:
    """Factory so tests can inject fake/mocked backends (and an isolated
    history file) instead of the real HTTP-backed ones / shared state."""
    app = FastAPI(title="videogen", description="统一视频生成服务")
    app.state.backends = backends if backends is not None else _default_backends()
    app.state.history = HistoryStore(history_path or config.VIDEOGEN_HISTORY_FILE)

    if STATIC_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")

        @app.get("/", include_in_schema=False)
        async def root_redirect():
            return RedirectResponse(url="/ui/")

    def _error(status_code: int, error: str, backend: str | None, detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content=ErrorResponse(error=error, backend=backend, detail=detail).model_dump(),
        )

    @app.exception_handler(BackendUnavailableError)
    async def _h_unavailable(_request: Request, exc: BackendUnavailableError):
        return _error(503, "backend_unavailable", exc.backend, str(exc))

    @app.exception_handler(BackendBusyError)
    async def _h_busy(_request: Request, exc: BackendBusyError):
        return _error(409, "backend_busy", exc.backend, str(exc))

    @app.exception_handler(InvalidRequestError)
    async def _h_invalid(_request: Request, exc: InvalidRequestError):
        return _error(400, "invalid_request", exc.backend, str(exc))

    @app.exception_handler(GenerationError)
    async def _h_generation(_request: Request, exc: GenerationError):
        # Upstream backend accepted the request but failed — distinct from a
        # bug in this process, so 502 (bad gateway) rather than 500.
        logger.error("backend=%s generation failed: %s", exc.backend, exc)
        return _error(502, "generation_failed", exc.backend, str(exc))

    @app.exception_handler(BackendTimeoutError)
    async def _h_timeout(_request: Request, exc: BackendTimeoutError):
        return _error(504, "backend_timeout", exc.backend, str(exc))

    @app.exception_handler(Exception)
    async def _h_unexpected(_request: Request, exc: Exception):
        logger.exception("unhandled error in videogen API")
        return _error(500, "internal_error", None, str(exc))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "videogen"}

    @app.get("/v1/backends", response_model=list[BackendInfo])
    async def list_backends() -> list[BackendInfo]:
        infos: list[BackendInfo] = []
        for name, backend in app.state.backends.items():
            try:
                health_result = await backend.health()
                infos.append(
                    BackendInfo(
                        name=name,
                        available=True,
                        busy=health_result.get("busy"),
                        capabilities=backend.capabilities(),
                    )
                )
            except BackendUnavailableError as exc:
                infos.append(
                    BackendInfo(
                        name=name,
                        available=False,
                        capabilities=backend.capabilities(),
                        detail=str(exc),
                    )
                )
        return infos

    @app.post("/v1/videos/generate", response_model=GenerateResponse)
    async def generate_video(request: GenerateRequest) -> GenerateResponse:
        backend = app.state.backends.get(request.backend)
        if backend is None:
            exc = InvalidRequestError(
                f"unknown backend {request.backend!r}, available: "
                f"{list(app.state.backends.keys())}",
                backend=request.backend,
            )
            app.state.history.record_failure(request, str(exc))
            raise exc
        try:
            response = await backend.generate(request)
        except BackendError as exc:
            app.state.history.record_failure(request, str(exc))
            raise
        app.state.history.record_success(request, response)
        return response

    @app.get("/v1/videos", response_model=list[HistoryEntry])
    async def list_history(limit: int = 50) -> list[HistoryEntry]:
        return app.state.history.list_recent(limit=limit)

    return app


app = create_app()
