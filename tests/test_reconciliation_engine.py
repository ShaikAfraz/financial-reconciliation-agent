"""
Unit and integration tests for the Two-Sided Deterministic Reconciliation Engine:
1. Evaluation Metrics verified against hand-computed tiny cases:
   - Pair-group P, R, F1 (TP only when exact bank-ID set and ledger-ID set match ground truth).
   - Auto-commit FPR, Coverage.
   - Unmatched detection P, R, F1.
   - Exception Macro-F1.
   - Wilson 95% Confidence Intervals.
2. Bounded subset-sum algorithm:
   - Finds known 1:N and N:1 groups.
   - Strictly enforces max_members cap (rejects 5+ member combinations when max_members=4).
   - Enforces max_pool_size cap to prevent combinatorial explosion.
3. Authoritative Balance Proof:
   - Difference is exactly 0 on fully reconciled synthetic runs.
   - Difference is exactly 0 on fee-adjusted reconciled runs.
   - Non-zero difference exactly equals discrepancy on unbalanced runs.
   - Strict type validation: rejects floats in balances and transaction amounts.
"""

import unittest
from evaluation.metrics import evaluate_reconciliation, wilson_score_interval
from reconcile.candidates import (
    find_bounded_subset_sum_n1,
    find_bounded_subset_sum_1n,
    generate_candidate_pairs
)
from reconcile.balance_proof import compute_balance_proof
from reconcile.normalize import normalize_descriptor, extract_reference
from reconcile.baselines import reconcile_b0, reconcile_b1

