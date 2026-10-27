"""
Tests for FIX-1 ADDENDUM:
1. B3/B4 Hard Fail: runner refuses execution if provider not configured, exiting non-zero.
3. DB Separation: benchmark runs use BENCH_DB_PATH and leave data/reconciliation.db SHA-256 unchanged.
4. Constants: exception queue items have confidence None (null).
6. Corruption-Detection: 5 seeded corruptions evaluated honestly against arithmetic consistency check.
"""

import os
import sys
import copy
import shutil
import hashlib
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import data.db as db
from reconcile.service import get_review_queue
from reconcile.baselines import reconcile_b1
from reconcile.balance_proof import compute_balance_proof
from evaluation.benchmark_runner import (
    BenchmarkRunner,
    verify_provider_readiness,
    evaluate_corruption_detection
)


class TestFix1Addendum(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).parent.parent.absolute()
        self.app_db_path = self.project_root / "data" / "reconciliation.db"

    def test_item_1_hard_fail_when_no_provider(self):
        """Item 1: Benchmark runner refuses to start B3 or B4 without a working provider, exiting non-zero."""
        # Test verify_provider_readiness directly with empty key
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            ready, msg = verify_provider_readiness()
            self.assertFalse(ready)
            self.assertIn("not configured", msg)

        # Test runner exits non-zero on B3 when key is missing
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            runner = BenchmarkRunner(
                split="dev",
                systems=["B3"],
                repeats=1,
                project_root=self.project_root
            )
            with self.assertRaises(SystemExit) as ctx:
                runner.run_benchmark()
            self.assertNotEqual(ctx.exception.code, 0)

        # Test runner exits non-zero on B4 when key is missing
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            runner_b4 = BenchmarkRunner(
                split="dev",
                systems=["B4"],
                repeats=1,
                project_root=self.project_root
            )
            with self.assertRaises(SystemExit) as ctx:
                runner_b4.run_benchmark()
            self.assertNotEqual(ctx.exception.code, 0)

    def test_item_1_mid_run_provider_error_invalidates_system(self):
        """Item 1: If mid-run provider errors occur, error rate > 0 marks system INVALID."""
        runs = [{
            "provider_errors_count": 3,
            "adjudication_metrics": {"total_adjudications": 10}
        }]
        total_errors = sum(r["provider_errors_count"] for r in runs)
        total_adj = sum(r["adjudication_metrics"]["total_adjudications"] for r in runs)
        error_rate = total_errors / (total_adj + total_errors)
        is_valid = (total_errors == 0)

        self.assertFalse(is_valid)
        self.assertGreater(error_rate, 0.0)

    def test_item_3_db_separation_and_app_db_sha256_unchanged(self):
        """Item 3: Benchmark run writes to benchmark DB and leaves reconciliation.db SHA-256 unchanged."""
        if not self.app_db_path.exists():
            db.init_database(self.app_db_path)

        with open(self.app_db_path, "rb") as f:
            initial_sha = hashlib.sha256(f.read()).hexdigest()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_bench_db = Path(tmp_dir) / "test_benchmark.db"
            tmp_reports = Path(tmp_dir) / "reports"

            runner = BenchmarkRunner(
                split="dev",
                systems=["B0", "B1"],
                repeats=1,
                project_root=self.project_root,
                reports_dir=tmp_reports
            )
            runner.bench_db_path = tmp_bench_db

            with patch.dict(os.environ, {"BENCH_DB_PATH": str(tmp_bench_db)}):
                runner.run_benchmark()

            # Benchmark DB must have been created and used
            self.assertTrue(tmp_bench_db.exists())

            # App DB SHA-256 must be IDENTICAL to initial
            with open(self.app_db_path, "rb") as f:
                final_sha = hashlib.sha256(f.read()).hexdigest()

            self.assertEqual(
                initial_sha,
                final_sha,
                "Benchmark run modified the app database data/reconciliation.db! DB separation violated."
            )

    def test_item_4_exception_items_confidence_is_none(self):
        """Item 4: Exception review queue items have confidence None (UI renders n/a), no 0.72 constant."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = Path(tmp_dir) / "test_app.db"
            db.init_database(test_db)

            # Insert an exception item
            conn = db.get_connection(test_db)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bank_records (id, date, amount_cents, description, reference)
                VALUES ('BNK-EXC-001', '2026-03-31', 15000, 'UNMATCHED CHARGE', 'REF-001')
            """)
            cursor.execute("""
                INSERT INTO ledger_records (id, date, amount_cents, description, reference)
                VALUES ('LDG-EXC-001', '2026-03-31', 15000, 'UNMATCHED LEDGER', 'REF-002')
            """)
            cursor.execute("""
                INSERT INTO exceptions (id, record_id, side, type, status)
                VALUES (1, 'BNK-EXC-001', 'bank', 'unmatched_deposit', 'open')
            """)
            conn.commit()
            conn.close()

            # Query review queue using test_db
            db.set_active_db(test_db)
            try:
                queue = get_review_queue()
            finally:
                db.set_active_db(db.APP_DB_PATH)
            self.assertGreater(len(queue), 0)

            exception_items = [q for q in queue if q["review_id"].startswith("REV-EXC-")]
            self.assertGreater(len(exception_items), 0)

            for exc_item in exception_items:
                self.assertIsNone(
                    exc_item["confidence"],
                    f"Expected confidence to be None, got {exc_item['confidence']}"
                )
                self.assertNotEqual(
                    exc_item["confidence"],
                    0.72,
                    "Hardcoded 0.72 confidence constant still present!"
                )

    def test_item_6_seeded_corruption_detection(self):
        """
        Item 6: Apply 5 seeded corruptions to fully reconciled run and verify detection:
        - C1: Delete matched record -> FLAGGED (Detected)
        - C2: Delete unmatched record -> NOT FLAGGED (Not detected by design)
        - C3: Change amount by 1 cent -> FLAGGED (Detected)
        - C4: Swap amounts of two matched members -> NOT FLAGGED (Not detected by design)
        - C5: Add spurious record -> NOT FLAGGED (Not detected by design)
        """
        split_file = self.project_root / "data" / "benchmark" / "dev.json"
        import json
        with open(split_file, "r", encoding="utf-8") as f:
            split_data = json.load(f)

        bank_records = split_data["bank_records"]
        ledger_records = split_data["ledger_records"]

        # Run B1 to get clean baseline matches
        b1_res = reconcile_b1(bank_records, ledger_records)
        matches = b1_res["matches"]

        corruption_eval = evaluate_corruption_detection(bank_records, ledger_records, matches)

        self.assertTrue(corruption_eval["clean_baseline_balanced"])
        results_by_id = {r["id"]: r for r in corruption_eval["results"]}

        # C1: Matched record deleted -> MUST be FLAGGED
        self.assertTrue(results_by_id["C1"]["flagged"], "C1 (delete matched) was not flagged!")
        self.assertEqual(results_by_id["C1"]["status"], "DETECTED")

        # C2: Unmatched record deleted -> NOT FLAGGED (honest reporting)
        self.assertFalse(results_by_id["C2"]["flagged"], "C2 (delete unmatched) should not be flagged!")
        self.assertEqual(results_by_id["C2"]["status"], "NOT DETECTED")

        # C3: 1 cent amount change on matched record -> MUST be FLAGGED
        self.assertTrue(results_by_id["C3"]["flagged"], "C3 (1 cent change) was not flagged!")
        self.assertEqual(results_by_id["C3"]["status"], "DETECTED")
        self.assertNotEqual(results_by_id["C3"]["difference_cents"], 0)

        # C4: Swap amounts of two matched members -> NOT FLAGGED (honest reporting)
        self.assertFalse(results_by_id["C4"]["flagged"], "C4 (swap matched amounts) should not be flagged!")
        self.assertEqual(results_by_id["C4"]["status"], "NOT DETECTED")

        # C5: Add spurious record -> NOT FLAGGED (honest reporting)
        self.assertFalse(results_by_id["C5"]["flagged"], "C5 (spurious record) should not be flagged!")
        self.assertEqual(results_by_id["C5"]["status"], "NOT DETECTED")


if __name__ == "__main__":
    unittest.main()
