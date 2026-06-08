#!/usr/bin/env python3
"""Fetch up to 10 leads and their `conversations` document for quick testing."""
import os
import json
from firestore_modules import FirestoreLeadsManager

COLLECTION_ROOT = os.getenv("FIRESTORE_DOCUMENTS_COLLECTION", "dev_documents")
DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", None)
CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", None)

manager = FirestoreLeadsManager(credentials_path=CREDENTIALS, collection_root=COLLECTION_ROOT, database_id=DATABASE_ID)

output = {"collection_root": COLLECTION_ROOT, "database_id": DATABASE_ID, "results": []}

try:
    leads = manager.fetch_leads_with_meta(limit=10)
except Exception as e:
    print(json.dumps({"error": str(e)}))
    raise

for entry in leads:
    lead_id = entry.get("lead_id")
    row = {"lead_id": lead_id}
    try:
        conv = manager.fetch_lead_document(lead_id, "conversations")
        # cap snippet to 1000 chars
        row["conversations_snippet"] = conv[:1000]
        row["exists"] = True
    except FileNotFoundError:
        row["conversations_snippet"] = None
        row["exists"] = False
    except Exception as e:
        row["error"] = str(e)
    output["results"].append(row)

print(json.dumps(output, indent=2))
