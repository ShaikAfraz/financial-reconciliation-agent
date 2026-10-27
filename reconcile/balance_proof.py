"""
Balance Proof Module for Two-Sided Financial Reconciliation.
Verifies the fundamental reconciliation proof in integer cents:
Ending Bank Balance +/- Reconciling Items = Adjusted Book Balance.
Computes and returns the exact integer cents difference.
"""

from typing import Dict, Any, List, Set

def compute_balance_proof(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    matches: List[Dict[str, Any]],
    opening_bank_balance_cents: int = 0,
    opening_book_balance_cents: int = 0
) -> Dict[str, Any]:
    """
    Computes the authoritative Two-Sided Balance Proof in integer cents.

    Formulation:
      Ending Bank Balance = Opening Bank + Total Bank Statement Activity
      Ending Book Balance = Opening Book + Total General Ledger Activity

      Reconciling Items:
        - Unmatched Ledger Records (In-transit / Outstanding items)
        - Unmatched Bank Records (Bank charges / Unposted deposits)
        - Fee Adjustments (Gross vs Net processing fees)

      Adjusted Bank Balance = Ending Bank Balance + Unmatched Ledger Items
      Adjusted Book Balance = Ending Book Balance + Unmatched Bank Items - Fee Adjustments

      Difference = Adjusted Bank Balance - Adjusted Book Balance

    Returns:
      Dict with all line items and difference_cents (0 when perfectly reconciled).
    """
    # Strict integer cents type validation
    for val in (opening_bank_balance_cents, opening_book_balance_cents):
        if not isinstance(val, int) or isinstance(val, bool):
            raise TypeError(f"Opening balances must be integer cents, got {type(val).__name__}")

    # 1. Ending balances
    bank_activity_cents = 0
    for b in bank_records:
        amt = b.get("amount_cents", 0)
        if not isinstance(amt, int) or isinstance(amt, bool):
            raise TypeError(f"bank record amount_cents must be int, got {type(amt).__name__}")
        bank_activity_cents += amt

    book_activity_cents = 0
    for l in ledger_records:
        amt = l.get("amount_cents", 0)
        if not isinstance(amt, int) or isinstance(amt, bool):
            raise TypeError(f"ledger record amount_cents must be int, got {type(amt).__name__}")
        book_activity_cents += amt

    ending_bank_balance_cents = opening_bank_balance_cents + bank_activity_cents
    ending_book_balance_cents = opening_book_balance_cents + book_activity_cents

    # 2. Extract matched IDs and fees from matches
    matched_bank_ids: Set[str] = set()
    matched_ledger_ids: Set[str] = set()
    fee_adjustments_cents = 0

    for m in matches:
        if m.get("status") in ("matched", "suggested", "approved"):
            for bid in m.get("bank_record_ids", []):
                matched_bank_ids.add(bid)
            for lid in m.get("ledger_record_ids", []):
                matched_ledger_ids.add(lid)
            fee = m.get("metadata", {}).get("fee_cents", 0) if isinstance(m.get("metadata"), dict) else 0
            if not isinstance(fee, int) or isinstance(fee, bool):
                raise TypeError(f"fee_cents must be integer cents, got {type(fee).__name__}")
            fee_adjustments_cents += fee

    # 3. Reconciling items
    unmatched_ledger_cents = 0
    unmatched_ledger_count = 0
    for l in ledger_records:
        if l["id"] not in matched_ledger_ids:
            unmatched_ledger_cents += int(l["amount_cents"])
            unmatched_ledger_count += 1

    unmatched_bank_cents = 0
    unmatched_bank_count = 0
    for b in bank_records:
        if b["id"] not in matched_bank_ids:
            unmatched_bank_cents += int(b["amount_cents"])
            unmatched_bank_count += 1

    # 4. Adjusted balances
    adjusted_bank_balance_cents = ending_bank_balance_cents + unmatched_ledger_cents
    adjusted_book_balance_cents = ending_book_balance_cents + unmatched_bank_cents - fee_adjustments_cents

    # 5. Difference
    difference_cents = adjusted_bank_balance_cents - adjusted_book_balance_cents

    return {
        "opening_bank_balance_cents": opening_bank_balance_cents,
        "opening_book_balance_cents": opening_book_balance_cents,
        "ending_bank_balance_cents": ending_bank_balance_cents,
        "ending_book_balance_cents": ending_book_balance_cents,
        "unmatched_ledger_records_count": unmatched_ledger_count,
        "unmatched_ledger_cents": unmatched_ledger_cents,
        "unmatched_bank_records_count": unmatched_bank_count,
        "unmatched_bank_cents": unmatched_bank_cents,
        "fee_adjustments_cents": fee_adjustments_cents,
        "adjusted_bank_balance_cents": adjusted_bank_balance_cents,
        "adjusted_book_balance_cents": adjusted_book_balance_cents,
        "difference_cents": difference_cents,
        "is_balanced": difference_cents == 0
    }
