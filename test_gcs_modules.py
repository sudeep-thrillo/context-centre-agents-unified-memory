import os
import unittest

from gcs_modules import GCSLeadsManager


class TestGCSLeadsManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        credentials_path = os.environ.get("GCS_CREDENTIALS_PATH") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not credentials_path:
            raise unittest.SkipTest(
                "Set GCS_CREDENTIALS_PATH or GOOGLE_APPLICATION_CREDENTIALS to run GCS tests"
            )

        cls.manager = GCSLeadsManager(credentials_path=credentials_path)
        cls.leads = cls.manager.fetch_all_leads()

    def test_fetch_all_leads(self):
        self.assertIsInstance(self.leads, list)
        self.assertTrue(all(isinstance(lead, str) for lead in self.leads))

    def test_fetch_files_in_lead(self):
        if not self.leads:
            self.skipTest("No leads found in the bucket")

        files = self.manager.fetch_files_in_lead(self.leads[0])
        self.assertIsInstance(files, list)
        self.assertTrue(all(isinstance(file_name, str) for file_name in files))

    def test_fetch_file_content(self):
        if not self.leads:
            self.skipTest("No leads found in the bucket")

        files = self.manager.fetch_files_in_lead(self.leads[0])
        if not files:
            self.skipTest(f"No files found for lead {self.leads[0]}")

        content = self.manager.fetch_file_content(self.leads[0], files[0])
        self.assertIsInstance(content, str)
        self.assertGreater(len(content), 0)


if __name__ == "__main__":
    unittest.main()
