"""
Unit tests for the FinancialReconciliationAgent orchestrator and trace logging in legacy budgeting mode.
"""

import unittest
from unittest.mock import patch
from agent.orchestrator import FinancialReconciliationAgent
from agent.providers.deterministic_provider import DeterministicAgentProvider
from legacy.tools.registry import execute_legacy_tool

class TestOrchestrator(unittest.TestCase):

    def setUp(self):
        self.patcher = patch("agent.orchestrator.execute_tool", side_effect=execute_legacy_tool)
        self.patcher.start()
        self.agent = FinancialReconciliationAgent(provider=DeterministicAgentProvider())

    def tearDown(self):
        self.patcher.stop()

    def test_single_transaction_flow(self):
        prompt = "Reconcile my $6.75 coffee at Blue Bottle Coffee and check my dining budget."
        context = {
            "transaction": {
                "merchant": "Blue Bottle Coffee",
                "description": "Pour-over",
                "amount": 6.75,
                "date": "2026-08-15T09:12:00"
            },
            "month": "2026-08"
        }
        trace = self.agent.run(prompt, context)
        self.assertTrue(trace.is_complete)
        self.assertGreater(len(trace.steps), 0)
        self.assertIn("categorize_transaction", trace.tool_sequence)
        self.assertIn("Dining", trace.final_recommendation)

    def test_missing_info_uncertainty_handling(self):
        prompt = "I spent money yesterday on something. Categorize it."
        trace = self.agent.run(prompt, context={})
        self.assertTrue(trace.is_complete)
        self.assertEqual(len(trace.steps), 0)  # Should not guess without info
        self.assertIn("sufficient information", trace.final_recommendation.lower())

    def test_variance_reallocation_flow(self):
        prompt = "Calculate spending variance for July 2026 and propose budget reallocation."
        context = {"month": "2026-07"}
        trace = self.agent.run(prompt, context)
        self.assertTrue(trace.is_complete)
        self.assertEqual(trace.tool_sequence, ["calculate_category_variance", "propose_reallocation"])
        self.assertIn("Reallocation", trace.final_recommendation)

if __name__ == "__main__":
    unittest.main()
