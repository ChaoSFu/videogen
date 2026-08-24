"""API-level tests for the unified videogen FastAPI service.

Uses create_app() with a fake in-memory backend — no real HTTP, no real
H3 runtime, no GPU. This exercises routing, request validation and the
error -> HTTP status mapping that videogen/api.py owns.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
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


class SlowFakeBackend(VideoBackend):
    """Backend whose generate() takes a moment and records call overlap,
    so concurrency/queueing behavior can actually be observed instead of
    just asserted against a backend that returns instantly."""

    name = "minimax-h3"

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.call_log: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def health(self) -> dict[str, Any]:
        return {"reachable": True, "busy": False}

    def capabilities(self) -> dict[str, Any]:
        return {"backend": self.name, "requires_comfyui": False, "modes": {"t2va": {"status": "available"}}}

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.call_log.append(f"start:{request.prompt}")
        await asyncio.sleep(self.delay)
        self.call_log.append(f"end:{request.prompt}")
        self.in_flight -= 1
        return sample_success_response()


def make_client(backend: VideoBackend, tmp_path: Path) -> TestClient:
    # Isolated history file per test — never touch the repo's real run/history.jsonl.
    app = create_app({"minimax-h3": backend}, history_path=tmp_path / "history.jsonl")
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


def test_health_endpoint(tmp_path):
    client = make_client(FakeBackend(), tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "videogen"}


def test_list_backends_available(tmp_path):
    client = make_client(FakeBackend(health_result={"reachable": True, "busy": True}), tmp_path)
    resp = client.get("/v1/backends")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "minimax-h3"
    assert data[0]["available"] is True
    assert data[0]["busy"] is True


def test_list_backends_unavailable(tmp_path):
    client = make_client(
        FakeBackend(health_error=BackendUnavailableError("connection refused", backend="minimax-h3")),
        tmp_path,
    )
    resp = client.get("/v1/backends")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["available"] is False
    assert "connection refused" in data[0]["detail"]


def test_generate_success(tmp_path):
    client = make_client(FakeBackend(generate_result=sample_success_response()), tmp_path)
    resp = client.post("/v1/videos/generate", json=sample_request_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["output"]["video_path"] == "/data/videogen-output/minimax-h3/abc.mp4"
    assert body["metadata"]["seed"] == 12345


def test_generate_busy_returns_409(tmp_path):
    client = make_client(
        FakeBackend(generate_error=BackendBusyError("busy", backend="minimax-h3")), tmp_path
    )
    resp = client.post("/v1/videos/generate", json=sample_request_body())
    assert resp.status_code == 409
    assert resp.json() == {"error": "backend_busy", "backend": "minimax-h3", "detail": "busy"}


def test_generate_backend_generation_error_returns_502(tmp_path):
    client = make_client(
        FakeBackend(generate_error=GenerationError("CUDA OOM", backend="minimax-h3")), tmp_path
    )
    resp = client.post("/v1/videos/generate", json=sample_request_body())
    assert resp.status_code == 502
    assert resp.json()["error"] == "generation_failed"


def test_generate_invalid_request_error_returns_400(tmp_path):
    client = make_client(
        FakeBackend(generate_error=InvalidRequestError("bad prompt", backend="minimax-h3")), tmp_path
    )
    resp = client.post("/v1/videos/generate", json=sample_request_body())
    assert resp.status_code == 400


def test_generate_unknown_backend_returns_400(tmp_path):
    client = make_client(FakeBackend(), tmp_path)
    resp = client.post("/v1/videos/generate", json=sample_request_body(backend="not-a-real-backend"))
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_request"
    assert body["backend"] == "not-a-real-backend"


def test_generate_invalid_mode_returns_422(tmp_path):
    # `mode` is a closed Literal in the schema; a made-up mode is a schema
    # validation error (422), not a backend-level InvalidRequestError.
    client = make_client(FakeBackend(), tmp_path)
    resp = client.post("/v1/videos/generate", json=sample_request_body(mode="not-a-real-mode"))
    assert resp.status_code == 422


@pytest.mark.parametrize("mode", ["fl2va", "ref2va"])
def test_generate_unimplemented_mode_surfaces_backend_error(mode, tmp_path):
    # A schema-valid mode the backend hasn't wired up yet should still come
    # back as a clean 400, not a raw 500.
    client = make_client(
        FakeBackend(generate_error=InvalidRequestError(f"mode={mode} not implemented", backend="minimax-h3")),
        tmp_path,
    )
    resp = client.post("/v1/videos/generate", json=sample_request_body(mode=mode))
    assert resp.status_code == 400


# --- history --------------------------------------------------------------


def test_history_starts_empty(tmp_path):
    client = make_client(FakeBackend(), tmp_path)
    resp = client.get("/v1/videos")
    assert resp.status_code == 200
    assert resp.json() == []


def test_successful_generation_is_recorded_in_history(tmp_path):
    client = make_client(FakeBackend(generate_result=sample_success_response()), tmp_path)
    client.post("/v1/videos/generate", json=sample_request_body())

    resp = client.get("/v1/videos")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["status"] == "succeeded"
    assert entries[0]["prompt"] == sample_request_body()["prompt"]
    assert entries[0]["video_url"] == "http://127.0.0.1:18611/outputs/abc.mp4"


def test_failed_generation_is_recorded_in_history(tmp_path):
    client = make_client(
        FakeBackend(generate_error=BackendBusyError("busy", backend="minimax-h3")), tmp_path
    )
    client.post("/v1/videos/generate", json=sample_request_body())

    resp = client.get("/v1/videos")
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["status"] == "failed"
    assert entries[0]["video_url"] is None
    assert "busy" in entries[0]["error"]


def test_history_orders_most_recent_first(tmp_path):
    client = make_client(FakeBackend(generate_result=sample_success_response()), tmp_path)
    client.post("/v1/videos/generate", json=sample_request_body(prompt="first"))
    client.post("/v1/videos/generate", json=sample_request_body(prompt="second"))

    entries = client.get("/v1/videos").json()
    assert [e["prompt"] for e in entries] == ["second", "first"]


def test_history_respects_limit(tmp_path):
    client = make_client(FakeBackend(generate_result=sample_success_response()), tmp_path)
    for i in range(5):
        client.post("/v1/videos/generate", json=sample_request_body(prompt=f"prompt {i}"))

    entries = client.get("/v1/videos?limit=2").json()
    assert len(entries) == 2
    assert entries[0]["prompt"] == "prompt 4"


# --- queueing (needs real async concurrency, not the sync TestClient) ----


async def test_concurrent_generate_requests_queue_instead_of_409(tmp_path):
    backend = SlowFakeBackend(delay=0.05)
    app = create_app({"minimax-h3": backend}, history_path=tmp_path / "history.jsonl")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        results = await asyncio.gather(
            client.post("/v1/videos/generate", json=sample_request_body(prompt="first")),
            client.post("/v1/videos/generate", json=sample_request_body(prompt="second")),
        )

    # Neither request should see H3's 409 — both queue and both succeed.
    assert [r.status_code for r in results] == [200, 200]
    # And they must not have actually run concurrently against the backend.
    assert backend.max_in_flight == 1
    assert backend.call_log == ["start:first", "end:first", "start:second", "end:second"]


async def test_current_job_reflects_in_flight_request(tmp_path):
    backend = SlowFakeBackend(delay=0.1)
    app = create_app({"minimax-h3": backend}, history_path=tmp_path / "history.jsonl")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/v1/videos/current")).json() is None

        task = asyncio.create_task(
            client.post("/v1/videos/generate", json=sample_request_body(prompt="watch me"))
        )
        await asyncio.sleep(0.02)  # let the request actually start running

        current = (await client.get("/v1/videos/current")).json()
        assert current is not None
        assert current["prompt"] == "watch me"
        assert current["backend"] == "minimax-h3"

        await task
        assert (await client.get("/v1/videos/current")).json() is None


async def test_backends_endpoint_reports_queue_depth(tmp_path):
    backend = SlowFakeBackend(delay=0.1)
    app = create_app({"minimax-h3": backend}, history_path=tmp_path / "history.jsonl")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task1 = asyncio.create_task(
            client.post("/v1/videos/generate", json=sample_request_body(prompt="a"))
        )
        await asyncio.sleep(0.02)
        task2 = asyncio.create_task(
            client.post("/v1/videos/generate", json=sample_request_body(prompt="b"))
        )
        await asyncio.sleep(0.02)

        backends = (await client.get("/v1/backends")).json()
        assert backends[0]["queue_depth"] == 1

        await asyncio.gather(task1, task2)
        backends = (await client.get("/v1/backends")).json()
        assert backends[0]["queue_depth"] == 0


def test_ui_is_served(tmp_path):
    # Static frontend must actually be reachable — no fake backend needed,
    # this doesn't touch /v1/* routes at all.
    client = TestClient(create_app({}, history_path=tmp_path / "history.jsonl"))
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "videogen" in resp.text
