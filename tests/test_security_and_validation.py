"""
Unit and integration tests for security hardening and request validation:
1. Gemini provider authentication via header only (API key absent from URL, never logged).
2. Elimination of hardcoded 'LLM synthesis verified.' claim and trace verification='none'.
3. /api/test-tool gated behind ENABLE_DEBUG_TOOLS=1 (404 by default).
4. Comprehensive request validation for /api/run (type checks, length, unknown scenario IDs -> 400).
"""

import os
import json
import threading
import unittest
from unittest.mock import patch, MagicMock
import urllib.request
import urllib.error
from http.server import HTTPServer

from agent.providers.gemini_provider import GeminiAgentProvider
from agent.orchestrator import FinancialReconciliationAgent
from server import FinancialAgentServerHandler, MAX_PROMPT_LENGTH

class TestGeminiSecurityAndTrace(unittest.TestCase):

    @patch("urllib.request.urlopen")
    def test_key_absent_from_url_and_sent_in_header(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{"text": "Reconciliation finalized successfully."}]
                }
            }]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        secret_key = "AIzaSySecretApiKeyHeaderOnly12345"
        provider = GeminiAgentProvider(api_key=secret_key)

        decision = provider.plan_step("Audit this statement", {}, [])

        # Verify urllib.request.urlopen was called
        self.assertTrue(mock_urlopen.called)
        sent_req = mock_urlopen.call_args[0][0]

        # Assert key is absent from the request URL
        self.assertNotIn(secret_key, sent_req.full_url)
        self.assertNotIn("?key=", sent_req.full_url)
        self.assertEqual(
            sent_req.full_url,
            f"https://generativelanguage.googleapis.com/v1beta/models/{provider.model}:generateContent"
        )

        # Assert key is sent via x-goog-api-key header
        self.assertEqual(sent_req.headers.get("X-goog-api-key"), secret_key)

        # Assert hardcoded verification claim is removed
        self.assertNotIn("LLM synthesis verified.", decision.get("grounded_facts", []))
        self.assertEqual(decision.get("grounded_facts"), [])
        self.assertEqual(decision.get("verification"), "none")

    def test_key_never_logged_or_exposed_in_repr(self):
        secret_key = "AIzaSySecretApiKeyHeaderOnly12345"
        provider = GeminiAgentProvider(api_key=secret_key)
        self.assertNotIn(secret_key, repr(provider))
        self.assertNotIn(secret_key, str(provider))

    @patch("urllib.request.urlopen")
    def test_trace_verification_none_with_gemini(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{"text": "Audit complete."}]
                }
            }]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        provider = GeminiAgentProvider(api_key="AIzaDummyKey")
        agent = FinancialReconciliationAgent(provider=provider)
        trace = agent.run("Reconcile coffee purchase")

        self.assertEqual(trace.grounded_facts, [])
        self.assertEqual(trace.verification, "none")
        self.assertEqual(trace.to_dict()["verification"], "none")


class TestServerSecurityAndValidation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Create test admin user and session
        import auth.service as auth_service
        try:
            cls.user = auth_service.create_user(
                username="test_sec_admin",
                email="test_sec_admin@example.com",
                password="AdminPassword123!",
                role="admin"
            )
        except ValueError:
            cls.user = auth_service.get_user_by_username("test_sec_admin")
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

    def _post_json(self, path: str, payload: bytes or dict) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        if isinstance(payload, bytes):
            data = payload
        else:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Cookie": f"session_id={self.raw_token}",
                "X-CSRF-Token": self.csrf_token,
                "Origin": f"http://127.0.0.1:{self.port}"
            }
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                return status, body
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            finally:
                e.close()
            return e.code, body

    def test_debug_endpoint_404_by_default(self):
        # Ensure ENABLE_DEBUG_TOOLS is not '1'
        old_val = os.environ.pop("ENABLE_DEBUG_TOOLS", None)
        try:
            status, body = self._post_json("/api/test-tool", {"tool_name": "flag_anomaly", "arguments": {}})
            self.assertEqual(status, 404)
            self.assertIn("error", body)
        finally:
            if old_val is not None:
                os.environ["ENABLE_DEBUG_TOOLS"] = old_val

    def test_debug_endpoint_available_when_flag_enabled(self):
        old_val = os.environ.get("ENABLE_DEBUG_TOOLS")
        os.environ["ENABLE_DEBUG_TOOLS"] = "1"
        try:
            status, body = self._post_json("/api/test-tool", {
                "tool_name": "flag_anomaly",
                "arguments": {"merchant": "Chipotle Mexican Grill", "amount": 14.50, "category": "Dining"}
            })
            self.assertEqual(status, 200)
            self.assertTrue(body.get("is_success"))
            self.assertFalse(body.get("output", {}).get("is_anomaly"))
        finally:
            if old_val is not None:
                os.environ["ENABLE_DEBUG_TOOLS"] = old_val
            else:
                os.environ.pop("ENABLE_DEBUG_TOOLS", None)

    def test_run_validation_invalid_json(self):
        status, body = self._post_json("/api/run", b"{invalid json payload")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_run_validation_non_dict_payload(self):
        status, body = self._post_json("/api/run", b"[1, 2, 3]")
        self.assertEqual(status, 400)
        self.assertIn("Request body must be a JSON object", body.get("error", ""))

    def test_run_validation_missing_query_and_scenario(self):
        status, body = self._post_json("/api/run", {})
        self.assertEqual(status, 400)
        self.assertIn("Missing required field", body.get("error", ""))

    def test_run_validation_unknown_scenario_id(self):
        status, body = self._post_json("/api/run", {"scenario_id": "SCENARIO-DOES-NOT-EXIST"})
        self.assertEqual(status, 400)
        self.assertIn("Unknown scenario_id", body.get("error", ""))

    def test_run_validation_non_string_scenario_id(self):
        status, body = self._post_json("/api/run", {"scenario_id": 999})
        self.assertEqual(status, 400)
        self.assertIn("must be a string", body.get("error", ""))

    def test_run_validation_empty_query(self):
        status, body = self._post_json("/api/run", {"query": "    "})
        self.assertEqual(status, 400)
        self.assertIn("cannot be empty", body.get("error", ""))

    def test_run_validation_non_string_query(self):
        status, body = self._post_json("/api/run", {"query": 12345})
        self.assertEqual(status, 400)
        self.assertIn("must be a string", body.get("error", ""))

    def test_run_validation_max_prompt_length_exceeded(self):
        long_query = "Please reconcile " + ("X" * (MAX_PROMPT_LENGTH + 10))
        status, body = self._post_json("/api/run", {"query": long_query})
        self.assertEqual(status, 400)
        self.assertIn("exceeds maximum allowed limit", body.get("error", ""))

    def test_run_validation_invalid_context_type(self):
        status, body = self._post_json("/api/run", {
            "query": "Reconcile my coffee",
            "context": "invalid-context-string"
        })
        self.assertEqual(status, 400)
        self.assertIn("must be a JSON object", body.get("error", ""))

    def test_run_valid_scenario_id_success(self):
        status, body = self._post_json("/api/run", {"scenario_id": "SCENARIO-01"})
        self.assertEqual(status, 200)
        self.assertIn("steps", body)
        self.assertTrue(body.get("is_complete"))

    def test_run_valid_custom_query_success(self):
        status, body = self._post_json("/api/run", {
            "query": "I bought coffee this morning for $6.75 at Blue Bottle Coffee. Reconcile this transaction."
        })
        self.assertEqual(status, 200)
        self.assertIn("steps", body)
        self.assertTrue(body.get("is_complete"))

if __name__ == "__main__":
    unittest.main()
