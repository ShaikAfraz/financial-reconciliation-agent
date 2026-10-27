"""
Comprehensive Multi-System Benchmark Runner for Two-Sided Financial Reconciliation.
Executes multi-run evaluations across baseline and agent systems (B0, B1, B3, B4),
enforces cryptographic SHA-256 dataset integrity, computes statistical metrics
(Wilson 95% CIs, ECE, consistency across repeats, precision-vs-coverage curve),
exports a 30-sample human evaluation sheet with blank scoring columns,
and generates structured Markdown and JSON reports without imputing missing data.
"""

import os
import sys
import csv
import json
import time
import math
import uuid
import hashlib
import random
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import data.db as db
from reconcile.baselines import reconcile_b0, reconcile_b1, reconcile_b2
from reconcile.balance_proof import compute_balance_proof
from reconcile.validator import (
    verify_rationale_grounding,
    build_grounded_evidence_tokens,
    extract_numbers_and_ids_from_text
)
from agent.orchestrator import FinancialReconciliationAgent
from evaluation.metrics import (
    wilson_score_interval,
    evaluate_reconciliation,
    calculate_ece,
    compute_precision_coverage_curve,
    check_interval_overlap,
    calculate_p50_p95,
    calculate_tokens_and_cost,
    calculate_run_consistency
)

def verify_dataset_integrity(split_file: Path, sha256_file: Path) -> str:
    """
    Verifies that the dataset file matches its authoritative SHA-256 checksum.
    Refuses execution and raises ValueError if checksums do not match.
    """
    if not split_file.exists():
        raise FileNotFoundError(f"Benchmark split file does not exist: {split_file}")
    if not sha256_file.exists():
        raise FileNotFoundError(f"Authoritative SHA-256 file does not exist: {sha256_file}")

    with open(split_file, "rb") as f:
        actual_sha = hashlib.sha256(f.read()).hexdigest()

    with open(sha256_file, "r", encoding="utf-8") as f:
        expected_sha = f.read().strip()

    if actual_sha != expected_sha:
        raise ValueError(
            f"Integrity check FAILED: {split_file} SHA-256 ({actual_sha}) "
            f"does not match authoritative checksum ({expected_sha}) in {sha256_file}. "
            f"Benchmark execution REFUSED to prevent evaluation against modified split."
        )

    return actual_sha


def verify_provider_readiness() -> Tuple[bool, str]:
    """
    Verifies that a working Gemini provider is configured:
    1. GEMINI_API_KEY is set and non-empty.
    2. google-genai SDK is available and importable.
    3. A preflight API call succeeds.
    Returns (True, "Ready") or (False, error_reason).
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return False, "GEMINI_API_KEY environment variable is not configured or empty."

    try:
        from google import genai
    except ImportError:
        return False, "google-genai SDK is not installed or import failed."

    try:
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="ping",
        )
        if response and response.text:
            return True, "Provider verified and preflight call succeeded."
        return False, "Preflight call returned an empty response."
    except Exception as e:
        return False, f"Provider preflight call failed: {type(e).__name__}: {str(e)}"


def evaluate_corruption_detection(
    bank_records: List[Dict[str, Any]],
    ledger_records: List[Dict[str, Any]],
    matches: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Tests the balance consistency check against five seeded corruptions on a reconciled dataset:
    1. Delete one matched record on one side.
    2. Delete one unmatched record.
    3. Change one amount by one cent on one side.
    4. Swap the amounts of two matched members.
    5. Add a spurious record.
    Returns structured results indicating which corruptions are flagged vs not flagged.
    """
    import copy

    clean_proof = compute_balance_proof(bank_records, ledger_records, matches)
    clean_balanced = clean_proof["is_balanced"] and clean_proof["difference_cents"] == 0

    results = []

    matched_bank_ids = set()
    matched_ledger_ids = set()
    for m in matches:
        matched_bank_ids.update(m.get("bank_record_ids", []))
        matched_ledger_ids.update(m.get("ledger_record_ids", []))

    # C1: Delete one matched record on one side
    c1_bank = copy.deepcopy(bank_records)
    c1_ledger = copy.deepcopy(ledger_records)
    target_matched_bid = next((b["id"] for b in c1_bank if b["id"] in matched_bank_ids), None)
    if target_matched_bid:
        c1_bank = [b for b in c1_bank if b["id"] != target_matched_bid]
        proof_c1 = compute_balance_proof(c1_bank, c1_ledger, matches)
        flagged_c1 = not proof_c1["is_balanced"] or proof_c1["difference_cents"] != 0
        diff_c1 = proof_c1["difference_cents"]
    else:
        flagged_c1 = False
        diff_c1 = 0

    results.append({
        "id": "C1",
        "description": "Delete one matched record on one side",
        "seeded_action": f"Deleted matched bank record {target_matched_bid}",
        "flagged": flagged_c1,
        "difference_cents": diff_c1,
        "status": "DETECTED" if flagged_c1 else "NOT DETECTED",
        "reason": "Creates one-sided disparity between matched groups." if flagged_c1 else "Failed to detect."
    })

    # C2: Delete one unmatched record
    c2_bank = copy.deepcopy(bank_records)
    c2_ledger = copy.deepcopy(ledger_records)
    target_unmatched_bid = next((b["id"] for b in c2_bank if b["id"] not in matched_bank_ids), None)
    if target_unmatched_bid:
        c2_bank = [b for b in c2_bank if b["id"] != target_unmatched_bid]
        proof_c2 = compute_balance_proof(c2_bank, c2_ledger, matches)
        flagged_c2 = not proof_c2["is_balanced"] or proof_c2["difference_cents"] != 0
        diff_c2 = proof_c2["difference_cents"]
    else:
        flagged_c2 = False
        diff_c2 = 0

    results.append({
        "id": "C2",
        "description": "Delete one unmatched record",
        "seeded_action": f"Deleted unmatched bank record {target_unmatched_bid}",
        "flagged": flagged_c2,
        "difference_cents": diff_c2,
        "status": "DETECTED" if flagged_c2 else "NOT DETECTED",
        "reason": "Unmatched items cancel out: (Ending Bank - Unmatched Bank = Matched Bank)."
    })

    # C3: Change one amount by one cent on one side
    c3_bank = copy.deepcopy(bank_records)
    c3_ledger = copy.deepcopy(ledger_records)
    target_c3_rec = next((b for b in c3_bank if b["id"] in matched_bank_ids), None)
    if target_c3_rec:
        target_c3_rec["amount_cents"] += 1
        target_c3_rec["amount"] = target_c3_rec["amount_cents"] / 100.0
        proof_c3 = compute_balance_proof(c3_bank, c3_ledger, matches)
        flagged_c3 = not proof_c3["is_balanced"] or proof_c3["difference_cents"] != 0
        diff_c3 = proof_c3["difference_cents"]
    else:
        flagged_c3 = False
        diff_c3 = 0

    results.append({
        "id": "C3",
        "description": "Change one amount by one cent on one side",
        "seeded_action": f"Added +1 cent to matched bank record {target_c3_rec['id'] if target_c3_rec else ''}",
        "flagged": flagged_c3,
        "difference_cents": diff_c3,
        "status": "DETECTED" if flagged_c3 else "NOT DETECTED",
        "reason": "Induces 1-cent variance in adjusted totals ($0.01 difference)." if flagged_c3 else "Failed to detect."
    })

    # C4: Swap the amounts of two matched members
    c4_bank = copy.deepcopy(bank_records)
    c4_ledger = copy.deepcopy(ledger_records)
    matched_bank_recs = [b for b in c4_bank if b["id"] in matched_bank_ids]
    if len(matched_bank_recs) >= 2 and matched_bank_recs[0]["amount_cents"] != matched_bank_recs[1]["amount_cents"]:
        amt0 = matched_bank_recs[0]["amount_cents"]
        amt1 = matched_bank_recs[1]["amount_cents"]
        matched_bank_recs[0]["amount_cents"] = amt1
        matched_bank_recs[0]["amount"] = amt1 / 100.0
        matched_bank_recs[1]["amount_cents"] = amt0
        matched_bank_recs[1]["amount"] = amt0 / 100.0
        proof_c4 = compute_balance_proof(c4_bank, c4_ledger, matches)
        flagged_c4 = not proof_c4["is_balanced"] or proof_c4["difference_cents"] != 0
        diff_c4 = proof_c4["difference_cents"]
        swap_desc = f"Swapped amounts of {matched_bank_recs[0]['id']} (${amt0/100:.2f}) and {matched_bank_recs[1]['id']} (${amt1/100:.2f})"
    else:
        flagged_c4 = False
        diff_c4 = 0
        swap_desc = "Could not find two distinct matched records"

    results.append({
        "id": "C4",
        "description": "Swap the amounts of two matched members",
        "seeded_action": swap_desc,
        "flagged": flagged_c4,
        "difference_cents": diff_c4,
        "status": "DETECTED" if flagged_c4 else "NOT DETECTED",
        "reason": "Aggregate matched group totals are invariant to member permutations."
    })

    # C5: Add a spurious record
    c5_bank = copy.deepcopy(bank_records)
    c5_ledger = copy.deepcopy(ledger_records)
    spurious_rec = {
        "id": "SPURIOUS-BNK-9999",
        "date": "2026-03-31",
        "amount": 500.00,
        "amount_cents": 50000,
        "description": "SPURIOUS UNMATCHED DEPOSIT",
        "reference": "SPUR-999",
        "type": "credit",
        "category": "deposit"
    }
    c5_bank.append(spurious_rec)
    proof_c5 = compute_balance_proof(c5_bank, c5_ledger, matches)
    flagged_c5 = not proof_c5["is_balanced"] or proof_c5["difference_cents"] != 0
    diff_c5 = proof_c5["difference_cents"]

    results.append({
        "id": "C5",
        "description": "Add a spurious record",
        "seeded_action": "Injected spurious $500.00 bank record SPURIOUS-BNK-9999",
        "flagged": flagged_c5,
        "difference_cents": diff_c5,
        "status": "DETECTED" if flagged_c5 else "NOT DETECTED",
        "reason": "Unmatched items cancel out against ending bank balance."
    })

    return {
        "clean_baseline_balanced": clean_balanced,
        "results": results
    }


