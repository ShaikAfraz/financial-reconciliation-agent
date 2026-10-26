"""
Multi-Step Benchmark Evaluator for the Autonomous Financial Reconciliation Agent.
Measures the core metrics from Section 7:
1. Tool-Selection Accuracy (Target >= 0.85)
2. Plan-Sequence Correctness (Target >= 0.70 — headline number)
3. Recommendation Groundedness (Target >= 0.90)
"""

import json
from pathlib import Path
from typing import Dict, Any, List
from agent.orchestrator import FinancialReconciliationAgent
from agent.providers.deterministic_provider import DeterministicAgentProvider

DATA_DIR = Path(__file__).parent.parent / "data"

class BenchmarkEvaluator:
    """
    Executes the 18 ground-truth reconciliation scenarios and computes
    sequence correctness, tool selection accuracy, and recommendation groundedness.
    """

    def __init__(self, agent: FinancialReconciliationAgent = None):
        self.agent = agent or FinancialReconciliationAgent(provider=DeterministicAgentProvider())
        with open(DATA_DIR / "ground_truth_scenarios.json", "r") as f:
            self.scenarios = json.load(f)

    def evaluate_scenario(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        prompt = scenario["input_prompt"]
        context = scenario.get("context", {})
        expected_seq = scenario.get("expected_tool_sequence", [])
        valid_alts = scenario.get("valid_alternative_sequences", [])
        criteria = scenario.get("expected_recommendation_criteria", {})

        # Run agent
        trace = self.agent.run(prompt, context)
        actual_seq = trace.tool_sequence

        # 1. Plan-Sequence Correctness
        seq_match = (actual_seq == expected_seq) or (actual_seq in valid_alts)
        sequence_correctness = 1.0 if seq_match else 0.0

        # 2. Tool-Selection Accuracy
        if not actual_seq and not expected_seq:
            tool_selection_accuracy = 1.0
        elif not actual_seq and expected_seq:
            tool_selection_accuracy = 0.0
        else:
            correct_tools = sum(1 for t in actual_seq if t in expected_seq)
            tool_selection_accuracy = round(correct_tools / max(len(actual_seq), len(expected_seq)), 3)

        # 3. Recommendation Groundedness
        rec_text = trace.final_recommendation.lower()
        must_contains = criteria.get("must_contain", [])
        matched_items = 0
        total_items = len(must_contains)

        for item in must_contains:
            if item.lower() in rec_text:
                matched_items += 1

        groundedness_score = round(matched_items / total_items, 3) if total_items > 0 else 1.0

        txn_ctx = scenario.get("context", {}).get("transaction", {})
        return {
            "scenario_id": scenario["scenario_id"],
            "title": scenario["title"],
            "input_prompt": prompt,
            "merchant": txn_ctx.get("merchant", "General Ledger"),
            "amount": txn_ctx.get("amount", None),
            "expected_sequence": expected_seq,
            "actual_sequence": actual_seq,
            "sequence_correctness": sequence_correctness,
            "tool_selection_accuracy": tool_selection_accuracy,
            "groundedness_score": groundedness_score,
            "steps_taken": len(actual_seq),
            "execution_ms": trace.total_duration_ms,
            "final_recommendation": trace.final_recommendation,
            "trace": trace.to_dict()
        }

    def run_all(self) -> Dict[str, Any]:
        results = []
        for sc in self.scenarios:
            res = self.evaluate_scenario(sc)
            results.append(res)

        total_scenarios = len(results)
        avg_seq_correctness = round(sum(r["sequence_correctness"] for r in results) / total_scenarios, 3)
        avg_tool_accuracy = round(sum(r["tool_selection_accuracy"] for r in results) / total_scenarios, 3)
        avg_groundedness = round(sum(r["groundedness_score"] for r in results) / total_scenarios, 3)

        return {
            "total_scenarios": total_scenarios,
            "metrics": {
                "plan_sequence_correctness": avg_seq_correctness,
                "target_plan_sequence_correctness": 0.70,
                "plan_sequence_status": "PASSED" if avg_seq_correctness >= 0.70 else "FAILED",

                "tool_selection_accuracy": avg_tool_accuracy,
                "target_tool_selection_accuracy": 0.85,
                "tool_selection_status": "PASSED" if avg_tool_accuracy >= 0.85 else "FAILED",

                "recommendation_groundedness": avg_groundedness,
                "target_recommendation_groundedness": 0.90,
                "groundedness_status": "PASSED" if avg_groundedness >= 0.90 else "FAILED"
            },
            "scenario_results": results
        }
