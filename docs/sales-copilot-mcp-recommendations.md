# Sales-Copilot ↔ Context-Center MCP: client-side recommendations

**Audience:** sales-copilot maintainers.
**Status:** advisory. These are consumer-side changes to pair with the MCP server
scaling fix; none of them live in the `context-centre-agents-unified-memory` repo.

## Why this exists

On **2026-07-17 04:00–06:00 UTC** a bulk `refresh:*` fan-out from sales-copilot
overwhelmed the context-center MCP:

- MCP returned **1000+ `The request was aborted because there was no available
  instance` (503)** in one hour; SSE streams were truncated (725+); the MCP was
  hard-capped at **5 instances** by a stale service-level annotation.
- sales-copilot logged **`pipeline failed` 485 + 515** in the same two hours
  (~85% failure over the surrounding 2 days).

The MCP itself is fast (Firestore reads 10–38 ms). The failure was capacity +
call-pattern, not query speed.

## What changed on the MCP server (already done)

- Tool handlers no longer block the event loop — blocking Firestore reads are
  offloaded to a bounded worker-thread pool, so one instance serves many calls
  concurrently (was effectively serialized before).
- Over-capacity calls **fail fast** with a `TimeoutError` (~20 s wait budget)
  instead of hanging to the 300 s request timeout.
- Cloud Run: `min-instances=1` (warm pool), `max-instances=40` (was capped at 5),
  request concurrency 40, **session affinity enabled**, memory 1 GiB.

Session affinity only helps if the client **reuses one connection per MCP
session** — which drives the first recommendation below.

## Recommendations for sales-copilot

### 1. Reuse one MCP session per worker (highest impact)
Open a single long-lived MCP `ClientSession` per worker/process and reuse it
across events. Do **not** `initialize` a fresh session per event.
- Each new streamable-HTTP session opens a server→client SSE `GET /mcp` that
  holds a Cloud Run concurrency slot for its lifetime; per-event sessions
  multiply slot usage and defeat session affinity.
- Reusing one httpx-backed session means the affinity cookie is carried, so
  follow-up calls return to the instance that owns the session (no 404s).

### 2. Bound and stagger the refresh fan-out
- Cap in-flight MCP calls with the existing `MAX_CONCURRENCY` env; keep it at or
  below the MCP's total capacity (≈ `max-instances × concurrency = 40 × 40`, but
  start conservative — a few hundred in-flight, not thousands).
- Stagger the scheduled `REFRESH_TIMES` batch (spread lead refreshes over time
  rather than firing them all at once) and add **backoff + jitter** between
  chunks so the batch can't stampede the instance ceiling.

### 3. Retry policy
- On **503 / "no available instance"**: exponential backoff with jitter, then
  retry (the autoscaler needs a moment to add instances).
- On **404** (session not found — the instance holding the session went away):
  re-`initialize` the session, then retry the call.
- On the MCP's new fast **`TimeoutError`** (server saturated): treat like 503 —
  back off and retry.

### 4. Prefer targeted tools over the full fetch
`get_lead_complete_details` makes up to **7 Firestore reads per call**. When the
pipeline only needs a subset (e.g. just the manifest, or just scores), call the
single-doc tools (`get_lead_manifest`, `get_lead_scores`, …). Reserve
`get_lead_complete_details` for when a full multi-dimensional view is genuinely
needed.

### 5. Auth heads-up (not yet enabled)
The MCP will require a bearer token on the HTTP transport. sales-copilot
currently sends **no** `Authorization` header (it only has `CONTEXT_CENTER_MCP_URL`).
Before auth is switched on server-side, sales-copilot must send
`Authorization: Bearer <token>` (store the token as a secret env, not in code).
This is tracked separately from the scaling work — coordinate the rollout so the
token is in place before the gate is enabled, or every call will 401.

## Firestore indexes (prevents a latency/OOM landmine)

`get_leads_list` filtering tries Firestore collection-group queries first and
falls back to a **whole-collection client-side scan** (`list_documents()` over
the entire `prod` collection) when a required composite index is missing. That
fallback is cheap on a small dev set but a latency/memory landmine on prod under
load.

Action: for each filter combination sales-copilot actually uses, ensure a
collection-group index exists on
`event_documents.latest_event.snapshot.<field>` (both the `lead`-doc fields and
the `scoring`-doc fields). Trigger each common filter once and watch the MCP logs
for `Firestore index missing … falling back to client-side filtering`; create any
index that shows up (the log/console provides a direct creation link). Steady
state should never hit the fallback path.

## Residual risk

Keeping the stateful streamable-HTTP transport means correctness under scale-out
depends on session affinity holding for the MCP client. If, after session reuse
+ affinity, 404s still appear across instances under load, the durable fix is to
switch the MCP to **stateless HTTP** (`stateless_http=True`, `json_response=True`) —
deferred for now by decision.
