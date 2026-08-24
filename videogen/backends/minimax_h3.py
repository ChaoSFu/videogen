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
  POST /api/fl2va    -> multipart/form-data (same Form fields as t2va, plus
                        optional `image`/`last_image` file uploads; at
                        least one of the two is required)
  Both raise 400 (bad request) / 409 (generation_lock busy) / 500
  (generation failed) via HTTPException, JSON body {"detail": "..."} in
  all three cases, and share the same response shape (both call
  app.py's `_run_generation` -> `core/runner.py`'s `generate()`).

t2va (P0) and fl2va (P1) are wired up to real generation. ref2va (P2) is
listed in capabilities() as "planned" and rejected with
InvalidRequestError until implemented.
"""

from __future__ import annotations

import base64
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


def _decode_image(label: str, data_url_or_b64: str) -> bytes:
    """Accepts either a raw base64 string or a `data:image/...;base64,...`
    data URL (what browsers' FileReader.readAsDataURL produces — the
    frontend sends these as-is rather than stripping the prefix client-side)."""
    payload = data_url_or_b64
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        return base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise InvalidRequestError(f"{label} is not valid base64: {exc}") from exc


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
                    "status": "available",
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
        if request.mode == "fl2va":
            return await self._generate_fl2va(request)
        raise InvalidRequestError(
            f"mode={request.mode!r} is not implemented yet for backend {self.name!r} "
            "(t2va/fl2va are supported in this phase; ref2va is planned)",
            backend=self.name,
        )

    async def _generate_t2va(self, request: GenerateRequest) -> GenerateResponse:
        form = self._build_shared_form(request)
        data = await self._call_h3("/api/t2va", data=form)
        return self._normalize_result(request, data)

    async def _generate_fl2va(self, request: GenerateRequest) -> GenerateResponse:
        options = request.options or {}
        first_frame = options.get("first_frame")
        last_frame = options.get("last_frame")
        if not first_frame and not last_frame:
            raise InvalidRequestError(
                "fl2va requires options.first_frame and/or options.last_frame "
                "(base64-encoded image, optionally a data: URL)",
                backend=self.name,
            )

        form = self._build_shared_form(request)
        files: dict[str, tuple[str, bytes, str]] = {}
        if first_frame:
            files["image"] = ("first_frame.png", _decode_image("first_frame", first_frame), "image/png")
        if last_frame:
            files["last_image"] = ("last_frame.png", _decode_image("last_frame", last_frame), "image/png")

        data = await self._call_h3("/api/fl2va", data=form, files=files)
        return self._normalize_result(request, data)

    async def _call_h3(
        self,
        path: str,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout, transport=self._transport
            ) as client:
                resp = await client.post(f"{self.base_url}{path}", data=data, files=files)
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
        return resp.json()

    def _build_shared_form(self, request: GenerateRequest) -> dict[str, Any]:
        """Form fields common to /api/t2va and /api/fl2va (both take the
        same Form(...) signature modulo the fl2va-only file uploads)."""
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

    def _normalize_result(self, request: GenerateRequest, data: dict[str, Any]) -> GenerateResponse:
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
