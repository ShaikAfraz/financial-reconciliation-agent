"""
Legacy package containing deprecated personal budgeting tools, scenarios, and tests.
These modules are preserved for reference and backward compatibility but are
unregistered from the active two-sided bank-vs-ledger reconciliation tool registry.
"""

from legacy.tools.categorize_transaction import categorize_transaction
from legacy.tools.check_against_budget_rule import check_against_budget_rule
from legacy.tools.calculate_category_variance import calculate_category_variance
from legacy.tools.propose_reallocation import propose_reallocation

__all__ = [
    "categorize_transaction",
    "check_against_budget_rule",
    "calculate_category_variance",
    "propose_reallocation",
]