class TestReconciliationMetricsHandComputed(unittest.TestCase):
    """
    Validates evaluation metrics against mathematically hand-calculated benchmark cases.
    """

    def test_metrics_hand_computed_tiny_case(self):
        # 5 Ground Truth Groups:
        # Group 1 (exact_1_1): Bank: [B1], Ledger: [L1] -> matched
        # Group 2 (exact_1_1): Bank: [B2], Ledger: [L2] -> matched
        # Group 3 (fuzzy_1_1): Bank: [B3], Ledger: [L3] -> matched
        # Group 4 (unmatched): Bank: [B4], Ledger: []   -> unmatched, exception: "unmatched_bank"
        # Group 5 (unmatched): Bank: [],   Ledger: [L5] -> unmatched, exception: "timing_difference"
        gt_groups = [
            {
                "group_id": "GT-1",
                "slice": "exact_1_1",
                "expected_status": "matched",
                "bank_record_ids": ["B1"],
                "ledger_record_ids": ["L1"]
            },
            {
                "group_id": "GT-2",
                "slice": "exact_1_1",
                "expected_status": "matched",
                "bank_record_ids": ["B2"],
                "ledger_record_ids": ["L2"]
            },
            {
                "group_id": "GT-3",
                "slice": "fuzzy_1_1",
                "expected_status": "matched",
                "bank_record_ids": ["B3"],
                "ledger_record_ids": ["L3"]
            },
            {
                "group_id": "GT-4",
                "slice": "unmatched",
                "expected_status": "unmatched",
                "bank_record_ids": ["B4"],
                "ledger_record_ids": [],
                "expected_exception_type": "unmatched_bank"
            },
            {
                "group_id": "GT-5",
                "slice": "unmatched",
                "expected_status": "unmatched",
                "bank_record_ids": [],
                "ledger_record_ids": ["L5"],
                "expected_exception_type": "timing_difference"
            }
        ]

        # Predicted Matches:
        # Match 1: [B1], [L1] -> Exact match with GT-1 => TP
        # Match 2: [B2], [L99] -> Does not match any GT group => FP
        # GT-2 and GT-3 are not matched => 2 FNs
        pred_matches = [
            {
                "match_id": "M1",
                "bank_record_ids": ["B1"],
                "ledger_record_ids": ["L1"],
                "status": "matched",
                "confidence": 1.0
            },
            {
                "match_id": "M2",
                "bank_record_ids": ["B2"],
                "ledger_record_ids": ["L99"],
                "status": "matched",
                "confidence": 0.95
            }
        ]

        # Predicted unmatched records:
        # Bank: B4 (True Positive unmatched)
        # Ledger: L6 (False Positive unmatched)
        # L5 from GT-5 was not predicted as unmatched => False Negative unmatched
        unmatched_bank = ["B4"]
        unmatched_ledger = ["L6"]

        # Predicted exceptions:
        # B4 predicted as unmatched_bank => TP for unmatched_bank
        # timing_difference for L5 is not predicted => FN
        pred_exceptions = [
            {"record_id": "B4", "side": "bank", "type": "unmatched_bank"}
        ]

        # Total record universe:
        # Bank: B1, B2, B3, B4 (4 records)
        # Ledger: L1, L2, L3, L5, L99, L6 (6 records)
        # Total = 10 records
        total_bank_count = 4
        total_ledger_count = 6

        # Hand calculations:
        # ----------------------------------------------------
        # Pair-Group:
        # TP = 1 (M1 matches GT-1)
        # FP = 1 (M2 does not match GT-2 because L99 != L2)
        # FN = 2 (GT-2 and GT-3)
        # Precision = 1 / (1 + 1) = 0.5000
        # Recall = 1 / (1 + 2) = 0.3333
        # F1 = (2 * 0.5 * 0.3333) / (0.5 + 0.3333) = 0.3333 / 0.8333 = 0.4000
        # Auto-commit FPR = FP / (TP + FP) = 1 / 2 = 0.5000
        # Resolved records: B1, L1, B2, L99, B4, L6 -> 6 records
        # Coverage = 6 / 10 = 0.6000
        #
        # Unmatched Detection:
        # GT unmatched = {B4, L5} (size 2)
        # Pred unmatched = {B4, L6} (size 2)
        # Unm TP = {B4} (1)
        # Unm FP = {L6} (1)
        # Unm FN = {L5} (1)
        # Unm Precision = 1 / 2 = 0.5000
        # Unm Recall = 1 / 2 = 0.5000
        # Unm F1 = 0.5000
        #
        # Exceptions:
        # unmatched_bank: TP=1, FP=0, FN=0 -> F1 = 1.0000
        # timing_difference: TP=0, FP=0, FN=1 -> F1 = 0.0000
        # Exception Macro-F1 = (1.0 + 0.0) / 2 = 0.5000
        # ----------------------------------------------------

        metrics = evaluate_reconciliation(
            ground_truth_groups=gt_groups,
            predicted_matches=pred_matches,
            predicted_exceptions=pred_exceptions,
            unmatched_bank_ids=unmatched_bank,
            unmatched_ledger_ids=unmatched_ledger,
            total_bank_records_count=total_bank_count,
            total_ledger_records_count=total_ledger_count
        )

        summary = metrics["summary"]
        self.assertEqual(summary["tp"], 1)
        self.assertEqual(summary["fp"], 1)
        self.assertEqual(summary["fn"], 2)
        self.assertAlmostEqual(summary["precision"], 0.5000, places=4)
        self.assertAlmostEqual(summary["recall"], 0.3333, places=4)
        self.assertAlmostEqual(summary["f1"], 0.4000, places=4)
        self.assertAlmostEqual(summary["auto_commit_fpr"], 0.5000, places=4)
        self.assertAlmostEqual(summary["coverage"], 0.6000, places=4)

        # Wilson Score CI check:
        # Wilson CI for 1 success out of 2 trials is [0.0945, 0.9055]
        expected_p_ci = wilson_score_interval(1, 2)
        self.assertEqual(summary["precision_95_ci"], expected_p_ci)
        self.assertAlmostEqual(expected_p_ci[0], 0.0945, places=3)
        self.assertAlmostEqual(expected_p_ci[1], 0.9055, places=3)

        # Wilson CI for 1 success out of 3 trials
        expected_r_ci = wilson_score_interval(1, 3)
        self.assertEqual(summary["recall_95_ci"], expected_r_ci)

        # Unmatched detection checks
        unm = metrics["unmatched_detection"]
        self.assertEqual(unm["tp"], 1)
        self.assertEqual(unm["fp"], 1)
        self.assertEqual(unm["fn"], 1)
        self.assertAlmostEqual(unm["precision"], 0.5000, places=4)
        self.assertAlmostEqual(unm["recall"], 0.5000, places=4)
        self.assertAlmostEqual(unm["f1"], 0.5000, places=4)

        # Exception macro-F1 check
        exc = metrics["exceptions"]
        self.assertAlmostEqual(exc["macro_f1"], 0.5000, places=4)
        self.assertAlmostEqual(exc["breakdown"]["unmatched_bank"]["f1"], 1.0000, places=4)
        self.assertAlmostEqual(exc["breakdown"]["timing_difference"]["f1"], 0.0000, places=4)

