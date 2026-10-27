"""
Authoritative Evaluation Metrics for Two-Sided Financial Reconciliation.
Implements:
1. Pair-group Precision, Recall, F1 (TP only when exact bank-ID set and ledger-ID set equal ground truth).
2. Auto-commit false-positive rate.
3. Record coverage.
4. Unmatched detection Precision, Recall, F1.
5. Exception macro-F1.
6. Per-slice breakdown and macro-averaged results.
7. Wilson 95% confidence intervals for all primary proportions.
"""

import math
from typing import Dict, Any, List, Set, Tuple, Optional

def wilson_score_interval(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """
    Computes Wilson score 95% confidence interval for a binomial proportion.
    """
    if n <= 0:
        return (0.0, 0.0)

    z = 1.959964  # 95% confidence z-score
    p_hat = float(k) / float(n)
    denom = 1.0 + (z ** 2) / float(n)
    center = (p_hat + (z ** 2) / (2.0 * float(n))) / denom
    margin = (z / denom) * math.sqrt((p_hat * (1.0 - p_hat) / float(n)) + ((z ** 2) / (4.0 * (float(n) ** 2))))

    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return (round(lower, 4), round(upper, 4))

def evaluate_reconciliation(
    ground_truth_groups: List[Dict[str, Any]],
    predicted_matches: List[Dict[str, Any]],
    predicted_exceptions: Optional[List[Dict[str, Any]]] = None,
    unmatched_bank_ids: Optional[List[str]] = None,
    unmatched_ledger_ids: Optional[List[str]] = None,
    total_bank_records_count: int = 0,
    total_ledger_records_count: int = 0
) -> Dict[str, Any]:
    """
    Evaluates two-sided reconciliation performance against ground truth groups.
    """
    pred_exceptions = predicted_exceptions or []
    pred_unmatched_b = set(unmatched_bank_ids or [])
    pred_unmatched_l = set(unmatched_ledger_ids or [])

    # 1. Index Ground Truth Groups
    # GT match pairs: (frozenset(bank_ids), frozenset(ledger_ids)) -> group
    gt_matched_groups: Dict[Tuple[frozenset, frozenset], Dict[str, Any]] = {}
    gt_by_slice: Dict[str, List[Dict[str, Any]]] = {}
    gt_unmatched_bank: Set[str] = set()
    gt_unmatched_ledger: Set[str] = set()
    gt_exceptions_by_type: Dict[str, Set[str]] = {}

    for g in ground_truth_groups:
        s_name = g.get("slice", "default")
        gt_by_slice.setdefault(s_name, []).append(g)

        b_ids = frozenset(g.get("bank_record_ids", []))
        l_ids = frozenset(g.get("ledger_record_ids", []))

        if g.get("expected_status") in ("matched", "matched_with_fee"):
            gt_matched_groups[(b_ids, l_ids)] = g

        # Track unmatched
        if g.get("expected_status") == "unmatched":
            for bid in b_ids:
                gt_unmatched_bank.add(bid)
            for lid in l_ids:
                gt_unmatched_ledger.add(lid)

        # Track exceptions
        exc_type = g.get("expected_exception_type")
        if exc_type:
            for rec_id in list(b_ids) + list(l_ids):
                gt_exceptions_by_type.setdefault(exc_type, set()).add(rec_id)

    # 2. Index Predicted Matches
    # Predicted match pairs: (frozenset(bank_ids), frozenset(ledger_ids))
    pred_matched_set: Set[Tuple[frozenset, frozenset]] = set()
    pred_records_resolved: Set[str] = set()

    for m in predicted_matches:
        if m.get("status") in ("matched", "suggested", "approved"):
            b_ids = frozenset(m.get("bank_record_ids", []))
            l_ids = frozenset(m.get("ledger_record_ids", []))
            pred_matched_set.add((b_ids, l_ids))
            for bid in b_ids:
                pred_records_resolved.add(bid)
            for lid in l_ids:
                pred_records_resolved.add(lid)

    for ex in pred_exceptions:
        rec_id = ex.get("record_id")
        if rec_id:
            pred_records_resolved.add(rec_id)

    for ub in pred_unmatched_b:
        pred_records_resolved.add(ub)
    for ul in pred_unmatched_l:
        pred_records_resolved.add(ul)

    # 3. Overall Pair-Group P / R / F1
    # TP: exact bank-ID set and ledger-ID set equal ground truth match
    tp_pairs = pred_matched_set & set(gt_matched_groups.keys())
    fp_pairs = pred_matched_set - set(gt_matched_groups.keys())
    fn_pairs = set(gt_matched_groups.keys()) - pred_matched_set

    tp = len(tp_pairs)
    fp = len(fp_pairs)
    fn = len(fn_pairs)

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
    f1 = round((2 * precision * recall) / (precision + recall), 4) if (precision + recall) > 0 else 0.0

    # Auto-commit false-positive rate
    auto_commit_fpr = round(fp / (tp + fp), 4) if (tp + fp) > 0 else 0.0

    # Coverage
    total_records = total_bank_records_count + total_ledger_records_count
    coverage = round(len(pred_records_resolved) / total_records, 4) if total_records > 0 else 1.0

    # 4. Wilson 95% Confidence Intervals
    precision_ci = wilson_score_interval(tp, tp + fp)
    recall_ci = wilson_score_interval(tp, tp + fn)

    # 5. Unmatched Detection P / R / F1
    gt_total_unmatched = gt_unmatched_bank | gt_unmatched_ledger
    pred_total_unmatched = pred_unmatched_b | pred_unmatched_l

    unm_tp = len(pred_total_unmatched & gt_total_unmatched)
    unm_fp = len(pred_total_unmatched - gt_total_unmatched)
    unm_fn = len(gt_total_unmatched - pred_total_unmatched)

    unm_prec = round(unm_tp / (unm_tp + unm_fp), 4) if (unm_tp + unm_fp) > 0 else (1.0 if unm_fn == 0 else 0.0)
    unm_rec = round(unm_tp / (unm_tp + unm_fn), 4) if (unm_tp + unm_fn) > 0 else 0.0
    unm_f1 = round((2 * unm_prec * unm_rec) / (unm_prec + unm_rec), 4) if (unm_prec + unm_rec) > 0 else 0.0

    # 6. Exception Macro-F1
    pred_exceptions_by_type: Dict[str, Set[str]] = {}
    for ex in pred_exceptions:
        e_type = ex.get("type", "unknown")
        r_id = ex.get("record_id")
        if r_id:
            pred_exceptions_by_type.setdefault(e_type, set()).add(r_id)

    exception_f1_scores = []
    exception_breakdown = {}
    for e_type, gt_recs in gt_exceptions_by_type.items():
        pred_recs = pred_exceptions_by_type.get(e_type, set())
        e_tp = len(pred_recs & gt_recs)
        e_fp = len(pred_recs - gt_recs)
        e_fn = len(gt_recs - pred_recs)
        e_p = e_tp / (e_tp + e_fp) if (e_tp + e_fp) > 0 else 0.0
        e_r = e_tp / (e_tp + e_fn) if (e_tp + e_fn) > 0 else 0.0
        e_f1 = (2 * e_p * e_r) / (e_p + e_r) if (e_p + e_r) > 0 else 0.0
        exception_f1_scores.append(e_f1)
        exception_breakdown[e_type] = {
            "tp": e_tp,
            "fp": e_fp,
            "fn": e_fn,
            "precision": round(e_p, 4),
            "recall": round(e_r, 4),
            "f1": round(e_f1, 4)
        }

    exception_macro_f1 = round(sum(exception_f1_scores) / len(exception_f1_scores), 4) if exception_f1_scores else 0.0

    # 7. Per-Slice Breakdown
    slice_results = {}
    slice_f1_list = []

    for s_name, s_groups in sorted(gt_by_slice.items()):
        s_gt_matched = {
            (frozenset(g.get("bank_record_ids", [])), frozenset(g.get("ledger_record_ids", []))): g
            for g in s_groups if g.get("expected_status") in ("matched", "matched_with_fee")
        }

        # Slices with matched pairs
        if s_gt_matched:
            s_tp_pairs = pred_matched_set & set(s_gt_matched.keys())
            s_fn_pairs = set(s_gt_matched.keys()) - pred_matched_set

            # FP for slice: predicted matches whose records belonged to this slice but were wrong
            s_record_ids = set()
            for g in s_groups:
                s_record_ids.update(g.get("bank_record_ids", []))
                s_record_ids.update(g.get("ledger_record_ids", []))

            s_fp_pairs = {
                pair for pair in fp_pairs
                if any(bid in s_record_ids for bid in pair[0]) or any(lid in s_record_ids for lid in pair[1])
            }

            s_tp = len(s_tp_pairs)
            s_fp = len(s_fp_pairs)
            s_fn = len(s_fn_pairs)

            s_prec = round(s_tp / (s_tp + s_fp), 4) if (s_tp + s_fp) > 0 else 0.0
            s_rec = round(s_tp / (s_tp + s_fn), 4) if (s_tp + s_fn) > 0 else 0.0
            s_f1 = round((2 * s_prec * s_rec) / (s_prec + s_rec), 4) if (s_prec + s_rec) > 0 else 0.0
        else:
            # Slices that expect exceptions / unmatched
            # E.g. unmatched, duplicate, ambiguous
            s_tp = 0
            s_fp = 0
            s_fn = 0
            if s_name == "unmatched":
                s_tp = unm_tp
                s_fp = unm_fp
                s_fn = unm_fn
            elif s_name in exception_breakdown:
                s_tp = exception_breakdown[s_name]["tp"]
                s_fp = exception_breakdown[s_name]["fp"]
                s_fn = exception_breakdown[s_name]["fn"]

            s_prec = round(s_tp / (s_tp + s_fp), 4) if (s_tp + s_fp) > 0 else 0.0
            s_rec = round(s_tp / (s_tp + s_fn), 4) if (s_tp + s_fn) > 0 else 0.0
            s_f1 = round((2 * s_prec * s_rec) / (s_prec + s_rec), 4) if (s_prec + s_rec) > 0 else 0.0

        s_p_ci = wilson_score_interval(s_tp, s_tp + s_fp)
        s_r_ci = wilson_score_interval(s_tp, s_tp + s_fn)

        slice_results[s_name] = {
            "count": len(s_groups),
            "tp": s_tp,
            "fp": s_fp,
            "fn": s_fn,
            "precision": s_prec,
            "recall": s_rec,
            "f1": s_f1,
            "precision_95_ci": s_p_ci,
            "recall_95_ci": s_r_ci
        }
        slice_f1_list.append(s_f1)

    macro_f1 = round(sum(slice_f1_list) / len(slice_f1_list), 4) if slice_f1_list else 0.0

    return {
        "summary": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "precision_95_ci": precision_ci,
            "recall_95_ci": recall_ci,
            "auto_commit_fpr": auto_commit_fpr,
            "coverage": coverage,
            "macro_f1": macro_f1
        },
        "unmatched_detection": {
            "tp": unm_tp,
            "fp": unm_fp,
            "fn": unm_fn,
            "precision": unm_prec,
            "recall": unm_rec,
            "f1": unm_f1
        },
        "exceptions": {
            "macro_f1": exception_macro_f1,
            "breakdown": exception_breakdown
        },
        "per_slice": slice_results
    }


