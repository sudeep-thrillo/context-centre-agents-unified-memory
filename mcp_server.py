from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP
from firestore_modules import FirestoreLeadsManager, FilterCondition as FSFilterCondition
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
import os
import re
import time
import anyio
from logger import emit_log

class FilterCondition(BaseModel):
    field: str = Field(
        description=(
            "Snapshot field to filter on. "
            "Lead fields: current_lead_state, current_lead_stage, planned_region, lead_level, "
            "booking_time_preference, dot_preference, date_of_journey, number_of_adults, "
            "departure_city, traveller_type, special_occasion, tags_v2, utm_source, utm_medium, "
            "lead_campaign, enquiry_source, enquiry_section_source, required_services, "
            "number_of_infants, number_of_senior_citizens, ticket_status, "
            "lead_creation_time, lead_assignment_time, trip_duration_preference, budget_per_person. "
            "Score fields: booking_urgency_score, customer_travel_intent_score, "
            "competition_signals_score, customer_persona_score, seller_evaluation_score, "
            "thrillophilia_affinity_score, product_intelligence_score."
        )
    )
    op: str = Field(
        description=(
            "Filter operator. Supported: == (equality), != (not equal), "
            "> / >= / < / <= (numeric/string comparison), "
            "in (value is a list, field must match one element), "
            "not-in (field must not match any element in the list), "
            "array_contains (field is an array containing the value)."
        )
    )
    value: Any = Field(
        description=(
            "Filter value. Use a Python list for 'in' and 'not-in' operators. "
            "For scores use numeric values (e.g. 7.0). "
            "For date_of_journey use ISO date strings (e.g. '2026-07-01')."
        )
    )


BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Credentials
# Never falls back to a hardcoded filename.
# On Cloud Run: attach a service account with Firestore access (no file needed).
# Locally: set GOOGLE_APPLICATION_CREDENTIALS or GCS_SERVICE_ACCOUNT_PATH.
# ---------------------------------------------------------------------------
SERVICE_ACCOUNT_PATH = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    os.environ.get("GCS_SERVICE_ACCOUNT_PATH"),
)
if SERVICE_ACCOUNT_PATH and not Path(SERVICE_ACCOUNT_PATH).exists():
    SERVICE_ACCOUNT_PATH = None

COLLECTION_ROOT = os.getenv("FIRESTORE_DOCUMENTS_COLLECTION", "dev_documents")
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "(default)")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", os.getenv("PORT", "8000")))

# ---------------------------------------------------------------------------
# Concurrency
# The MCP SDK calls sync tool handlers directly on the event loop
# (func_metadata.py: `return fn(...)`), so a single blocking Firestore read
# would freeze the whole uvicorn worker. We offload every blocking read to a
# bounded worker-thread pool so one instance can serve many calls concurrently.
# The limiter caps concurrent threads (sized to Cloud Run containerConcurrency;
# env-overridable for load testing).
# ---------------------------------------------------------------------------
_FS_CONCURRENCY = int(os.getenv("MCP_FS_CONCURRENCY", "40"))
_FS_LIMITER = anyio.CapacityLimiter(_FS_CONCURRENCY)
# Max seconds to wait for a free worker slot before failing fast, so calls don't
# queue until the Cloud Run request timeout (300s) and produce truncated responses.
_FS_ACQUIRE_TIMEOUT = float(os.getenv("MCP_FS_ACQUIRE_TIMEOUT", "20"))
_thread_limiter_synced = False


async def _offload(fn, *args):
    """Run a blocking callable in a worker thread, bounded by _FS_LIMITER.

    Only the wait for a free slot is time-bounded: if none frees within
    _FS_ACQUIRE_TIMEOUT the call raises TimeoutError (fail fast) instead of
    hanging to the request timeout. A call that has acquired a slot runs to
    completion.
    """
    global _thread_limiter_synced
    if not _thread_limiter_synced:
        # Keep the shared worker-thread pool at least as large as our limiter so
        # acquiring an _FS_LIMITER slot is never re-throttled inside run_sync.
        anyio.to_thread.current_default_thread_limiter().total_tokens = max(
            _FS_CONCURRENCY, 40
        )
        _thread_limiter_synced = True

    with anyio.fail_after(_FS_ACQUIRE_TIMEOUT):
        await _FS_LIMITER.acquire()
    try:
        return await anyio.to_thread.run_sync(fn, *args)
    finally:
        _FS_LIMITER.release()


