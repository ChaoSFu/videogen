"""Unit tests for BackendCoordinator — no HTTP, no FastAPI, pure asyncio."""

from __future__ import annotations

import asyncio

from videogen.coordinator import BackendCoordinator


async def test_single_call_runs_immediately():
    coord = BackendCoordinator()
    assert coord.current is None
    assert coord.queue_depth == 0

    async def job():
        assert coord.current is not None
        assert coord.current.prompt == "hello"
        return "done"

    result = await coord.run("b", "t2va", "hello", job)
    assert result == "done"
    assert coord.current is None
    assert coord.queue_depth == 0


async def test_concurrent_calls_are_serialized_not_rejected():
    coord = BackendCoordinator()
    order: list[str] = []

    async def slow_job(name: str):
        order.append(f"start:{name}")
        await asyncio.sleep(0.05)
        order.append(f"end:{name}")
        return name

    results = await asyncio.gather(
        coord.run("b", "t2va", "first", lambda: slow_job("first")),
        coord.run("b", "t2va", "second", lambda: slow_job("second")),
    )

    assert set(results) == {"first", "second"}
    # Second job must not start until the first one has fully finished —
    # this is the whole point: no two H3 calls in flight at once.
    assert order == ["start:first", "end:first", "start:second", "end:second"]


async def test_queue_depth_reflects_waiters():
    coord = BackendCoordinator()
    release = asyncio.Event()

    async def blocking_job():
        await release.wait()
        return "ok"

    first = asyncio.create_task(coord.run("b", "t2va", "p1", blocking_job))
    await asyncio.sleep(0.01)  # let `first` acquire the lock and start running
    assert coord.current is not None
    assert coord.queue_depth == 0

    async def instant_job():
        return "ok"

    second = asyncio.create_task(coord.run("b", "t2va", "p2", instant_job))
    await asyncio.sleep(0.01)  # let `second` start waiting on the lock
    assert coord.queue_depth == 1

    release.set()
    await first
    await second
    assert coord.queue_depth == 0
    assert coord.current is None


async def test_current_job_current_reflects_active_prompt():
    coord = BackendCoordinator()
    started = asyncio.Event()
    release = asyncio.Event()

    async def job():
        started.set()
        await release.wait()

    task = asyncio.create_task(coord.run("minimax-h3", "t2va", "a cat in the rain", job))
    await started.wait()

    assert coord.current is not None
    assert coord.current.backend == "minimax-h3"
    assert coord.current.prompt == "a cat in the rain"
    snapshot = coord.current.to_dict()
    assert snapshot["prompt"] == "a cat in the rain"
    assert snapshot["elapsed_s"] >= 0

    release.set()
    await task
    assert coord.current is None
