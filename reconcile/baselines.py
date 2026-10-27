"""
Baseline Reconciliation Engines:
- B0: Strict Exact Matcher (amount + date + reference-or-normalized description).
- B1: B0 + Fuzzy / Date-Window / Fee-Adjusted / Bounded Split Matcher with tuned thresholds.
Thresholds are tuned strictly on the dev split via reconcile/tune.py.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Set, Optional

from reconcile.normalize import normalize_descriptor, extract_reference
from reconcile.candidates import (
    generate_candidate_pairs,
    find_bounded_subset_sum_n1,
    find_bounded_subset_sum_1n,
    parse_iso_date
)

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "tuning_split": "dev",
    "sim_threshold": 70.0,
    "max_date_days": 5,
    "fee_sim_threshold": 65.0,
    "max_fee_cents": 500,
    "subset_max_members": 4,
    "subset_max_pool_size": 25
}

def load_reconcile_config() -> Dict[str, Any]:
    """Loads tuned threshold configuration from disk, falling back to defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def reconcile_b0(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Baseline B0: Exact Matcher.
    Matches records if and only if:
      - amount_cents matches exactly
      - date matches exactly (0 days delta)
      - reference matches OR normalized description matches OR raw description matches
    Guarantees 1:1 disjoint assignment.
    """
    matched_bank_ids: Set[str] = set()
    matched_ledger_ids: Set[str] = set()
    matches = []
    exceptions = []

    # Pre-parse records
    prep_b = []
    for b in bank_records:
        prep_b.append({
            "id": b["id"],
            "date": str(parse_iso_date(b.get("date", ""))),
            "amount_cents": int(b["amount_cents"]),
            "norm_desc": normalize_descriptor(b.get("description", "")),
            "raw_desc": b.get("description", "").strip(),
            "ref": (b.get("reference") or extract_reference(b.get("description", ""))),
            "raw": b
        })

    prep_l = []
    for l in ledger_records:
        prep_l.append({
            "id": l["id"],
            "date": str(parse_iso_date(l.get("date", ""))),
            "amount_cents": int(l["amount_cents"]),
            "norm_desc": normalize_descriptor(l.get("description", "")),
            "raw_desc": l.get("description", "").strip(),
            "ref": (l.get("reference") or extract_reference(l.get("description", ""))),
            "raw": l
        })

    match_counter = 1

    # Exact pass: amount + date + (ref or norm or raw)
    for b in prep_b:
        if b["id"] in matched_bank_ids:
            continue

        best_l = None
        for l in prep_l:
            if l["id"] in matched_ledger_ids:
                continue

            if b["amount_cents"] != l["amount_cents"]:
                continue
            if b["date"] != l["date"]:
                continue

            # Reference match
            b_ref = b["ref"].upper() if b["ref"] else None
            l_ref = l["ref"].upper() if l["ref"] else None
            ref_match = bool(b_ref and l_ref and b_ref == l_ref)

            # Description match
            desc_match = bool(
                (b["norm_desc"] and l["norm_desc"] and b["norm_desc"] == l["norm_desc"]) or
                (b["raw_desc"] and l["raw_desc"] and b["raw_desc"] == l["raw_desc"])
            )

            if ref_match or desc_match:
                best_l = l
                break

        if best_l:
            matched_bank_ids.add(b["id"])
            matched_ledger_ids.add(best_l["id"])
            matches.append({
                "match_id": f"MATCH-B0-{match_counter:04d}",
                "status": "matched",
                "method": "B0_exact",
                "confidence": 1.0,
                "bank_record_ids": [b["id"]],
                "ledger_record_ids": [best_l["id"]],
                "metadata": {
                    "cents_sum": b["amount_cents"]
                }
            })
            match_counter += 1

    unmatched_b = [b["id"] for b in prep_b if b["id"] not in matched_bank_ids]
    unmatched_l = [l["id"] for l in prep_l if l["id"] not in matched_ledger_ids]

    for ub in unmatched_b:
        exceptions.append({
            "record_id": ub,
            "side": "bank",
            "type": "unmatched_bank_record",
            "status": "open"
        })
    for ul in unmatched_l:
        exceptions.append({
            "record_id": ul,
            "side": "ledger",
            "type": "unmatched_ledger_record",
            "status": "open"
        })

    return {
        "system": "B0",
        "matches": matches,
        "exceptions": exceptions,
        "unmatched_bank_ids": unmatched_b,
        "unmatched_ledger_ids": unmatched_l
    }

def reconcile_b1(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Baseline B1: B0 + Fuzzy / Date-Window / Fee-Adjusted / Bounded Split Matching.
    Tuned on dev split only.
    """
    cfg = config or load_reconcile_config()
    sim_threshold = float(cfg.get("sim_threshold", 70.0))
    max_date_days = int(cfg.get("max_date_days", 5))
    fee_sim_threshold = float(cfg.get("fee_sim_threshold", 65.0))
    max_fee_cents = int(cfg.get("max_fee_cents", 500))
    max_members = int(cfg.get("subset_max_members", 4))
    max_pool_size = int(cfg.get("subset_max_pool_size", 25))

    # Step 1: Run B0 first to lock in exact matches
    b0_res = reconcile_b0(bank_records, ledger_records)
    matches = list(b0_res["matches"])
    exceptions = []

    matched_bank_ids: Set[str] = set()
    matched_ledger_ids: Set[str] = set()

    for m in matches:
        for bid in m["bank_record_ids"]:
            matched_bank_ids.add(bid)
        for lid in m["ledger_record_ids"]:
            matched_ledger_ids.add(lid)

    match_counter = len(matches) + 1

    rem_bank = [b for b in bank_records if b["id"] not in matched_bank_ids]
    rem_ledger = [l for l in ledger_records if l["id"] not in matched_ledger_ids]

    # Step 2: Reference match on remaining records within date window
    for b in list(rem_bank):
        if b["id"] in matched_bank_ids:
            continue
        b_ref = b.get("reference") or extract_reference(b.get("description", ""))
        if not b_ref:
            continue

        b_date = parse_iso_date(b.get("date", ""))
        for l in list(rem_ledger):
            if l["id"] in matched_ledger_ids:
                continue
            l_ref = l.get("reference") or extract_reference(l.get("description", ""))
            if not l_ref:
                continue

            if b_ref.upper() == l_ref.upper():
                l_date = parse_iso_date(l.get("date", ""))
                date_diff = abs((b_date - l_date).days)
                if date_diff <= max_date_days and int(b["amount_cents"]) == int(l["amount_cents"]):
                    matched_bank_ids.add(b["id"])
                    matched_ledger_ids.add(l["id"])
                    matches.append({
                        "match_id": f"MATCH-B1-REF-{match_counter:04d}",
                        "status": "matched",
                        "method": "B1_reference_date_drift",
                        "confidence": 0.98,
                        "bank_record_ids": [b["id"]],
                        "ledger_record_ids": [l["id"]],
                        "metadata": {
                            "cents_sum": int(b["amount_cents"]),
                            "reference": b_ref.upper(),
                            "date_drift_days": date_diff
                        }
                    })
                    match_counter += 1
                    break

    # Step 3: Fuzzy 1:1 Matching
    rem_b = [b for b in bank_records if b["id"] not in matched_bank_ids]
    rem_l = [l for l in ledger_records if l["id"] not in matched_ledger_ids]

    candidate_pairs = generate_candidate_pairs(
        rem_b, rem_l,
        date_window_days=max_date_days,
        fee_tolerance_cents=max_fee_cents
    )

    # Sort candidate pairs: exact amount first, higher similarity first, lower date delta first
    def sort_key(c):
        f = c["features"]
        return (
            -f["ref_match"],
            -f["is_exact_amount"],
            -f["token_set_ratio"],
            f["date_delta_days"]
        )

    candidate_pairs.sort(key=sort_key)

    for c in candidate_pairs:
        bid = c["bank_id"]
        lid = c["ledger_id"]
        if bid in matched_bank_ids or lid in matched_ledger_ids:
            continue

        feats = c["features"]
        sim = feats["token_set_ratio"]
        amt_delta = feats["amount_delta_cents"]
        date_delta = feats["date_delta_days"]

        # Exact Amount Fuzzy Match
        if amt_delta == 0 and date_delta <= max_date_days:
            if sim >= sim_threshold or feats["ref_match"] == 1:
                matched_bank_ids.add(bid)
                matched_ledger_ids.add(lid)
                conf = min(1.0, round(0.70 + 0.30 * (sim / 100.0), 4))
                matches.append({
                    "match_id": f"MATCH-B1-FUZZY-{match_counter:04d}",
                    "status": "matched",
                    "method": "B1_fuzzy_1_1",
                    "confidence": conf,
                    "bank_record_ids": [bid],
                    "ledger_record_ids": [lid],
                    "metadata": {
                        "cents_sum": int(c["bank_record"]["amount_cents"]),
                        "token_set_ratio": sim,
                        "date_drift_days": date_delta
                    }
                })
                match_counter += 1
                continue

        # Fee-Adjusted Match: Bank Net = Ledger Gross - Fee (Bank Net < Ledger Gross)
        b_amt = int(c["bank_record"]["amount_cents"])
        l_amt = int(c["ledger_record"]["amount_cents"])
        fee_cents = l_amt - b_amt

        if 0 < fee_cents <= max_fee_cents and date_delta <= max_date_days:
            if sim >= fee_sim_threshold:
                matched_bank_ids.add(bid)
                matched_ledger_ids.add(lid)
                matches.append({
                    "match_id": f"MATCH-B1-FEE-{match_counter:04d}",
                    "status": "matched",
                    "method": "B1_fee_adjusted",
                    "confidence": 0.90,
                    "bank_record_ids": [bid],
                    "ledger_record_ids": [lid],
                    "metadata": {
                        "gross_cents": l_amt,
                        "net_cents": b_amt,
                        "fee_cents": fee_cents,
                        "token_set_ratio": sim
                    }
                })
                exceptions.append({
                    "record_id": bid,
                    "side": "bank",
                    "type": "fee_adjustment",
                    "status": "resolved"
                })
                match_counter += 1

    # Step 4: Bounded Subset-Sum Splits (N:1 and 1:N)
    active_rem_b = [b for b in bank_records if b["id"] not in matched_bank_ids]
    active_rem_l = [l for l in ledger_records if l["id"] not in matched_ledger_ids]

    # Try N:1 splits (multiple bank -> 1 ledger)
    for l_rec in active_rem_l:
        if l_rec["id"] in matched_ledger_ids:
            continue
        splits_n1 = find_bounded_subset_sum_n1(
            active_rem_b, l_rec,
            max_members=max_members,
            max_pool_size=max_pool_size,
            date_window_days=max_date_days
        )
        for s_cand in splits_n1:
            if all(bid not in matched_bank_ids for bid in s_cand["bank_ids"]):
                for bid in s_cand["bank_ids"]:
                    matched_bank_ids.add(bid)
                matched_ledger_ids.add(l_rec["id"])
                matches.append({
                    "match_id": f"MATCH-B1-SPLIT-N1-{match_counter:04d}",
                    "status": "matched",
                    "method": "B1_split_n_1",
                    "confidence": 0.92,
                    "bank_record_ids": s_cand["bank_ids"],
                    "ledger_record_ids": [l_rec["id"]],
                    "metadata": {
                        "cents_sum": s_cand["target_cents"],
                        "members_count": s_cand["members_count"]
                    }
                })
                match_counter += 1
                break

    # Try 1:N splits (1 bank -> multiple ledger)
    active_rem_b = [b for b in bank_records if b["id"] not in matched_bank_ids]
    active_rem_l = [l for l in ledger_records if l["id"] not in matched_ledger_ids]

    for b_rec in active_rem_b:
        if b_rec["id"] in matched_bank_ids:
            continue
        splits_1n = find_bounded_subset_sum_1n(
            b_rec, active_rem_l,
            max_members=max_members,
            max_pool_size=max_pool_size,
            date_window_days=max_date_days
        )
        for s_cand in splits_1n:
            if all(lid not in matched_ledger_ids for lid in s_cand["ledger_ids"]):
                matched_bank_ids.add(b_rec["id"])
                for lid in s_cand["ledger_ids"]:
                    matched_ledger_ids.add(lid)
                matches.append({
                    "match_id": f"MATCH-B1-SPLIT-1N-{match_counter:04d}",
                    "status": "matched",
                    "method": "B1_split_1_n",
                    "confidence": 0.92,
                    "bank_record_ids": [b_rec["id"]],
                    "ledger_record_ids": s_cand["ledger_ids"],
                    "metadata": {
                        "cents_sum": s_cand["target_cents"],
                        "members_count": s_cand["members_count"]
                    }
                })
                match_counter += 1
                break

    # Step 5: Check remaining for ambiguity / duplicate exceptions
    final_unmatched_b = [b["id"] for b in bank_records if b["id"] not in matched_bank_ids]
    final_unmatched_l = [l["id"] for l in ledger_records if l["id"] not in matched_ledger_ids]

    # Flag duplicate exceptions if identical bank records exist
    bank_amt_counts = {}
    for b in bank_records:
        if b["id"] in final_unmatched_b:
            amt = int(b["amount_cents"])
            bank_amt_counts.setdefault(amt, []).append(b["id"])

    for amt, bids in bank_amt_counts.items():
        if len(bids) > 1:
            for bid in bids:
                exceptions.append({
                    "record_id": bid,
                    "side": "bank",
                    "type": "potential_duplicate",
                    "status": "open"
                })

    for ub in final_unmatched_b:
        if not any(ex["record_id"] == ub for ex in exceptions):
            exceptions.append({
                "record_id": ub,
                "side": "bank",
                "type": "unmatched_bank_record",
                "status": "open"
            })

    for ul in final_unmatched_l:
        exceptions.append({
            "record_id": ul,
            "side": "ledger",
            "type": "unmatched_ledger_record",
            "status": "open"
        })

    return {
        "system": "B1",
        "matches": matches,
        "exceptions": exceptions,
        "unmatched_bank_ids": final_unmatched_b,
        "unmatched_ledger_ids": final_unmatched_l
    }


def reconcile_b2(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    embedding_service: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Baseline B2: B1 + Embedding similarity as an additional candidate-ranking feature.
    Uses google-genai embedding endpoint with persistent disk caching.
    Candidate pairs are ranked and scored by combining deterministic lexical similarity
    with embedding cosine similarity weighted by embedding_weight (tuned on DEV only).
    """
    cfg = config or load_reconcile_config()
    sim_threshold = float(cfg.get("sim_threshold", 60.0))
    fee_sim_threshold = float(cfg.get("fee_sim_threshold", 55.0))
    max_date_days = int(cfg.get("max_date_days", 5))
    max_fee_cents = int(cfg.get("max_fee_cents", 1000))
    max_members = int(cfg.get("subset_max_members", 4))
    max_pool_size = int(cfg.get("subset_max_pool_size", 25))
    embedding_weight = float(cfg.get("embedding_weight", 0.20))

    from reconcile.embeddings import get_embedding_service
    emb_svc = embedding_service or get_embedding_service()

    # Step 1: Exact Match (B0 pass)
    b0_res = reconcile_b0(bank_records, ledger_records)
    matches = list(b0_res["matches"])
    exceptions = []

    matched_bank_ids: Set[str] = set()
    matched_ledger_ids: Set[str] = set()

    for m in matches:
        for bid in m["bank_record_ids"]:
            matched_bank_ids.add(bid)
        for lid in m["ledger_record_ids"]:
            matched_ledger_ids.add(lid)

    match_counter = len(matches) + 1

    rem_bank = [b for b in bank_records if b["id"] not in matched_bank_ids]
    rem_ledger = [l for l in ledger_records if l["id"] not in matched_ledger_ids]

    # Step 2: Reference match on remaining records within date window
    for b in list(rem_bank):
        if b["id"] in matched_bank_ids:
            continue
        b_ref = b.get("reference") or extract_reference(b.get("description", ""))
        if not b_ref:
            continue

        b_date = parse_iso_date(b.get("date", ""))
        for l in list(rem_ledger):
            if l["id"] in matched_ledger_ids:
                continue
            l_ref = l.get("reference") or extract_reference(l.get("description", ""))
            if not l_ref:
                continue

            if b_ref.upper() == l_ref.upper():
                l_date = parse_iso_date(l.get("date", ""))
                date_diff = abs((b_date - l_date).days)
                if date_diff <= max_date_days and int(b["amount_cents"]) == int(l["amount_cents"]):
                    matched_bank_ids.add(b["id"])
                    matched_ledger_ids.add(l["id"])
                    matches.append({
                        "match_id": f"MATCH-B2-REF-{match_counter:04d}",
                        "status": "matched",
                        "method": "B2_reference_date_drift",
                        "confidence": 0.98,
                        "bank_record_ids": [b["id"]],
                        "ledger_record_ids": [l["id"]],
                        "metadata": {
                            "cents_sum": int(b["amount_cents"]),
                            "reference": b_ref.upper(),
                            "date_drift_days": date_diff
                        }
                    })
                    match_counter += 1
                    break

    # Step 3: Fuzzy 1:1 Matching with Embedding-Enhanced Candidate Ranking
    rem_b = [b for b in bank_records if b["id"] not in matched_bank_ids]
    rem_l = [l for l in ledger_records if l["id"] not in matched_ledger_ids]

    candidate_pairs = generate_candidate_pairs(
        rem_b, rem_l,
        date_window_days=max_date_days,
        fee_tolerance_cents=max_fee_cents
    )

    # Enrich candidate pairs with embedding similarity and composite score
    for c in candidate_pairs:
        b_desc = c["bank_record"]["description"]
        l_desc = c["ledger_record"]["description"]
        emb_sim = emb_svc.text_similarity(b_desc, l_desc)
        lex_sim = float(c["features"]["token_set_ratio"])
        # Composite similarity combines lexical ratio (0-100) and embedding similarity (0-100)
        comp_sim = (1.0 - embedding_weight) * lex_sim + embedding_weight * (emb_sim * 100.0)
        c["features"]["embedding_similarity"] = emb_sim
        c["features"]["composite_similarity"] = comp_sim

    # Sort candidate pairs using composite embedding-enhanced similarity
    def sort_key_b2(c):
        f = c["features"]
        return (
            -f["ref_match"],
            -f["is_exact_amount"],
            -f["composite_similarity"],
            f["date_delta_days"]
        )

    candidate_pairs.sort(key=sort_key_b2)

    for c in candidate_pairs:
        bid = c["bank_id"]
        lid = c["ledger_id"]
        if bid in matched_bank_ids or lid in matched_ledger_ids:
            continue

        feats = c["features"]
        sim = feats["composite_similarity"]
        amt_delta = feats["amount_delta_cents"]
        date_delta = feats["date_delta_days"]

        # Exact Amount Match
        if amt_delta == 0 and date_delta <= max_date_days:
            if sim >= sim_threshold or feats["ref_match"] == 1:
                matched_bank_ids.add(bid)
                matched_ledger_ids.add(lid)
                conf = min(1.0, round(0.70 + 0.30 * (sim / 100.0), 4))
                matches.append({
                    "match_id": f"MATCH-B2-FUZZY-{match_counter:04d}",
                    "status": "matched",
                    "method": "B2_embedding_fuzzy_1_1",
                    "confidence": conf,
                    "bank_record_ids": [bid],
                    "ledger_record_ids": [lid],
                    "metadata": {
                        "cents_sum": int(c["bank_record"]["amount_cents"]),
                        "token_set_ratio": feats["token_set_ratio"],
                        "lexical_similarity": feats["token_set_ratio"],
                        "embedding_similarity": feats["embedding_similarity"],
                        "composite_similarity": round(sim, 2),
                        "embedding_weight": embedding_weight,
                        "date_drift_days": date_delta
                    }
                })
                match_counter += 1
                continue

        # Fee-Adjusted Match: Bank Net = Ledger Gross - Fee (Bank Net < Ledger Gross)
        b_amt = int(c["bank_record"]["amount_cents"])
        l_amt = int(c["ledger_record"]["amount_cents"])
        fee_cents = l_amt - b_amt

        if 0 < fee_cents <= max_fee_cents and date_delta <= max_date_days:
            if sim >= fee_sim_threshold:
                matched_bank_ids.add(bid)
                matched_ledger_ids.add(lid)
                matches.append({
                    "match_id": f"MATCH-B2-FEE-{match_counter:04d}",
                    "status": "matched",
                    "method": "B2_embedding_fee_adjusted",
                    "confidence": 0.90,
                    "bank_record_ids": [bid],
                    "ledger_record_ids": [lid],
                    "metadata": {
                        "gross_cents": l_amt,
                        "net_cents": b_amt,
                        "fee_cents": fee_cents,
                        "token_set_ratio": feats["token_set_ratio"],
                        "lexical_similarity": feats["token_set_ratio"],
                        "embedding_similarity": feats["embedding_similarity"],
                        "composite_similarity": round(sim, 2),
                        "embedding_weight": embedding_weight
                    }
                })
                exceptions.append({
                    "record_id": bid,
                    "side": "bank",
                    "type": "fee_adjustment",
                    "status": "resolved"
                })
                match_counter += 1

    # Step 4: Bounded Subset-Sum Splits (N:1 and 1:N)
    active_rem_b = [b for b in bank_records if b["id"] not in matched_bank_ids]
    active_rem_l = [l for l in ledger_records if l["id"] not in matched_ledger_ids]

    for l_rec in active_rem_l:
        if l_rec["id"] in matched_ledger_ids:
            continue
        splits_n1 = find_bounded_subset_sum_n1(
            active_rem_b, l_rec,
            max_members=max_members,
            max_pool_size=max_pool_size,
            date_window_days=max_date_days
        )
        for s_cand in splits_n1:
            if all(bid not in matched_bank_ids for bid in s_cand["bank_ids"]):
                for bid in s_cand["bank_ids"]:
                    matched_bank_ids.add(bid)
                matched_ledger_ids.add(l_rec["id"])
                matches.append({
                    "match_id": f"MATCH-B2-SPLIT-N1-{match_counter:04d}",
                    "status": "matched",
                    "method": "B2_split_n_1",
                    "confidence": 0.92,
                    "bank_record_ids": s_cand["bank_ids"],
                    "ledger_record_ids": [l_rec["id"]],
                    "metadata": {
                        "cents_sum": s_cand["target_cents"],
                        "members_count": s_cand["members_count"]
                    }
                })
                match_counter += 1
                break

    active_rem_b2 = [b for b in bank_records if b["id"] not in matched_bank_ids]
    active_rem_l2 = [l for l in ledger_records if l["id"] not in matched_ledger_ids]

    for b_rec in active_rem_b2:
        if b_rec["id"] in matched_bank_ids:
            continue
        splits_1n = find_bounded_subset_sum_1n(
            b_rec, active_rem_l2,
            max_members=max_members,
            max_pool_size=max_pool_size,
            date_window_days=max_date_days
        )
        for s_cand in splits_1n:
            if all(lid not in matched_ledger_ids for lid in s_cand["ledger_ids"]):
                matched_bank_ids.add(b_rec["id"])
                for lid in s_cand["ledger_ids"]:
                    matched_ledger_ids.add(lid)
                matches.append({
                    "match_id": f"MATCH-B2-SPLIT-1N-{match_counter:04d}",
                    "status": "matched",
                    "method": "B2_split_1_n",
                    "confidence": 0.92,
                    "bank_record_ids": [b_rec["id"]],
                    "ledger_record_ids": s_cand["ledger_ids"],
                    "metadata": {
                        "cents_sum": s_cand["target_cents"],
                        "members_count": s_cand["members_count"]
                    }
                })
                match_counter += 1
                break

    # Step 5: Duplicate and unmatched exceptions
    final_unmatched_b = [b["id"] for b in bank_records if b["id"] not in matched_bank_ids]
    final_unmatched_l = [l["id"] for l in ledger_records if l["id"] not in matched_ledger_ids]

    bank_amt_counts = {}
    for b in bank_records:
        if b["id"] in final_unmatched_b:
            amt = int(b["amount_cents"])
            bank_amt_counts.setdefault(amt, []).append(b["id"])

    for amt, bids in bank_amt_counts.items():
        if len(bids) > 1:
            for bid in bids:
                exceptions.append({
                    "record_id": bid,
                    "side": "bank",
                    "type": "potential_duplicate",
                    "status": "open"
                })

    for ub in final_unmatched_b:
        if not any(ex["record_id"] == ub for ex in exceptions):
            exceptions.append({
                "record_id": ub,
                "side": "bank",
                "type": "unmatched_bank_record",
                "status": "open"
            })

    for ul in final_unmatched_l:
        exceptions.append({
            "record_id": ul,
            "side": "ledger",
            "type": "unmatched_ledger_record",
            "status": "open"
        })

    return {
        "system": "B2",
        "matches": matches,
        "exceptions": exceptions,
        "unmatched_bank_ids": final_unmatched_b,
        "unmatched_ledger_ids": final_unmatched_l
    }