manager = FirestoreLeadsManager(
    credentials_path=SERVICE_ACCOUNT_PATH,
    collection_root=COLLECTION_ROOT,
    database_id=DATABASE_ID,
)

mcp = FastMCP(
    "context_centre",
    log_level=os.getenv("LOG_LEVEL", "WARNING"),
    host=MCP_HOST,
    port=MCP_PORT,
)

# Lead IDs follow the pattern ENQ<digits> but we allow any alphanumeric + hyphens/underscores
# to future-proof. The regex prevents Firestore path traversal via crafted IDs.
_LEAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


def _get_mcp_transport_settings() -> dict:
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        return {"transport": "streamable-http"}
    return {"transport": "stdio"}


def _validate_lead_id(lead_id: str) -> None:
    """Reject lead IDs that could construct malicious Firestore paths."""
    if not _LEAD_ID_RE.match(lead_id):
        raise ValueError(f"Invalid lead_id format: {lead_id!r}")


def _normalize_document_name(filename: str) -> str:
    """Map a .md filename to its Firestore document ID.

    Firestore document IDs mirror the doc_type of each document
    (e.g. manifest.md → manifest, conversations.md → conversations).
    The legacy lead.md → manifest mapping has been removed; manifest.md
    is now used directly to fetch the manifest document.
    """
    return filename.rsplit(".md", 1)[0]


def _load_lead_file(lead_id: str, filename: str) -> str:
    _validate_lead_id(lead_id)
    document_name = _normalize_document_name(filename)
    return manager.fetch_lead_document(lead_id, document_name)

_COMPLETE_KEY_TO_DOC = {
    "manifest":        "manifest",
    "lead_data":       "lead",
    "scores":          "scoring",
    "conversations":   "conversations",
    "quotations":      "quotations",
    "pre_lead_events": "events_pre_lead",
    "post_lead_events": "events_post_lead",
}

_COMPLETE_FILES = {
    "manifest": "manifest.md",
    "lead_data": "lead.md",
    "scores": "scoring.md",
    "conversations": "conversations.md",
    "quotations": "quotations.md",
    "pre_lead_events": "events_pre_lead.md",
    "post_lead_events": "events_post_lead.md",
}


