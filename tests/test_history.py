"""Unit tests for the JSONL-backed history store (no HTTP, no FastAPI)."""

from __future__ import annotations

from videogen.history import HistoryStore
from videogen.schemas import GenerateRequest, GenerateResponse, VideoOutput


def make_request(**overrides) -> GenerateRequest:
    defaults = {
        "backend": "minimax-h3",
        "mode": "t2va",
        "prompt": "a test prompt",
        "duration": 5.0,
        "width": 768,
        "height": 768,
    }
    defaults.update(overrides)
    return GenerateRequest(**defaults)


def make_response() -> GenerateResponse:
    return GenerateResponse(
        backend="minimax-h3",
        mode="t2va",
        status="succeeded",
        output=VideoOutput(video_path="/tmp/x.mp4", video_url="http://x/x.mp4", duration=5.0, width=768, height=768),
        metadata={"seed": 1},
    )


def test_empty_store_returns_no_entries(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    assert store.list_recent() == []


def test_record_success_round_trips(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.record_success(make_request(prompt="hello"), make_response())

    entries = store.list_recent()
    assert len(entries) == 1
    assert entries[0].status == "succeeded"
    assert entries[0].prompt == "hello"
    assert entries[0].video_url == "http://x/x.mp4"


def test_record_failure_has_no_video_fields(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.record_failure(make_request(), "boom")

    entries = store.list_recent()
    assert entries[0].status == "failed"
    assert entries[0].video_url is None
    assert entries[0].error == "boom"


def test_survives_a_corrupt_line(tmp_path):
    path = tmp_path / "history.jsonl"
    store = HistoryStore(path)
    store.record_success(make_request(prompt="good"), make_response())
    with path.open("a") as f:
        f.write("not json at all\n")
    store.record_success(make_request(prompt="also good"), make_response())

    entries = store.list_recent()
    assert [e.prompt for e in entries] == ["also good", "good"]


def test_persists_across_store_instances(tmp_path):
    path = tmp_path / "history.jsonl"
    HistoryStore(path).record_success(make_request(prompt="first run"), make_response())
    entries = HistoryStore(path).list_recent()
    assert entries[0].prompt == "first run"