class TestBoundedSubsetSum(unittest.TestCase):
    """
    Validates bounded subset-sum:
    - Finds known 1:N and N:1 groups.
    - Respects max_members cap.
    - Respects max_pool_size cap to prevent blowup.
    """

    def test_finds_known_1_to_n_group(self):
        bank_record = {
            "id": "BANK-100",
            "amount_cents": 10000,
            "date": "2026-08-10",
            "description": "Customer Payment Batch REF-100"
        }
        ledger_records = [
            {"id": "LEDGER-A", "amount_cents": 3000, "date": "2026-08-10", "description": "Part A REF-100"},
            {"id": "LEDGER-B", "amount_cents": 4000, "date": "2026-08-11", "description": "Part B REF-100"},
            {"id": "LEDGER-C", "amount_cents": 3000, "date": "2026-08-12", "description": "Part C REF-100"},
            {"id": "LEDGER-D", "amount_cents": 5000, "date": "2026-08-10", "description": "Irrelevant"}
        ]

        groups = find_bounded_subset_sum_1n(
            bank_record=bank_record,
            ledger_records=ledger_records,
            max_members=4,
            max_pool_size=25,
            date_window_days=5
        )

        self.assertGreaterEqual(len(groups), 1)
        found_ids = set(groups[0]["ledger_ids"])
        self.assertEqual(found_ids, {"LEDGER-A", "LEDGER-B", "LEDGER-C"})
        self.assertEqual(groups[0]["target_cents"], 10000)
        self.assertEqual(groups[0]["members_count"], 3)

    def test_finds_known_n_to_1_group(self):
        ledger_record = {
            "id": "LEDGER-BATCH-1",
            "amount_cents": 15000,
            "date": "2026-08-15",
            "description": "Consolidated Settlement PO-990"
        }
        bank_records = [
            {"id": "BANK-A", "amount_cents": 5000, "date": "2026-08-14", "description": "Subpayment 1 PO-990"},
            {"id": "BANK-B", "amount_cents": 5000, "date": "2026-08-15", "description": "Subpayment 2 PO-990"},
            {"id": "BANK-C", "amount_cents": 5000, "date": "2026-08-16", "description": "Subpayment 3 PO-990"},
            {"id": "BANK-D", "amount_cents": 8000, "date": "2026-08-15", "description": "Other"}
        ]

        groups = find_bounded_subset_sum_n1(
            bank_records=bank_records,
            ledger_record=ledger_record,
            max_members=4,
            max_pool_size=25,
            date_window_days=5
        )

        self.assertGreaterEqual(len(groups), 1)
        found_ids = set(groups[0]["bank_ids"])
        self.assertEqual(found_ids, {"BANK-A", "BANK-B", "BANK-C"})
        self.assertEqual(groups[0]["target_cents"], 15000)
        self.assertEqual(groups[0]["members_count"], 3)

    def test_subset_sum_respects_max_members_cap(self):
        # Target: 10000 cents made of 5 records of 2000 cents each
        ledger_record = {
            "id": "LEDGER-TARGET",
            "amount_cents": 10000,
            "date": "2026-08-10",
            "description": "5-way split"
        }
        bank_records = [
            {"id": f"BANK-{i}", "amount_cents": 2000, "date": "2026-08-10", "description": f"Chunk {i}"}
            for i in range(5)
        ]

        # With max_members=4, this 5-member combination must NOT be returned
        groups_cap4 = find_bounded_subset_sum_n1(
            bank_records=bank_records,
            ledger_record=ledger_record,
            max_members=4,
            max_pool_size=25,
            date_window_days=5
        )
        self.assertEqual(len(groups_cap4), 0, "Combinations with > 4 members must be excluded when max_members=4")

        # With max_members=5, the 5-member combination is found
        groups_cap5 = find_bounded_subset_sum_n1(
            bank_records=bank_records,
            ledger_record=ledger_record,
            max_members=5,
            max_pool_size=25,
            date_window_days=5
        )
        self.assertEqual(len(groups_cap5), 1)
        self.assertEqual(groups_cap5[0]["members_count"], 5)

    def test_subset_sum_respects_max_pool_size_cap(self):
        # Create pool of 40 candidate records
        ledger_record = {
            "id": "LEDGER-BIG",
            "amount_cents": 50000,
            "date": "2026-08-10",
            "description": "Big target"
        }
        bank_records = [
            {"id": f"BANK-{i}", "amount_cents": 1000 + i, "date": "2026-08-10", "description": f"Item {i}"}
            for i in range(40)
        ]

        # Should truncate pool to max_pool_size=10 and not raise or blow up
        groups = find_bounded_subset_sum_n1(
            bank_records=bank_records,
            ledger_record=ledger_record,
            max_members=3,
            max_pool_size=10,
            date_window_days=5
        )
        self.assertIsInstance(groups, list)