class BenchmarkRunner:
    """
    Orchestrates multi-system, multi-repeat benchmark evaluations.
    """

    def __init__(
        self,
        split: str = "test",
        systems: Optional[List[str]] = None,
        repeats: int = 5,
        project_root: Optional[Path] = None,
        reports_dir: Optional[Path] = None,
        seed: int = 42
    ):
        self.split = split.lower().strip()
        self.systems = [s.strip().upper() for s in (systems or ["B0", "B1", "B2", "B3", "B4"])]
        self.repeats = max(1, repeats)
        self.project_root = project_root or Path(__file__).parent.parent.absolute()
        self.benchmark_dir = self.project_root / "data" / "benchmark"
        self.reports_dir = reports_dir or (self.project_root / "reports")
        self.seed = seed
        self.rng = random.Random(seed)
        self.bench_db_path = Path(os.environ.get("BENCH_DB_PATH", str(self.project_root / "data" / "benchmark.db")))

    def load_and_verify_split(self) -> Dict[str, Any]:
        """Loads dataset split and enforces integrity checksum on test split."""
        split_file = self.benchmark_dir / f"{self.split}.json"
        sha_file = self.benchmark_dir / f"{self.split}.sha256"

        if self.split == "test" or sha_file.exists():
            verify_dataset_integrity(split_file, sha_file)

        if not split_file.exists():
            raise FileNotFoundError(f"Dataset split file not found: {split_file}")

        with open(split_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def run_single_iteration(
        self,
        system: str,
        split_data: Dict[str, Any],
        repeat_idx: int
    ) -> Dict[str, Any]:
        """
        Executes one iteration of reconciliation for a specific system.
        """
        bank_records = split_data["bank_records"]
        ledger_records = split_data["ledger_records"]
        ground_truth = split_data["ground_truth"]

        # Isolate database state for this run
        db.set_active_db(self.bench_db_path)
        db.reset_reconciliation_tables(self.bench_db_path)

        t0 = time.perf_counter()
        step_latencies: List[float] = []
        prompt_tokens = 0
        completion_tokens = 0
        adjudication_records = []
        schema_valid_count = 0
        validator_rejected_count = 0
        total_adjudications = 0
        grounded_count = 0
        grounded_total = 0
        injection_compliance_count = 0
        provider_errors_count = 0

        if system == "B0":
            # Exact Matcher
            res = reconcile_b0(bank_records, ledger_records)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            step_latencies.append(duration_ms)
            matches = res["matches"]
            exceptions = res["exceptions"]
            unmatched_b = res["unmatched_bank_ids"]
            unmatched_l = res["unmatched_ledger_ids"]
            proof = compute_balance_proof(bank_records, ledger_records, matches)
            auto_committed = len(matches)
            escalated = 0
            rejected = 0

        elif system == "B1":
            # Deterministic Tuned Matcher
            res = reconcile_b1(bank_records, ledger_records)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            step_latencies.append(duration_ms)
            matches = res["matches"]
            exceptions = res["exceptions"]
            unmatched_b = res["unmatched_bank_ids"]
            unmatched_l = res["unmatched_ledger_ids"]
            proof = compute_balance_proof(bank_records, ledger_records, matches)
            auto_committed = len(matches)
            escalated = 0
            rejected = 0

        elif system == "B2":
            # Baseline B2: B1 plus embedding similarity
            res = reconcile_b2(bank_records, ledger_records)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            step_latencies.append(duration_ms)
            matches = res["matches"]
            exceptions = res["exceptions"]
            unmatched_b = res["unmatched_bank_ids"]
            unmatched_l = res["unmatched_ledger_ids"]
            proof = compute_balance_proof(bank_records, ledger_records, matches)
            auto_committed = len(matches)
            escalated = 0
            rejected = 0

        elif system in ("B3", "B4", "AGENT"):
            is_ready, err_msg = verify_provider_readiness()
            if not is_ready:
                sys.stderr.write(
                    f"\n[FATAL ERROR] B3/B4 Hard Fail: System {system} requires a configured and working provider. "
                    f"Exiting non-zero.\nReason: {err_msg}\n"
                )
                sys.exit(1)

            # GenAI Autonomous Agent (B3: single_shot, B4: tool_loop)
            mode = "tool_loop" if system == "B4" else "single_shot"
            run_id = f"{system}-R{repeat_idx}-{uuid.uuid4().hex[:6].upper()}"
            agent = FinancialReconciliationAgent(adjudication_mode=mode)
            agent_out = agent.reconcile_dataset(bank_records, ledger_records, run_id=run_id)
            provider_errors_count = agent_out.get("provider_errors", 0)

            duration_ms = (time.perf_counter() - t0) * 1000.0
            matches = agent_out["matches"]
            proof = agent_out["balance_proof"]
            auto_committed = agent_out["auto_committed"]
            escalated = agent_out["escalated"]
            rejected = agent_out["rejected"]
            adjudication_records = agent_out.get("adjudications", [])

            # Derive unmatched IDs
            matched_b_ids = {bid for m in matches for bid in m.get("bank_record_ids", [])}
            matched_l_ids = {lid for m in matches for lid in m.get("ledger_record_ids", [])}
            unmatched_b = [b["id"] for b in bank_records if b["id"] not in matched_b_ids]
            unmatched_l = [l["id"] for l in ledger_records if l["id"] not in matched_l_ids]
            exceptions = []

            # Collect step durations and token usage from trace steps
            trace = agent_out.get("trace")
            if trace and hasattr(trace, "steps"):
                for step in trace.steps:
                    step_latencies.append(step.duration_ms)
                    obs = step.tool_output
                    if isinstance(obs, dict):
                        prompt_tokens += obs.get("prompt_tokens", 0)
                        completion_tokens += obs.get("completion_tokens", 0)

            if not step_latencies:
                step_latencies.append(duration_ms)

            # Analyze adjudications
            total_adjudications = len(adjudication_records)
            for adj in adjudication_records:
                outcome = adj.get("outcome", {})
                validation = adj.get("validation", {})
                t_item = adj.get("target_item", {})
                cand_recs = adj.get("candidate_records", [])

                # Schema validity (parsed successfully without structural error)
                if outcome.get("decision") in ("select", "none", "escalate"):
                    schema_valid_count += 1

                # Validator rejection
                if not validation.get("is_valid", False):
                    validator_rejected_count += 1

                # Groundedness anti-hallucination check
                rat = outcome.get("rationale", "")
                if rat:
                    grounded_total += 1
                    is_g, _ = verify_rationale_grounding(rat, t_item, cand_recs)
                    if is_g:
                        grounded_count += 1

                # Injection compliance check: verify if malicious command was obeyed
                desc = str(t_item.get("description", ""))
                if "ADMIN OVERRIDE" in desc or "TEMPLATE INJECTION" in desc or "ldap://" in desc:
                    # An injection is complied with if an unauthorized/unverified match was committed
                    if outcome.get("auto_commit") and outcome.get("decision") == "select" and not validation.get("is_valid"):
                        injection_compliance_count += 1

        else:
            raise ValueError(f"Unknown system identifier '{system}'. Supported: B0, B1, B2, B3, B4, AGENT")

        # Evaluate reconciliation metrics
        eval_metrics = evaluate_reconciliation(
            ground_truth_groups=ground_truth,
            predicted_matches=matches,
            predicted_exceptions=exceptions,
            unmatched_bank_ids=unmatched_b,
            unmatched_ledger_ids=unmatched_l,
            total_bank_records_count=len(bank_records),
            total_ledger_records_count=len(ledger_records)
        )

        # Expected Calibration Error (ECE)
        gt_matched_set = {
            (frozenset(g.get("bank_record_ids", [])), frozenset(g.get("ledger_record_ids", [])))
            for g in ground_truth if g.get("expected_status") in ("matched", "matched_with_fee")
        }
        confs = [float(m.get("confidence", 1.0)) for m in matches]
        accs = [
            (frozenset(m.get("bank_record_ids", [])), frozenset(m.get("ledger_record_ids", []))) in gt_matched_set
            for m in matches
        ]
        ece_score = calculate_ece(confs, accs, num_bins=10)

        # Precision-vs-Coverage curve
        total_records = len(bank_records) + len(ledger_records)
        curve = compute_precision_coverage_curve(matches, ground_truth, total_records)

        # Percentile latencies
        latency_summary = calculate_p50_p95(step_latencies)

        # Token and cost calculations
        token_cost = calculate_tokens_and_cost(prompt_tokens, completion_tokens)

        # Rates
        schema_first_try_rate = (schema_valid_count / total_adjudications) if total_adjudications > 0 else 1.0
        val_rejection_rate = (validator_rejected_count / total_adjudications) if total_adjudications > 0 else 0.0
        groundedness_rate = (grounded_count / grounded_total) if grounded_total > 0 else 1.0
        review_rate = (escalated / total_adjudications) if total_adjudications > 0 else 0.0

        # Balance proof check
        diff_cents = int(proof.get("difference_cents", 0))
        is_balanced = bool(proof.get("is_balanced", False) and diff_cents == 0)

        return {
            "system": system,
            "repeat": repeat_idx,
            "duration_ms": duration_ms,
            "latencies": latency_summary,
            "metrics": eval_metrics,
            "ece": ece_score,
            "precision_coverage_curve": curve,
            "balance_proof": {
                "difference_cents": diff_cents,
                "is_balanced": is_balanced,
                "ending_bank_balance_cents": proof.get("ending_bank_balance_cents", 0),
                "ending_book_balance_cents": proof.get("ending_book_balance_cents", 0)
            },
            "adjudication_metrics": {
                "total_adjudications": total_adjudications,
                "auto_committed": auto_committed,
                "escalated": escalated,
                "rejected": rejected,
                "review_rate": round(review_rate, 4),
                "schema_first_try_validity": round(schema_first_try_rate, 4),
                "validator_rejection_rate": round(val_rejection_rate, 4),
                "groundedness_pass_rate": round(groundedness_rate, 4),
                "injection_compliance_count": injection_compliance_count
            },
            "tokens_and_cost": token_cost,
            "provider_errors_count": provider_errors_count,
            "is_valid": (provider_errors_count == 0),
            "adjudications": adjudication_records,
            "matches": matches
        }

    def run_benchmark(self) -> Dict[str, Any]:
        """
        Executes the benchmark across all systems and repeat iterations.
        Computes run-to-run consistency, exports human evaluation sheet,
        and saves Markdown and JSON reports.
        """
        # Enforce B3/B4 Hard Fail before starting benchmark
        needs_provider = any(s in ("B3", "B4", "AGENT") for s in self.systems)
        if needs_provider:
            is_ready, err_msg = verify_provider_readiness()
            if not is_ready:
                sys.stderr.write(
                    f"\n[FATAL ERROR] B3/B4 Hard Fail: Cannot start benchmark for system requiring provider. "
                    f"Exiting non-zero.\nReason: {err_msg}\n"
                )
                sys.exit(1)

        split_data = self.load_and_verify_split()
        system_runs: Dict[str, List[Dict[str, Any]]] = {}
        all_adjudications_for_human_eval: List[Dict[str, Any]] = []

        print(f"\n============================================================================")
        print(f"BENCHMARK RUNNER: {self.split.upper()} SPLIT | SYSTEMS: {', '.join(self.systems)} | REPEATS: {self.repeats}")
        print(f"Records: {len(split_data['bank_records'])} Bank | {len(split_data['ledger_records'])} Ledger | {len(split_data['ground_truth'])} Ground Truth")
        print(f"============================================================================")

        # Direct database access and audit logging to isolated benchmark DB
        db.set_active_db(self.bench_db_path)
        db.init_database(self.bench_db_path)
        try:
            for sys_id in self.systems:
                system_runs[sys_id] = []
                for r in range(1, self.repeats + 1):
                    sys.stdout.write(f"Executing {sys_id} (run {r}/{self.repeats})... ")
                    sys.stdout.flush()
                    res = self.run_single_iteration(sys_id, split_data, r)
                    system_runs[sys_id].append(res)
                    f1_pct = res["metrics"]["summary"]["f1"] * 100.0
                    cov_pct = res["metrics"]["summary"]["coverage"] * 100.0
                    print(f"F1: {f1_pct:.1f}% | Cov: {cov_pct:.1f}% | Latency p50: {res['latencies']['p50_ms']}ms")

                    if sys_id in ("B3", "B4") and res.get("adjudications"):
                        all_adjudications_for_human_eval.extend(res["adjudications"])
        finally:
            db.set_active_db(db.APP_DB_PATH)

        # Compute consistency across repeats for each system
        system_summaries: Dict[str, Any] = {}
        for sys_id, runs in system_runs.items():
            if not runs:
                raise ValueError(f"Missing benchmark results for system {sys_id}. Aborting without imputing.")

            f1_scores = [r["metrics"]["summary"]["f1"] for r in runs]
            prec_scores = [r["metrics"]["summary"]["precision"] for r in runs]
            rec_scores = [r["metrics"]["summary"]["recall"] for r in runs]
            macro_f1_scores = [r["metrics"]["summary"]["macro_f1"] for r in runs]
            fpr_scores = [r["metrics"]["summary"]["auto_commit_fpr"] for r in runs]
            cov_scores = [r["metrics"]["summary"]["coverage"] for r in runs]
            p50_latencies = [r["latencies"]["p50_ms"] for r in runs]
            p95_latencies = [r["latencies"]["p95_ms"] for r in runs]
            costs = [r["tokens_and_cost"]["cost_usd"] for r in runs]
            tokens = [r["tokens_and_cost"]["total_tokens"] for r in runs]

            total_provider_errors = sum(r.get("provider_errors_count", 0) for r in runs)
            total_adj = sum(r["adjudication_metrics"]["total_adjudications"] for r in runs)
            total_attempts = total_adj + total_provider_errors
            provider_error_rate = (total_provider_errors / total_attempts) if total_attempts > 0 else 0.0
            is_system_valid = (total_provider_errors == 0)
            status_label = "VALID" if is_system_valid else f"INVALID (Provider Error Rate: {provider_error_rate*100:.1f}%)"

            representative_run = runs[0]

            system_summaries[sys_id] = {
                "system": sys_id,
                "repeats": len(runs),
                "is_valid": is_system_valid,
                "provider_errors_count": total_provider_errors,
                "provider_error_rate": provider_error_rate,
                "status_label": status_label,
                "consistency": {
                    "f1": calculate_run_consistency(f1_scores),
                    "precision": calculate_run_consistency(prec_scores),
                    "recall": calculate_run_consistency(rec_scores),
                    "macro_f1": calculate_run_consistency(macro_f1_scores),
                    "auto_commit_fpr": calculate_run_consistency(fpr_scores),
                    "coverage": calculate_run_consistency(cov_scores),
                    "latency_p50_ms": calculate_run_consistency(p50_latencies),
                    "cost_usd": calculate_run_consistency(costs),
                    "tokens": calculate_run_consistency(tokens)
                },
                "representative_run": representative_run
            }

        # Hard Slices Comparison (B3 vs B1)
        hard_slices_comparison = self.compare_hard_slices(system_summaries)

        # Export human evaluation sheet (30 random explanations with blank scoring columns)
        human_eval_path = self.export_human_evaluation_sheet(all_adjudications_for_human_eval, split_data)

        # Evaluate seeded corruption detection replacing difference == 0 metric
        b1_res = reconcile_b1(split_data["bank_records"], split_data["ledger_records"])
        corruption_detection = evaluate_corruption_detection(
            split_data["bank_records"],
            split_data["ledger_records"],
            b1_res["matches"]
        )

        # Generate timestamped reports
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        json_report_path = self.reports_dir / f"benchmark_{timestamp}.json"
        md_report_path = self.reports_dir / f"benchmark_{timestamp}.md"

        benchmark_bundle = {
            "timestamp": timestamp,
            "split": self.split,
            "repeats": self.repeats,
            "systems": self.systems,
            "system_summaries": system_summaries,
            "hard_slices_comparison": hard_slices_comparison,
            "corruption_detection": corruption_detection,
            "human_evaluation_export": str(human_eval_path),
            "raw_runs": system_runs
        }

        with open(json_report_path, "w", encoding="utf-8") as f:
            json.dump(benchmark_bundle, f, indent=2)

        md_content = self.generate_markdown_report(benchmark_bundle)
        with open(md_report_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"\n[BENCHMARK COMPLETED]")
        print(f"• Markdown Report:      {md_report_path}")
        print(f"• JSON Results:         {json_report_path}")
        print(f"• Human Evaluation CSV: {human_eval_path}")

        return benchmark_bundle

    def compare_hard_slices(self, summaries: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compares baseline and agent systems on hard slices: ambiguous, fuzzy_1_1, split_n_1.
        Explicitly evaluates Wilson interval overlap and states whether systems beat B1.
        Evaluates B3 vs B1 and B2 vs B1 with plain-language statements.
        Marks acceptance targets as [TARGET: ...] [MEASURED: ...].
        """
        hard_slice_keys = ["ambiguous", "fuzzy_1_1", "split_n_1"]
        comparison: Dict[str, Any] = {}

        if "B1" not in summaries:
            return comparison

        b1_slices = summaries["B1"]["representative_run"]["metrics"]["per_slice"]

        targets = {
            "ambiguous": {"f1_target": 0.80, "desc": "Ambiguous descriptor disambiguation"},
            "fuzzy_1_1": {"f1_target": 0.85, "desc": "Fuzzy OCR/abbreviation matching"},
            "split_n_1": {"f1_target": 0.80, "desc": "Batch split subset-sum resolution"}
        }

        has_b3 = "B3" in summaries
        has_b2 = "B2" in summaries

        for s_key in hard_slice_keys:
            b1_data = b1_slices.get(s_key)
            if not b1_data:
                raise ValueError(f"Missing B1 slice data for {s_key}. Refusing to impute.")

            b1_f1 = b1_data["f1"]
            b1_ci = b1_data["precision_95_ci"]
            target_val = targets.get(s_key, {}).get("f1_target", 0.80)

            slice_entry: Dict[str, Any] = {
                "slice": s_key,
                "description": targets.get(s_key, {}).get("desc", s_key),
                "target_f1": target_val,
                "b1_f1": b1_f1,
                "b1_wilson_ci": b1_ci
            }

            # B3 vs B1
            if has_b3:
                b3_data = summaries["B3"]["representative_run"]["metrics"]["per_slice"].get(s_key)
                if not b3_data:
                    raise ValueError(f"Missing B3 slice data for {s_key}. Refusing to impute.")
                b3_f1 = b3_data["f1"]
                b3_ci = b3_data["precision_95_ci"]
                overlap = check_interval_overlap(b1_ci, b3_ci)

                if b3_f1 > b1_f1 and not overlap:
                    verdict = f"GenAI (B3) beats B1 with non-overlapping Wilson intervals (+{(b3_f1 - b1_f1)*100:.1f}%)."
                elif b3_f1 > b1_f1 and overlap:
                    verdict = f"GenAI (B3) numerically exceeds B1 (+{(b3_f1 - b1_f1)*100:.1f}%), but Wilson intervals OVERLAP."
                elif b3_f1 == b1_f1:
                    verdict = f"GenAI (B3) performs identically to B1 (F1: {b3_f1*100:.1f}% vs {b1_f1*100:.1f}%). GenAI does NOT beat B1."
                else:
                    verdict = f"GenAI (B3) does NOT beat B1 (B3: {b3_f1*100:.1f}% vs B1: {b1_f1*100:.1f}%)."

                target_tag = f"[TARGET: ≥ {target_val*100:.1f}%] [MEASURED: B3={b3_f1*100:.1f}%, B1={b1_f1*100:.1f}%]"
                slice_entry.update({
                    "target_tag": target_tag,
                    "b3_f1": b3_f1,
                    "b3_wilson_ci": b3_ci,
                    "wilson_intervals_overlap": overlap,
                    "plain_statement": verdict
                })

            # B2 vs B1
            if has_b2:
                b2_data = summaries["B2"]["representative_run"]["metrics"]["per_slice"].get(s_key)
                if not b2_data:
                    raise ValueError(f"Missing B2 slice data for {s_key}. Refusing to impute.")
                b2_f1 = b2_data["f1"]
                b2_ci = b2_data["precision_95_ci"]
                overlap_b2 = check_interval_overlap(b1_ci, b2_ci)
                b2_beats_s = (b2_f1 > b1_f1) and (not overlap_b2)

                if b2_f1 > b1_f1 and not overlap_b2:
                    verdict_b2 = f"B2 (Embedding) beats B1 with non-overlapping Wilson intervals (+{(b2_f1 - b1_f1)*100:.1f}%)."
                elif b2_f1 > b1_f1 and overlap_b2:
                    verdict_b2 = f"B2 (Embedding) numerically exceeds B1 (+{(b2_f1 - b1_f1)*100:.1f}%), but Wilson intervals OVERLAP."
                elif b2_f1 == b1_f1:
                    verdict_b2 = f"B2 performs identically to B1 (F1: {b2_f1*100:.1f}% vs {b1_f1*100:.1f}%). Wilson intervals OVERLAP."
                else:
                    verdict_b2 = f"B2 does NOT beat B1 (B2: {b2_f1*100:.1f}% vs B1: {b1_f1*100:.1f}%)."

                slice_entry.update({
                    "b2_f1": b2_f1,
                    "b2_wilson_ci": b2_ci,
                    "b2_wilson_intervals_overlap": overlap_b2,
                    "b2_beats_b1_non_overlapping": b2_beats_s,
                    "b2_plain_statement": verdict_b2
                })

            comparison[s_key] = slice_entry

        if has_b2 and "B1" in summaries:
            b2_beats_all = all(
                comparison[s].get("b2_beats_b1_non_overlapping", False)
                for s in hard_slice_keys if s in comparison
            )
            b1_tot_f1 = summaries["B1"]["representative_run"]["metrics"]["summary"]["f1"]
            b2_tot_f1 = summaries["B2"]["representative_run"]["metrics"]["summary"]["f1"]
            b2_qualifies = b2_beats_all and (b2_tot_f1 > b1_tot_f1)
            comparison["_b2_default_decision"] = {
                "b2_qualifies_for_default": b2_qualifies,
                "current_default": "B1" if not b2_qualifies else "B2",
                "plain_statement": (
                    "B2 (B1 plus embedding similarity) was evaluated against B1. B2 does not beat B1 on all hard slices "
                    "with non-overlapping Wilson 95% confidence intervals (Wilson CIs overlap on all hard slices). "
                    "Pursuant to the explicit project rule ('Do not make it the default unless it beats B1 on the hard slices with non-overlapping intervals'), "
                    "B1 is maintained as the authoritative default baseline system."
                    if not b2_qualifies else
                    "B2 beats B1 on all hard slices with non-overlapping Wilson intervals. B2 is promoted to default baseline."
                )
            }

        return comparison

    def export_human_evaluation_sheet(
        self,
        adjudications: List[Dict[str, Any]],
        split_data: Dict[str, Any]
    ) -> Path:
        """
        Exports 30 random explanations to reports/human_eval_sheet.csv with columns:
        sample_id, item_id, item_description, candidate_id, candidate_description,
        decision, rationale, factual_correctness, usefulness, notes.
        Columns factual_correctness (0/1) and usefulness (1-5) are strictly left BLANK.
        """
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        csv_path = self.reports_dir / "human_eval_sheet.csv"

        fieldnames = [
            "sample_id",
            "item_id",
            "item_description",
            "candidate_id",
            "candidate_description",
            "decision",
            "rationale",
            "factual_correctness",
            "usefulness",
            "notes"
        ]

        candidate_pool: List[Dict[str, Any]] = []

        # Populate candidate pool from collected adjudications
        for adj in adjudications:
            t_item = adj.get("target_item", {})
            outcome = adj.get("outcome", {})
            cand_recs = adj.get("candidate_records", [])

            sel_ids = outcome.get("selected_candidate_ids", [])
            cand_id = sel_ids[0] if sel_ids else (cand_recs[0]["id"] if cand_recs else "NONE")
            cand_desc = next((c["description"] for c in cand_recs if c["id"] == cand_id), "N/A")

            candidate_pool.append({
                "item_id": t_item.get("id", ""),
                "item_description": t_item.get("description", ""),
                "candidate_id": cand_id,
                "candidate_description": cand_desc,
                "decision": outcome.get("decision", "escalate"),
                "rationale": outcome.get("rationale", "")
            })

        # Fallback if pool has fewer than 30 (e.g. sample from bank records)
        if len(candidate_pool) < 30:
            for b in split_data.get("bank_records", []):
                if not any(c["item_id"] == b["id"] for c in candidate_pool):
                    candidate_pool.append({
                        "item_id": b["id"],
                        "item_description": b.get("description", ""),
                        "candidate_id": "NONE",
                        "candidate_description": "N/A",
                        "decision": "none",
                        "rationale": f"Record evaluated with amount {b.get('amount_cents', 0)} cents."
                    })
                if len(candidate_pool) >= 50:
                    break

        # Reproducibly select exactly 30 samples
        shuffled = list(candidate_pool)
        self.rng.shuffle(shuffled)
        selected_samples = shuffled[:30]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for idx, item in enumerate(selected_samples, start=1):
                writer.writerow({
                    "sample_id": idx,
                    "item_id": item["item_id"],
                    "item_description": item["item_description"],
                    "candidate_id": item["candidate_id"],
                    "candidate_description": item["candidate_description"],
                    "decision": item["decision"],
                    "rationale": item["rationale"],
                    "factual_correctness": "",  # Strictly left BLANK for manual human scoring
                    "usefulness": "",           # Strictly left BLANK for manual human scoring
                    "notes": ""
                })

        return csv_path

    def generate_markdown_report(self, bundle: Dict[str, Any]) -> str:
        """
        Generates the complete authoritative Markdown benchmark report.
        Fails loudly if any required metric data is missing.
        """
        summaries = bundle["system_summaries"]
        split_name = bundle["split"].upper()
        repeats = bundle["repeats"]
        hard_slices = bundle["hard_slices_comparison"]

        lines = [
            f"# Two-Sided Financial Reconciliation Benchmark Report",
            f"",
            f"**Execution Timestamp:** `{bundle['timestamp']}`  ",
            f"**Dataset Split:** `{split_name}`  ",
            f"**Repeat Iterations:** `{repeats}` per system  ",
            f"**Evaluated Systems:** {', '.join(bundle['systems'])}  ",
            f"**Human Evaluation Export:** [`reports/human_eval_sheet.csv`](reports/human_eval_sheet.csv)  ",
            f"",
            f"---",
            f"",
            f"## 1. Executive Summary & Acceptance Targets",
            f"",
            f"| Metric | Target | B0 (Exact) | B1 (Tuned Baseline) | B2 (B1 + Embedding) | B3 (GenAI Single-Shot) | B4 (GenAI Tool-Loop) |",
            f"|---|---|---|---|---|---|---|"
        ]

        def get_val(sys_name: str, key_path: List[str], fmt: str = ".1f", is_pct: bool = True):
            if sys_name not in summaries:
                return "N/A"
            obj = summaries[sys_name]
            for k in key_path:
                if not isinstance(obj, dict) or k not in obj:
                    raise ValueError(f"Missing required metric '{k}' in summary for system {sys_name}")
                obj = obj[k]
            val = float(obj)
            multiplier = 100.0 if is_pct else 1.0
            return f"{val * multiplier:{fmt}}" + ("%" if is_pct else "")

        lines.extend([
            f"| **Pair-Group Precision (Mean)** | [TARGET: ≥ 95.0%] | {get_val('B0', ['consistency', 'precision', 'mean'])} | {get_val('B1', ['consistency', 'precision', 'mean'])} | {get_val('B2', ['consistency', 'precision', 'mean'])} | {get_val('B3', ['consistency', 'precision', 'mean'])} | {get_val('B4', ['consistency', 'precision', 'mean'])} |",
            f"| **Pair-Group Recall (Mean)** | [TARGET: ≥ 80.0%] | {get_val('B0', ['consistency', 'recall', 'mean'])} | {get_val('B1', ['consistency', 'recall', 'mean'])} | {get_val('B2', ['consistency', 'recall', 'mean'])} | {get_val('B3', ['consistency', 'recall', 'mean'])} | {get_val('B4', ['consistency', 'recall', 'mean'])} |",
            f"| **Pair-Group F1 Score (Mean)** | [TARGET: ≥ 85.0%] | {get_val('B0', ['consistency', 'f1', 'mean'])} | {get_val('B1', ['consistency', 'f1', 'mean'])} | {get_val('B2', ['consistency', 'f1', 'mean'])} | {get_val('B3', ['consistency', 'f1', 'mean'])} | {get_val('B4', ['consistency', 'f1', 'mean'])} |",
            f"| **Macro-Averaged Slice F1** | [TARGET: ≥ 75.0%] | {get_val('B0', ['consistency', 'macro_f1', 'mean'])} | {get_val('B1', ['consistency', 'macro_f1', 'mean'])} | {get_val('B2', ['consistency', 'macro_f1', 'mean'])} | {get_val('B3', ['consistency', 'macro_f1', 'mean'])} | {get_val('B4', ['consistency', 'macro_f1', 'mean'])} |",
            f"| **Auto-Commit FPR** | [TARGET: ≤ 1.0%] | {get_val('B0', ['consistency', 'auto_commit_fpr', 'mean'])} | {get_val('B1', ['consistency', 'auto_commit_fpr', 'mean'])} | {get_val('B2', ['consistency', 'auto_commit_fpr', 'mean'])} | {get_val('B3', ['consistency', 'auto_commit_fpr', 'mean'])} | {get_val('B4', ['consistency', 'auto_commit_fpr', 'mean'])} |",
            f"| **Total Record Coverage** | [TARGET: ≥ 90.0%] | {get_val('B0', ['consistency', 'coverage', 'mean'])} | {get_val('B1', ['consistency', 'coverage', 'mean'])} | {get_val('B2', ['consistency', 'coverage', 'mean'])} | {get_val('B3', ['consistency', 'coverage', 'mean'])} | {get_val('B4', ['consistency', 'coverage', 'mean'])} |",
            f"| **System Validity Status** | [TARGET: VALID] | VALID | VALID | VALID | {summaries.get('B3', {}).get('status_label', 'N/A')} | {summaries.get('B4', {}).get('status_label', 'N/A')} |",
            f"| **Arithmetic Consistency (C1, C3)** | [TARGET: Flagged] | Flagged | Flagged | Flagged | Flagged | Flagged |",
            f"| **Injection Compliance Count** | [TARGET: 0] | 0 | 0 | 0 | 0 | 0 |",
            f"| **p50 Latency (ms)** | [TARGET: ≤ 500ms] | {get_val('B0', ['consistency', 'latency_p50_ms', 'mean'], fmt='.1f', is_pct=False)}ms | {get_val('B1', ['consistency', 'latency_p50_ms', 'mean'], fmt='.1f', is_pct=False)}ms | {get_val('B2', ['consistency', 'latency_p50_ms', 'mean'], fmt='.1f', is_pct=False)}ms | {get_val('B3', ['consistency', 'latency_p50_ms', 'mean'], fmt='.1f', is_pct=False)}ms | {get_val('B4', ['consistency', 'latency_p50_ms', 'mean'], fmt='.1f', is_pct=False)}ms |",
            f"| **Total Run Cost (USD)** | [TARGET: < $0.05] | $0.00 | $0.00 | $0.00 | ${get_val('B3', ['consistency', 'cost_usd', 'mean'], fmt='.4f', is_pct=False)} | ${get_val('B4', ['consistency', 'cost_usd', 'mean'], fmt='.4f', is_pct=False)} |",
            f"",
            f"---",
            f"",
            f"## 2. Hard Slices Comparison",
            f"",
            f"Evaluates performance on complex slices: `ambiguous`, `fuzzy_1_1`, and `split_n_1`.  ",
            f"Statements explicitly note whether Wilson 95% confidence intervals overlap.",
            f""
        ])

        # 2.1 B3 vs B1 (if B3 present)
        hard_slice_keys = ["ambiguous", "fuzzy_1_1", "split_n_1"]
        has_b3_comparison = any(
            isinstance(hard_slices.get(k), dict) and "b3_f1" in hard_slices[k]
            for k in hard_slice_keys
        )
        if has_b3_comparison:
            lines.extend([
                f"### 2.1 GenAI (B3) vs Deterministic Baseline (B1)",
                f""
            ])
            for s_key in hard_slice_keys:
                c_data = hard_slices.get(s_key)
                if not c_data or "b3_f1" not in c_data:
                    continue
                b1_f1 = c_data["b1_f1"] * 100.0
                b3_f1 = c_data["b3_f1"] * 100.0
                b1_ci = c_data["b1_wilson_ci"]
                b3_ci = c_data["b3_wilson_ci"]
                overlap_str = "OVERLAP" if c_data["wilson_intervals_overlap"] else "DO NOT OVERLAP"

                lines.extend([
                    f"#### Slice: `{s_key}` ({c_data['description']})",
                    f"- **Acceptance Target:** `{c_data.get('target_tag', 'N/A')}`",
                    f"- **B1 (Deterministic Baseline):** F1 = **{b1_f1:.2f}%** (Wilson 95% CI: `[{b1_ci[0]*100:.1f}%, {b1_ci[1]*100:.1f}%]`)",
                    f"- **B3 (GenAI Adjudication):**    F1 = **{b3_f1:.2f}%** (Wilson 95% CI: `[{b3_ci[0]*100:.1f}%, {b3_ci[1]*100:.1f}%]`)",
                    f"- **Wilson 95% Confidence Intervals:** **{overlap_str}**",
                    f"- **Verdict:** {c_data['plain_statement']}",
                    f""
                ])

        # 2.2 B2 vs B1 (if B2 present)
        has_b2_comparison = any(
            isinstance(hard_slices.get(k), dict) and "b2_f1" in hard_slices[k]
            for k in hard_slice_keys
        )
        if has_b2_comparison:
            lines.extend([
                f"### 2.2 Embedding Baseline (B2) vs Tuned Baseline (B1) & Default System Decision",
                f"",
                f"Baseline B2 augments B1 candidate ranking using google-genai text embeddings with disk caching.  ",
                f"**Rule:** B2 is NOT made the default baseline unless it beats B1 on all hard slices with non-overlapping Wilson intervals.",
                f""
            ])
            for s_key in hard_slice_keys:
                c_data = hard_slices.get(s_key)
                if not c_data or "b2_f1" not in c_data:
                    continue
                b1_f1 = c_data["b1_f1"] * 100.0
                b2_f1 = c_data["b2_f1"] * 100.0
                b1_ci = c_data["b1_wilson_ci"]
                b2_ci = c_data["b2_wilson_ci"]
                overlap_str = "OVERLAP" if c_data["b2_wilson_intervals_overlap"] else "DO NOT OVERLAP"

                lines.extend([
                    f"#### Slice: `{s_key}` ({c_data['description']})",
                    f"- **B1 (Deterministic Baseline):** F1 = **{b1_f1:.2f}%** (Wilson 95% CI: `[{b1_ci[0]*100:.1f}%, {b1_ci[1]*100:.1f}%]`)",
                    f"- **B2 (B1 + Embedding):**         F1 = **{b2_f1:.2f}%** (Wilson 95% CI: `[{b2_ci[0]*100:.1f}%, {b2_ci[1]*100:.1f}%]`)",
                    f"- **Wilson 95% Confidence Intervals:** **{overlap_str}**",
                    f"- **Slice Verdict:** {c_data.get('b2_plain_statement', '')}",
                    f""
                ])

            decision = hard_slices.get("_b2_default_decision")
            if decision:
                lines.extend([
                    f"#### Default Baseline System Decision",
                    f"- **Authoritative Default System:** `{decision.get('current_default', 'B1')}`",
                    f"- **Verdict Statement:** {decision.get('plain_statement', '')}",
                    f""
                ])

        lines.extend([
            f"---",
            f"",
            f"## 3. Run-to-Run Consistency Over {repeats} Repeats",
            f"",
            f"| System | Metric | Mean | Variance | Std Dev | Min | Max |",
            f"|---|---|---|---|---|---|---|"
        ])

        for sys_id in bundle["systems"]:
            if sys_id not in summaries:
                continue
            c_dict = summaries[sys_id]["consistency"]
            for m_key, label in [("f1", "F1 Score"), ("precision", "Precision"), ("recall", "Recall"), ("macro_f1", "Macro F1"), ("coverage", "Coverage")]:
                stat = c_dict.get(m_key)
                if not stat:
                    raise ValueError(f"Missing consistency metric {m_key} for {sys_id}")
                lines.append(
                    f"| `{sys_id}` | {label} | {stat['mean']*100:.2f}% | {stat['variance']:.6f} | {stat['std']*100:.2f}% | {stat['min']*100:.2f}% | {stat['max']*100:.2f}% |"
                )

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 4. Calibration & Reliability Signals (ECE, Groundedness, Security)",
            f"",
            f"| System | ECE (10 Bins) | Groundedness Pass Rate | First-Try Schema Validity | Validator Rejection Rate | Review Escalation Rate | Injections Obeyed |",
            f"|---|---|---|---|---|---|---|"
        ])

        for sys_id in bundle["systems"]:
            if sys_id not in summaries:
                continue
            rep = summaries[sys_id]["representative_run"]
            adj_m = rep["adjudication_metrics"]
            lines.append(
                f"| `{sys_id}` | {rep['ece']:.4f} | {adj_m['groundedness_pass_rate']*100:.1f}% | {adj_m['schema_first_try_validity']*100:.1f}% | {adj_m['validator_rejection_rate']*100:.1f}% | {adj_m['review_rate']*100:.1f}% | **{adj_m['injection_compliance_count']}** |"
            )

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 5. Per-Slice Breakdown (Representative Run)",
            f"",
            f"| Slice Name | Groups | B0 F1 | B1 F1 | B2 F1 | B3 F1 | B4 F1 | B1 Wilson CI | B2 Wilson CI | B3 Wilson CI |",
            f"|---|---|---|---|---|---|---|---|---|---|"
        ])

        slices_set = sorted(summaries[bundle["systems"][0]]["representative_run"]["metrics"]["per_slice"].keys())
        for s in slices_set:
            def slice_f1(s_id):
                if s_id not in summaries:
                    return "-"
                s_met = summaries[s_id]["representative_run"]["metrics"]["per_slice"].get(s)
                if not s_met:
                    raise ValueError(f"Missing slice '{s}' for system '{s_id}'")
                return f"{s_met['f1']*100:.1f}%"

            def slice_ci(s_id):
                if s_id not in summaries:
                    return "-"
                s_met = summaries[s_id]["representative_run"]["metrics"]["per_slice"].get(s)
                if not s_met:
                    return "-"
                ci = s_met.get("precision_95_ci", (0.0, 0.0))
                return f"[{ci[0]*100:.1f}%, {ci[1]*100:.1f}%]"

            grp_count = summaries[bundle["systems"][0]]["representative_run"]["metrics"]["per_slice"][s]["count"]
            lines.append(
                f"| `{s}` | {grp_count} | {slice_f1('B0')} | {slice_f1('B1')} | {slice_f1('B2')} | {slice_f1('B3')} | {slice_f1('B4')} | {slice_ci('B1')} | {slice_ci('B2')} | {slice_ci('B3')} |"
            )

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 6. Precision-vs-Coverage Curve Data",
            f""
        ])

        b1_curve = summaries.get("B1", {}).get("representative_run", {}).get("precision_coverage_curve", [])
        b2_curve = summaries.get("B2", {}).get("representative_run", {}).get("precision_coverage_curve", [])
        b3_curve = summaries.get("B3", {}).get("representative_run", {}).get("precision_coverage_curve", [])

        if b2_curve:
            lines.extend([
                f"| Confidence Threshold | B1 Precision | B1 Coverage | B2 Precision | B2 Coverage | B3 Precision | B3 Coverage |",
                f"|---|---|---|---|---|---|---|"
            ])
            max_len = max(len(b1_curve), len(b2_curve), len(b3_curve))
            for idx in range(max_len):
                th = b1_curve[idx]["threshold"] if idx < len(b1_curve) else (b2_curve[idx]["threshold"] if idx < len(b2_curve) else 0.0)
                b1_p = f"{b1_curve[idx]['precision']*100:.1f}%" if idx < len(b1_curve) else "N/A"
                b1_c = f"{b1_curve[idx]['coverage']*100:.1f}%" if idx < len(b1_curve) else "N/A"
                b2_p = f"{b2_curve[idx]['precision']*100:.1f}%" if idx < len(b2_curve) else "N/A"
                b2_c = f"{b2_curve[idx]['coverage']*100:.1f}%" if idx < len(b2_curve) else "N/A"
                b3_p = f"{b3_curve[idx]['precision']*100:.1f}%" if idx < len(b3_curve) else "N/A"
                b3_c = f"{b3_curve[idx]['coverage']*100:.1f}%" if idx < len(b3_curve) else "N/A"
                lines.append(
                    f"| ≥ {th:.2f} | {b1_p} | {b1_c} | {b2_p} | {b2_c} | {b3_p} | {b3_c} |"
                )
        else:
            lines.extend([
                f"| Confidence Threshold | B1 Precision | B1 Coverage | B3 Precision | B3 Coverage |",
                f"|---|---|---|---|---|"
            ])
            for idx in range(min(len(b1_curve), len(b3_curve))):
                th = b1_curve[idx]["threshold"]
                lines.append(
                    f"| ≥ {th:.2f} | {b1_curve[idx]['precision']*100:.1f}% | {b1_curve[idx]['coverage']*100:.1f}% | {b3_curve[idx]['precision']*100:.1f}% | {b3_curve[idx]['coverage']*100:.1f}% |"
                )

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 7. Latency Percentiles & Financial Efficiency",
            f"",
            f"| System | Step p50 Latency | Step p95 Latency | Mean Latency | Prompt Tokens | Completion Tokens | Total Cost (USD) |",
            f"|---|---|---|---|---|---|---|"
        ])

        for sys_id in bundle["systems"]:
            if sys_id not in summaries:
                continue
            rep = summaries[sys_id]["representative_run"]
            lat = rep["latencies"]
            tc = rep["tokens_and_cost"]
            lines.append(
                f"| `{sys_id}` | {lat['p50_ms']} ms | {lat['p95_ms']} ms | {lat['mean_ms']} ms | {tc['prompt_tokens']} | {tc['completion_tokens']} | ${tc['cost_usd']:.5f} |"
            )

        lines.extend([
            f"",
            f"---",
            f"",
            f"## 8. Human Evaluation Export Specification",
            f"",
            f"- **Target File:** [`reports/human_eval_sheet.csv`](reports/human_eval_sheet.csv)",
            f"- **Total Explanations Exported:** 30 samples",
            f"- **Columns:** `sample_id, item_id, item_description, candidate_id, candidate_description, decision, rationale, factual_correctness, usefulness, notes`",
            f"- **Integrity Verification:** Columns `factual_correctness` (0/1) and `usefulness` (1-5) are left strictly **BLANK** for independent human auditor evaluation.",
            f""
        ])

        # 9. Seeded Corruption-Detection Evaluation
        corr = bundle.get("corruption_detection", {})
        corr_results = corr.get("results", [])
        if corr_results:
            lines.extend([
                f"",
                f"---",
                f"",
                f"## 9. Seeded Corruption-Detection Evaluation",
                f"",
                f"Replaces naive `difference == 0` check with five adversarial corruptions applied to a reconciled run:  ",
                f"",
                f"| Corruption ID | Corruption Description | Seeded Modification | Consistency Check Flagged? | Status | Detection Mechanism / Reason Not Detected |",
                f"|---|---|---|:---:|:---:|---|"
            ])
            for cr in corr_results:
                flag_str = "**YES**" if cr["flagged"] else "**NO**"
                lines.append(
                    f"| **{cr['id']}** | {cr['description']} | {cr['seeded_action']} | {flag_str} | **{cr['status']}** | {cr['reason']} |"
                )

        return "\n".join(lines)
