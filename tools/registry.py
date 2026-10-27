"""
Tool Registry & Schema Definitions for the Autonomous Financial Reconciliation Agent.
Defines active pipeline tools and read-only LLM-facing tools:
Pipeline Tools:
- ingest_records
- generate_candidates
- adjudicate_item
- validate_decision
- commit_match (deterministic only, requires a validator-pass token)
- compute_balance_proof
- list_review_queue

LLM-Facing Tools (Read-Only, Hard Cap of 3 Calls per Item):
- get_record
- get_counterparty_history
Note: The LLM cannot call commit_match.
"""

from typing import Dict, Any, Callable, List
from tools.flag_anomaly import flag_anomaly
from tools.reconcile_tools import (
    ingest_records_tool,
    generate_candidates_tool,
    adjudicate_item_tool,
    validate_decision_tool,
    commit_match_tool,
    compute_balance_proof_tool,
    list_review_queue_tool,
    get_record_tool,
    get_counterparty_history_tool,
    generate_candidates_tool as generate_candidate_pairs_tool,
    compute_balance_proof_tool as verify_balance_proof_tool,
)
from reconcile.normalize import analyze_descriptor
from reconcile.baselines import reconcile_b0, reconcile_b1

TOOL_DEFINITIONS = [
    {
        "name": "ingest_records",
        "description": "Ingests bank and general ledger records with strict integer cents enforcement.",
        "parameters": {
            "type": "object",
            "properties": {
                "bank_records": {"type": "array", "description": "List of bank statement transaction record dicts."},
                "ledger_records": {"type": "array", "description": "List of accounting ledger record dicts."}
            },
            "required": ["bank_records", "ledger_records"]
        }
    },
    {
        "name": "generate_candidates",
        "description": "Generates candidate match pairs using date window and amount blocking with deterministic features.",
        "parameters": {
            "type": "object",
            "properties": {
                "bank_records": {"type": "array", "description": "Bank records."},
                "ledger_records": {"type": "array", "description": "Ledger records."},
                "date_window_days": {"type": "integer", "description": "Maximum allowed date discrepancy (days)."},
                "fee_tolerance_cents": {"type": "integer", "description": "Maximum allowed fee discrepancy (cents)."}
            },
            "required": ["bank_records", "ledger_records"]
        }
    },
    {
        "name": "adjudicate_item",
        "description": "Adjudicates an ambiguous or unmatched transaction using isolated LLM sampling and deterministic confidence blend.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_item": {"type": "object", "description": "The item to reconcile."},
                "candidate_records": {"type": "array", "description": "Candidate records from opposite side."},
                "deterministic_features": {"type": "array", "description": "Deterministic features for candidates."},
                "item_side": {"type": "string", "enum": ["bank", "ledger"], "description": "Side of target item."},
                "adjudication_mode": {"type": "string", "enum": ["single_shot", "tool_loop"], "description": "Adjudication mode."}
            },
            "required": ["target_item", "candidate_records"]
        }
    },
    {
        "name": "validate_decision",
        "description": "Deterministically validates an adjudication output. Generates a validator-pass token if valid.",
        "parameters": {
            "type": "object",
            "properties": {
                "adjudication_response": {"type": "object", "description": "The candidate adjudication decision."},
                "target_item": {"type": "object", "description": "The original target item."},
                "candidate_records": {"type": "array", "description": "Candidate records pool."},
                "deterministic_features": {"type": "array", "description": "Deterministic features."},
                "fee_tolerance_cents": {"type": "integer", "description": "Configured fee tolerance."}
            },
            "required": ["adjudication_response", "target_item", "candidate_records"]
        }
    },
    {
        "name": "commit_match",
        "description": "Authoritative state change: commits approved match to the database. Deterministic only; requires valid validator_token. Cannot be called by LLM.",
        "parameters": {
            "type": "object",
            "properties": {
                "match_id": {"type": "string", "description": "Unique identifier for the match."},
                "bank_ids": {"type": "array", "items": {"type": "string"}, "description": "Bank record IDs in match."},
                "ledger_ids": {"type": "array", "items": {"type": "string"}, "description": "Ledger record IDs in match."},
                "status": {"type": "string", "description": "Match status (e.g. 'matched')."},
                "method": {"type": "string", "description": "Reconciliation method used."},
                "confidence": {"type": "number", "description": "Composite confidence score."},
                "validator_token": {"type": "string", "description": "Cryptographic validator-pass token."}
            },
            "required": ["match_id", "bank_ids", "ledger_ids", "status", "method", "validator_token"]
        }
    },
    {
        "name": "compute_balance_proof",
        "description": "Calculates the authoritative two-sided balance proof in integer cents (Ending Bank +/- Reconciling Items = Adjusted Book).",
        "parameters": {
            "type": "object",
            "properties": {
                "bank_records": {"type": "array", "description": "List of bank records."},
                "ledger_records": {"type": "array", "description": "List of ledger records."},
                "matches": {"type": "array", "description": "List of matched groups."},
                "opening_bank_balance_cents": {"type": "integer", "description": "Opening bank balance in cents."},
                "opening_book_balance_cents": {"type": "integer", "description": "Opening book balance in cents."}
            },
            "required": ["bank_records", "ledger_records", "matches"]
        }
    },
    {
        "name": "list_review_queue",
        "description": "Lists pending exceptions and human auditor review queue items.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum number of items to retrieve (default: 50)."},
                "status": {"type": "string", "description": "Filter by exception status."}
            }
        }
    },
    {
        "name": "flag_anomaly",
        "description": "Evaluates a transaction against defined threshold rules to detect anomalies.",
        "parameters": {
            "type": "object",
            "properties": {
                "merchant": {"type": "string"},
                "amount": {"type": "number"},
                "category": {"type": "string"},
                "date": {"type": "string"},
                "description": {"type": "string"}
            },
            "required": ["merchant", "amount"]
        }
    }
]

