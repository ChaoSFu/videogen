"""Unit tests for the JSONL-backed history store (no HTTP, no FastAPI)."""

from __future__ import annotations

from videogen.history import HistoryStore, _summarize_request
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


# --- request_summary (for "复用"/reuse) ------------------------------------


def test_request_summary_captures_reusable_fields():
    request = make_request(prompt="a cat", seed=42, options={"num_inference_steps": 25, "turbo": True})
    summary = _summarize_request(request)

    assert summary.mode == "t2va"
    assert summary.prompt == "a cat"
    assert summary.seed == 42
    assert summary.options == {"num_inference_steps": 25, "turbo": True}
    assert summary.had_media_inputs is False


def test_request_summary_strips_media_payloads_but_flags_them():
    request = make_request(
        mode="fl2va",
        options={"first_frame": "data:image/png;base64,AAAA", "num_inference_steps": 30},
    )
    summary = _summarize_request(request)

    assert "first_frame" not in summary.options
    assert summary.options == {"num_inference_steps": 30}
    assert summary.had_media_inputs is True


def test_history_entry_includes_request_summary(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.record_success(make_request(prompt="hello", seed=7), make_response())

    entry = store.list_recent()[0]
    assert entry.request_summary is not None
    assert entry.request_summary.prompt == "hello"
    assert entry.request_summary.seed == 7


# --- delete -----------------------------------------------------------------


def test_delete_removes_entry_and_returns_it(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.record_success(make_request(prompt="keep me"), make_response())
    store.record_success(make_request(prompt="delete me"), make_response())

    target = next(e for e in store.list_recent() if e.prompt == "delete me")
    deleted = store.delete(target.id)

    assert deleted is not None
    assert deleted.prompt == "delete me"
    remaining = store.list_recent()
    assert [e.prompt for e in remaining] == ["keep me"]


def test_delete_unknown_id_returns_none(tmp_path):
    store = HistoryStore(tmp_path / "history.jsonl")
    store.record_success(make_request(), make_response())
    assert store.delete("not-a-real-id") is None
    assert len(store.list_recent()) == 1


def test_delete_persists_across_store_instances(tmp_path):
    path = tmp_path / "history.jsonl"
    store = HistoryStore(path)
    store.record_success(make_request(prompt="a"), make_response())
    entry_b = store.record_success(make_request(prompt="b"), make_response())

    HistoryStore(path).delete(entry_b.id)

    reloaded = HistoryStore(path).list_recent()
    assert [e.prompt for e in reloaded] == ["a"]