def calculate_ece(confidences: List[float], accuracies: List[bool], num_bins: int = 10) -> float:
    """
    Computes Expected Calibration Error (ECE) for confidence predictions.
    Bins predictions into num_bins uniform intervals [0, 1/M), ..., [(M-1)/M, 1].
    ECE = sum_{m=1}^M (|B_m| / N) * |acc(B_m) - conf(B_m)|.
    """
    if not confidences or not accuracies:
        return 0.0
    if len(confidences) != len(accuracies):
        raise ValueError(f"Length mismatch: {len(confidences)} confidences vs {len(accuracies)} accuracies")

    n = len(confidences)
    bin_boundaries = [i / float(num_bins) for i in range(num_bins + 1)]
    ece = 0.0

    for i in range(num_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]

        # Last bin is closed on both sides: [0.9, 1.0]
        if i == num_bins - 1:
            bin_indices = [idx for idx, c in enumerate(confidences) if bin_lower <= c <= bin_upper]
        else:
            bin_indices = [idx for idx, c in enumerate(confidences) if bin_lower <= c < bin_upper]

        bin_size = len(bin_indices)
        if bin_size > 0:
            bin_acc = sum(1 for idx in bin_indices if accuracies[idx]) / float(bin_size)
            bin_conf = sum(confidences[idx] for idx in bin_indices) / float(bin_size)
            ece += (bin_size / float(n)) * abs(bin_acc - bin_conf)

    return round(ece, 4)


