"""
Legacy Tool Registry & Schema Definitions for the Budgeting Agent.
"""

from typing import Dict, Any, Callable
from legacy.tools.categorize_transaction import categorize_transaction
from legacy.tools.check_against_budget_rule import check_against_budget_rule
from legacy.tools.calculate_category_variance import calculate_category_variance
from legacy.tools.propose_reallocation import propose_reallocation
from tools.flag_anomaly import flag_anomaly

LEGACY_TOOL_FUNCTIONS: Dict[str, Callable] = {
    "categorize_transaction": categorize_transaction,
    "check_against_budget_rule": check_against_budget_rule,
    "flag_anomaly": flag_anomaly,
    "calculate_category_variance": calculate_category_variance,
    "propose_reallocation": propose_reallocation
}

def execute_legacy_tool(tool_name: str, arguments: Dict[str, Any], *args, **kwargs) -> Dict[str, Any]:
    if tool_name not in LEGACY_TOOL_FUNCTIONS:
        return {
            "error": f"Tool '{tool_name}' not found in legacy registry.",
            "is_success": False
        }
    func = LEGACY_TOOL_FUNCTIONS[tool_name]
    try:
        result = func(**arguments)
        return {
            "is_success": True,
            "tool_name": tool_name,
            "output": result
        }
    except Exception as e:
        return {
            "is_success": False,
            "tool_name": tool_name,
            "error": f"Tool execution failed: {str(e)}"
        }
