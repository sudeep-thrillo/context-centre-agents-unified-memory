import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

# Default caller attribution for request logs. Set MCP_DEFAULT_CALLER on the
# deployment (e.g. "sales-copilot") so load can be attributed per consumer.
_DEFAULT_CALLER = os.getenv("MCP_DEFAULT_CALLER", "unknown")

# Canonical Firestore document names (not result-key aliases)
TOOL_DOCS_MAP = {
    "get_lead_manifest":        ["manifest"],
    "get_lead_data":            ["lead"],
    "get_lead_scores":          ["scoring"],
    "get_conversations":        ["conversations"],
    "get_quotation_information":["quotations"],
    "get_pre_lead_events":      ["events_pre_lead"],
    "get_post_lead_events":     ["events_post_lead"],
    "get_lead_complete_details":["manifest", "lead", "scoring", "conversations",
                                 "quotations", "events_pre_lead", "events_post_lead"],
    "get_leads_list":           [],
}

def emit_log(
    tool_name: str,
    latency_ms: int,
    status: str,
    lead_id: Optional[str] = None,
    caller_source: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    docs_found: Optional[list] = None,
    composite_score_available: Optional[bool] = None,
    region: Optional[str] = None,
) -> None:
    docs_requested = TOOL_DOCS_MAP.get(tool_name, [])
    docs_found = docs_found or []
    docs_missing = [d for d in docs_requested if d not in docs_found]
    caller_source = caller_source or _DEFAULT_CALLER

    record = {
        "log_type":                   "cc_request_log",
        "request_id":                 str(uuid.uuid4()),
        "timestamp":                  datetime.now(timezone.utc).isoformat(),
        "date":                       datetime.now(timezone.utc).date().isoformat(),
        "tool_name":                  tool_name,
        "lead_id":                    lead_id,
        "caller_source":              caller_source,
        "latency_ms":                 latency_ms,
        "status":                     status,
        "error_type":                 error_type,
        "error_message":              error_message,
        "docs_requested":             docs_requested,
        "docs_found":                 docs_found,
        "docs_missing":               docs_missing,
        "composite_score_available":  composite_score_available,
        "region":                     region,
    }
    print(json.dumps(record), flush=True)