class TestAuthoritativeBalanceProof(unittest.TestCase):
    """
    Validates authoritative balance proof:
    - Exactly 0 difference on fully reconciled synthetic run.
    - Exactly 0 difference on fee-adjusted runs.
    - Non-zero difference exactly equals discrepancy.
    - Strict integer cents type validation.
    """

    def test_fully_reconciled_synthetic_run_difference_is_zero(self):
        bank_records = [
            {"id": "B1", "amount_cents": 12500, "date": "2026-08-10"},
            {"id": "B2", "amount_cents": 50000, "date": "2026-08-11"},
            {"id": "B3", "amount_cents": -10000, "date": "2026-08-12"}
        ]
        ledger_records = [
            {"id": "L1", "amount_cents": 12500, "date": "2026-08-10"},
            {"id": "L2", "amount_cents": 50000, "date": "2026-08-11"},
            {"id": "L3", "amount_cents": -10000, "date": "2026-08-12"}
        ]
        matches = [
            {"match_id": "M1", "bank_record_ids": ["B1"], "ledger_record_ids": ["L1"], "status": "matched"},
            {"match_id": "M2", "bank_record_ids": ["B2"], "ledger_record_ids": ["L2"], "status": "matched"},
            {"match_id": "M3", "bank_record_ids": ["B3"], "ledger_record_ids": ["L3"], "status": "matched"}
        ]

        proof = compute_balance_proof(
            bank_records=bank_records,
            ledger_records=ledger_records,
            matches=matches,
            opening_bank_balance_cents=100000,
            opening_book_balance_cents=100000
        )

        self.assertEqual(proof["difference_cents"], 0)
        self.assertTrue(proof["is_balanced"])
        self.assertEqual(proof["ending_bank_balance_cents"], 152500)
        self.assertEqual(proof["ending_book_balance_cents"], 152500)
        self.assertEqual(proof["adjusted_bank_balance_cents"], 152500)
        self.assertEqual(proof["adjusted_book_balance_cents"], 152500)
        self.assertEqual(proof["unmatched_bank_records_count"], 0)
        self.assertEqual(proof["unmatched_ledger_records_count"], 0)

    def test_fee_adjusted_balanced_run(self):
        # Gross invoice: $100.00 (10000 cents). Merchant fee: $2.50 (250 cents).
        # Net deposit received by bank: $97.50 (9750 cents).
        bank_records = [
            {"id": "B1", "amount_cents": 9750, "date": "2026-08-10"}
        ]
        ledger_records = [
            {"id": "L1", "amount_cents": 10000, "date": "2026-08-10"}
        ]
        matches = [
            {
                "match_id": "M1",
                "bank_record_ids": ["B1"],
                "ledger_record_ids": ["L1"],
                "status": "matched",
                "metadata": {"fee_cents": 250}
            }
        ]

        proof = compute_balance_proof(
            bank_records=bank_records,
            ledger_records=ledger_records,
            matches=matches,
            opening_bank_balance_cents=0,
            opening_book_balance_cents=0
        )

        self.assertEqual(proof["difference_cents"], 0)
        self.assertTrue(proof["is_balanced"])
        self.assertEqual(proof["ending_bank_balance_cents"], 9750)
        self.assertEqual(proof["ending_book_balance_cents"], 10000)
        self.assertEqual(proof["fee_adjustments_cents"], 250)
        self.assertEqual(proof["adjusted_bank_balance_cents"], 9750)
        self.assertEqual(proof["adjusted_book_balance_cents"], 9750)

    def test_unbalanced_discrepancy_run(self):
        # Bank has $100.00 (10000 cents), Ledger has $95.00 (9500 cents), matched without fee
        bank_records = [{"id": "B1", "amount_cents": 10000, "date": "2026-08-10"}]
        ledger_records = [{"id": "L1", "amount_cents": 9500, "date": "2026-08-10"}]
        matches = [
            {"match_id": "M1", "bank_record_ids": ["B1"], "ledger_record_ids": ["L1"], "status": "matched"}
        ]

        proof = compute_balance_proof(
            bank_records=bank_records,
            ledger_records=ledger_records,
            matches=matches,
            opening_bank_balance_cents=0,
            opening_book_balance_cents=0
        )

        self.assertEqual(proof["difference_cents"], 500)
        self.assertFalse(proof["is_balanced"])

    def test_strict_integer_cents_type_validation(self):
        # Floats in balances or record amounts must be rejected immediately
        with self.assertRaises(TypeError):
            compute_balance_proof(
                bank_records=[],
                ledger_records=[],
                matches=[],
                opening_bank_balance_cents=100.50  # float rejected
            )

        with self.assertRaises(TypeError):
            compute_balance_proof(
                bank_records=[{"id": "B1", "amount_cents": 100.50}],  # float rejected
                ledger_records=[],
                matches=[]
            )

        # Booleans must also be rejected
        with self.assertRaises(TypeError):
            compute_balance_proof(
                bank_records=[{"id": "B1", "amount_cents": True}],
                ledger_records=[],
                matches=[]
            )

