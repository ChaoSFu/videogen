"""Unified request/response schemas for the videogen API.

These are intentionally backend-agnostic. A backend's raw response is
normalized into `GenerateResponse` by its own backend class; nothing here
should leak a specific backend's private parameter names or payload shape
(those go in `options` on the way in, and `raw_metadata` on the way out).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Mode = Literal["t2va", "fl2va", "ref2va"]


class GenerateRequest(BaseModel):
    backend: str
    mode: Mode = "t2va"
    prompt: str
    duration: float = 5.0
    width: int = 768
    height: int = 768
    seed: int | None = None
    # Backend-specific knobs (e.g. H3's num_inference_steps, turbo, cache,
    # attn) live here rather than as top-level fields, so the unified
    # schema doesn't grow a field per backend.
    options: dict[str, Any] = Field(default_factory=dict)


class VideoOutput(BaseModel):
    video_path: str | None = None
    video_url: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None


class GenerateResponse(BaseModel):
    backend: str
    mode: str
    status: Literal["succeeded", "failed"]
    output: VideoOutput | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Backend's original response, kept for debugging only. Not part of
    # the stable contract other backends are expected to match.
    raw_metadata: dict[str, Any] | None = None


class BackendInfo(BaseModel):
    name: str
    available: bool
    busy: bool | None = None
    capabilities: dict[str, Any]
    detail: str | None = None


class ErrorResponse(BaseModel):
    error: str
    backend: str | None = None
    detail: str | None = None
