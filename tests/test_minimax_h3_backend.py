"""Mocked HTTP tests for MiniMaxH3Backend.

No real GPU/model weights involved — httpx.MockTransport stands in for
the H3 runtime process, using response shapes taken directly from
vendor/Diffusers_minimax-h3's app.py / core/runner.py (see that submodule's
source for the ground truth this is pinned against).
"""

from __future__ import annotations

import httpx
import pytest

from videogen.backends import (
    BackendBusyError,
    BackendUnavailableError,
    GenerationError,
    InvalidRequestError,
)
from videogen.backends.minimax_h3 import MiniMaxH3Backend
from videogen.schemas import GenerateRequest

BASE_URL = "http://127.0.0.1:18611"


def make_backend(handler) -> MiniMaxH3Backend:
    return MiniMaxH3Backend(
        base_url=BASE_URL,
        request_timeout=5.0,
        health_timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


def t2va_request(**overrides) -> GenerateRequest:
    defaults = {
        "backend": "minimax-h3",
        "mode": "t2va",
        "prompt": "a cat walking through rainy Tokyo at night",
        "duration": 5.0,
        "width": 768,
        "height": 768,
        "seed": 42,
    }
    defaults.update(overrides)
    return GenerateRequest(**defaults)


# --- health() ---------------------------------------------------------


async def test_health_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/status"
        return httpx.Response(200, json={"busy": False, "runner": {"loaded": True}})

    backend = make_backend(handler)
    result = await backend.health()
    assert result["reachable"] is True
    assert result["busy"] is False


async def test_health_backend_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend = make_backend(handler)
    with pytest.raises(BackendUnavailableError) as exc_info:
        await backend.health()
    assert exc_info.value.backend == "minimax-h3"


# --- generate() / t2va request mapping --------------------------------


async def test_t2va_request_mapping():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["content_type"] = request.headers.get("content-type", "")
        body = request.read().decode()
        captured["form"] = dict(pair.split("=") for pair in body.split("&"))
        return httpx.Response(
            200,
            json={
                "mp4_path": "/data/videogen-output/minimax-h3/abc123.mp4",
                "mp4_filename": "abc123.mp4",
                "video_url": "/outputs/abc123.mp4",
                "duration_s": 5.19,
                "width": 768,
                "height": 768,
                "seed": 42,
                "num_inference_steps": 30,
                "total_elapsed_s": 28.1,
                "peak_vram_gb": 45.6,
                "job_id": "abc123",
            },
        )

    backend = make_backend(handler)
    request = t2va_request(options={"num_inference_steps": 25, "turbo": True})
    response = await backend.generate(request)

    assert captured["path"] == "/api/t2va"
    assert "application/x-www-form-urlencoded" in captured["content_type"]
    assert captured["form"]["prompt"] == request.prompt.replace(" ", "+")
    assert captured["form"]["seconds"] == "5.0"
    assert captured["form"]["height"] == "768"
    assert captured["form"]["width"] == "768"
    assert captured["form"]["seed"] == "42"
    assert captured["form"]["num_inference_steps"] == "25"
    assert captured["form"]["turbo"] == "true"

    assert response.status == "succeeded"
    assert response.output.video_path == "/data/videogen-output/minimax-h3/abc123.mp4"
    assert response.output.video_url == f"{BASE_URL}/outputs/abc123.mp4"
    assert response.output.duration == 5.19
    assert response.metadata["seed"] == 42
    assert response.raw_metadata["job_id"] == "abc123"


# --- error mapping ------------------------------------------------------


async def test_h3_busy_returns_backend_busy_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "別の生成が進行中です。"})

    backend = make_backend(handler)
    with pytest.raises(BackendBusyError) as exc_info:
        await backend.generate(t2va_request())
    assert exc_info.value.backend == "minimax-h3"


async def test_h3_server_error_returns_generation_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "generation failed: CUDA OOM"})

    backend = make_backend(handler)
    with pytest.raises(GenerationError) as exc_info:
        await backend.generate(t2va_request())
    assert "CUDA OOM" in str(exc_info.value)


async def test_h3_bad_request_returns_invalid_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "prompt is required"})

    backend = make_backend(handler)
    with pytest.raises(InvalidRequestError):
        await backend.generate(t2va_request(prompt=" "))


async def test_unreachable_runtime_during_generate():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend = make_backend(handler)
    with pytest.raises(BackendUnavailableError):
        await backend.generate(t2va_request())


# --- unsupported modes (extension points for FL2VA/Ref2VA) -------------


async def test_fl2va_mode_not_yet_implemented():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not reach the H3 runtime for an unsupported mode")

    backend = make_backend(handler)
    with pytest.raises(InvalidRequestError) as exc_info:
        await backend.generate(t2va_request(mode="fl2va"))
    assert "fl2va" in str(exc_info.value)


async def test_ref2va_mode_not_yet_implemented():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not reach the H3 runtime for an unsupported mode")

    backend = make_backend(handler)
    with pytest.raises(InvalidRequestError):
        await backend.generate(t2va_request(mode="ref2va"))


def test_capabilities_lists_all_modes_without_network():
    backend = MiniMaxH3Backend(base_url=BASE_URL, request_timeout=5.0)
    caps = backend.capabilities()
    assert caps["requires_comfyui"] is False
    assert caps["modes"]["t2va"]["status"] == "available"
    assert caps["modes"]["fl2va"]["status"] == "planned"
    assert caps["modes"]["ref2va"]["status"] == "planned"
