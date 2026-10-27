"""
Candidate Generation and Feature Extraction for Two-Sided Financial Reconciliation.
Supports:
1. Blocking on date window (default ±5 days) and amount.
2. Exact reference matching.
3. Configurable fee tolerance.
4. Bounded subset-sum for N:1 and 1:N splits (capped at max 4 members and pool size cap).
5. Per-candidate deterministic features (amount_delta_cents, date_delta_days, token_set_ratio, ref_match).
"""

import itertools
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple
from rapidfuzz import fuzz

from reconcile.normalize import normalize_descriptor, extract_reference

def parse_iso_date(d_val: str) -> date:
    """Parses date string ('YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS') into date object."""
    if not d_val:
        return date(1970, 1, 1)
    if "T" in d_val:
        return datetime.fromisoformat(d_val).date()
    return datetime.strptime(d_val[:10], "%Y-%m-%d").date()

def generate_candidate_pairs(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    date_window_days: int = 5,
    fee_tolerance_cents: int = 500,
    min_token_set_ratio: float = 50.0
) -> List[Dict[str, Any]]:
    """
    Generates 1:1 candidate pairs passing blocking criteria and computes
    deterministic features for each candidate.
    """
    # Pre-normalize and pre-parse records
    prep_bank = []
    for b in bank_records:
        norm_desc = normalize_descriptor(b.get("description", ""))
        ref = b.get("reference") or extract_reference(b.get("description", ""))
        prep_bank.append({
            "raw": b,
            "id": b["id"],
            "date": parse_iso_date(b.get("date", "")),
            "amount_cents": int(b["amount_cents"]),
            "norm_desc": norm_desc,
            "ref": ref.upper() if ref else None
        })

    prep_ledger = []
    for l in ledger_records:
        norm_desc = normalize_descriptor(l.get("description", ""))
        ref = l.get("reference") or extract_reference(l.get("description", ""))
        prep_ledger.append({
            "raw": l,
            "id": l["id"],
            "date": parse_iso_date(l.get("date", "")),
            "amount_cents": int(l["amount_cents"]),
            "norm_desc": norm_desc,
            "ref": ref.upper() if ref else None
        })

    candidates = []

    for b in prep_bank:
        b_amt = b["amount_cents"]
        b_date = b["date"]
        b_ref = b["ref"]
        b_norm = b["norm_desc"]

        for l in prep_ledger:
            l_amt = l["amount_cents"]
            l_date = l["date"]
            l_ref = l["ref"]
            l_norm = l["norm_desc"]

            # 1. Feature: Reference match
            ref_match = 1 if (b_ref and l_ref and b_ref == l_ref) else 0

            # 2. Feature: Date delta
            date_delta = abs((b_date - l_date).days)

            # 3. Feature: Amount delta
            amt_delta = abs(b_amt - l_amt)

            # Blocking check
            # Pass if:
            # - exact reference match, OR
            # - date delta <= window AND amount delta <= fee tolerance, OR
            # - date delta <= window AND exact amount
            passes_blocking = (
                ref_match == 1 or
                (date_delta <= date_window_days and amt_delta <= fee_tolerance_cents)
            )

            if not passes_blocking:
                continue

            # 4. Feature: RapidFuzz token_set_ratio
            sim = float(fuzz.token_set_ratio(b_norm, l_norm))

            candidates.append({
                "bank_id": b["id"],
                "ledger_id": l["id"],
                "bank_record": b["raw"],
                "ledger_record": l["raw"],
                "features": {
                    "amount_delta_cents": amt_delta,
                    "date_delta_days": date_delta,
                    "token_set_ratio": sim,
                    "ref_match": ref_match,
                    "is_exact_amount": 1 if amt_delta == 0 else 0,
                    "is_exact_date": 1 if date_delta == 0 else 0
                }
            })

    return candidates

