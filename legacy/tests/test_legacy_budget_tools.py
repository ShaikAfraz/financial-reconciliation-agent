"""
Tests for legacy budgeting tools.
"""

import unittest
from legacy.tools.categorize_transaction import categorize_transaction
from legacy.tools.check_against_budget_rule import check_against_budget_rule
from legacy.tools.calculate_category_variance import calculate_category_variance
from legacy.tools.propose_reallocation import propose_reallocation

class TestLegacyBudgetTools(unittest.TestCase):

    def test_legacy_categorize_transaction(self):
        res = categorize_transaction(merchant="Blue Bottle Coffee", description="Latte", amount=6.50)
        self.assertEqual(res["category"], "Dining")
        self.assertGreaterEqual(res["confidence"], 0.90)

    def test_legacy_check_against_budget_rule(self):
        res = check_against_budget_rule(category="Dining", additional_spend=50.0, month="2026-08")
        self.assertIn(res["status"], ["OK", "WARNING", "EXCEEDED"])
        self.assertEqual(res["budget_limit"], 400.0)

    def test_legacy_calculate_category_variance(self):
        res = calculate_category_variance(month="2026-08")
        self.assertEqual(res["month"], "2026-08")
        self.assertIn("category_variances", res)

    def test_legacy_propose_reallocation(self):
        res = propose_reallocation(month="2026-08")
        self.assertIn("reallocations", res)

if __name__ == "__main__":
    unittest.main()
