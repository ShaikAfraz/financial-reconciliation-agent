"""
Tool Registry & Schema Definitions for the Autonomous Financial Reconciliation Agent.
Contains the fixed 5-tool definitions and execution dispatcher.
"""

from typing import Dict, Any, Callable
from tools.categorize_transaction import categorize_transaction
from tools.check_against_budget_rule import check_against_budget_rule
from tools.flag_anomaly import flag_anomaly
from tools.calculate_category_variance import calculate_category_variance
from tools.propose_reallocation import propose_reallocation

TOOL_DEFINITIONS = [
    {
        "name": "categorize_transaction",
        "description": "Assigns a spending category to a transaction based on merchant name, description keywords, and amount heuristics.",
        "parameters": {
            "type": "object",
            "properties": {
                "merchant": {
                    "type": "string",
                    "description": "The merchant or vendor name (e.g. 'Blue Bottle Coffee', 'Uber', 'Trader Joe\\'s')."
                },
                "description": {
                    "type": "string",
                    "description": "Additional text details or itemized description of the transaction."
                },
                "amount": {
                    "type": "number",
                    "description": "The transaction amount in USD."
                }
            },
            "required": ["merchant"]
        }
    },
    {
        "name": "check_against_budget_rule",
        "description": "Compares a category's cumulative monthly spend against its defined budget limit and alert thresholds (Warning at 80%, Exceeded at 100%).",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "The spending category to check (e.g. 'Dining', 'Groceries', 'Utilities')."
                },
                "additional_spend": {
                    "type": "number",
                    "description": "Optional incoming transaction amount to project post-transaction budget status."
                },
                "month": {
                    "type": "string",
                    "description": "The target budget month formatted as 'YYYY-MM' (default '2026-08')."
                }
            },
            "required": ["category"]
        }
    },
    {
        "name": "flag_anomaly",
        "description": "Evaluates a transaction against defined threshold rules to detect statistical amount spikes, rapid duplicate billing, suspicious merchants, or frequency bursts.",
        "parameters": {
            "type": "object",
            "properties": {
                "merchant": {
                    "type": "string",
                    "description": "The merchant or vendor name."
                },
                "amount": {
                    "type": "number",
                    "description": "The transaction amount in USD."
                },
                "category": {
                    "type": "string",
                    "description": "The spending category (if already determined)."
                },
                "date": {
                    "type": "string",
                    "description": "ISO timestamp of the transaction (e.g. '2026-08-15T09:12:00')."
                },
                "description": {
                    "type": "string",
                    "description": "Transaction description or line item note."
                }
            },
            "required": ["merchant", "amount"]
        }
    },
    {
        "name": "calculate_category_variance",
        "description": "Computes budget versus actual spending variance, dollar differences, and percentage over/under for all categories in a specified month.",
        "parameters": {
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "description": "Target month in 'YYYY-MM' format (e.g. '2026-08')."
                }
            },
            "required": ["month"]
        }
    },
    {
        "name": "propose_reallocation",
        "description": "Given variance data, calculates a mathematically sound, priority-constrained budget rebalancing plan from surplus discretionary pools to cover deficit categories.",
        "parameters": {
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "description": "Target month in 'YYYY-MM' format (e.g. '2026-08')."
                },
                "variance_data": {
                    "type": "object",
                    "description": "Optional upstream output from calculate_category_variance."
                }
            },
            "required": ["month"]
        }
    }
]

TOOL_FUNCTIONS: Dict[str, Callable] = {
    "categorize_transaction": categorize_transaction,
    "check_against_budget_rule": check_against_budget_rule,
    "flag_anomaly": flag_anomaly,
    "calculate_category_variance": calculate_category_variance,
    "propose_reallocation": propose_reallocation
}

def get_tool_definitions() -> list:
    """Returns the list of 5 tool definitions in standard tool-calling format."""
    return TOOL_DEFINITIONS

def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a tool by name with arguments and handles error trapping.
    """
    if tool_name not in TOOL_FUNCTIONS:
        return {
            "error": f"Tool '{tool_name}' not found. Available tools: {list(TOOL_FUNCTIONS.keys())}",
            "is_success": False
        }

    func = TOOL_FUNCTIONS[tool_name]
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
