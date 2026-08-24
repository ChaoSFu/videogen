"""Unified videogen FastAPI service.

    GET  /health               liveness of this process (cheap, no backend calls)
    GET  /v1/backends          per-backend reachability + capabilities
    POST /v1/videos/generate   dispatch to the requested backend

This process never loads a video model itself. Each backend is an HTTP
client to a separately-running runtime process (see videogen/backends/).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from videogen import config
from videogen.backends import (
    BackendBusyError,
    BackendTimeoutError,
    BackendUnavailableError,
    GenerationError,
    InvalidRequestError,
    MiniMaxH3Backend,
    VideoBackend,
)
from videogen.schemas import BackendInfo, ErrorResponse, GenerateRequest, GenerateResponse

logger = logging.getLogger("videogen.api")


def _default_backends() -> dict[str, VideoBackend]:
    return {
        "minimax-h3": MiniMaxH3Backend(
            base_url=config.H3_BASE_URL,
            request_timeout=config.H3_REQUEST_TIMEOUT,
            health_timeout=config.H3_HEALTH_TIMEOUT,
        ),
    }


def create_app(backends: dict[str, VideoBackend] | None = None) -> FastAPI:
    """Factory so tests can inject fake/mocked backends instead of the real
    HTTP-backed ones without monkeypatching module globals."""
    app = FastAPI(title="videogen", description="统一视频生成服务")
    app.state.backends = backends if backends is not None else _default_backends()

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
            raise InvalidRequestError(
                f"unknown backend {request.backend!r}, available: "
                f"{list(app.state.backends.keys())}",
                backend=request.backend,
            )
        return await backend.generate(request)

    return app


app = create_app()
