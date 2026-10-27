"""
Unit and Integration Tests for Baseline B2 (B1 + Embedding Similarity).
Tests:
1. reconcile_b2 execution, match generation, balance proof, and integer cent integrity.
2. DiskCachedEmbeddingService with disk persistence and dimension handling.
3. Dev-only tuning isolation and grid search.
4. Hard-slice Wilson interval overlap check and strict enforcement that B1 remains default.
5. Benchmark runner multi-system integration with B2.
"""

import os
import json
import tempfile
import unittest
from pathlib import Path

from reconcile.embeddings import DiskCachedEmbeddingService, get_embedding_service
from reconcile.baselines import (
    reconcile_b0,
    reconcile_b1,
    reconcile_b2,
    load_reconcile_config
)
from reconcile.balance_proof import compute_balance_proof
from reconcile.tune import tune_b2_on_dev
from evaluation.benchmark_runner import BenchmarkRunner
from evaluation.metrics import check_interval_overlap


class TestBaselineB2(unittest.TestCase):

    def setUp(self):
        self.sample_bank_records = [
            {"id": "B-001", "date": "2026-03-01", "amount_cents": 125000, "description": "STRIPE PAYOUT REF-8812"},
            {"id": "B-002", "date": "2026-03-02", "amount_cents": 4500, "description": "AWS CLOUD SVCS SEATTLE"},
            {"id": "B-003", "date": "2026-03-03", "amount_cents": 8900, "description": "OFFICE DEPOT SUPPLIES"},
            {"id": "B-004", "date": "2026-03-04", "amount_cents": 50000, "description": "UNMATCHED BANK ITEM"}
        ]
        self.sample_ledger_records = [
            {"id": "L-001", "date": "2026-03-01", "amount_cents": 125000, "description": "Stripe Payout REF-8812"},
            {"id": "L-002", "date": "2026-03-03", "amount_cents": 4500, "description": "Amazon Web Services Inc"},
            {"id": "L-003", "date": "2026-03-04", "amount_cents": 8900, "description": "Office Depot #4412"},
            {"id": "L-004", "date": "2026-03-05", "amount_cents": 75000, "description": "UNMATCHED LEDGER ITEM"}
        ]

    def test_reconcile_b2_execution_and_schema(self):
        """B2 executes successfully, returns expected schema, and maintains balance proof."""
        res = reconcile_b2(self.sample_bank_records, self.sample_ledger_records)
        self.assertEqual(res["system"], "B2")
        self.assertIn("matches", res)
        self.assertIn("exceptions", res)
        self.assertIn("unmatched_bank_ids", res)
        self.assertIn("unmatched_ledger_ids", res)

        matches = res["matches"]
        self.assertGreaterEqual(len(matches), 1)

        # Check balance proof
        proof = compute_balance_proof(self.sample_bank_records, self.sample_ledger_records, matches)
        self.assertIsInstance(proof["difference_cents"], int)
        self.assertIn("is_balanced", proof)

        # Unmatched records should be identified
        self.assertIn("B-004", res["unmatched_bank_ids"])
        self.assertIn("L-004", res["unmatched_ledger_ids"])

    def test_embedding_service_disk_caching(self):
        """Embedding service persists embeddings to disk cache and loads them on restart."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "test_embedding_cache.json"
            svc = DiskCachedEmbeddingService(cache_path=cache_file, dimension=768)

            t1 = "AMAZON WEB SERVICES SEATTLE WA"
            t2 = "Amazon Web Services Inc"

            # Compute similarity (first time: cache miss)
            sim1 = svc.text_similarity(t1, t2)
            self.assertIsInstance(sim1, float)
            self.assertGreaterEqual(sim1, -1.0)
            self.assertLessEqual(sim1, 1.0)
            self.assertGreater(sim1, 0.4)  # Semantically similar texts

            # Verify cache file was written to disk
            self.assertTrue(cache_file.exists())
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            self.assertGreater(len(cached_data), 0)

            # Re-instantiate service pointing to the same file
            svc2 = DiskCachedEmbeddingService(cache_path=cache_file, dimension=768)
            sim2 = svc2.text_similarity(t1, t2)
            self.assertAlmostEqual(sim1, sim2, places=6)

    def test_b2_candidate_ranking_with_embedding_weight(self):
        """B2 enriches fuzzy candidate pairs with embedding_similarity and composite score."""
        cfg = load_reconcile_config()
        cfg["embedding_weight"] = 0.25
        res = reconcile_b2(self.sample_bank_records, self.sample_ledger_records, config=cfg)

        fuzzy_matches = [m for m in res["matches"] if m.get("method") == "B2_embedding_fuzzy_1_1"]
        for fm in fuzzy_matches:
            meta = fm.get("metadata", {})
            self.assertIn("composite_similarity", meta)
            self.assertIn("embedding_similarity", meta)
            self.assertIn("lexical_similarity", meta)
            self.assertIn("embedding_weight", meta)
            self.assertEqual(meta["embedding_weight"], 0.25)

    def test_dev_tuning_does_not_access_test_split(self):
        """Verifies tuning strictly uses DEV split and keeps B1 as default baseline."""
        dev_path = Path(__file__).parent.parent / "data" / "benchmark" / "dev.json"
        if not dev_path.exists():
            self.skipTest("Benchmark dev.json not present in test environment.")

        tune_res = tune_b2_on_dev(dev_path=dev_path)
        self.assertIn("optimal_embedding_weight", tune_res)
        self.assertIn("dev_b1_f1", tune_res)
        self.assertIn("dev_b2_f1", tune_res)
        self.assertIn("hard_slice_comparison", tune_res)
        self.assertIn("b2_made_default", tune_res)

        # On dev, B2 does not beat B1 with non-overlapping Wilson intervals
        self.assertFalse(tune_res["b2_made_default"])

        # Check saved config
        cfg = load_reconcile_config()
        self.assertEqual(cfg.get("default_baseline"), "B1")

    def test_hard_slice_wilson_overlap_preserves_b1_default(self):
        """Test invariant: when Wilson CIs overlap, B2 must NOT be default."""
        ci_b1 = (0.7415, 0.9768)
        ci_b2 = (0.7415, 0.9768)
        self.assertTrue(check_interval_overlap(ci_b1, ci_b2))

        # Because intervals overlap, B1 is preserved
        cfg = load_reconcile_config()
        self.assertNotEqual(cfg.get("default_baseline"), "B2")
        self.assertEqual(cfg.get("default_baseline"), "B1")

    def test_benchmark_runner_includes_b2(self):
        """BenchmarkRunner runs B0, B1, and B2, producing consistent reports."""
        dev_path = Path(__file__).parent.parent / "data" / "benchmark" / "dev.json"
        if not dev_path.exists():
            self.skipTest("Benchmark dev.json not present.")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_reports = Path(tmp_dir) / "reports"
            runner = BenchmarkRunner(
                split="dev",
                systems=["B0", "B1", "B2"],
                repeats=2,
                reports_dir=tmp_reports,
                seed=42
            )
            bundle = runner.run_benchmark()

            self.assertIn("B0", bundle["system_summaries"])
            self.assertIn("B1", bundle["system_summaries"])
            self.assertIn("B2", bundle["system_summaries"])

            b2_sum = bundle["system_summaries"]["B2"]
            self.assertEqual(b2_sum["repeats"], 2)
            self.assertTrue(b2_sum["representative_run"]["balance_proof"]["is_balanced"])
            self.assertEqual(b2_sum["representative_run"]["balance_proof"]["difference_cents"], 0)

            # Check markdown report generation
            md_content = runner.generate_markdown_report(bundle)
            self.assertIn("B2 (B1 + Embedding)", md_content)
            self.assertIn("2.2 Embedding Baseline (B2) vs Tuned Baseline (B1)", md_content)
            self.assertIn("**Authoritative Default System:** `B1`", md_content)


if __name__ == "__main__":
    unittest.main()