# Read-only tools exposed to the LLM (hard cap of 3 calls per item)
LLM_TOOL_DEFINITIONS = [
    {
        "name": "get_record",
        "description": "Read-only query: retrieves full transaction details for a bank or ledger record by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string", "description": "The transaction record ID to inspect."},
                "side": {"type": "string", "enum": ["bank", "ledger", "auto"], "description": "Side to query."}
            },
            "required": ["record_id"]
        }
    },
    {
        "name": "get_counterparty_history",
        "description": "Read-only query: retrieves historical transactions matching a counterparty or merchant keyword.",
        "parameters": {
            "type": "object",
            "properties": {
                "counterparty": {"type": "string", "description": "Merchant or counterparty keyword to search."},
                "limit": {"type": "integer", "description": "Maximum history items to return (default: 10)."}
            },
            "required": ["counterparty"]
        }
    }
]

TOOL_FUNCTIONS: Dict[str, Callable] = {
    "ingest_records": ingest_records_tool,
    "generate_candidates": generate_candidates_tool,
    "adjudicate_item": adjudicate_item_tool,
    "validate_decision": validate_decision_tool,
    "commit_match": commit_match_tool,
    "compute_balance_proof": compute_balance_proof_tool,
    "list_review_queue": list_review_queue_tool,
    "get_record": get_record_tool,
    "get_counterparty_history": get_counterparty_history_tool,
    "flag_anomaly": flag_anomaly,
    # Backward compatibility aliases
    "normalize_descriptor": analyze_descriptor,
    "generate_candidate_pairs": generate_candidate_pairs_tool,
    "reconcile_records": lambda bank_records, ledger_records, system="B1": reconcile_b1(bank_records, ledger_records) if system.upper()=="B1" else reconcile_b0(bank_records, ledger_records),
    "verify_balance_proof": verify_balance_proof_tool,
}

def get_tool_definitions() -> list:
    """Returns all active pipeline tools."""
    return TOOL_DEFINITIONS

def get_llm_tool_definitions() -> list:
    """
    Returns ONLY the read-only LLM-facing tools.
    The LLM CANNOT call commit_match or mutating tools.
    """
    return LLM_TOOL_DEFINITIONS

def execute_tool(tool_name: str, arguments: Dict[str, Any], conn: Any = None) -> Dict[str, Any]:
    """
    Executes an active tool by name with arguments and handles error trapping.
    Supports passing an active database connection transaction for commit_match.
    """
    if tool_name not in TOOL_FUNCTIONS:
        return {
            "error": f"Tool '{tool_name}' not found. Available active tools: {list(TOOL_FUNCTIONS.keys())}",
            "is_success": False
        }

    func = TOOL_FUNCTIONS[tool_name]
    try:
        # Pass conn if tool accepts it
        if tool_name in ("commit_match", "ingest_records") and conn is not None:
            result = func(**arguments, conn=conn)
        else:
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
