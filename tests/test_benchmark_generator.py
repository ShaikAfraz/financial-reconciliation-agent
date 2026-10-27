"""
Unit and regression tests for Two-Sided Reconciliation Benchmark & Dataset Generator:
1. Byte-identical output for same seed.
2. Distinct output for different seeds.
3. Every ground-truth group's cents sum constraints hold down to the exact integer cent.
4. Slice proportions within exact tolerances.
5. No record ID appears in both dev and test splits.
6. Handwritten descriptors loader & validator (clear error if < 40).
7. Database schema and integer cents enforcement for two-sided tables.
"""

import json
import tempfile
import unittest
from pathlib import Path
from evaluation.dataset_generator import (
    generate_benchmark,
    BenchmarkDatasetGenerator,
    load_handwritten_descriptors,
    validate_handwritten_descriptors,
    SLICE_TARGETS
)
import data.db as db

class TestBenchmarkGenerator(unittest.TestCase):

    def test_same_seed_gives_byte_identical_output(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            dir1 = Path(tmp1)
            dir2 = Path(tmp2)

            dev1, test1, sha1 = generate_benchmark(seed=42, output_dir=dir1)
            dev2, test2, sha2 = generate_benchmark(seed=42, output_dir=dir2)

            # Byte-identical checks
            self.assertEqual(dev1.read_bytes(), dev2.read_bytes(), "dev.json bytes must be identical for same seed")
            self.assertEqual(test1.read_bytes(), test2.read_bytes(), "test.json bytes must be identical for same seed")
            self.assertEqual(sha1.read_text(), sha2.read_text(), "test.sha256 must be identical for same seed")

    def test_different_seeds_produce_different_outputs(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            dev1, test1, _ = generate_benchmark(seed=42, output_dir=Path(tmp1))
            dev2, test2, _ = generate_benchmark(seed=999, output_dir=Path(tmp2))

            self.assertNotEqual(dev1.read_bytes(), dev2.read_bytes())
            self.assertNotEqual(test1.read_bytes(), test2.read_bytes())

    def test_no_record_id_appears_in_both_splits(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev_p, test_p, _ = generate_benchmark(seed=42, output_dir=Path(tmp))
            with open(dev_p) as f:
                dev = json.load(f)
            with open(test_p) as f:
                test = json.load(f)

            dev_b_ids = {r["id"] for r in dev["bank_records"]}
            test_b_ids = {r["id"] for r in test["bank_records"]}
            self.assertTrue(dev_b_ids.isdisjoint(test_b_ids), "Bank record IDs must not overlap between splits")

            dev_l_ids = {r["id"] for r in dev["ledger_records"]}
            test_l_ids = {r["id"] for r in test["ledger_records"]}
            self.assertTrue(dev_l_ids.isdisjoint(test_l_ids), "Ledger record IDs must not overlap between splits")

            dev_g_ids = {g["group_id"] for g in dev["ground_truth"]}
            test_g_ids = {g["group_id"] for g in test["ground_truth"]}
            self.assertTrue(dev_g_ids.isdisjoint(test_g_ids), "Group IDs must not overlap between splits")

    def test_cents_sum_constraints_hold_for_all_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev_p, test_p, _ = generate_benchmark(seed=42, output_dir=Path(tmp))

            for split_file in [dev_p, test_p]:
                with open(split_file) as f:
                    data = json.load(f)

                bank_map = {r["id"]: r for r in data["bank_records"]}
                ledger_map = {r["id"]: r for r in data["ledger_records"]}

                for g in data["ground_truth"]:
                    s_name = g["slice"]
                    b_ids = g["bank_record_ids"]
                    l_ids = g["ledger_record_ids"]

                    b_records = [bank_map[bid] for bid in b_ids]
                    l_records = [ledger_map[lid] for lid in l_ids]

                    # Verify all amount_cents are integers
                    for r in b_records:
                        self.assertIsInstance(r["amount_cents"], int)
                        self.assertNotIsInstance(r["amount_cents"], bool)
                    for r in l_records:
                        self.assertIsInstance(r["amount_cents"], int)
                        self.assertNotIsInstance(r["amount_cents"], bool)

                    b_sum = sum(r["amount_cents"] for r in b_records)
                    l_sum = sum(r["amount_cents"] for r in l_records)

                    if s_name in ("exact_1_1", "fuzzy_1_1", "split_n_1", "split_1_n", "malformed_injection"):
                        self.assertEqual(
                            b_sum, l_sum,
                            f"Exact sum constraint violated in group {g['group_id']} for slice {s_name}: bank={b_sum}, ledger={l_sum}"
                        )

                    elif s_name == "fee_adjusted":
                        fee = g["metadata"]["fee_cents"]
                        self.assertIsInstance(fee, int)
                        self.assertEqual(
                            b_sum + fee, l_sum,
                            f"Fee adjustment constraint violated in group {g['group_id']}: net({b_sum}) + fee({fee}) != gross({l_sum})"
                        )

                    elif s_name == "ambiguous":
                        # Exactly one correct candidate matches the bank record amount
                        self.assertEqual(len(b_records), 1)
                        self.assertGreaterEqual(len(l_records), 2)
                        for l_rec in l_records:
                            self.assertEqual(b_records[0]["amount_cents"], l_rec["amount_cents"])

                    elif s_name == "duplicate":
                        self.assertEqual(len(b_records), 2)
                        self.assertEqual(len(l_records), 1)
                        self.assertEqual(b_records[0]["amount_cents"], b_records[1]["amount_cents"])
                        self.assertEqual(b_records[0]["amount_cents"], l_records[0]["amount_cents"])

                    elif s_name == "unmatched":
                        self.assertTrue(len(b_records) == 0 or len(l_records) == 0)

    def test_slice_proportions_within_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev_p, test_p, _ = generate_benchmark(seed=42, output_dir=Path(tmp))
            with open(dev_p) as f:
                dev = json.load(f)
            with open(test_p) as f:
                test = json.load(f)

            total_groups = len(dev["ground_truth"]) + len(test["ground_truth"])
            self.assertEqual(total_groups, 400)

            # Combined counts
            counts = {}
            for s in SLICE_TARGETS:
                counts[s] = dev["metadata"]["slice_counts"][s] + test["metadata"]["slice_counts"][s]

            # Verify target shares
            self.assertEqual(counts["exact_1_1"], 120)  # 30%
            self.assertEqual(counts["fuzzy_1_1"], 100)  # 25%
            self.assertEqual(counts["split_n_1"] + counts["split_1_n"], 48)  # 12%
            self.assertEqual(counts["fee_adjusted"], 24)  # 6%
            self.assertEqual(counts["ambiguous"], 48)  # 12%
            self.assertEqual(counts["duplicate"], 20)  # 5%
            self.assertEqual(counts["unmatched"], 32)  # 8%
            self.assertEqual(counts["malformed_injection"], 8)  # 2%

            # Stratification: exact 50/50 split for each slice
            for s in SLICE_TARGETS:
                self.assertEqual(dev["metadata"]["slice_counts"][s], test["metadata"]["slice_counts"][s])

    def test_handwritten_descriptors_validation(self):
        # Valid loader
        descriptors = load_handwritten_descriptors()
        self.assertGreaterEqual(len(descriptors), 40)
        for d in descriptors:
            self.assertIn("bank_descriptor", d)
            self.assertIn("ledger_descriptor", d)
            self.assertTrue(bool(d["bank_descriptor"].strip()))
            self.assertTrue(bool(d["ledger_descriptor"].strip()))

        # Rejection when fewer than 40 pairs
        with self.assertRaises(ValueError) as ctx:
            validate_handwritten_descriptors(descriptors[:39])
        self.assertIn("expected at least 40 pairs", str(ctx.exception))

        # Rejection on malformed items
        with self.assertRaises(ValueError):
            validate_handwritten_descriptors(descriptors[:40] + [{"bank_descriptor": ""}])
        with self.assertRaises(ValueError):
            validate_handwritten_descriptors("not a list")

    def test_database_two_sided_records_integer_cents(self):
        # Verify db insertion works with integer cents
        db.init_database()

        bank_id = "BANK-TEST-001"
        ledger_id = "LEDGER-TEST-001"
        match_id = "MATCH-TEST-001"

        db.insert_bank_record(bank_id, "2026-08-15", 12500, "AMZN MKTP US*2B710", "REF-001")
        db.insert_ledger_record(ledger_id, "2026-08-15", 12500, "Amazon Web Services INV-88210", "REF-001")
        db.insert_match(match_id, "matched", "exact_1_1", 1.0)
        db.add_match_member(match_id, "bank", bank_id)
        db.add_match_member(match_id, "ledger", ledger_id)
        db.insert_exception(bank_id, "bank", "timing_difference", "open")
        db.insert_review(match_id, "approved", "Deterministic verification confirmed")

        conn = db.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT amount_cents, description FROM bank_records WHERE id = ?", (bank_id,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["amount_cents"], 12500)
        self.assertIsInstance(row["amount_cents"], int)

        cursor.execute("SELECT amount_cents FROM ledger_records WHERE id = ?", (ledger_id,))
        row_l = cursor.fetchone()
        self.assertEqual(row_l["amount_cents"], 12500)

        cursor.execute("SELECT COUNT(*) FROM match_members WHERE match_id = ?", (match_id,))
        self.assertEqual(cursor.fetchone()[0], 2)

        conn.close()

        # Reject floats in bank_records and ledger_records
        with self.assertRaises(TypeError):
            db.insert_bank_record("B-FAIL", "2026-08-15", 125.00, "Float rejected")  # float
        with self.assertRaises(TypeError):
            db.insert_ledger_record("L-FAIL", "2026-08-15", 125.00, "Float rejected")  # float

if __name__ == "__main__":
    unittest.main()