def compute_precision_coverage_curve(
    matches_with_conf: List[Dict[str, Any]],
    ground_truth_groups: List[Dict[str, Any]],
    total_records: int,
    thresholds: Optional[List[float]] = None
) -> List[Dict[str, Any]]:
    """
    Computes empirical Precision-vs-Coverage curve points across confidence thresholds.
    """
    if thresholds is None:
        thresholds = [0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 0.98, 1.00]

    gt_matched = {
        (frozenset(g.get("bank_record_ids", [])), frozenset(g.get("ledger_record_ids", []))): g
        for g in ground_truth_groups if g.get("expected_status") in ("matched", "matched_with_fee")
    }

    curve = []
    for th in thresholds:
        filtered = [m for m in matches_with_conf if float(m.get("confidence", 1.0)) >= th]
        pred_set = {
            (frozenset(m.get("bank_record_ids", [])), frozenset(m.get("ledger_record_ids", [])))
            for m in filtered
        }
        tp = len(pred_set & set(gt_matched.keys()))
        fp = len(pred_set - set(gt_matched.keys()))
        prec = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 1.0

        resolved_records = set()
        for m in filtered:
            resolved_records.update(m.get("bank_record_ids", []))
            resolved_records.update(m.get("ledger_record_ids", []))

        cov = round(len(resolved_records) / total_records, 4) if total_records > 0 else 0.0

        curve.append({
            "threshold": th,
            "precision": prec,
            "coverage": cov,
            "tp": tp,
            "fp": fp,
            "matches_count": len(filtered)
        })

    return curve


