"""Mocked HTTP tests for MiniMaxH3Backend.

No real GPU/model weights involved — httpx.MockTransport stands in for
the H3 runtime process, using response shapes taken directly from
vendor/Diffusers_minimax-h3's app.py / core/runner.py (see that submodule's
source for the ground truth this is pinned against).
"""

from __future__ import annotations

import base64

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


# --- fl2va --------------------------------------------------------------

TINY_IMAGE_B64 = base64.b64encode(b"not-really-a-png-just-test-bytes").decode()


def fl2va_request(**overrides) -> GenerateRequest:
    defaults = {
        "backend": "minimax-h3",
        "mode": "fl2va",
        "prompt": "a cat walking through rainy Tokyo at night",
        "duration": 5.0,
        "width": 768,
        "height": 768,
    }
    defaults.update(overrides)
    return GenerateRequest(**defaults)


def fl2va_result_json(**overrides) -> dict:
    data = {
        "mp4_path": "/data/videogen-output/minimax-h3/fl2va123.mp4",
        "mp4_filename": "fl2va123.mp4",
        "video_url": "/outputs/fl2va123.mp4",
        "duration_s": 5.19,
        "width": 768,
        "height": 768,
        "seed": 7,
        "num_inference_steps": 30,
        "total_elapsed_s": 40.2,
        "peak_vram_gb": 43.1,
        "job_id": "fl2va123",
    }
    data.update(overrides)
    return data


async def test_fl2va_sends_multipart_with_first_frame():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.read()
        return httpx.Response(200, json=fl2va_result_json())

    backend = make_backend(handler)
    request = fl2va_request(options={"first_frame": f"data:image/png;base64,{TINY_IMAGE_B64}"})
    response = await backend.generate(request)

    assert captured["path"] == "/api/fl2va"
    assert "multipart/form-data" in captured["content_type"]
    body = captured["body"]
    assert b'name="image"' in body
    assert b'name="last_image"' not in body  # only first_frame was supplied
    assert b'name="prompt"' in body
    assert request.prompt.encode() in body

    assert response.status == "succeeded"
    assert response.mode == "fl2va"
    assert response.output.video_url == f"{BASE_URL}/outputs/fl2va123.mp4"


async def test_fl2va_sends_both_frames_when_both_supplied():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=fl2va_result_json())

    backend = make_backend(handler)
    request = fl2va_request(
        options={"first_frame": TINY_IMAGE_B64, "last_frame": TINY_IMAGE_B64}
    )
    await backend.generate(request)

    assert b'name="image"' in captured["body"]
    assert b'name="last_image"' in captured["body"]


async def test_fl2va_without_any_frame_is_rejected_before_hitting_h3():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not reach the H3 runtime with no image at all")

    backend = make_backend(handler)
    with pytest.raises(InvalidRequestError) as exc_info:
        await backend.generate(fl2va_request())  # no first_frame/last_frame in options
    assert "first_frame" in str(exc_info.value) or "last_frame" in str(exc_info.value)


async def test_fl2va_invalid_base64_is_rejected_before_hitting_h3():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not reach the H3 runtime with undecodable image data")

    backend = make_backend(handler)
    with pytest.raises(InvalidRequestError):
        await backend.generate(fl2va_request(options={"first_frame": "not valid base64!!"}))


async def test_fl2va_busy_maps_to_backend_busy_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "別の生成が進行中です。"})

    backend = make_backend(handler)
    with pytest.raises(BackendBusyError):
        await backend.generate(fl2va_request(options={"first_frame": TINY_IMAGE_B64}))


# --- ref2va --------------------------------------------------------------


def ref2va_request(**overrides) -> GenerateRequest:
    defaults = {
        "backend": "minimax-h3",
        "mode": "ref2va",
        "prompt": "a character consistent across scenes",
        "duration": 5.0,
        "width": 768,
        "height": 768,
    }
    defaults.update(overrides)
    return GenerateRequest(**defaults)


def ref2va_result_json(**overrides) -> dict:
    data = {
        "mp4_path": "/data/videogen-output/minimax-h3/ref123.mp4",
        "mp4_filename": "ref123.mp4",
        "video_url": "/outputs/ref123.mp4",
        "duration_s": 5.19,
        "width": 768,
        "height": 768,
        "seed": 3,
        "num_inference_steps": 30,
        "total_elapsed_s": 55.0,
        "peak_vram_gb": 44.0,
        "job_id": "ref123",
    }
    data.update(overrides)
    return data


async def test_ref2va_sends_multipart_with_repeated_references_field():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.read()
        return httpx.Response(200, json=ref2va_result_json())

    backend = make_backend(handler)
    request = ref2va_request(
        options={
            "references": [
                {"data": TINY_IMAGE_B64, "filename": "ref1.png", "content_type": "image/png"},
                {"data": f"data:audio/wav;base64,{TINY_IMAGE_B64}", "filename": "ref2.wav"},
            ]
        }
    )
    response = await backend.generate(request)

    assert captured["path"] == "/api/ref2va"
    assert "multipart/form-data" in captured["content_type"]
    body = captured["body"]
    # Both references go under the SAME repeated field name "references".
    assert body.count(b'name="references"') == 2
    assert b'filename="ref1.png"' in body
    assert b'filename="ref2.wav"' in body
    assert b"image/png" in body
    assert b"audio/wav" in body  # taken from the data: URL, not guessed

    assert response.status == "succeeded"
    assert response.mode == "ref2va"
    assert response.output.video_url == f"{BASE_URL}/outputs/ref123.mp4"


async def test_ref2va_guesses_content_type_from_filename_when_not_given():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=ref2va_result_json())

    backend = make_backend(handler)
    request = ref2va_request(options={"references": [{"data": TINY_IMAGE_B64, "filename": "photo.jpg"}]})
    await backend.generate(request)

    assert b"image/jpeg" in captured["body"]


async def test_ref2va_without_any_reference_is_rejected_before_hitting_h3():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not reach the H3 runtime with no references at all")

    backend = make_backend(handler)
    with pytest.raises(InvalidRequestError) as exc_info:
        await backend.generate(ref2va_request())  # no options.references
    assert "references" in str(exc_info.value)


async def test_ref2va_too_many_references_is_rejected_before_hitting_h3():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not reach the H3 runtime with an over-limit reference count")

    backend = make_backend(handler)
    too_many = [{"data": TINY_IMAGE_B64, "filename": f"r{i}.png"} for i in range(13)]
    with pytest.raises(InvalidRequestError) as exc_info:
        await backend.generate(ref2va_request(options={"references": too_many}))
    assert "12" in str(exc_info.value)


async def test_ref2va_reference_missing_data_field_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not reach the H3 runtime with a malformed reference")

    backend = make_backend(handler)
    with pytest.raises(InvalidRequestError):
        await backend.generate(ref2va_request(options={"references": [{"filename": "no-data.png"}]}))


async def test_ref2va_busy_maps_to_backend_busy_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "別の生成が進行中です。"})

    backend = make_backend(handler)
    with pytest.raises(BackendBusyError):
        await backend.generate(
            ref2va_request(options={"references": [{"data": TINY_IMAGE_B64, "filename": "r.png"}]})
        )


def test_capabilities_lists_all_modes_without_network():
    backend = MiniMaxH3Backend(base_url=BASE_URL, request_timeout=5.0)
    caps = backend.capabilities()
    assert caps["requires_comfyui"] is False
    assert caps["modes"]["t2va"]["status"] == "available"
    assert caps["modes"]["fl2va"]["status"] == "available"
    assert caps["modes"]["ref2va"]["status"] == "available"
