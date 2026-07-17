"""Concurrency tests for the MCP tool handlers.

These verify that blocking Firestore reads are offloaded to worker threads so a
single uvicorn worker's event loop is never blocked by one slow call. All
Firestore I/O is monkeypatched — no credentials or live database required.

Run with:  .venv/bin/python -m pytest test_concurrency.py -v
"""

import asyncio
import time

import anyio
import pytest

import mcp_server


def test_handlers_do_not_block_event_loop(monkeypatch):
    """Two concurrent handler calls must overlap (offloaded to threads),
    not serialize on the event loop."""

    def slow_load(lead_id, filename):
        time.sleep(0.5)  # simulate a blocking Firestore read
        return "ok"

    monkeypatch.setattr(mcp_server, "_load_lead_file", slow_load)

    async def scenario():
        return await asyncio.gather(
            mcp_server.get_lead_manifest("ENQ1"),
            mcp_server.get_lead_manifest("ENQ2"),
        )

    t0 = time.monotonic()
    results = asyncio.run(scenario())
    elapsed = time.monotonic() - t0

    assert results == ["ok", "ok"]
    # If offloaded to threads the two 0.5s reads overlap -> < 0.9s.
    # If run on the loop they serialize -> ~1.0s+.
    assert elapsed < 0.9, f"handlers serialized on the event loop (elapsed={elapsed:.2f}s)"


def test_overload_fails_fast(monkeypatch):
    """When every worker slot is taken, a new call fails fast with TimeoutError
    instead of queueing until the Cloud Run request timeout (300s)."""

    monkeypatch.setattr(mcp_server, "_FS_LIMITER", anyio.CapacityLimiter(1))
    monkeypatch.setattr(mcp_server, "_FS_ACQUIRE_TIMEOUT", 0.05)

    def slow_load(lead_id, filename):
        time.sleep(0.3)  # holds the only worker slot
        return "ok"

    monkeypatch.setattr(mcp_server, "_load_lead_file", slow_load)

    async def scenario():
        async with anyio.create_task_group() as tg:
            # First call grabs the single slot and holds it for 0.3s.
            tg.start_soon(mcp_server.get_lead_manifest, "ENQ1")
            await anyio.sleep(0.05)  # let ENQ1 acquire the slot first
            # Second call can't get a slot within 0.05s -> fails fast.
            # Awaited directly (not via the task group) so TimeoutError is not
            # wrapped in an ExceptionGroup.
            with pytest.raises(TimeoutError):
                await mcp_server.get_lead_manifest("ENQ2")

    asyncio.run(scenario())
