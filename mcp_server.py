from pydantic import Field
from mcp.server.fastmcp import FastMCP
from firestore_modules import FirestoreLeadsManager
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re

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
def get_lead_manifest(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    return _load_lead_file(lead_id, "manifest.md")


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
def get_lead_data(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    return _load_lead_file(lead_id, "lead.md")


@mcp.tool(
    name="get_customer_details",
    description=(
        "Returns the customer/enquirer profile record, including contact identifiers and personal "
        "details that are stored separately from the trip and booking data. "
        "Use when you need customer profile fields that are not covered by the lead record. "
        "If this document is unavailable for a lead, contact details can be found in "
        "get_lead_manifest (Quick Snapshot table) or get_lead_data."
    ),
)
def get_customer_details(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    return _load_lead_file(lead_id, "customer.md")


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
def get_lead_scores(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    return _load_lead_file(lead_id, "scoring.md")


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
def get_conversations(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    return _load_lead_file(lead_id, "conversations.md")


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
def get_quotation_information(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    return _load_lead_file(lead_id, "quotations.md")


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
def get_pre_lead_events(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    return _load_lead_file(lead_id, "events_pre_lead.md")


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
def get_post_lead_events(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> str:
    return _load_lead_file(lead_id, "events_post_lead.md")


@mcp.tool(
    name="get_lead_complete_details",
    description=(
        "Fetches ALL lead documents concurrently and returns them as a structured dict with keys: "
        "manifest, lead_data, customer_details, scores, conversations, quotations, "
        "pre_lead_events, post_lead_events. "
        "Keys whose Firestore document does not yet exist for this lead are null. "
        "Use only when a full multi-dimensional view is needed and latency is acceptable "
        "(makes up to 8 Firestore reads in parallel). "
        "For targeted single-dimension queries, use the individual tools — they are faster and cheaper."
    ),
)
def get_lead_complete_details(
    lead_id: str = Field(description="Lead ID, for example ENQ1133874342"),
) -> dict:
    files = {
        "manifest": "manifest.md",
        "lead_data": "lead.md",
        "customer_details": "customer.md",
        "scores": "scoring.md",
        "conversations": "conversations.md",
        "quotations": "quotations.md",
        "pre_lead_events": "events_pre_lead.md",
        "post_lead_events": "events_post_lead.md",
    }
    result: dict = {}
    with ThreadPoolExecutor(max_workers=len(files)) as executor:
        future_to_key = {
            executor.submit(_load_lead_file, lead_id, filename): key
            for key, filename in files.items()
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                result[key] = future.result()
            except FileNotFoundError:
                result[key] = None
    return result


@mcp.tool(
    name="get_leads_list",
    description=(
        "Returns a sorted list of all lead IDs in the active Firestore collection "
        "(e.g. ['ENQ1133874342', 'ENQ5466305225', ...]). "
        "Use only for lead discovery when you do not already have a lead_id from context. "
        "Note: streams all document references — may be slow on very large collections."
    ),
)
def get_leads_list() -> list[str]:
    return manager.fetch_all_leads()


if __name__ == "__main__":
    mcp.run(**_get_mcp_transport_settings())
