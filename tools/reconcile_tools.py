"""
Two-Sided Financial Reconciliation Tools.
Implements the 7 active pipeline tools plus read-only LLM inquiry tools:
1. ingest_records
2. generate_candidates
3. adjudicate_item
4. validate_decision
5. commit_match (deterministic only, requires a validator-pass token)
6. compute_balance_proof
7. list_review_queue
Read-only LLM tools: get_record, get_counterparty_history (hard capped at 3 calls).
The LLM cannot call commit_match.
"""

import sqlite3
from typing import Dict, Any, List, Optional
import data.db as db
from reconcile.candidates import generate_candidate_pairs
from reconcile.validator import validate_adjudication_response, verify_validator_token, generate_validator_token
from reconcile.adjudicator import adjudicate_item
from reconcile.balance_proof import compute_balance_proof
from reconcile.baselines import load_reconcile_config

# ----------------------------------------------------------------------
# 1. Ingest Records Tool
# ----------------------------------------------------------------------
def ingest_records_tool(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Ingests bank and general ledger records into the database.
    Strictly enforces integer cents for all monetary values.
    """
    for b in bank_records:
        amt = b.get("amount_cents")
        if not isinstance(amt, int) or isinstance(amt, bool):
            raise TypeError(f"Bank record {b.get('id')} amount_cents must be int, got {type(amt).__name__}")
        db.insert_bank_record(
            rec_id=b["id"],
            date=b.get("date", ""),
            amount_cents=amt,
            description=b.get("description", ""),
            reference=b.get("reference")
        )

    for l in ledger_records:
        amt = l.get("amount_cents")
        if not isinstance(amt, int) or isinstance(amt, bool):
            raise TypeError(f"Ledger record {l.get('id')} amount_cents must be int, got {type(amt).__name__}")
        db.insert_ledger_record(
            rec_id=l["id"],
            date=l.get("date", ""),
            amount_cents=amt,
            description=l.get("description", ""),
            reference=l.get("reference")
        )

    return {
        "status": "success",
        "bank_ingested_count": len(bank_records),
        "ledger_ingested_count": len(ledger_records)
    }

# ----------------------------------------------------------------------
# 2. Generate Candidates Tool
# ----------------------------------------------------------------------
def generate_candidates_tool(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    date_window_days: int = 5,
    fee_tolerance_cents: int = 500
) -> Dict[str, Any]:
    """
    Generates candidate match pairs using date window and amount blocking
    along with rapidfuzz deterministic similarity features.
    """
    pairs = generate_candidate_pairs(
        bank_records=bank_records,
        ledger_records=ledger_records,
        date_window_days=date_window_days,
        fee_tolerance_cents=fee_tolerance_cents
    )
    return {
        "total_candidates": len(pairs),
        "candidates": pairs
    }

# ----------------------------------------------------------------------
# 3. Adjudicate Item Tool
# ----------------------------------------------------------------------
def adjudicate_item_tool(
    target_item: Dict[str, Any],
    candidate_records: List[Dict[str, Any]],
    deterministic_features: Optional[List[Dict[str, Any]]] = None,
    item_side: str = "bank",
    adjudication_mode: Optional[str] = None,
    provider: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Adjudicates an ambiguous or unmatched transaction via isolated LLM sampling,
    computing composite confidence from agreement and deterministic features.
    """
    cfg = load_reconcile_config()
    mode = adjudication_mode or cfg.get("adjudication_mode", "single_shot")
    k = int(cfg.get("adjudication_sample_k", 3))
    threshold = float(cfg.get("adjudication_confidence_threshold", 0.85))
    fee_tol = int(cfg.get("max_fee_cents", 1000))

    outcome = adjudicate_item(
        target_item=target_item,
        candidate_records=candidate_records,
        deterministic_features=deterministic_features,
        item_side=item_side,
        provider=provider,
        k=k,
        confidence_threshold=threshold,
        fee_tolerance_cents=fee_tol,
        adjudication_mode=mode
    )
    return outcome.model_dump()

# ----------------------------------------------------------------------
# 4. Validate Decision Tool
# ----------------------------------------------------------------------
def validate_decision_tool(
    adjudication_response: Dict[str, Any],
    target_item: Dict[str, Any],
    candidate_records: List[Dict[str, Any]],
    deterministic_features: Optional[List[Dict[str, Any]]] = None,
    fee_tolerance_cents: int = 1000
) -> Dict[str, Any]:
    """
    Deterministic validator that runs arithmetic checks, evidence grounding,
    and schema checks. Generates and returns a cryptographic validator_token
    ONLY if all checks pass.
    """
    val_result = validate_adjudication_response(
        response_obj=adjudication_response,
        target_item=target_item,
        candidate_records=candidate_records,
        deterministic_features=deterministic_features,
        fee_tolerance_cents=fee_tolerance_cents
    )
    return val_result.model_dump()

# ----------------------------------------------------------------------
# 5. Commit Match Tool (Deterministic Only - Requires Validator Token)
# ----------------------------------------------------------------------
def commit_match_tool(
    match_id: str,
    bank_ids: List[str],
    ledger_ids: List[str],
    status: str,
    method: str,
    confidence: float,
    validator_token: str,
    conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """
    Authoritative state change: commits approved match to the database.
    HARD SECURITY CONSTRAINT:
    - Deterministic only.
    - Requires a valid cryptographic validator-pass token.
    - LLM cannot call this tool.
    """
    if not validator_token or not isinstance(validator_token, str):
        raise PermissionError("commit_match requires a valid validator-pass token. Unvalidated matches cannot be committed.")

    # Verify validator token
    token_valid = False
    if bank_ids and ledger_ids:
        # Check bank_ids[0] -> ledger_ids
        if verify_validator_token(validator_token, bank_ids[0], ledger_ids):
            token_valid = True
        # Check ledger_ids[0] -> bank_ids
        elif verify_validator_token(validator_token, ledger_ids[0], bank_ids):
            token_valid = True

    if not token_valid:
        raise PermissionError("Invalid or forged validator-pass token. commit_match rejected.")

    # State update inside transaction
    should_close = False
    if conn is None:
        conn = db.get_connection()
        should_close = True

    try:
        db.insert_match(
            match_id=match_id,
            status=status,
            method=method,
            confidence=confidence,
            conn=conn
        )
        for bid in bank_ids:
            db.add_match_member(match_id=match_id, side="bank", record_id=bid, conn=conn)
        for lid in ledger_ids:
            db.add_match_member(match_id=match_id, side="ledger", record_id=lid, conn=conn)

        if should_close:
            conn.commit()
    finally:
        if should_close:
            conn.close()

    return {
        "status": "committed",
        "match_id": match_id,
        "bank_ids": bank_ids,
        "ledger_ids": ledger_ids,
        "confidence": confidence
    }

# ----------------------------------------------------------------------
# 6. Compute Balance Proof Tool
# ----------------------------------------------------------------------
def compute_balance_proof_tool(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    matches: List[Dict[str, Any]],
    opening_bank_balance_cents: int = 0,
    opening_book_balance_cents: int = 0
) -> Dict[str, Any]:
    """
    Computes the authoritative Two-Sided Balance Proof in integer cents:
    Ending Bank Balance +/- Reconciling Items = Adjusted Book Balance.
    """
    return compute_balance_proof(
        bank_records=bank_records,
        ledger_records=ledger_records,
        matches=matches,
        opening_bank_balance_cents=opening_bank_balance_cents,
        opening_book_balance_cents=opening_book_balance_cents
    )

# ----------------------------------------------------------------------
# 7. List Review Queue Tool
# ----------------------------------------------------------------------
def list_review_queue_tool(
    limit: int = 50,
    status: Optional[str] = None
) -> Dict[str, Any]:
    """Queries pending exceptions and human auditor reviews."""
    conn = db.get_connection()
    cursor = conn.cursor()

    if status:
        cursor.execute("SELECT * FROM exceptions WHERE status = ? LIMIT ?", (status, limit))
    else:
        cursor.execute("SELECT * FROM exceptions LIMIT ?", (limit,))
    exceptions = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM reviews ORDER BY ts DESC LIMIT ?", (limit,))
    reviews = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {
        "exceptions": exceptions,
        "reviews": reviews,
        "total_pending": len(exceptions) + len(reviews)
    }

# ----------------------------------------------------------------------
# Read-Only LLM Inquiry Tools (Exposed to LLM with 3-call hard cap)
# ----------------------------------------------------------------------
def get_record_tool(record_id: str, side: str = "auto") -> Dict[str, Any]:
    """Read-only tool: fetches a single bank or ledger transaction record by ID."""
    conn = db.get_connection()
    cursor = conn.cursor()

    rec = None
    if side in ("bank", "auto"):
        cursor.execute("SELECT * FROM bank_records WHERE id = ?", (record_id,))
        row = cursor.fetchone()
        if row:
            rec = dict(row)
            rec["side"] = "bank"

    if rec is None and side in ("ledger", "auto"):
        cursor.execute("SELECT * FROM ledger_records WHERE id = ?", (record_id,))
        row = cursor.fetchone()
        if row:
            rec = dict(row)
            rec["side"] = "ledger"

    conn.close()
    if rec:
        return {"found": True, "record": rec}
    return {"found": False, "record": None, "message": f"Record '{record_id}' not found."}

def get_counterparty_history_tool(counterparty: str, limit: int = 10) -> Dict[str, Any]:
    """Read-only tool: queries transaction history for a vendor or counterparty string."""
    conn = db.get_connection()
    cursor = conn.cursor()
    query_pattern = f"%{counterparty.strip()}%"

    cursor.execute("""
    SELECT id, date, amount_cents, description, reference, 'bank' as side
    FROM bank_records WHERE description LIKE ?
    UNION ALL
    SELECT id, date, amount_cents, description, reference, 'ledger' as side
    FROM ledger_records WHERE description LIKE ?
    ORDER BY date DESC LIMIT ?
    """, (query_pattern, query_pattern, limit))

    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {
        "counterparty": counterparty,
        "count": len(rows),
        "history": rows
    }