def _complete_details_sync(lead_id: str) -> tuple[dict, str, list]:
    """Blocking fan-out fetch of all lead documents. Runs in a worker thread
    (via anyio.to_thread) so the event loop stays free; internally parallelises
    the individual Firestore reads. Returns (result, status, docs_found)."""
    result: dict = {}
    with ThreadPoolExecutor(max_workers=len(_COMPLETE_FILES)) as executor:
        future_to_key = {
            executor.submit(_load_lead_file, lead_id, filename): key
            for key, filename in _COMPLETE_FILES.items()
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                result[key] = future.result()
            except FileNotFoundError:
                result[key] = None

    docs_found = [_COMPLETE_KEY_TO_DOC[k] for k, v in result.items() if v is not None]
    found_count = len(docs_found)
    status = "success"
    if found_count == 0:
        status = "error"
    elif found_count < len(_COMPLETE_FILES):
        status = "partial"
    return result, status, docs_found

# ---------------------------------------------------------------------------
# Tools — ordered from broadest (start here) to most specific
# ---------------------------------------------------------------------------

@mcp.tool(
    name="get_lead_manifest",
    description=(
        "Entry point for any lead query. Returns the synthesised lead brief (manifest) containing: "
        "quick snapshot table (enquirer/contact, region and trip type, travel date and window, pax count, "
        "lead status and stage, score tier, last interaction channel and time, quote status, "
        "assigned agent/seller, open threads and follow-ups); overall lead summary narrative; "
        "current priority and score tier (Hot/Warm/Nurture/Drop when available); "
        "last interaction details (timestamp, channel, outcome); active open threads with owner; "
        "trip and quote context (destination, dates, duration, special occasion, departure city); "
        "recommended next step for the agent; and detail-doc links to deeper documents. "
        "Always call this first — read 'Recommended Next Step' to guide action and "
        "'Detail Links' to decide which deeper tool to call next "
        "(get_lead_data, get_lead_scores, get_conversations, get_quotation_information). "
        "The embedded confidence level matters: Low or Medium means cross-check key facts "
        "with the raw-data tools before taking any action."
    ),
)
async def get_lead_manifest(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    t0 = time.monotonic()
    status, error_type, error_message, docs_found = "success", None, None, []
    try:
        result = await _offload(_load_lead_file, lead_id, "manifest.md")
        docs_found = ["manifest"]
        return result
    except FileNotFoundError as e:
        status, error_type, error_message = "error", "FileNotFoundError", str(e)
        raise
    except Exception as e:
        status, error_type, error_message = "error", type(e).__name__, str(e)
        raise
    finally:
        emit_log("get_lead_manifest", int((time.monotonic() - t0) * 1000),
                 status, lead_id=lead_id, docs_found=docs_found,
                 error_type=error_type, error_message=error_message)


@mcp.tool(
    name="get_lead_data",
    description=(
        "Returns the raw lead record with: lifecycle and status (state, stage, lead level, creation time); "
        "attribution data (page URL, UTM source/medium/campaign, enquiry section, lead campaign); "
        "trip requirements (planned region, travel date, duration preference, special occasion, "
        "budget per person, departure city, required services such as flights or hotels); "
        "traveller/pax breakdown (adults, infants, senior citizens); "
        "preferences and intent inputs (date-of-travel preference, booking urgency); "
        "and an intent signal evaluation section listing positive/activating signals "
        "versus friction/uncertainty signals with an overall read. "
        "Use when you need raw lead facts — exact UTM attribution, full pax breakdown, "
        "or the structured intent signal analysis — rather than the synthesised view in get_lead_manifest."
    ),
)
async def get_lead_data(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    t0 = time.monotonic()
    status, error_type, error_message, docs_found = "success", None, None, []
    try:
        result = await _offload(_load_lead_file, lead_id, "lead.md")
        docs_found = ["lead"]
        return result
    except FileNotFoundError as e:
        status, error_type, error_message = "error", "FileNotFoundError", str(e)
        raise
    except Exception as e:
        status, error_type, error_message = "error", type(e).__name__, str(e)
        raise
    finally:
        emit_log("get_lead_data", int((time.monotonic() - t0) * 1000),
                 status, lead_id=lead_id, docs_found=docs_found,
                 error_type=error_type, error_message=error_message)


@mcp.tool(
    name="get_lead_scores",
    description=(
        "Returns the full scoring breakdown across 7 dimensions, each scored 0–10: "
        "Customer Travel Intent, Booking Urgency, Competition Signals, Customer Persona, "
        "Seller Evaluation, Thrillophilia Affinity, and Product Intelligence. "
        "Also includes: per-dimension rationale explaining each score; "
        "a scoring journey narrative (first snapshot vs latest delta); "
        "and an Agent Notes section with specific, actionable recommendations on "
        "tone, approach, what to emphasise, and what to avoid. "
        "Composite score and official action tier (Hot/Warm/Nurture/Drop) may be absent "
        "for newly created leads. "
        "Always read Agent Notes before any sales or follow-up action — "
        "it contains calibrated guidance derived directly from the scoring rationale."
    ),
)
async def get_lead_scores(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    t0 = time.monotonic()
    status, error_type, error_message, docs_found = "success", None, None, []
    try:
        result = await _offload(_load_lead_file, lead_id, "scoring.md")
        docs_found = ["scoring"]
        return result
    except FileNotFoundError as e:
        status, error_type, error_message = "error", "FileNotFoundError", str(e)
        raise
    except Exception as e:
        status, error_type, error_message = "error", type(e).__name__, str(e)
        raise
    finally:
        emit_log("get_lead_scores", int((time.monotonic() - t0) * 1000),
                 status, lead_id=lead_id, docs_found=docs_found,
                 composite_score_available=bool(docs_found),
                 error_type=error_type, error_message=error_message)


@mcp.tool(
    name="get_conversations",
    description=(
        "Returns the unified conversation summary combining all calls and WhatsApp messages: "
        "snapshot table (total messages, lead/agent reply counts, first/last message timestamps, "
        "total call duration, agent and seller emails, awaiting-response-from field); "
        "latest interaction summary; active open threads with owner and basis; "
        "full conversation journey narrative (what was discussed, what was offered, objections raised, "
        "outcomes); message summary (lead vs agent counts, key customer asks, latest message summary); "
        "call summary (count, total and answered duration, agent email, overall and latest call outcomes); "
        "recent message excerpts table; and recommended next step. "
        "Call this before any customer contact to understand prior discussions, "
        "commitments made, objections raised, and whose turn it is to respond."
    ),
)
async def get_conversations(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    t0 = time.monotonic()
    status, error_type, error_message, docs_found = "success", None, None, []
    try:
        result = await _offload(_load_lead_file, lead_id, "conversations.md")
        docs_found = ["conversations"]
        return result
    except FileNotFoundError as e:
        status, error_type, error_message = "error", "FileNotFoundError", str(e)
        raise
    except Exception as e:
        status, error_type, error_message = "error", type(e).__name__, str(e)
        raise
    finally:
        emit_log("get_conversations", int((time.monotonic() - t0) * 1000),
                 status, lead_id=lead_id, docs_found=docs_found,
                 error_type=error_type, error_message=error_message)


@mcp.tool(
    name="get_quotation_information",
    description=(
        "Returns the full quotation record for a lead, containing: "
        "quote snapshot table (quotation ref, quote status such as booked/sent/pending, created date, "
        "region/destination, travel dates, trip duration, pax breakdown, total quote value); "
        "trip overview narrative; pricing summary with line-item breakdown by cost category "
        "(e.g. DMC Group, Activity) showing group cost and booked service amount in INR; "
        "day-by-day itinerary table (main plan, transfers, key stops per day); "
        "hotel list per day and location; inclusions and exclusions lists; "
        "cancellation policy; pending revision or quote activity; "
        "and agent notes flagging any data gaps (e.g. missing final total, missing cancellation policy). "
        "Use when you need the quote status, proposed itinerary, pricing breakdown, "
        "hotel details, or inclusions/exclusions before discussing the package with a customer. "
        "Always check Agent Notes — they flag missing totals or policy gaps that must be resolved "
        "before making any payment or cancellation commitments. "
        "A high-level quote status is also visible in get_lead_manifest when this document exists."
    ),
)
async def get_quotation_information(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    t0 = time.monotonic()
    status, error_type, error_message, docs_found = "success", None, None, []
    try:
        result = await _offload(_load_lead_file, lead_id, "quotations.md")
        docs_found = ["quotations"]
        return result
    except FileNotFoundError as e:
        status, error_type, error_message = "error", "FileNotFoundError", str(e)
        raise
    except Exception as e:
        status, error_type, error_message = "error", type(e).__name__, str(e)
        raise
    finally:
        emit_log("get_quotation_information", int((time.monotonic() - t0) * 1000),
                 status, lead_id=lead_id, docs_found=docs_found,
                 error_type=error_type, error_message=error_message)


@mcp.tool(
    name="get_pre_lead_events",
    description=(
        "Returns the pre-lead event timeline — customer activity recorded before the lead was formally "
        "created: page views, product card interactions, search queries, session data, and the specific "
        "trigger event that generated the enquiry. "
        "Use to understand the customer's initial browsing intent, which destinations or products "
        "they explored, and what prompted them to submit the enquiry."
    ),
)
async def get_pre_lead_events(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    t0 = time.monotonic()
    status, error_type, error_message, docs_found = "success", None, None, []
    try:
        result = await _offload(_load_lead_file, lead_id, "events_pre_lead.md")
        docs_found = ["events_pre_lead"]
        return result
    except FileNotFoundError as e:
        status, error_type, error_message = "error", "FileNotFoundError", str(e)
        raise
    except Exception as e:
        status, error_type, error_message = "error", type(e).__name__, str(e)
        raise
    finally:
        emit_log("get_pre_lead_events", int((time.monotonic() - t0) * 1000),
                 status, lead_id=lead_id, docs_found=docs_found,
                 error_type=error_type, error_message=error_message)


@mcp.tool(
    name="get_post_lead_events",
    description=(
        "Returns the post-lead event timeline — all system and agent events recorded after the lead "
        "was created: status changes, re-assignments, escalations, follow-up attempts, call "
        "dispositions, and pipeline stage transitions. "
        "Use to trace the full lifecycle of a lead, identify gaps in follow-up cadence, "
        "or understand how and when the lead progressed through the sales pipeline."
    ),
)
async def get_post_lead_events(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    t0 = time.monotonic()
    status, error_type, error_message, docs_found = "success", None, None, []
    try:
        result = await _offload(_load_lead_file, lead_id, "events_post_lead.md")
        docs_found = ["events_post_lead"]
        return result
    except FileNotFoundError as e:
        status, error_type, error_message = "error", "FileNotFoundError", str(e)
        raise
    except Exception as e:
        status, error_type, error_message = "error", type(e).__name__, str(e)
        raise
    finally:
        emit_log("get_post_lead_events", int((time.monotonic() - t0) * 1000),
                 status, lead_id=lead_id, docs_found=docs_found,
                 error_type=error_type, error_message=error_message)


@mcp.tool(
    name="get_lead_complete_details",
    description=(
        "Fetches ALL lead documents concurrently and returns them as a structured dict with keys: "
        "manifest, lead_data, scores, conversations, quotations, "
        "pre_lead_events, post_lead_events. "
        "Keys whose Firestore document does not yet exist for this lead are null. "
        "Use only when a full multi-dimensional view is needed and latency is acceptable "
        "(makes up to 8 Firestore reads in parallel). "
        "For targeted single-dimension queries, use the individual tools — they are faster and cheaper."
    ),
)
async def get_lead_complete_details(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> dict:
    t0 = time.monotonic()
    try:
        result, status, docs_found = await _offload(_complete_details_sync, lead_id)
        emit_log(
            "get_lead_complete_details",
            int((time.monotonic() - t0) * 1000),
            status,
            lead_id=lead_id,
            docs_found=docs_found,
        )
        return result
    except Exception as e:
        emit_log(
            "get_lead_complete_details",
            int((time.monotonic() - t0) * 1000),
            "error",
            lead_id=lead_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        raise


@mcp.tool(
    name="get_leads_list",
    description=(
        "Discover leads with optional Firestore-level filtering. Returns each matched lead with "
        "lead_snapshot (all raw lead fields) and score_snapshot (all 7 scoring dimensions) "
        "so you can triage without making additional tool calls. "
        "Default limit is 10 leads. "
        "Filters are applied at the database level — lead fields and score fields are each "
        "queried in their respective Firestore documents and the results intersected. "
        "Example queries: "
        "• High-urgency open leads: filters=[{field:'booking_urgency_score',op:'>=',value:7},{field:'current_lead_state',op:'==',value:'open'}] "
        "• High-intent Europe leads: filters=[{field:'customer_travel_intent_score',op:'>=',value:8},{field:'planned_region',op:'==',value:'Europe Tours'}] "
        "• High-value tag: filters=[{field:'tags_v2',op:'array_contains',value:'high_value'}] "
        "• Multiple regions: filters=[{field:'planned_region',op:'in',value:['Ladakh Tours','Europe Tours']}] "
        "Use get_lead_manifest / get_lead_data / get_lead_scores for deeper per-lead detail."
    ),
)
async def get_leads_list(
    filters: Optional[list[FilterCondition]] = Field(
        default=None,
        description="Optional list of filter conditions applied at the Firestore level.",
    ),
    limit: int = Field(
        default=10,
        description="Maximum number of leads to return. Defaults to 10, max 100.",
        ge=1,
        le=100,
    ),
) -> list[dict]:
    t0 = time.monotonic()
    status, error_type, error_message = "success", None, None
    try:
        fs_filters = None
        if filters:
            fs_filters = [FSFilterCondition(field=f.field, op=f.op, value=f.value) for f in filters]
        result = await _offload(
            lambda: manager.fetch_leads_with_meta(filters=fs_filters, limit=limit)
        )
        return result
    except Exception as e:
        status, error_type, error_message = "error", type(e).__name__, str(e)
        raise
    finally:
        emit_log("get_leads_list", int((time.monotonic() - t0) * 1000),
                 status, error_type=error_type, error_message=error_message)


if __name__ == "__main__":
    mcp.run(**_get_mcp_transport_settings())
