"""Firestore module for managing leads and markdown documents."""

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import itertools
import os


# Fields that live in the 'lead' event document under latest_event.snapshot
LEAD_DOC_FIELDS: Set[str] = {
    "current_lead_state", "current_lead_stage", "planned_region",
    "lead_level", "booking_time_preference", "dot_preference",
    "date_of_journey", "number_of_adults", "departure_city",
    "traveller_type", "special_occasion", "tags_v2",
    "utm_source", "utm_medium", "lead_campaign",
    "enquiry_source", "enquiry_section_source", "required_services",
    "number_of_infants", "number_of_senior_citizens", "ticket_status",
    "lead_creation_time", "lead_assignment_time",
    "trip_duration_preference", "budget_per_person",
}

# Fields that live in the 'scoring' event document under latest_event.snapshot
SCORE_DOC_FIELDS: Set[str] = {
    "booking_urgency_score",
    "customer_travel_intent_score",
    "competition_signals_score",
    "customer_persona_score",
    "seller_evaluation_score",
    "thrillophilia_affinity_score",
    "product_intelligence_score",
}


@dataclass
class FilterCondition:
    field: str
    op: str  # ==, !=, >, >=, <, <=, in, not-in, array_contains
    value: Any


class FirestoreLeadsManager:
    """Firestore manager for lead documents stored as markdown content."""

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        collection_root: str = "dev_documents",
        database_id: Optional[str] = None,
    ):
        """Initialize the Firestore client.

        Args:
            credentials_path: Optional path to a service account JSON file.
            collection_root: Top-level Firestore collection root for lead documents.
            database_id: Firestore database ID, defaults to '(default)'.
        """
        if credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

        self.client = firestore.Client(database=database_id)
        self.collection_root = collection_root

    def _lead_collection(self):
        return self.client.collection(self.collection_root)

    def fetch_all_leads(self) -> List[str]:
        """Fetch all lead document IDs from the top-level collection."""
        docs = self._lead_collection().list_documents()
        return sorted(doc.id for doc in docs)

    def fetch_lead_document(self, lead_id: str, document_name: str) -> str:
        """Fetch markdown content for a specific lead document.

        Args:
            lead_id: Lead document ID.
            document_name: Document name in the `event_documents` subcollection.

        Returns:
            The markdown content string.

        Raises:
            FileNotFoundError: If the document does not exist or contains no markdown.
        """
        doc_ref = self._lead_collection().document(lead_id).collection("event_documents").document(document_name)
        doc_snapshot = doc_ref.get()
        if not doc_snapshot.exists:
            raise FileNotFoundError(f"Document not found: {lead_id}/{document_name}")

        data = doc_snapshot.to_dict() or {}
        markdown = data.get("markdown_content")
        if markdown is None:
            raise FileNotFoundError(
                f"Markdown content not found for document: {lead_id}/{document_name}"
            )

        return markdown

    def _get_doc_snapshot(self, lead_id: str, doc_name: str) -> Optional[Dict]:
        """Fetch latest_event.snapshot dict from a single event_documents entry."""
        doc_ref = (
            self._lead_collection()
            .document(lead_id)
            .collection("event_documents")
            .document(doc_name)
        )
        snap = doc_ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        return (data.get("latest_event") or {}).get("snapshot")

    def _enrich_leads_with_meta(
        self,
        lead_ids: List[str],
        cached_snapshots: Optional[Dict[str, Dict]] = None,
    ) -> List[Dict]:
        """Parallel-fetch lead + scoring snapshots for each lead_id.

        cached_snapshots may already contain partial data from collection_group
        queries so we avoid redundant reads.
        """
        if cached_snapshots is None:
            cached_snapshots = {}

        to_fetch: List[tuple] = []
        for lid in lead_ids:
            cached = cached_snapshots.get(lid, {})
            if "lead" not in cached:
                to_fetch.append((lid, "lead"))
            if "scoring" not in cached:
                to_fetch.append((lid, "scoring"))

        if to_fetch:
            with ThreadPoolExecutor(max_workers=min(len(to_fetch), 20)) as executor:
                future_map = {
                    executor.submit(self._get_doc_snapshot, lid, doc_name): (lid, doc_name)
                    for lid, doc_name in to_fetch
                }
                for future in as_completed(future_map):
                    lid, doc_name = future_map[future]
                    cached_snapshots.setdefault(lid, {})[doc_name] = future.result()

        return [
            {
                "lead_id": lid,
                "lead_snapshot": (cached_snapshots.get(lid) or {}).get("lead"),
                "score_snapshot": (cached_snapshots.get(lid) or {}).get("scoring"),
            }
            for lid in lead_ids
        ]

    def _run_collection_group_query(
        self, filters: List[FilterCondition]
    ) -> Dict[str, Optional[Dict]]:
        """Run a collection_group('event_documents') query and return {lead_id: snapshot}."""
        query = self.client.collection_group("event_documents")
        for f in filters:
            query = query.where(
                filter=FieldFilter(f"latest_event.snapshot.{f.field}", f.op, f.value)
            )

        results: Dict[str, Optional[Dict]] = {}
        for doc in query.stream():
            if not doc.reference.path.startswith(f"{self.collection_root}/"):
                continue
            lead_id = doc.reference.parent.parent.id
            data = doc.to_dict() or {}
            snapshot = (data.get("latest_event") or {}).get("snapshot")
            results[lead_id] = snapshot
        return results

    def fetch_leads_with_meta(
        self,
        filters: Optional[List[FilterCondition]] = None,
        limit: int = 10,
    ) -> List[Dict]:
        """Return leads enriched with lead_snapshot and score_snapshot.

        When filters are provided they are pushed to Firestore via collection_group
        queries (one per document type) and the results intersected. Snapshots
        retrieved during filtering are reused to avoid duplicate reads.
        """
        if not filters:
            lead_ids = [
                doc.id
                for doc in itertools.islice(
                    self._lead_collection().list_documents(), limit
                )
            ]
            return self._enrich_leads_with_meta(lead_ids)

        lead_filters = [f for f in filters if f.field in LEAD_DOC_FIELDS]
        score_filters = [f for f in filters if f.field in SCORE_DOC_FIELDS]

        cached_snapshots: Dict[str, Dict] = {}
        result_sets: List[Set[str]] = []

        if lead_filters:
            result = self._run_collection_group_query(lead_filters)
            for lid, snap in result.items():
                cached_snapshots.setdefault(lid, {})["lead"] = snap
            result_sets.append(set(result.keys()))

        if score_filters:
            result = self._run_collection_group_query(score_filters)
            for lid, snap in result.items():
                cached_snapshots.setdefault(lid, {})["scoring"] = snap
            result_sets.append(set(result.keys()))

        if not result_sets:
            # All filter fields were unrecognised — fall back to unfiltered
            lead_ids = [
                doc.id
                for doc in itertools.islice(
                    self._lead_collection().list_documents(), limit
                )
            ]
            return self._enrich_leads_with_meta(lead_ids)

        intersection: Set[str] = result_sets[0].copy()
        for s in result_sets[1:]:
            intersection &= s

        final_ids = sorted(intersection)[:limit]
        return self._enrich_leads_with_meta(final_ids, cached_snapshots)

    def fetch_all_documents_for_lead(self, lead_id: str) -> dict:
        """Return all documents for a lead under the `event_documents` subcollection."""
        docs = self._lead_collection().document(lead_id).collection("event_documents").list_documents()
        result = {}
        for doc in docs:
            doc_snapshot = doc.get()
            data = doc_snapshot.to_dict() or {}
            result[doc.id] = data.get("markdown_content")
        return result
