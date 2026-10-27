"""
Unit tests for the financial reconciliation tools.
Verifies active tools and legacy tools in their respective modules.
"""

import unittest
from tools.flag_anomaly import flag_anomaly
from tools.registry import get_tool_definitions, execute_tool
from legacy.tools.categorize_transaction import categorize_transaction
from legacy.tools.check_against_budget_rule import check_against_budget_rule
from legacy.tools.calculate_category_variance import calculate_category_variance
from legacy.tools.propose_reallocation import propose_reallocation

class TestTools(unittest.TestCase):

    def test_active_tool_registry_unregisters_budget_tools(self):
        active_tools = [t["name"] for t in get_tool_definitions()]
        self.assertNotIn("categorize_transaction", active_tools)
        self.assertNotIn("check_against_budget_rule", active_tools)
        self.assertNotIn("calculate_category_variance", active_tools)
        self.assertNotIn("propose_reallocation", active_tools)
        self.assertIn("flag_anomaly", active_tools)

        # Executing unregistered tool returns not found
        res = execute_tool("categorize_transaction", {})
        self.assertFalse(res["is_success"])
        self.assertIn("not found", res["error"])

    def test_tool1_categorize_transaction(self):
        # Known merchant match
        res1 = categorize_transaction(merchant="Blue Bottle Coffee", description="Latte", amount=6.50)
        self.assertEqual(res1["category"], "Dining")
        self.assertGreaterEqual(res1["confidence"], 0.90)

        # Grocery match
        res2 = categorize_transaction(merchant="Whole Foods Market", description="Groceries", amount=94.20)
        self.assertEqual(res2["category"], "Groceries")

        # Missing info / ambiguous
        res3 = categorize_transaction(merchant="", description="")
        self.assertTrue(res3["is_ambiguous"])
        self.assertEqual(res3["category"], "Uncategorized")

    def test_tool2_check_against_budget_rule(self):
        # Known category
        res = check_against_budget_rule(category="Dining", additional_spend=50.0, month="2026-08")
        self.assertIn(res["status"], ["OK", "WARNING", "EXCEEDED"])
        self.assertIn("utilization_pct", res)
        self.assertEqual(res["budget_limit"], 400.0)

        # Invalid category check
        err_res = check_against_budget_rule(category="SpaceshipRepairs")
        self.assertEqual(err_res["status"], "ERROR")

    def test_tool3_flag_anomaly(self):
        # Normal amount
        norm = flag_anomaly(merchant="Chipotle Mexican Grill", amount=14.50, category="Dining")
        self.assertFalse(norm["is_anomaly"])
        self.assertEqual(norm["anomaly_type"], "none")

        # Severe spike
        spike = flag_anomaly(merchant="Le Bernardin NYC", amount=745.00, category="Dining")
        self.assertTrue(spike["is_anomaly"])
        self.assertEqual(spike["anomaly_type"], "amount_spike")
        self.assertEqual(spike["severity"], "HIGH")

        # Suspicious merchant
        susp = flag_anomaly(merchant="BitPay Global Valparaiso CL", amount=289.50, category="Shopping")
        self.assertTrue(susp["is_anomaly"])
        self.assertEqual(susp["anomaly_type"], "suspicious_merchant")

    def test_tool4_calculate_category_variance(self):
        res = calculate_category_variance(month="2026-08")
        self.assertEqual(res["month"], "2026-08")
        self.assertIn("category_variances", res)
        self.assertIn("net_variance", res)
        self.assertGreater(len(res["category_variances"]), 0)

        # Check structure of each category item
        first = res["category_variances"][0]
        self.assertIn("category", first)
        self.assertIn("budget_limit", first)
        self.assertIn("actual_spend", first)
        self.assertIn("status", first)

    def test_tool5_propose_reallocation(self):
        # Test reallocation with month data
        res = propose_reallocation(month="2026-08")
        self.assertIn("reallocations", res)
        self.assertIn("is_fully_covered", res)
        self.assertIn("recommendation_summary", res)

        # Ensure non-discretionary categories are NOT used as donors
        for r in res["reallocations"]:
            donor = r["from_category"]
            self.assertNotIn(donor, ["Housing", "Utilities", "Healthcare"])

if __name__ == "__main__":
    unittest.main()
