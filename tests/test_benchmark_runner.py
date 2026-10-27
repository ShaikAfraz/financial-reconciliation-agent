"""
Unit and regression tests for BenchmarkRunner and evaluation metrics:
1. Expected Calibration Error (ECE) calculation against known distributions.
2. Wilson interval overlap detection.
3. Precision-vs-Coverage curve computation.
4. Run-to-run consistency calculations (mean, variance, std dev, min, max).
5. Strict refusal to run on SHA-256 mismatch (integrity gate).
6. Fail-loudly behavior on missing report data (no silent imputation).
7. Human evaluation sheet export format (exactly 30 rows with blank scoring columns).
"""

import os
import csv
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.metrics import (
    calculate_ece,
    check_interval_overlap,
    compute_precision_coverage_curve,
    calculate_p50_p95,
    calculate_tokens_and_cost,
    calculate_run_consistency,
    wilson_score_interval
)
from evaluation.benchmark_runner import (
    BenchmarkRunner,
    verify_dataset_integrity
)

class TestBenchmarkMetricsAndRunner(unittest.TestCase):

    def test_ece_calculation_perfect_calibration(self):
        """ECE should be 0.0 when confidences exactly match accuracies."""
        # 10 predictions with 1.0 confidence, all correct
        confs = [1.0] * 10
        accs = [True] * 10
        ece = calculate_ece(confs, accs, num_bins=10)
        self.assertEqual(ece, 0.0)

    def test_ece_calculation_known_value(self):
        """ECE calculation on known binned distribution."""
        # Bin [0.8, 0.9): 5 predictions at 0.85 confidence, all False (acc = 0.0) -> error = 0.85
        # Bin [0.5, 0.6): 5 predictions at 0.55 confidence, all True (acc = 1.0) -> error = 0.45
        # Weighted ECE = 0.5 * 0.85 + 0.5 * 0.45 = 0.65
        confs = [0.85] * 5 + [0.55] * 5
        accs = [False] * 5 + [True] * 5
        ece = calculate_ece(confs, accs, num_bins=10)
        self.assertAlmostEqual(ece, 0.65, places=3)

    def test_ece_empty_inputs(self):
        self.assertEqual(calculate_ece([], []), 0.0)

    def test_ece_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            calculate_ece([0.9], [True, False])

    def test_interval_overlap_cases(self):
        """Test interval overlap checking logic."""
        # Clearly overlapping
        self.assertTrue(check_interval_overlap((0.70, 0.90), (0.85, 0.95)))
        # Boundary touching is considered overlapping (closed intervals)
        self.assertTrue(check_interval_overlap((0.50, 0.70), (0.70, 0.85)))
        # Strictly disjoint
        self.assertFalse(check_interval_overlap((0.40, 0.60), (0.75, 0.90)))
        # Reversed order should still check overlap correctly
        self.assertTrue(check_interval_overlap((0.85, 0.95), (0.70, 0.90)))

    def test_precision_coverage_curve(self):
        """Verify precision-vs-coverage curve computation across thresholds."""
        gt_groups = [
            {"group_id": "G1", "bank_record_ids": ["B1"], "ledger_record_ids": ["L1"], "expected_status": "matched"},
            {"group_id": "G2", "bank_record_ids": ["B2"], "ledger_record_ids": ["L2"], "expected_status": "matched"},
        ]
        matches = [
            {"match_id": "M1", "bank_record_ids": ["B1"], "ledger_record_ids": ["L1"], "confidence": 0.95},
            {"match_id": "M2", "bank_record_ids": ["B2"], "ledger_record_ids": ["L3"], "confidence": 0.80},  # FP
        ]
        total_records = 4  # B1, B2, L1, L2
        curve = compute_precision_coverage_curve(matches, gt_groups, total_records, thresholds=[0.70, 0.90])

        self.assertEqual(len(curve), 2)
        # At threshold 0.70: both M1 and M2 included -> TP=1, FP=1, precision=0.5, coverage=4/4=1.0
        pt70 = next(c for c in curve if c["threshold"] == 0.70)
        self.assertEqual(pt70["precision"], 0.5)
        self.assertEqual(pt70["coverage"], 1.0)
        self.assertEqual(pt70["tp"], 1)
        self.assertEqual(pt70["fp"], 1)

        # At threshold 0.90: only M1 included -> TP=1, FP=0, precision=1.0, coverage=2/4=0.5
        pt90 = next(c for c in curve if c["threshold"] == 0.90)
        self.assertEqual(pt90["precision"], 1.0)
        self.assertEqual(pt90["coverage"], 0.5)
        self.assertEqual(pt90["tp"], 1)
        self.assertEqual(pt90["fp"], 0)

    def test_run_consistency_calculation(self):
        """Test calculation of mean, variance, sample std dev, min, and max."""
        values = [0.80, 0.82, 0.84, 0.86, 0.88]
        stats = calculate_run_consistency(values)
        self.assertAlmostEqual(stats["mean"], 0.84, places=4)
        self.assertEqual(stats["min"], 0.80)
        self.assertEqual(stats["max"], 0.88)
        self.assertGreater(stats["std"], 0.0)
        self.assertGreater(stats["variance"], 0.0)

    def test_run_consistency_fails_loudly_on_empty(self):
        """Strictly refuses to impute missing data by raising ValueError."""
        with self.assertRaises(ValueError):
            calculate_run_consistency([])

    def test_sha256_verification_integrity_gate(self):
        """Test that dataset integrity verification succeeds on match and aborts on mismatch."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            data_file = tmp_path / "test.json"
            sha_file = tmp_path / "test.sha256"

            content = b'{"status": "authentic_benchmark_data"}'
            data_file.write_bytes(content)
            correct_sha = "d0cb37492c9f7a77e20560a0a5aa87f2fae3fbf2585f67b458b091f08fa0d4c1"  # SHA-256 of content
            import hashlib
            computed_sha = hashlib.sha256(content).hexdigest()
            sha_file.write_text(computed_sha)

            # 1. Matching checksum succeeds
            verified = verify_dataset_integrity(data_file, sha_file)
            self.assertEqual(verified, computed_sha)

            # 2. Tampered checksum fails loudly
            sha_file.write_text("corrupted_or_tampered_hash_value_12345")
            with self.assertRaises(ValueError) as ctx:
                verify_dataset_integrity(data_file, sha_file)
            self.assertIn("Integrity check FAILED", str(ctx.exception))
            self.assertIn("REFUSED", str(ctx.exception))

    def test_report_generator_fails_loudly_on_missing_metric(self):
        """Report generator must raise ValueError rather than imputing missing metrics."""
        runner = BenchmarkRunner(split="dev", systems=["B1"], repeats=1)
        corrupted_bundle = {
            "timestamp": "20261027_000000",
            "split": "dev",
            "repeats": 1,
            "systems": ["B1"],
            "system_summaries": {
                "B1": {
                    "system": "B1",
                    "repeats": 1,
                    "consistency": {
                        "f1": {"mean": 0.90, "variance": 0.0, "std": 0.0, "min": 0.90, "max": 0.90}
                        # Intentionally missing 'precision', 'recall', etc.
                    },
                    "representative_run": {}
                }
            },
            "hard_slices_comparison": {},
            "human_evaluation_export": "reports/human_eval_sheet.csv"
        }
        with self.assertRaises(ValueError):
            runner.generate_markdown_report(corrupted_bundle)

    def test_human_evaluation_sheet_format(self):
        """Verifies exactly 30 rows exported and factual_correctness/usefulness left strictly blank."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_reports = Path(tmp_dir) / "reports"
            runner = BenchmarkRunner(split="dev", reports_dir=tmp_reports, seed=42)

            # Create mock adjudications
            mock_adjudications = []
            for i in range(40):
                mock_adjudications.append({
                    "target_item": {"id": f"BANK-{i:03d}", "description": f"Target item {i}"},
                    "candidate_records": [{"id": f"LEDGER-{i:03d}", "description": f"Cand item {i}"}],
                    "outcome": {
                        "decision": "select" if i % 2 == 0 else "escalate",
                        "selected_candidate_ids": [f"LEDGER-{i:03d}"] if i % 2 == 0 else [],
                        "rationale": f"Deterministic feature alignment for item {i}."
                    }
                })

            csv_path = runner.export_human_evaluation_sheet(mock_adjudications, {})
            self.assertTrue(csv_path.exists())

            rows = []
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                expected_cols = [
                    "sample_id", "item_id", "item_description", "candidate_id",
                    "candidate_description", "decision", "rationale",
                    "factual_correctness", "usefulness", "notes"
                ]
                self.assertEqual(reader.fieldnames, expected_cols)
                for r in reader:
                    rows.append(r)

            # Strictly 30 samples
            self.assertEqual(len(rows), 30)

            # Strictly left blank for human evaluation
            for r in rows:
                self.assertEqual(r["factual_correctness"], "", f"factual_correctness must be blank: {r}")
                self.assertEqual(r["usefulness"], "", f"usefulness must be blank: {r}")
                self.assertEqual(r["notes"], "")
                self.assertTrue(r["item_id"])
                self.assertTrue(r["rationale"])

    def test_end_to_end_benchmark_runner_b0_b1(self):
        """Executes a 2-repeat benchmark of B0 and B1 and checks all outputs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_reports = Path(tmp_dir) / "reports"
            runner = BenchmarkRunner(
                split="dev",
                systems=["B0", "B1"],
                repeats=2,
                reports_dir=tmp_reports,
                seed=42
            )
            bundle = runner.run_benchmark()

            self.assertIn("B0", bundle["system_summaries"])
            self.assertIn("B1", bundle["system_summaries"])

            b0_sum = bundle["system_summaries"]["B0"]
            b1_sum = bundle["system_summaries"]["B1"]

            self.assertEqual(b0_sum["repeats"], 2)
            self.assertEqual(b1_sum["repeats"], 2)

            # Both runs balanced
            self.assertTrue(b0_sum["representative_run"]["balance_proof"]["is_balanced"])
            self.assertEqual(b0_sum["representative_run"]["balance_proof"]["difference_cents"], 0)
            self.assertTrue(b1_sum["representative_run"]["balance_proof"]["is_balanced"])
            self.assertEqual(b1_sum["representative_run"]["balance_proof"]["difference_cents"], 0)

            # B1 should have higher F1 than B0
            self.assertGreaterEqual(b1_sum["consistency"]["f1"]["mean"], b0_sum["consistency"]["f1"]["mean"])

            # Verify files were created
            csv_file = tmp_reports / "human_eval_sheet.csv"
            self.assertTrue(csv_file.exists())

            json_files = list(tmp_reports.glob("benchmark_*.json"))
            md_files = list(tmp_reports.glob("benchmark_*.md"))
            self.assertEqual(len(json_files), 1)
            self.assertEqual(len(md_files), 1)

if __name__ == "__main__":
    unittest.main()
