from pydantic import Field
from mcp.server.fastmcp import FastMCP
from gcs_modules import GCSLeadsManager
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent
SERVICE_ACCOUNT_PATH = os.environ.get(
    "GCS_SERVICE_ACCOUNT_PATH",
    str(BASE_DIR / "data-platform-421407-d95c009c5786.json"),
)
if SERVICE_ACCOUNT_PATH and not Path(SERVICE_ACCOUNT_PATH).exists():
    SERVICE_ACCOUNT_PATH = None

manager = GCSLeadsManager(credentials_path=SERVICE_ACCOUNT_PATH)

mcp = FastMCP("context_centre", log_level="ERROR")


def _load_lead_file(lead_id: str, filename: str) -> str:
    return manager.fetch_file_content(lead_id, filename)


@mcp.tool(
    name="get_lead_overview",
    description="Get the main overview content for a lead from lead.md.",
)
def get_lead_overview(
    lead_id: str = Field(description="Lead ID, for example lead_8f3a92"),
) -> str:
    return _load_lead_file(lead_id, "lead.md")


@mcp.tool(
    name="get_scores",
    description="Get scoring details for the lead from scoring.md.",
)
def get_scores(
    lead_id: str = Field(description="Lead ID, for example lead_8f3a92"),
) -> str:
    return _load_lead_file(lead_id, "scoring.md")


@mcp.tool(
    name="get_conversation",
    description="Get both calls and WhatsApp conversation content for a lead.",
)
def get_conversation(
    lead_id: str = Field(description="Lead ID, for example lead_8f3a92"),
) -> str:
    calls = ""
    whatsapp = ""
    try:
        calls = _load_lead_file(lead_id, "calls_index.md")
    except FileNotFoundError:
        calls = ""
    try:
        whatsapp = _load_lead_file(lead_id, "whatsapp.md")
    except FileNotFoundError:
        whatsapp = ""

    combined = []
    if calls:
        combined.append("## Calls Conversation\n" + calls)
    if whatsapp:
        combined.append("## WhatsApp Conversation\n" + whatsapp)
    if not combined:
        raise FileNotFoundError(f"No conversation files found for lead {lead_id}")
    return "\n\n".join(combined)


@mcp.tool(
    name="get_lead_details",
    description="Get customer details for the lead from customer.md.",
)
def get_lead_details(
    lead_id: str = Field(description="Lead ID, for example lead_8f3a92"),
) -> str:
    return _load_lead_file(lead_id, "customer.md")


@mcp.tool(
    name="get_lead_complete_details",
    description="Get complete lead details from all available lead files.",
)
def get_lead_complete_details(
    lead_id: str = Field(description="Lead ID, for example lead_8f3a92"),
) -> dict:
    files = {
        "lead_overview": "lead.md",
        "scores": "scoring.md",
        "customer_details": "customer.md",
        "conversation_calls": "calls_index.md",
        "conversation_whatsapp": "whatsapp.md",
        "pre_lead_events": "events_pre_lead.md",
        "post_lead_events": "events_post_lead.md",
    }
    result = {}
    for key, filename in files.items():
        try:
            result[key] = _load_lead_file(lead_id, filename)
        except FileNotFoundError:
            result[key] = None
    return result


@mcp.tool(
    name="get_pre_lead_events",
    description="Get pre-lead event content from events_pre_lead.md.",
)
def get_pre_lead_events(
    lead_id: str = Field(description="Lead ID, for example lead_8f3a92"),
) -> str:
    return _load_lead_file(lead_id, "events_pre_lead.md")


@mcp.tool(
    name="get_post_lead_events",
    description="Get post-lead event content from events_post_lead.md.",
)
def get_post_lead_events(
    lead_id: str = Field(description="Lead ID, for example lead_8f3a92"),
) -> str:
    return _load_lead_file(lead_id, "events_post_lead.md")


@mcp.tool(
    name="get_leads_list",
    description="Get the list of all lead IDs under the GCS leads folder.",
)
def get_leads_list() -> list[str]:
    return manager.fetch_all_leads()


if __name__ == "__main__":
    mcp.run(transport="stdio")