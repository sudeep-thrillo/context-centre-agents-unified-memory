"""Google Cloud Storage module for managing leads and files."""

from google.cloud import storage
from typing import List, Optional
import os


class GCSLeadsManager:
    """Simple GCS manager for leads, files, and content."""
    
    def __init__(self, credentials_path: Optional[str] = None):
        """
        Initialize GCS client.
        
        Args:
            credentials_path: Path to service account JSON file. 
                            If None, uses GOOGLE_APPLICATION_CREDENTIALS env var.
        """
        if credentials_path:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_path
        
        self.client = storage.Client()
        self.bucket_name = "context_center_stage"
        self.leads_prefix = "leads/"
        self.bucket = self.client.bucket(self.bucket_name)
    
    def fetch_all_leads(self) -> List[str]:
        """
        Fetch all lead folder names.
        
        Returns:
            List of lead IDs (e.g., ['lead_8f3a92', 'lead_abc123'])
        """
        leads = set()
        iterator = self.client.list_blobs(
            self.bucket_name,
            prefix=self.leads_prefix,
            delimiter="/"
        )
        
        # Consume the iterator so prefixes are populated
        list(iterator)
        
        # Get all prefixes (folders)
        for prefix in iterator.prefixes:
            lead_id = prefix.replace(self.leads_prefix, "").rstrip("/")
            if lead_id:
                leads.add(lead_id)
        
        return sorted(list(leads))
    
    def fetch_files_in_lead(self, lead_id: str) -> List[str]:
        """
        Fetch all files in a specific lead folder.
        
        Args:
            lead_id: Lead ID (e.g., 'lead_8f3a92')
            
        Returns:
            List of file names (e.g., ['calls_index.md', 'notes.txt'])
        """
        prefix = f"{self.leads_prefix}{lead_id}/"
        files = []
        
        blobs = self.client.list_blobs(
            self.bucket_name, 
            prefix=prefix
        )
        
        for blob in blobs:
            # Get filename without the full path
            filename = blob.name.replace(prefix, "")
            if filename and not filename.endswith("/"):
                files.append(filename)
        
        return sorted(files)
    
    def fetch_file_content(self, lead_id: str, filename: str) -> str:
        """
        Fetch content of a specific file in a lead.
        
        Args:
            lead_id: Lead ID (e.g., 'lead_8f3a92')
            filename: File name (e.g., 'calls_index.md')
            
        Returns:
            File content as string
            
        Raises:
            FileNotFoundError: If file doesn't exist
        """
        blob_path = f"{self.leads_prefix}{lead_id}/{filename}"
        blob = self.bucket.blob(blob_path)
        
        if not blob.exists():
            raise FileNotFoundError(f"File not found: {blob_path}")
        
        return blob.download_as_text()
    
    def list_all_with_structure(self) -> dict:
        """
        Get complete structure: all leads and their files.
        
        Returns:
            Dict with leads as keys and file lists as values
        """
        structure = {}
        leads = self.fetch_all_leads()
        
        for lead_id in leads:
            structure[lead_id] = self.fetch_files_in_lead(lead_id)
        
        return structure


# Example usage
if __name__ == "__main__":
    # Initialize with service account credentials
    manager = GCSLeadsManager(credentials_path="/Users/thrillophilia/Documents/Project/context-centre-agents-unified-memory/data-platform-421407-d95c009c5786.json")
    
    # Fetch all leads
    leads = manager.fetch_all_leads()
    print(f"Leads: {leads}")
    
    # Fetch files in a lead
    if leads:
        files = manager.fetch_files_in_lead(leads[0])
        print(f"Files in {leads[0]}: {files}")
        
        # Fetch file content
        if files:
            content = manager.fetch_file_content(leads[0], files[0])
            print(f"Content of {files[0]}:\n{content}")
    
    # Get complete structure
    structure = manager.list_all_with_structure()
    print(f"\nComplete structure:\n{structure}")
