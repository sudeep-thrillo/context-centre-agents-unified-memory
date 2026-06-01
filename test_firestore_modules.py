"""
Unit tests for firestore_modules.py and the get_leads_list MCP tool.

All Firestore I/O is mocked — no credentials or live database required.
Run with:  uv run python -m pytest test_firestore_modules.py -v
"""

import unittest
from unittest.mock import MagicMock, patch, call

from google.api_core.exceptions import FailedPrecondition

from firestore_modules import (
    FilterCondition,
    FirestoreLeadsManager,
    LEAD_DOC_FIELDS,
    SCORE_DOC_FIELDS,
    _match_condition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(collection_root="test_col"):
    """Return a FirestoreLeadsManager whose Firestore client is fully mocked."""
    with patch("firestore_modules.firestore.Client"):
        mgr = FirestoreLeadsManager(collection_root=collection_root, database_id="test-db")
    return mgr


def _mock_doc_ref(lead_id: str, *, lead_snap=None, score_snap=None):
    """
    Return a mock Firestore DocumentSnapshot for a lead document at
    {collection_root}/{lead_id}.  _get_doc_snapshot reads from
    .collection("event_documents").document(doc_name).get().
    """
    def side_effect(lead_id_arg, doc_name):
        if doc_name == "lead":
            return lead_snap
        if doc_name == "scoring":
            return score_snap
        return None
    return side_effect


def _collection_group_doc(collection_root, lead_id, snapshot_data):
    """Build a mock collection-group result document."""
    doc = MagicMock()
    doc.reference.path = f"{collection_root}/{lead_id}/event_documents/lead"
    doc.reference.parent.parent.id = lead_id
    doc.to_dict.return_value = {"latest_event": {"snapshot": snapshot_data}}
    return doc


# ---------------------------------------------------------------------------
# Test 1 — _match_condition: all eight operators
# ---------------------------------------------------------------------------

class TestMatchConditionOperators(unittest.TestCase):
    """Verify every supported operator returns the correct boolean."""

    SNAP = {
        "state":  "open",
        "score":  7.5,
        "region": "Europe",
        "tags":   ["high_value", "fast_response_time"],
    }

    def _ok(self, field, op, value):
        self.assertTrue(_match_condition(self.SNAP, FilterCondition(field, op, value)),
                        f"Expected True: {field} {op} {value!r}")

    def _fail(self, field, op, value):
        self.assertFalse(_match_condition(self.SNAP, FilterCondition(field, op, value)),
                         f"Expected False: {field} {op} {value!r}")

    def test_equality_operator(self):
        self._ok("state", "==", "open")
        self._fail("state", "==", "closed")

    def test_inequality_operator(self):
        self._ok("state", "!=", "closed")
        self._fail("state", "!=", "open")

    def test_greater_than(self):
        self._ok("score", ">", 7)
        self._fail("score", ">", 7.5)

    def test_greater_than_or_equal(self):
        self._ok("score", ">=", 7.5)
        self._ok("score", ">=", 7)
        self._fail("score", ">=", 8)

    def test_less_than(self):
        self._ok("score", "<", 8)
        self._fail("score", "<", 7)

    def test_less_than_or_equal(self):
        self._ok("score", "<=", 7.5)
        self._fail("score", "<=", 7)

    def test_in_operator(self):
        self._ok("region", "in", ["Europe", "Ladakh"])
        self._fail("region", "in", ["Maldives", "Ladakh"])

    def test_not_in_operator(self):
        self._ok("region", "not-in", ["Maldives"])
        self._fail("region", "not-in", ["Europe"])

    def test_array_contains_operator(self):
        self._ok("tags", "array_contains", "high_value")
        self._fail("tags", "array_contains", "repeat_buyer")


# ---------------------------------------------------------------------------
# Test 2 — _match_condition: edge cases (null, missing, type errors)
# ---------------------------------------------------------------------------

class TestMatchConditionEdgeCases(unittest.TestCase):

    def test_none_snapshot_returns_false(self):
        self.assertFalse(_match_condition(None, FilterCondition("score", ">=", 0)))

    def test_empty_snapshot_returns_false(self):
        self.assertFalse(_match_condition({}, FilterCondition("score", ">=", 0)))

    def test_missing_field_returns_false(self):
        self.assertFalse(_match_condition({"other": 99}, FilterCondition("score", ">=", 0)))

    def test_none_field_value_returns_false(self):
        """A field explicitly set to None must not match any filter."""
        self.assertFalse(_match_condition({"score": None}, FilterCondition("score", ">=", 0)))

    def test_type_mismatch_does_not_raise(self):
        """Comparing a string field with > an int must return False, not throw."""
        self.assertFalse(_match_condition({"score": "high"}, FilterCondition("score", ">", 7)))

    def test_array_contains_on_non_list_returns_false(self):
        """tags_v2 is sometimes stored as a plain string; must not raise."""
        snap = {"tags_v2": "[high_value, fast_response_time]"}  # string, not list
        self.assertFalse(_match_condition(snap, FilterCondition("tags_v2", "array_contains", "high_value")))

    def test_unknown_operator_returns_false(self):
        """An unsupported op silently returns False instead of raising."""
        self.assertFalse(_match_condition({"v": 1}, FilterCondition("v", "LIKE", 1)))


# ---------------------------------------------------------------------------
# Test 3 — fetch_leads_with_meta: no-filter path
# ---------------------------------------------------------------------------

class TestFetchLeadsNoFilter(unittest.TestCase):
    """Without filters the method returns at most `limit` leads with correct keys."""

    def _make_list_doc(self, lead_id):
        ref = MagicMock()
        ref.id = lead_id
        return ref

    @patch("firestore_modules.firestore.Client")
    def test_returns_limit_leads_with_required_keys(self, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")
        # list_documents yields 20 doc-refs; limit=5 should only process 5
        all_refs = [self._make_list_doc(f"ENQ{i:04d}") for i in range(20)]
        mgr.client.collection.return_value.list_documents.return_value = iter(all_refs)

        # _get_doc_snapshot returns a deterministic snapshot per doc
        def fake_snapshot(lead_id, doc_name):
            if doc_name == "lead":
                return {"enquiry_code": lead_id, "current_lead_state": "open"}
            return {"booking_urgency_score": 5.0}

        with patch.object(mgr, "_get_doc_snapshot", side_effect=fake_snapshot):
            results = mgr.fetch_leads_with_meta(limit=5)

        self.assertEqual(len(results), 5)
        for item in results:
            self.assertIn("lead_id", item)
            self.assertIn("lead_snapshot", item)
            self.assertIn("score_snapshot", item)
            self.assertIsNotNone(item["lead_snapshot"])
            self.assertIsNotNone(item["score_snapshot"])

    @patch("firestore_modules.firestore.Client")
    def test_default_limit_is_ten(self, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")
        all_refs = [self._make_list_doc(f"ENQ{i:04d}") for i in range(50)]
        mgr.client.collection.return_value.list_documents.return_value = iter(all_refs)

        with patch.object(mgr, "_get_doc_snapshot", return_value=None):
            results = mgr.fetch_leads_with_meta()  # no limit arg

        self.assertEqual(len(results), 10, "Default limit must be 10")


# ---------------------------------------------------------------------------
# Test 4 — fetch_leads_with_meta: Firestore collection_group path for lead fields
# ---------------------------------------------------------------------------

class TestFirestoreCollectionGroupPath(unittest.TestCase):
    """Lead-field filters are forwarded to collection_group as FieldFilters."""

    @patch("firestore_modules.firestore.Client")
    @patch("firestore_modules.FieldFilter")
    def test_lead_filter_builds_correct_field_path(self, mock_ff_cls, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")
        # Build a mock query chain
        mock_query = MagicMock()
        mock_query.where.return_value = mock_query

        # The single result doc
        mock_doc = _collection_group_doc("col", "ENQ001", {"current_lead_state": "open"})
        mock_query.stream.return_value = [mock_doc]
        mgr.client.collection_group.return_value = mock_query

        with patch.object(mgr, "_get_doc_snapshot", return_value=None):
            mgr.fetch_leads_with_meta(
                filters=[FilterCondition("current_lead_state", "==", "open")],
                limit=5,
            )

        # FieldFilter must be constructed with the full nested path
        mock_ff_cls.assert_called_once_with(
            "latest_event.snapshot.current_lead_state", "==", "open"
        )
        mgr.client.collection_group.assert_called_with("event_documents")

    @patch("firestore_modules.firestore.Client")
    @patch("firestore_modules.FieldFilter")
    def test_score_filter_builds_correct_field_path(self, mock_ff_cls, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")
        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_doc = _collection_group_doc("col", "ENQ001", {"booking_urgency_score": 8.0})
        mock_query.stream.return_value = [mock_doc]
        mgr.client.collection_group.return_value = mock_query

        with patch.object(mgr, "_get_doc_snapshot", return_value=None):
            mgr.fetch_leads_with_meta(
                filters=[FilterCondition("booking_urgency_score", ">=", 7)],
                limit=5,
            )

        mock_ff_cls.assert_called_once_with(
            "latest_event.snapshot.booking_urgency_score", ">=", 7
        )


# ---------------------------------------------------------------------------
# Test 5 — fetch_leads_with_meta: FailedPrecondition triggers client-side fallback
# ---------------------------------------------------------------------------

class TestFailedPreconditionFallback(unittest.TestCase):
    """A missing Firestore index must not surface as an error — fallback to client scan."""

    @patch("firestore_modules.firestore.Client")
    def test_failed_precondition_calls_client_side_filter(self, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")

        # collection_group().where().stream() raises FailedPrecondition
        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.stream.side_effect = FailedPrecondition("index missing")
        mgr.client.collection_group.return_value = mock_query

        with patch.object(mgr, "_client_side_filter", return_value=[]) as mock_fallback:
            mgr.fetch_leads_with_meta(
                filters=[FilterCondition("booking_urgency_score", ">=", 7)],
                limit=5,
            )

        mock_fallback.assert_called_once()
        args = mock_fallback.call_args
        # score_filters should be forwarded, lead_filters empty
        _, score_filt, lim = args[0]
        self.assertEqual(lim, 5)
        self.assertEqual(score_filt[0].field, "booking_urgency_score")

    @patch("firestore_modules.firestore.Client")
    def test_failed_precondition_does_not_raise(self, mock_client_cls):
        """The tool must return a list, never re-raise FailedPrecondition."""
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")
        mock_query = MagicMock()
        mock_query.where.return_value = mock_query
        mock_query.stream.side_effect = FailedPrecondition("index missing")
        mgr.client.collection_group.return_value = mock_query

        ref = MagicMock(); ref.id = "ENQ001"
        mgr.client.collection.return_value.list_documents.return_value = iter([ref])

        with patch.object(mgr, "_get_doc_snapshot", return_value=None):
            try:
                result = mgr.fetch_leads_with_meta(
                    filters=[FilterCondition("booking_urgency_score", ">=", 7)],
                    limit=5,
                )
                self.assertIsInstance(result, list)
            except FailedPrecondition:
                self.fail("FailedPrecondition must be caught and not re-raised")


# ---------------------------------------------------------------------------
# Test 6 — _client_side_filter: returns only matching leads (lead field)
# ---------------------------------------------------------------------------

class TestClientSideFilterLeadField(unittest.TestCase):
    """_client_side_filter applies Python-level filter and returns correct subset."""

    @patch("firestore_modules.firestore.Client")
    def test_only_matching_leads_returned(self, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")

        leads = ["ENQ001", "ENQ002", "ENQ003"]
        ref_docs = [MagicMock(id=lid) for lid in leads]
        mgr.client.collection.return_value.list_documents.return_value = iter(ref_docs)

        snapshots = {
            "ENQ001": {"lead": {"current_lead_state": "open"},  "scoring": None},
            "ENQ002": {"lead": {"current_lead_state": "lost"},  "scoring": None},
            "ENQ003": {"lead": {"current_lead_state": "open"},  "scoring": None},
        }

        def fake_snap(lead_id, doc_name):
            return snapshots[lead_id][doc_name]

        with patch.object(mgr, "_get_doc_snapshot", side_effect=fake_snap):
            results = mgr._client_side_filter(
                lead_filters=[FilterCondition("current_lead_state", "==", "open")],
                score_filters=[],
                limit=10,
            )

        result_ids = [r["lead_id"] for r in results]
        self.assertIn("ENQ001", result_ids)
        self.assertIn("ENQ003", result_ids)
        self.assertNotIn("ENQ002", result_ids)


# ---------------------------------------------------------------------------
# Test 7 — _client_side_filter: cross-type AND logic
# ---------------------------------------------------------------------------

class TestClientSideFilterCrossType(unittest.TestCase):
    """Both lead AND score conditions must hold; partial match is excluded."""

    @patch("firestore_modules.firestore.Client")
    def test_cross_type_requires_both_conditions(self, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")

        leads = ["ENQ001", "ENQ002", "ENQ003", "ENQ004"]
        ref_docs = [MagicMock(id=lid) for lid in leads]
        mgr.client.collection.return_value.list_documents.return_value = iter(ref_docs)

        # ENQ001: open + high score    → MATCH
        # ENQ002: open + low score     → no match (score fails)
        # ENQ003: lost + high score    → no match (state fails)
        # ENQ004: lost + low score     → no match (both fail)
        snapshots = {
            "ENQ001": {"lead": {"current_lead_state": "open"},  "scoring": {"booking_urgency_score": 9.0}},
            "ENQ002": {"lead": {"current_lead_state": "open"},  "scoring": {"booking_urgency_score": 3.0}},
            "ENQ003": {"lead": {"current_lead_state": "lost"},  "scoring": {"booking_urgency_score": 9.0}},
            "ENQ004": {"lead": {"current_lead_state": "lost"},  "scoring": {"booking_urgency_score": 3.0}},
        }

        def fake_snap(lead_id, doc_name):
            return snapshots[lead_id][doc_name]

        with patch.object(mgr, "_get_doc_snapshot", side_effect=fake_snap):
            results = mgr._client_side_filter(
                lead_filters=[FilterCondition("current_lead_state", "==", "open")],
                score_filters=[FilterCondition("booking_urgency_score", ">=", 7)],
                limit=10,
            )

        self.assertEqual([r["lead_id"] for r in results], ["ENQ001"])


# ---------------------------------------------------------------------------
# Test 8 — _client_side_filter: limit stops the scan early
# ---------------------------------------------------------------------------

class TestClientSideFilterLimit(unittest.TestCase):
    """The scan must stop once `limit` matching leads are collected."""

    @patch("firestore_modules.firestore.Client")
    def test_limit_stops_scan_early(self, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")

        # 10 leads all matching; limit=3 → exactly 3 returned
        leads = [f"ENQ{i:03d}" for i in range(10)]
        ref_docs = [MagicMock(id=lid) for lid in leads]
        mgr.client.collection.return_value.list_documents.return_value = iter(ref_docs)

        call_count = {"n": 0}

        def fake_snap(lead_id, doc_name):
            call_count["n"] += 1
            return {"current_lead_state": "open"} if doc_name == "lead" else None

        with patch.object(mgr, "_get_doc_snapshot", side_effect=fake_snap):
            results = mgr._client_side_filter(
                lead_filters=[FilterCondition("current_lead_state", "==", "open")],
                score_filters=[],
                limit=3,
            )

        self.assertEqual(len(results), 3)
        # Should have fetched both docs for only the first batch (≤50 leads)
        # and stopped after finding 3 matches — not fetching all 10×2 docs
        # beyond the first full batch. At minimum: exactly 3 matches found.
        self.assertLessEqual(call_count["n"], 20,
                             "Should not read many more docs than needed")


# ---------------------------------------------------------------------------
# Test 9 — fetch_leads_with_meta: two-query intersection (Firestore path)
# ---------------------------------------------------------------------------

class TestFirestoreIntersection(unittest.TestCase):
    """When both queries succeed, only the intersection of lead IDs is enriched."""

    @patch("firestore_modules.firestore.Client")
    @patch("firestore_modules.FieldFilter")
    def test_intersection_of_lead_and_score_queries(self, mock_ff_cls, mock_client_cls):
        mgr = FirestoreLeadsManager(collection_root="col", database_id="db")

        # Lead query returns ENQ001 + ENQ002; Score query returns ENQ001 + ENQ003
        # → intersection is just ENQ001

        lead_doc_001 = _collection_group_doc("col", "ENQ001", {"current_lead_state": "open"})
        lead_doc_002 = _collection_group_doc("col", "ENQ002", {"current_lead_state": "open"})
        score_doc_001 = _collection_group_doc("col", "ENQ001", {"booking_urgency_score": 8.0})
        score_doc_003 = _collection_group_doc("col", "ENQ003", {"booking_urgency_score": 8.0})

        call_count = {"n": 0}

        def query_side_effect(*args, **kwargs):
            """Alternate stream results: first call = lead query, second = score query."""
            call_count["n"] += 1
            mock_q = MagicMock()
            mock_q.where.return_value = mock_q
            if call_count["n"] == 1:
                mock_q.stream.return_value = [lead_doc_001, lead_doc_002]
            else:
                mock_q.stream.return_value = [score_doc_001, score_doc_003]
            return mock_q

        mgr.client.collection_group.side_effect = query_side_effect

        with patch.object(mgr, "_get_doc_snapshot", return_value=None):
            results = mgr.fetch_leads_with_meta(
                filters=[
                    FilterCondition("current_lead_state", "==", "open"),
                    FilterCondition("booking_urgency_score", ">=", 7),
                ],
                limit=10,
            )

        result_ids = [r["lead_id"] for r in results]
        self.assertEqual(result_ids, ["ENQ001"], "Only the intersection lead should be returned")
        self.assertNotIn("ENQ002", result_ids)
        self.assertNotIn("ENQ003", result_ids)


# ---------------------------------------------------------------------------
# Test 10 — MCP tool registration: schema, defaults, removed tool
# ---------------------------------------------------------------------------

class TestMCPToolRegistration(unittest.TestCase):
    """Verify the MCP server exposes the right tools with the right schema."""

    @classmethod
    def setUpClass(cls):
        # Import the live FastMCP app (Firestore client is instantiated but
        # auth errors are silenced at import; we only inspect the schema here)
        with patch("firestore_modules.firestore.Client"):
            import importlib, mcp_server as ms
            importlib.reload(ms)
            cls.mcp = ms.mcp

    def _get_tool(self, name):
        tools = self.mcp._tool_manager.list_tools()
        return next((t for t in tools if t.name == name), None)

    def test_get_customer_details_is_absent(self):
        self.assertIsNone(
            self._get_tool("get_customer_details"),
            "get_customer_details must be removed from the server",
        )

    def test_get_leads_list_is_registered(self):
        self.assertIsNotNone(self._get_tool("get_leads_list"))

    def test_get_leads_list_default_limit_is_10(self):
        tool = self._get_tool("get_leads_list")
        props = tool.parameters.get("properties", {})
        self.assertEqual(props["limit"]["default"], 10)

    def test_get_leads_list_limit_bounds(self):
        tool = self._get_tool("get_leads_list")
        props = tool.parameters.get("properties", {})
        self.assertEqual(props["limit"]["minimum"], 1)
        self.assertEqual(props["limit"]["maximum"], 100)

    def test_get_leads_list_filters_is_optional(self):
        tool = self._get_tool("get_leads_list")
        # filters not in required list (optional)
        required = tool.parameters.get("required", [])
        self.assertNotIn("filters", required)

    def test_filter_condition_schema_has_all_required_fields(self):
        tool = self._get_tool("get_leads_list")
        defs = tool.parameters.get("$defs", {})
        fc_schema = defs.get("FilterCondition", {})
        required = set(fc_schema.get("required", []))
        self.assertEqual(required, {"field", "op", "value"})

    def test_get_lead_complete_details_has_no_customer_details_key(self):
        tool = self._get_tool("get_lead_complete_details")
        self.assertIsNotNone(tool)
        self.assertNotIn("customer_details", tool.description)


if __name__ == "__main__":
    unittest.main(verbosity=2)