def find_bounded_subset_sum_n1(
    bank_records: List[Dict[str, Any]],
    ledger_record: Dict[str, Any],
    max_members: int = 4,
    max_pool_size: int = 25,
    date_window_days: int = 5
) -> List[Dict[str, Any]]:
    """
    Finds combinations of N bank records (2 <= N <= max_members) whose integer cents sum
    exactly matches ledger_record['amount_cents'].
    Uses pool size cap to prevent combinatorial explosion.
    """
    target_cents = int(ledger_record["amount_cents"])
    target_date = parse_iso_date(ledger_record.get("date", ""))
    target_ref = ledger_record.get("reference") or extract_reference(ledger_record.get("description", ""))

    # Filter candidate pool of bank records
    pool = []
    for b in bank_records:
        b_amt = int(b["amount_cents"])
        if 0 < b_amt < target_cents:
            b_date = parse_iso_date(b.get("date", ""))
            days_diff = abs((b_date - target_date).days)
            if days_diff <= date_window_days:
                pool.append((days_diff, b_amt, b))

    # Sort pool deterministically by days_diff, then amount closeness
    pool.sort(key=lambda x: (x[0], -x[1]))
    capped_pool = [x[2] for x in pool[:max_pool_size]]

    results = []
    for k in range(2, min(max_members + 1, len(capped_pool) + 1)):
        for combo in itertools.combinations(capped_pool, k):
            if sum(int(item["amount_cents"]) for item in combo) == target_cents:
                # Check for reference consistency or common description tokens
                refs = [item.get("reference") or extract_reference(item.get("description", "")) for item in combo]
                ref_match = 1 if any(r and target_ref and r.upper() == target_ref.upper() for r in refs) else 0

                results.append({
                    "type": "split_n_1",
                    "bank_ids": [item["id"] for item in combo],
                    "ledger_ids": [ledger_record["id"]],
                    "bank_records": list(combo),
                    "ledger_record": ledger_record,
                    "target_cents": target_cents,
                    "members_count": k,
                    "ref_match": ref_match
                })
    return results

def find_bounded_subset_sum_1n(
    bank_record: Dict[str, Any],
    ledger_records: List[Dict[str, Any]],
    max_members: int = 4,
    max_pool_size: int = 25,
    date_window_days: int = 5
) -> List[Dict[str, Any]]:
    """
    Finds combinations of N ledger records (2 <= N <= max_members) whose integer cents sum
    exactly matches bank_record['amount_cents'].
    Uses pool size cap to prevent combinatorial explosion.
    """
    target_cents = int(bank_record["amount_cents"])
    target_date = parse_iso_date(bank_record.get("date", ""))
    target_ref = bank_record.get("reference") or extract_reference(bank_record.get("description", ""))

    pool = []
    for l in ledger_records:
        l_amt = int(l["amount_cents"])
        if 0 < l_amt < target_cents:
            l_date = parse_iso_date(l.get("date", ""))
            days_diff = abs((l_date - target_date).days)
            if days_diff <= date_window_days:
                pool.append((days_diff, l_amt, l))

    pool.sort(key=lambda x: (x[0], -x[1]))
    capped_pool = [x[2] for x in pool[:max_pool_size]]

    results = []
    for k in range(2, min(max_members + 1, len(capped_pool) + 1)):
        for combo in itertools.combinations(capped_pool, k):
            if sum(int(item["amount_cents"]) for item in combo) == target_cents:
                refs = [item.get("reference") or extract_reference(item.get("description", "")) for item in combo]
                ref_match = 1 if any(r and target_ref and r.upper() == target_ref.upper() for r in refs) else 0

                results.append({
                    "type": "split_1_n",
                    "bank_ids": [bank_record["id"]],
                    "ledger_ids": [item["id"] for item in combo],
                    "bank_record": bank_record,
                    "ledger_records": list(combo),
                    "target_cents": target_cents,
                    "members_count": k,
                    "ref_match": ref_match
                })
    return results
