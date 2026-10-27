"""
Hyperparameter Tuning Script for Baseline B1 and B2 Reconciliation Engines.
Strictly tunes parameters on the DEV split only.
The TEST split is NEVER read or accessed by this module.
Writes the optimal configuration parameters to reconcile/config.json.
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from reconcile.baselines import reconcile_b1, reconcile_b2, CONFIG_PATH, DEFAULT_CONFIG, load_reconcile_config
from evaluation.metrics import evaluate_reconciliation, check_interval_overlap
DEV_SPLIT_PATH = PROJECT_ROOT / "data" / "benchmark" / "dev.json"


def tune_b1_on_dev(dev_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Grid searches candidate threshold values on dev.json to maximize pair-group F1.
    Writes best parameters to reconcile/config.json.
    """
    path = dev_path or DEV_SPLIT_PATH
    if not path.exists():
        raise FileNotFoundError(f"Dev split not found at: {path}. Run 'cli.py gen-benchmark --seed 42' first.")

    with open(path, "r", encoding="utf-8") as f:
        dev_data = json.load(f)

    bank_records = dev_data["bank_records"]
    ledger_records = dev_data["ledger_records"]
    ground_truth = dev_data["ground_truth"]

    sim_candidates = [60.0, 65.0, 70.0, 75.0]
    date_candidates = [3, 5]
    fee_sim_candidates = [55.0, 60.0, 65.0]
    fee_amt_candidates = [300, 500, 1000]

    best_config = load_reconcile_config()
    best_f1 = -1.0
    best_summary = {}

    print("\n" + "=" * 76)
    print("STARTING B1 HYPERPARAMETER TUNING (DEV SPLIT ONLY)")
    print(f"Dataset: {path.name} ({len(ground_truth)} ground truth groups)")
    print("=" * 76)

    iteration = 0
    for sim_th in sim_candidates:
        for max_d in date_candidates:
            for fee_sim in fee_sim_candidates:
                for max_fee in fee_amt_candidates:
                    iteration += 1
                    test_cfg = dict(best_config)
                    test_cfg.update({
                        "tuning_split": "dev",
                        "sim_threshold": sim_th,
                        "max_date_days": max_d,
                        "fee_sim_threshold": fee_sim,
                        "max_fee_cents": max_fee,
                        "subset_max_members": 4,
                        "subset_max_pool_size": 25
                    })

                    res = reconcile_b1(bank_records, ledger_records, config=test_cfg)
                    metrics = evaluate_reconciliation(
                        ground_truth_groups=ground_truth,
                        predicted_matches=res["matches"],
                        predicted_exceptions=res["exceptions"],
                        unmatched_bank_ids=res["unmatched_bank_ids"],
                        unmatched_ledger_ids=res["unmatched_ledger_ids"],
                        total_bank_records_count=len(bank_records),
                        total_ledger_records_count=len(ledger_records)
                    )

                    f1 = metrics["summary"]["f1"]
                    if f1 > best_f1:
                        best_f1 = f1
                        best_config = test_cfg
                        best_summary = metrics["summary"]

    print(f"Tuning Completed ({iteration} grid evaluations).")
    print(f"Optimal Dev F1: {best_f1:.4f} (Precision: {best_summary.get('precision', 0):.4f}, Recall: {best_summary.get('recall', 0):.4f})")
    print(f"Optimal Configuration Parameters:")
    for k, v in best_config.items():
        print(f"  • {k}: {v}")

    # Write chosen values to reconcile/config.json
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(best_config, f, indent=2, sort_keys=True)
    print(f"Saved tuned configuration to: {CONFIG_PATH}")
    print("=" * 76)

    return best_config


