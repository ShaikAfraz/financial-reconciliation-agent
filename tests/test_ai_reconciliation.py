"""Grounding, failure, authorization-context, and opt-in live Gemini tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import data.db as db
import auth.service as auth_service
from core.csv_workflow import create_workflow_run
from ai.reconciliation_ai import (
    AssistantResponse, InvestigationResponse, generate_assistant_answer,
    generate_insights, get_saved_insights,
)


class ScriptedProvider:
    def __init__(self, payload=None, error=None, raw_text=None):
        self.payload = payload
        self.error = error
        self.raw_text = raw_text
        self.calls = 0

    def generate_structured(self, prompt, response_schema, **kwargs):
        self.calls += 1
        self.prompt = prompt
        self.kwargs = kwargs
        if self.error:
            raise self.error
        if self.raw_text is not None:
            return {"text": self.raw_text, "parsed": None, "cached": False}
        return {"text": "{}", "parsed": response_schema.model_validate(self.payload), "cached": False}


class TestAIReconciliation(unittest.TestCase):
    def setUp(self):
        self.old_db = db.get_active_db()
        self.tmp = tempfile.TemporaryDirectory()
        db.set_active_db(Path(self.tmp.name) / "ai-test.sqlite")
        db.init_database()
        self.user = auth_service.create_user("ai_tester", "TestPassword12345!", role="analyst")
        self.run = create_workflow_run(self.user["id"], "AI test run")
        self.run_id = self.run["id"]
        self.results = {
            "status": "reconciled",
            "summary": {
                "bank_records": 1, "ledger_records": 1, "matched_items": 0,
                "unmatched_bank_records": 1, "unmatched_ledger_records": 1,
                "review_required_items": 2, "bank_net_activity_cents": 12500,
                "ledger_net_activity_cents": 12500, "net_activity_difference_cents": 0,
                "matched_amount_difference_cents": 0,
            },
            "matches": [],
            "exceptions": [
                {"record_id": "BANK-RUN-0001", "side": "bank", "status": "open", "reason": "missing_record",
                 "date": "2026-10-01", "amount_cents": 12500, "description": "ACME OFFICE SUPPLY"},
                {"record_id": "LEDGER-RUN-0001", "side": "ledger", "status": "open", "reason": "missing_record",
                 "date": "2026-10-01", "amount_cents": 12500, "description": "ACME OFFICE SUPPLIES"},
            ],
        }

    def tearDown(self):
        db.set_active_db(self.old_db)
        self.tmp.cleanup()

    def insight_payload(self, ids=("BANK-RUN-0001", "LEDGER-RUN-0001")):
        return {"issues": [{
            "issue_type": "possible_match", "priority": "medium", "transaction_ids": list(ids),
            "observed_evidence": [{"record_id": ids[0], "field": "amount_cents"}],
            "possible_explanation": "The descriptions may refer to the same supplier purchase.",
            "recommended_action": "Compare the original supplier invoice before deciding.",
            "missing_information": ["Supplier invoice"], "review_required": True,
        }]}

    def test_missing_credentials_returns_explicit_rule_based_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            result = generate_insights(self.run_id, self.user["id"], self.results)
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["provider"], "rule_based")
        self.assertIn("not AI", result["label"])
        self.assertEqual(result["facts"]["matched_items"], 0)

    def test_successful_structured_insight_is_grounded_and_persisted(self):
        provider = ScriptedProvider(self.insight_payload())
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}):
            result = generate_insights(self.run_id, self.user["id"], self.results, provider=provider)
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["cached"])
        self.assertEqual(result["issues"][0]["observed_evidence"][0]["value"], 12500)
        self.assertEqual(result["issues"][0]["review_required"], True)
        self.assertEqual(provider.calls, 1)
        self.assertNotIn("reference", provider.prompt)
        self.assertIsNotNone(get_saved_insights(self.run_id, self.user["id"]))

    def test_nonexistent_transaction_reference_is_rejected(self):
        provider = ScriptedProvider(self.insight_payload(("BANK-DOES-NOT-EXIST",)))
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}):
            result = generate_insights(self.run_id, self.user["id"], self.results, provider=provider)
        self.assertEqual(result["status"], "invalid_response")
        self.assertEqual(result["provider"], "rule_based")
        self.assertIsNone(get_saved_insights(self.run_id, self.user["id"]))

    def test_malformed_model_output_falls_back_without_persisting_ai(self):
        provider = ScriptedProvider(raw_text="not-json")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}):
            result = generate_insights(self.run_id, self.user["id"], self.results, provider=provider)
        self.assertEqual(result["status"], "invalid_response")
        self.assertIsNone(get_saved_insights(self.run_id, self.user["id"]))

    def test_api_rate_limit_is_reported_and_falls_back(self):
        provider = ScriptedProvider(error=RuntimeError("429 RESOURCE_EXHAUSTED"))
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}):
            result = generate_insights(self.run_id, self.user["id"], self.results, provider=provider)
        self.assertEqual(result["status"], "rate_limited")
        self.assertIn("not AI", result["label"])

    def test_api_timeout_is_reported_and_falls_back(self):
        provider = ScriptedProvider(error=RuntimeError("request timed out"))
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}):
            result = generate_insights(self.run_id, self.user["id"], self.results, provider=provider)
        self.assertEqual(result["status"], "timeout")
        self.assertIn("not AI", result["label"])

    def test_assistant_rejects_unknown_record_ids(self):
        provider = ScriptedProvider({"answer": "Review BANK-OTHER-2.", "transaction_ids": ["BANK-OTHER-2"], "unknowns": []})
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}):
            result = generate_assistant_answer(self.run_id, self.user["id"], "Which item?", self.results, provider)
        self.assertEqual(result["status"], "invalid_response")
        self.assertEqual(result["transaction_ids"], [])

    @unittest.skipUnless(os.environ.get("GEMINI_API_KEY") and os.environ.get("RUN_LIVE_GEMINI_TEST") == "1",
                         "Live Gemini integration requires GEMINI_API_KEY and RUN_LIVE_GEMINI_TEST=1")
    def test_live_google_genai_integration_uses_synthetic_records(self):
        result = generate_insights(self.run_id, self.user["id"], self.results)
        self.assertEqual(result["provider"], "google_genai")
        self.assertFalse(result["cached"])

    @unittest.skipUnless(os.environ.get("GEMINI_API_KEY") and os.environ.get("RUN_LIVE_GEMINI_TEST") == "1",
                         "Live Gemini integration requires GEMINI_API_KEY and RUN_LIVE_GEMINI_TEST=1")
    def test_live_google_genai_assistant_answer(self):
        result = generate_assistant_answer(self.run_id, self.user["id"], "Which bank records remain unmatched?", self.results)
        self.assertEqual(result["provider"], "google_genai")
        self.assertEqual(result["status"], "success")
        self.assertIn("BANK-RUN-0001", result["transaction_ids"])


if __name__ == "__main__":
    unittest.main()
