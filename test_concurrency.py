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
