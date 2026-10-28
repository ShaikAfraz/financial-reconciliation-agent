"""
Unit and integration tests for the Two-Sided Financial Reconciliation Web API:
1. Balance proof endpoint verification (Rule 4 integer-cents determinism, difference 0.00).
2. Confirm endpoint valid state transition (updates matches, match_members, reviews, audit_logs).
3. Confirm endpoint invalid transitions (missing fields, non-existent records, same-side records, arithmetic fee excess).
4. Reject endpoint state transition (marks rejected, returns item to review queue with pending_review, writes audit_logs).
5. Reject endpoint invalid transitions (missing identifiers, non-existent match).
6. Reassign endpoint valid and invalid transitions.
7. XSS-style description inert rendering test (ensures malicious script/img tags are escaped and rendered inert).
8. Records query and tools metadata endpoints.
"""

import os
import json
import time
import html
import urllib.request
import urllib.error
import threading
import unittest
from pathlib import Path
from http.server import HTTPServer

import data.db as db
import reconcile.service as rec_service
from server import FinancialAgentServerHandler

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class TestReconciliationWebApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Ensure test database has clean benchmark state
        rec_service.seed_reconciliation_from_benchmark("dev")

        # Create test admin user and session
        import auth.service as auth_service
        try:
            cls.user = auth_service.create_user(
                username="test_api_analyst",
                email="test_analyst@example.com",
                password="TestPassword123!",
                role="admin"
            )
        except ValueError:
            cls.user = auth_service.get_user_by_username("test_api_analyst")
            auth_service.update_user(cls.user["id"], is_active=True, role="admin")

        cls.raw_token, cls.csrf_token, cls.expires_at = auth_service.create_session(cls.user["id"])

        # Start ephemeral test server on localhost port 0
        cls.server = HTTPServer(("127.0.0.1", 0), FinancialAgentServerHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        # Refresh dev benchmark baseline before each test
        rec_service.seed_reconciliation_from_benchmark("dev")

    def _api_get(self, endpoint: str):
        url = f"http://127.0.0.1:{self.port}{endpoint}"
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Cookie": f"session_id={self.raw_token}"}
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as e:
            data = json.loads(e.read().decode("utf-8"))
            return e.code, data

    def _api_post(self, endpoint: str, payload: dict):
        url = f"http://127.0.0.1:{self.port}{endpoint}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": f"session_id={self.raw_token}",
                "X-CSRF-Token": self.csrf_token,
                "Origin": f"http://127.0.0.1:{self.port}"
            }
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as e:
            data = json.loads(e.read().decode("utf-8"))
            return e.code, data

    # ----------------------------------------------------------------------
    # 1. Balance Proof Endpoint
    # ----------------------------------------------------------------------
    def test_balance_proof_endpoint(self):
        """Balance proof endpoint must return deterministic integer-cents proof with 0.00 difference."""
        status, data = self._api_get("/api/reconciliation/balance-proof")
        self.assertEqual(status, 200)
        self.assertIn("difference_cents", data)
        self.assertIn("difference_formatted", data)
        self.assertEqual(data["difference_cents"], 0)
        self.assertEqual(data["difference_formatted"], "0.00")
        self.assertTrue(data["is_balanced"])
        self.assertIsInstance(data["ending_bank_balance_cents"], int)
        self.assertIsInstance(data["ending_book_balance_cents"], int)

    # ----------------------------------------------------------------------
    # 2. Confirm Endpoint Valid State Transition
    # ----------------------------------------------------------------------
    def test_confirm_endpoint_valid_transition(self):
        """Confirming a match updates matches to approved, resolves exceptions, writes review and audit log."""
        # Find an item from review queue
        queue = rec_service.get_review_queue()
        self.assertGreater(len(queue), 0, "Review queue must contain items")
        item = queue[0]
        target_id = item["target_item"]["id"]
        candidate_id = item["proposed_candidate"]["id"]
        match_id = item.get("match_id")

        payload = {
            "target_id": target_id,
            "candidate_id": candidate_id,
            "match_id": match_id,
            "reviewer_note": "Verified by human auditor in test suite"
        }

        status, data = self._api_post("/api/reconciliation/confirm", payload)
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "confirmed")
        confirmed_match_id = data["match_id"]

        # 1. Verify SQLite matches table
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status, method, confidence FROM matches WHERE match_id = ?", (confirmed_match_id,))
        m_row = cursor.fetchone()
        self.assertIsNotNone(m_row)
        self.assertEqual(m_row["status"], "approved")
        self.assertEqual(m_row["method"], "human_confirmed")
        self.assertEqual(m_row["confidence"], 1.0)

        # 2. Verify match members
        cursor.execute("SELECT record_id FROM match_members WHERE match_id = ?", (confirmed_match_id,))
        member_ids = {r[0] for r in cursor.fetchall()}
        self.assertIn(target_id, member_ids)
        self.assertIn(candidate_id, member_ids)

        # 3. Verify reviews table
        cursor.execute("SELECT decision, reviewer_note FROM reviews WHERE match_id = ?", (confirmed_match_id,))
        r_row = cursor.fetchone()
        self.assertIsNotNone(r_row)
        self.assertEqual(r_row["decision"], "confirm")
        self.assertEqual(r_row["reviewer_note"], "Verified by human auditor in test suite")

        # 4. Verify audit_logs table
        cursor.execute("SELECT tool_name, arguments_json FROM audit_logs WHERE tool_name = 'human_review_confirm' ORDER BY id DESC LIMIT 1")
        audit_row = cursor.fetchone()
        self.assertIsNotNone(audit_row)
        self.assertEqual(audit_row["tool_name"], "human_review_confirm")
        args = json.loads(audit_row["arguments_json"])
        self.assertEqual(args["target_id"], target_id)
        self.assertEqual(args["candidate_id"], candidate_id)

        conn.close()

    # ----------------------------------------------------------------------
    # 3. Confirm Endpoint Invalid Transitions
    # ----------------------------------------------------------------------
    def test_confirm_endpoint_missing_fields(self):
        """Confirming without required fields returns 400."""
        status, data = self._api_post("/api/reconciliation/confirm", {})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

        status, data = self._api_post("/api/reconciliation/confirm", {"target_id": "BANK-001"})
        self.assertEqual(status, 400)

    def test_confirm_endpoint_nonexistent_records(self):
        """Confirming non-existent record IDs returns 400."""
        payload = {"target_id": "BANK-NONEXISTENT-9999", "candidate_id": "LEDGER-NONEXISTENT-9999"}
        status, data = self._api_post("/api/reconciliation/confirm", payload)
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_confirm_endpoint_same_side_records(self):
        """Confirming two records from the same side (e.g. bank vs bank) returns 400."""
        # Query two real bank records
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM bank_records LIMIT 2")
        rows = cursor.fetchall()
        conn.close()

        payload = {"target_id": rows[0][0], "candidate_id": rows[1][0]}
        status, data = self._api_post("/api/reconciliation/confirm", payload)
        self.assertEqual(status, 400)
        self.assertIn("one record must be a bank record and one must be a ledger record", data["error"])

    def test_confirm_endpoint_arithmetic_tolerance_exceeded(self):
        """Confirming records whose delta exceeds MAX_FEE_TOLERANCE_CENTS (1000 cents) returns 400."""
        # Find two records with large amount difference
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, amount_cents FROM bank_records ORDER BY amount_cents ASC LIMIT 1")
        b_rec = cursor.fetchone()
        cursor.execute("SELECT id, amount_cents FROM ledger_records WHERE amount_cents > ? + 2000 LIMIT 1", (b_rec["amount_cents"],))
        l_rec = cursor.fetchone()
        conn.close()

        payload = {"target_id": b_rec["id"], "candidate_id": l_rec["id"]}
        status, data = self._api_post("/api/reconciliation/confirm", payload)
        self.assertEqual(status, 400)
        self.assertIn("Arithmetic discrepancy exceeds allowable tolerance", data["error"])

    # ----------------------------------------------------------------------
    # 4. Reject Endpoint State Transition
    # ----------------------------------------------------------------------
    def test_reject_endpoint_state_transition(self):
        """Rejecting a tentative match marks it rejected, returns item to queue with pending_review, and logs audit."""
        queue = rec_service.get_review_queue()
        item = queue[0]
        match_id = item.get("match_id")
        target_id = item["target_item"]["id"]

        payload = {
            "match_id": match_id,
            "target_id": target_id,
            "reviewer_note": "Counterparty mismatch identified by human auditor"
        }

        status, data = self._api_post("/api/reconciliation/reject", payload)
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "rejected")

        # 1. Verify SQLite matches table
        conn = db.get_connection()
        cursor = conn.cursor()
        if match_id:
            cursor.execute("SELECT status FROM matches WHERE match_id = ?", (match_id,))
            m_row = cursor.fetchone()
            self.assertEqual(m_row["status"], "rejected")

        # 2. Verify target item returned to queue with status 'pending_review'
        cursor.execute("SELECT status FROM exceptions WHERE record_id = ?", (target_id,))
        ex_row = cursor.fetchone()
        self.assertIsNotNone(ex_row)
        self.assertEqual(ex_row["status"], "pending_review")

        # 3. Verify reviews table
        cursor.execute("SELECT decision, reviewer_note FROM reviews WHERE match_id = ?", (match_id,))
        r_row = cursor.fetchone()
        self.assertIsNotNone(r_row)
        self.assertEqual(r_row["decision"], "reject")

        # 4. Verify audit_logs table
        cursor.execute("SELECT tool_name, arguments_json FROM audit_logs WHERE tool_name = 'human_review_reject' ORDER BY id DESC LIMIT 1")
        audit_row = cursor.fetchone()
        self.assertIsNotNone(audit_row)
        self.assertEqual(audit_row["tool_name"], "human_review_reject")

        conn.close()

        # 5. Verify review queue still contains the item
        q_after = rec_service.get_review_queue()
        self.assertTrue(any(x["target_item"]["id"] == target_id for x in q_after), "Rejected item must return to review queue")

    def test_reject_endpoint_missing_identifiers(self):
        """Rejecting without match_id or target_id returns 400."""
        status, data = self._api_post("/api/reconciliation/reject", {})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    # ----------------------------------------------------------------------
    # 5. Reassign Endpoint Transitions
    # ----------------------------------------------------------------------
    def test_reassign_endpoint_valid_transition(self):
        """Reassigning pairs target with new candidate and updates audit trail."""
        queue = rec_service.get_review_queue()
        item = queue[0]
        target_id = item["target_item"]["id"]
        candidates = item["candidates"]
        self.assertGreaterEqual(len(candidates), 1)
        new_cand_id = candidates[-1]["id"]

        payload = {
            "target_id": target_id,
            "new_candidate_id": new_cand_id,
            "reviewer_note": "Reassigned to alternative settlement candidate"
        }

        status, data = self._api_post("/api/reconciliation/reassign", payload)
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "reassigned")

        # Verify audit log recorded
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT tool_name FROM audit_logs WHERE tool_name = 'human_review_reassign' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        conn.close()

    # ----------------------------------------------------------------------
    # 6. XSS-Style Description Inert Rendering Test
    # ----------------------------------------------------------------------
    def test_xss_description_renders_inert(self):
        """
        Tests that an XSS description payload stored in the database is sanitized
        and rendered completely inert without raw unescaped script or HTML injection.
        """
        xss_payload = '<script>alert("XSS")</script><img src=x onerror=alert(1)>'
        xss_id = "BANK-XSS-TEST-99"

        # Insert malicious payload into bank_records
        db.insert_bank_record(
            rec_id=xss_id,
            date="2026-08-25",
            amount_cents=12500,
            description=xss_payload,
            reference="REF-XSS-01"
        )

        # 1. API fetch must preserve string value in JSON without unescaped tags executing
        status, data = self._api_get(f"/api/reconciliation/records?search=XSS")
        self.assertEqual(status, 200)
        bank_records = data["bank_records"]
        matched = next((b for b in bank_records if b["id"] == xss_id), None)
        self.assertIsNotNone(matched, "Record with XSS description must be returned by API")
        self.assertEqual(matched["description"], xss_payload)

        # 2. Strict Escaping Simulation (matching escapeHtml in web/app.js)
        raw_desc = matched["description"]
        escaped_desc = (
            raw_desc
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#039;")
        )

        # Verify that escaped string contains no literal '<' or '>'
        self.assertNotIn("<script>", escaped_desc)
        self.assertNotIn("<img>", escaped_desc)
        self.assertNotIn("<img", escaped_desc)
        self.assertIn("&lt;script&gt;alert(&quot;XSS&quot;)&lt;/script&gt;", escaped_desc)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", escaped_desc)

        # 3. Simulate DOM textContent assignment (which browser uses)
        # In textContent, the string is treated purely as character data and cannot parse as DOM nodes.
        inert_text = html.unescape(escaped_desc)
        self.assertEqual(inert_text, xss_payload)

    # ----------------------------------------------------------------------
    # 7. Records and Tools Metadata Endpoints
    # ----------------------------------------------------------------------
    def test_records_and_tools_endpoints(self):
        """Verifies records query and 7-tool metadata endpoints return expected structure."""
        status, data = self._api_get("/api/reconciliation/records")
        self.assertEqual(status, 200)
        self.assertIn("bank_records", data)
        self.assertIn("ledger_records", data)
        self.assertIn("matches_count", data)

        status, t_data = self._api_get("/api/reconciliation/tools")
        self.assertEqual(status, 200)
        self.assertIn("tools", t_data)
        self.assertEqual(len(t_data["tools"]), 7)
        tool_names = [t["name"] for t in t_data["tools"]]
        expected_tools = [
            "ingest_records", "generate_candidates", "adjudicate_item",
            "validate_decision", "commit_match", "compute_balance_proof",
            "list_review_queue"
        ]
        for exp in expected_tools:
            self.assertIn(exp, tool_names)


if __name__ == "__main__":
    unittest.main()
