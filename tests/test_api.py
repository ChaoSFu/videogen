"""API-level tests for the unified videogen FastAPI service.

Uses create_app() with a fake in-memory backend — no real HTTP, no real
H3 runtime, no GPU. This exercises routing, request validation and the
error -> HTTP status mapping that videogen/api.py owns.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from videogen.api import create_app
from videogen.backends.base import (
    BackendBusyError,
    BackendUnavailableError,
    GenerationError,
    InvalidRequestError,
    VideoBackend,
)
from videogen.schemas import GenerateRequest, GenerateResponse, VideoOutput


class FakeBackend(VideoBackend):
    """Scripted backend: raises/returns whatever the test configures."""

    name = "minimax-h3"

    def __init__(self, *, health_result=None, health_error=None, generate_result=None, generate_error=None):
        self._health_result = health_result or {"reachable": True, "busy": False}
        self._health_error = health_error
        self._generate_result = generate_result
        self._generate_error = generate_error

    async def health(self) -> dict[str, Any]:
        if self._health_error:
            raise self._health_error
        return self._health_result

    def capabilities(self) -> dict[str, Any]:
        return {"backend": self.name, "requires_comfyui": False, "modes": {"t2va": {"status": "available"}}}

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        if self._generate_error:
            raise self._generate_error
        return self._generate_result


def make_client(backend: VideoBackend) -> TestClient:
    app = create_app({"minimax-h3": backend})
    return TestClient(app)


def sample_request_body(**overrides) -> dict:
    body = {
        "backend": "minimax-h3",
        "mode": "t2va",
        "prompt": "A cinematic shot of a cat walking through rainy Tokyo at night",
        "duration": 5,
        "width": 768,
        "height": 768,
        "seed": 12345,
    }
    body.update(overrides)
    return body


def sample_success_response() -> GenerateResponse:
    return GenerateResponse(
        backend="minimax-h3",
        mode="t2va",
        status="succeeded",
        output=VideoOutput(
            video_path="/data/videogen-output/minimax-h3/abc.mp4",
            video_url="http://127.0.0.1:18611/outputs/abc.mp4",
            duration=5.19,
            width=768,
            height=768,
        ),
        metadata={"seed": 12345},
        raw_metadata={"job_id": "abc"},
    )


def test_health_endpoint():
    client = make_client(FakeBackend())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "videogen"}


def test_list_backends_available():
    client = make_client(FakeBackend(health_result={"reachable": True, "busy": True}))
    resp = client.get("/v1/backends")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "minimax-h3"
    assert data[0]["available"] is True
    assert data[0]["busy"] is True


def test_list_backends_unavailable():
    client = make_client(
        FakeBackend(health_error=BackendUnavailableError("connection refused", backend="minimax-h3"))
    )
    resp = client.get("/v1/backends")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["available"] is False
    assert "connection refused" in data[0]["detail"]


def test_generate_success():
    client = make_client(FakeBackend(generate_result=sample_success_response()))
    resp = client.post("/v1/videos/generate", json=sample_request_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["output"]["video_path"] == "/data/videogen-output/minimax-h3/abc.mp4"
    assert body["metadata"]["seed"] == 12345


def test_generate_busy_returns_409():
    client = make_client(
        FakeBackend(generate_error=BackendBusyError("busy", backend="minimax-h3"))
    )
    resp = client.post("/v1/videos/generate", json=sample_request_body())
    assert resp.status_code == 409
    assert resp.json() == {"error": "backend_busy", "backend": "minimax-h3", "detail": "busy"}


def test_generate_backend_generation_error_returns_502():
    client = make_client(
        FakeBackend(generate_error=GenerationError("CUDA OOM", backend="minimax-h3"))
    )
    resp = client.post("/v1/videos/generate", json=sample_request_body())
    assert resp.status_code == 502
    assert resp.json()["error"] == "generation_failed"


def test_generate_invalid_request_error_returns_400():
    client = make_client(
        FakeBackend(generate_error=InvalidRequestError("bad prompt", backend="minimax-h3"))
    )
    resp = client.post("/v1/videos/generate", json=sample_request_body())
    assert resp.status_code == 400


def test_generate_unknown_backend_returns_400():
    client = make_client(FakeBackend())
    resp = client.post("/v1/videos/generate", json=sample_request_body(backend="not-a-real-backend"))
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_request"
    assert body["backend"] == "not-a-real-backend"


def test_generate_invalid_mode_returns_422():
    # `mode` is a closed Literal in the schema; a made-up mode is a schema
    # validation error (422), not a backend-level InvalidRequestError.
    client = make_client(FakeBackend())
    resp = client.post("/v1/videos/generate", json=sample_request_body(mode="not-a-real-mode"))
    assert resp.status_code == 422


@pytest.mark.parametrize("mode", ["fl2va", "ref2va"])
def test_generate_unimplemented_mode_surfaces_backend_error(mode):
    # A schema-valid mode the backend hasn't wired up yet should still come
    # back as a clean 400, not a raw 500.
    client = make_client(
        FakeBackend(generate_error=InvalidRequestError(f"mode={mode} not implemented", backend="minimax-h3"))
    )
    resp = client.post("/v1/videos/generate", json=sample_request_body(mode=mode))
    assert resp.status_code == 400