def check_interval_overlap(interval_a: Tuple[float, float], interval_b: Tuple[float, float]) -> bool:
    """
    Checks if two closed confidence intervals [a_low, a_high] and [b_low, b_high] overlap.
    """
    a_low, a_high = interval_a
    b_low, b_high = interval_b
    return max(a_low, b_low) <= min(a_high, b_high)


def calculate_p50_p95(latencies: List[float]) -> Dict[str, float]:
    """
    Computes p50 (median) and p95 (95th percentile) from a list of latencies in ms.
    """
    if not latencies:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}

    s = sorted(latencies)
    n = len(s)
    if n % 2 == 1:
        p50 = s[n // 2]
    else:
        p50 = (s[n // 2 - 1] + s[n // 2]) / 2.0

    idx95 = max(0, min(n - 1, int(math.ceil(0.95 * n)) - 1))
    p95 = s[idx95]

    return {
        "p50_ms": round(float(p50), 2),
        "p95_ms": round(float(p95), 2),
        "mean_ms": round(sum(s) / float(n), 2),
        "min_ms": round(float(s[0]), 2),
        "max_ms": round(float(s[-1]), 2)
    }


def calculate_tokens_and_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str = "gemini-2.5-flash"
) -> Dict[str, Any]:
    """
    Computes token counts and USD costs based on official pricing.
    gemini-2.5-flash standard pricing:
    Input: $0.075 per 1M tokens ($0.000000075 / token)
    Output: $0.30 per 1M tokens ($0.00000030 / token)
    """
    input_rate = 0.075 / 1_000_000.0
    output_rate = 0.30 / 1_000_000.0

    cost = (prompt_tokens * input_rate) + (completion_tokens * output_rate)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": round(cost, 6)
    }


def calculate_run_consistency(values: List[float]) -> Dict[str, float]:
    """
    Computes mean, variance, sample standard deviation, min, max across repeat runs.
    Fails loudly if values is empty (strictly refuses to impute missing data).
    """
    if not values:
        raise ValueError("Cannot calculate run consistency on empty values list (missing data).")

    n = len(values)
    mean_val = sum(values) / float(n)
    if n > 1:
        variance = sum((x - mean_val) ** 2 for x in values) / float(n - 1)
        std_dev = math.sqrt(variance)
    else:
        variance = 0.0
        std_dev = 0.0

    return {
        "mean": round(mean_val, 4),
        "variance": round(variance, 6),
        "std": round(std_dev, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4)
    }

