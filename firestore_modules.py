"""Firestore module for managing leads and markdown documents."""

from google.cloud import firestore
from typing import List, Optional
import os


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

    def fetch_all_documents_for_lead(self, lead_id: str) -> dict:
        """Return all documents for a lead under the `event_documents` subcollection."""
        docs = self._lead_collection().document(lead_id).collection("event_documents").list_documents()
        result = {}
        for doc in docs:
            doc_snapshot = doc.get()
            data = doc_snapshot.to_dict() or {}
            result[doc.id] = data.get("markdown_content")
        return result