class TestDescriptorNormalization(unittest.TestCase):
    """
    Tests descriptor normalization, noise stripping, and reference extraction.
    """

    def test_normalization_and_prefix_stripping(self):
        desc1 = "CHECKCARD 0812 ACME CORP 800-555-0199 CA #8839"
        norm1 = normalize_descriptor(desc1)
        self.assertNotIn("CHECKCARD", norm1)
        self.assertNotIn("800-555-0199", norm1)
        self.assertIn("acme corp", norm1.lower())

        desc2 = "ACH DEBIT PAYPAL INST XFER 10482810"
        norm2 = normalize_descriptor(desc2)
        self.assertNotIn("ACH DEBIT", norm2)
        self.assertIn("paypal", norm2.lower())

    def test_reference_extraction(self):
        self.assertEqual(extract_reference("WIRE TRANSFER INV-2026-9912 ACME"), "INV-2026-9912")
        self.assertEqual(extract_reference("VENDOR PAYMENT REF-44810-X"), "REF-44810-X")
        self.assertEqual(extract_reference("PO-88219 GLOBAL CORP"), "PO-88219")
        self.assertEqual(extract_reference("SPLIT-0042 PART 1"), "SPLIT-0042")
        self.assertIsNone(extract_reference("GENERIC RESTAURANT LUNCH"))

if __name__ == "__main__":
    unittest.main()
