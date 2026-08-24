"""HTTP client for a locally-running MiniMax-H3 runtime (animede/Diffusers_minimax-h3).

This class does no model loading and no CUDA work of its own — it only
speaks HTTP to `scripts/server-h3.sh`'s uvicorn process (see vendor/Diffusers_minimax-h3
/app.py). That process owns the GPUs, the pinned diffusers/torch stack and
the model lifecycle; keeping it out-of-process is what lets it crash,
restart or be upgraded without touching the unified API.

Endpoint mapping is taken directly from the upstream app.py source (not
guessed from prose docs):
  GET  /api/status  -> {"busy": bool, "progress": ..., "runner": ..., ...}
  POST /api/t2va     -> multipart/form-data (Form fields, no file upload)
                        raises 400 (bad request) / 409 (generation_lock busy)
                        / 500 (generation failed) via HTTPException, JSON
                        body {"detail": "..."} in all three cases.

Only t2va (P0) is wired up to real generation. fl2va/ref2va (P1/P2) are
listed in capabilities() as "planned" and rejected with InvalidRequestError
until implemented — see _build_fl2va_form / _build_ref2va_form stubs below
for where that work plugs in.
"""

from __future__ import annotations

from typing import Any

import httpx

from videogen.backends.base import (
    BackendBusyError,
    BackendTimeoutError,
    BackendUnavailableError,
    GenerationError,
    InvalidRequestError,
    VideoBackend,
)
from videogen.schemas import GenerateRequest, GenerateResponse, VideoOutput


def _form_value(value: Any) -> Any:
    """httpx form-encodes bools as True/False; H3's FastAPI Form(bool) parser
    expects the usual string tokens, so normalize before sending."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


class MiniMaxH3Backend(VideoBackend):
    name = "minimax-h3"

    def __init__(
        self,
        base_url: str,
        request_timeout: float,
        health_timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._request_timeout = request_timeout
        self._health_timeout = health_timeout
        # Injectable for tests (httpx.MockTransport); None = real network.
        self._transport = transport

    def capabilities(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "requires_comfyui": False,
            "modes": {
                "t2va": {
                    "status": "available",
                    "description": "text -> video + audio",
                },
                "fl2va": {
                    "status": "planned",
                    "description": "first/last frame + text -> video",
                },
                "ref2va": {
                    "status": "planned",
                    "description": "reference image/video/audio + text -> video",
                },
            },
        }

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout, transport=self._transport
            ) as client:
                resp = await client.get(f"{self.base_url}/api/status")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BackendUnavailableError(
                f"H3 runtime unreachable at {self.base_url}: {exc}",
                backend=self.name,
            ) from exc
        data = resp.json()
        return {"reachable": True, "busy": bool(data.get("busy", False)), "raw": data}

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        if request.mode == "t2va":
            return await self._generate_t2va(request)
        raise InvalidRequestError(
            f"mode={request.mode!r} is not implemented yet for backend {self.name!r} "
            "(only 't2va' is supported in this phase; fl2va/ref2va are planned)",
            backend=self.name,
        )

    async def _generate_t2va(self, request: GenerateRequest) -> GenerateResponse:
        form = self._build_t2va_form(request)
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout, transport=self._transport
            ) as client:
                resp = await client.post(f"{self.base_url}/api/t2va", data=form)
        except httpx.TimeoutException as exc:
            raise BackendTimeoutError(
                f"H3 generation timed out after {self._request_timeout}s", backend=self.name
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailableError(
                f"H3 runtime unreachable at {self.base_url}: {exc}", backend=self.name
            ) from exc

        if resp.status_code == 409:
            raise BackendBusyError(self._detail(resp), backend=self.name)
        if resp.status_code == 400:
            raise InvalidRequestError(self._detail(resp), backend=self.name)
        if resp.status_code >= 500:
            raise GenerationError(self._detail(resp), backend=self.name)
        if resp.status_code != 200:
            # Anything else unexpected from the upstream app is still a
            # generation-layer failure, not a videogen bug.
            raise GenerationError(
                f"unexpected status {resp.status_code}: {self._detail(resp)}", backend=self.name
            )

        return self._normalize_t2va(request, resp.json())

    def _build_t2va_form(self, request: GenerateRequest) -> dict[str, Any]:
        options = request.options or {}
        form: dict[str, Any] = {
            "prompt": request.prompt,
            "seconds": request.duration,
            "height": request.height,
            "width": request.width,
            "num_inference_steps": options.get("num_inference_steps", 30),
        }
        if request.seed is not None:
            form["seed"] = request.seed
        # H3-specific, instant-apply knobs (see app.py's _run_generation):
        # forwarded verbatim if present, left unset otherwise so the H3
        # runtime falls back to its own env-var defaults.
        for key in ("cache", "cache_threshold", "attn", "turbo", "mute", "upscale"):
            if key in options:
                form[key] = options[key]
        return {k: _form_value(v) for k, v in form.items()}

    def _normalize_t2va(self, request: GenerateRequest, data: dict[str, Any]) -> GenerateResponse:
        video_url = f"{self.base_url}{data['video_url']}" if data.get("video_url") else None
        output = VideoOutput(
            video_path=data.get("mp4_path"),
            video_url=video_url,
            duration=data.get("duration_s"),
            width=data.get("width"),
            height=data.get("height"),
        )
        return GenerateResponse(
            backend=self.name,
            mode=request.mode,
            status="succeeded",
            output=output,
            metadata={
                "seed": data.get("seed"),
                "num_inference_steps": data.get("num_inference_steps"),
                "generation_time_s": data.get("total_elapsed_s"),
                "peak_vram_gb": data.get("peak_vram_gb"),
                "job_id": data.get("job_id"),
            },
            raw_metadata=data,
        )

    @staticmethod
    def _detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            return str(body.get("detail", body))
        except ValueError:
            return resp.text