def tune_b2_on_dev(dev_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Tunes the embedding similarity weight for B2 strictly on DEV split only.
    Tests whether B2 beats B1 on hard slices with non-overlapping Wilson intervals.
    Rule: B2 is NOT made the default unless it beats B1 on hard slices with non-overlapping intervals.
    """
    path = dev_path or DEV_SPLIT_PATH
    if not path.exists():
        raise FileNotFoundError(f"Dev split not found at: {path}")

    with open(path, "r", encoding="utf-8") as f:
        dev_data = json.load(f)

    bank_records = dev_data["bank_records"]
    ledger_records = dev_data["ledger_records"]
    ground_truth = dev_data["ground_truth"]

    # 1. Run Baseline B1
    current_cfg = load_reconcile_config()
    res_b1 = reconcile_b1(bank_records, ledger_records, config=current_cfg)
    b1_metrics = evaluate_reconciliation(
        ground_truth_groups=ground_truth,
        predicted_matches=res_b1["matches"],
        predicted_exceptions=res_b1["exceptions"],
        unmatched_bank_ids=res_b1["unmatched_bank_ids"],
        unmatched_ledger_ids=res_b1["unmatched_ledger_ids"],
        total_bank_records_count=len(bank_records),
        total_ledger_records_count=len(ledger_records)
    )
    b1_f1 = b1_metrics["summary"]["f1"]

    print("\n" + "=" * 76)
    print("STARTING B2 EMBEDDING WEIGHT TUNING (DEV SPLIT ONLY)")
    print(f"Dataset: {path.name} | Baseline B1 F1: {b1_f1:.4f}")
    print("=" * 76)

    # 2. Grid search candidate embedding weights on DEV
    candidate_weights = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
    best_weight = 0.0
    best_b2_f1 = -1.0
    best_b2_metrics = None

    for w in candidate_weights:
        test_cfg = dict(current_cfg)
        test_cfg["embedding_weight"] = w
        res_b2 = reconcile_b2(bank_records, ledger_records, config=test_cfg)
        b2_met = evaluate_reconciliation(
            ground_truth_groups=ground_truth,
            predicted_matches=res_b2["matches"],
            predicted_exceptions=res_b2["exceptions"],
            unmatched_bank_ids=res_b2["unmatched_bank_ids"],
            unmatched_ledger_ids=res_b2["unmatched_ledger_ids"],
            total_bank_records_count=len(bank_records),
            total_ledger_records_count=len(ledger_records)
        )
        f1 = b2_met["summary"]["f1"]
        print(f"Weight {w:.2f}: F1 = {f1:.4f} | Prec: {b2_met['summary']['precision']:.4f} | Rec: {b2_met['summary']['recall']:.4f}")
        if f1 > best_b2_f1:
            best_b2_f1 = f1
            best_weight = w
            best_b2_metrics = b2_met

    print("-" * 76)
    print(f"Optimal B2 Embedding Weight on Dev: {best_weight:.2f} (F1: {best_b2_f1:.4f})")

    # 3. Check hard slice performance against B1
    hard_slices = ["ambiguous", "fuzzy_1_1", "split_n_1"]
    beats_all_hard_slices_non_overlapping = True
    hard_slice_results = {}

    for s_key in hard_slices:
        b1_s = b1_metrics["per_slice"].get(s_key, {})
        b2_s = best_b2_metrics["per_slice"].get(s_key, {})

        b1_s_f1 = b1_s.get("f1", 0.0)
        b2_s_f1 = b2_s.get("f1", 0.0)
        b1_ci = b1_s.get("precision_95_ci", (0.0, 0.0))
        b2_ci = b2_s.get("precision_95_ci", (0.0, 0.0))
        overlap = check_interval_overlap(b1_ci, b2_ci)

        b2_beats_s = (b2_s_f1 > b1_s_f1) and (not overlap)
        if not b2_beats_s:
            beats_all_hard_slices_non_overlapping = False

        hard_slice_results[s_key] = {
            "b1_f1": b1_s_f1,
            "b2_f1": b2_s_f1,
            "b1_wilson_ci": b1_ci,
            "b2_wilson_ci": b2_ci,
            "intervals_overlap": overlap,
            "b2_beats_b1_non_overlapping": b2_beats_s
        }
        print(f"Hard Slice '{s_key}': B1={b1_s_f1:.4f} (CI: {b1_ci}), B2={b2_s_f1:.4f} (CI: {b2_ci}), Overlap={overlap}")

    # 4. Enforce strict constraint: Do not make default unless beats B1 with non-overlapping intervals
    make_default = beats_all_hard_slices_non_overlapping and (best_b2_f1 > b1_f1)

    print("-" * 76)
    if make_default:
        print("[VERDICT] B2 beats B1 on hard slices with non-overlapping intervals. Setting B2 as default.")
        current_cfg["default_baseline"] = "B2"
    else:
        print("[VERDICT] B2 does NOT beat B1 on hard slices with non-overlapping intervals.")
        print("          Enforcing strict rule: B1 remains the default baseline system.")
        current_cfg["default_baseline"] = "B1"

    current_cfg["embedding_weight"] = best_weight
    current_cfg["b2_beats_b1_non_overlapping"] = beats_all_hard_slices_non_overlapping

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current_cfg, f, indent=2, sort_keys=True)
    print(f"Updated configuration saved to: {CONFIG_PATH}")
    print("=" * 76 + "\n")

    return {
        "optimal_embedding_weight": best_weight,
        "dev_b1_f1": b1_f1,
        "dev_b2_f1": best_b2_f1,
        "hard_slice_comparison": hard_slice_results,
        "b2_made_default": make_default
    }


if __name__ == "__main__":
    tune_b1_on_dev()
    tune_b2_on_dev()
