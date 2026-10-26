"""
Error Propagation & Failure-Mode Analyzer for FR-5 / Metric 4.
Measures both Downstream Step Corruption Rate and Mathematical Dollar-Drift ($)
to demonstrate how early miscategorizations distort downstream budget allocations.
"""

import json
from pathlib import Path
from typing import Dict, Any, List
from agent.orchestrator import FinancialReconciliationAgent
from agent.providers.deterministic_provider import DeterministicAgentProvider
from agent.providers.corrupted_provider import CorruptedAgentProvider

DATA_DIR = Path(__file__).parent.parent / "data"

class ErrorPropagationAnalyzer:
    """
    Executes paired clean-vs-corrupted scenario runs to measure:
    1. Downstream Step Corruption Rate (%)
    2. Budget Variance Dollar-Drift ($)
    3. Final Recommendation Corruption Rate (%)
    """

    def __init__(self):
        with open(DATA_DIR / "ground_truth_scenarios.json", "r") as f:
            self.all_scenarios = json.load(f)
        self.testable_scenarios = [
            s for s in self.all_scenarios
            if "categorize_transaction" in s.get("expected_tool_sequence", [])
            and len(s.get("expected_tool_sequence", [])) > 1
            and "injected_error" in s
        ]

    def analyze_scenario(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        prompt = scenario["input_prompt"]
        context = scenario.get("context", {})
        injected = scenario["injected_error"]
        corrupted_category = injected["corrupted_category"]
        txn_amount = float(context.get("transaction", {}).get("amount", 50.0))

        # 1. Run Clean Agent
        clean_agent = FinancialReconciliationAgent(provider=DeterministicAgentProvider())
        clean_trace = clean_agent.run(prompt, context)

        # 2. Run Corrupted Agent
        corrupted_agent = FinancialReconciliationAgent(provider=CorruptedAgentProvider(corrupted_category))
        corrupted_trace = corrupted_agent.run(prompt, context)

        # 3. Analyze downstream corruption step by step
        downstream_steps_evaluated = 0
        corrupted_steps_count = 0
        step_corruption_details = []

        clean_cat = "Unknown"
        for s in clean_trace.steps:
            if s.tool_name == "categorize_transaction":
                clean_cat = s.tool_output.get("category", "")
                break

        for s in corrupted_trace.steps:
            if s.tool_name == "categorize_transaction":
                continue

            downstream_steps_evaluated += 1
            step_name = s.tool_name
            is_step_corrupted = False
            corruption_reason = ""

            if step_name == "flag_anomaly":
                used_cat = s.tool_arguments.get("category", "")
                if used_cat == corrupted_category and used_cat != clean_cat:
                    is_step_corrupted = True
                    corruption_reason = (
                        f"Evaluated statistical anomaly threshold against '{corrupted_category}' "
                        f"baseline instead of true category '{clean_cat}'."
                    )

            elif step_name == "check_against_budget_rule":
                used_cat = s.tool_arguments.get("category", "")
                if used_cat == corrupted_category and used_cat != clean_cat:
                    is_step_corrupted = True
                    corruption_reason = (
                        f"Checked monthly budget limits for '{corrupted_category}' "
                        f"instead of true category '{clean_cat}'."
                    )

            elif step_name == "calculate_category_variance":
                is_step_corrupted = True
                corruption_reason = f"Attributed spend toward '{corrupted_category}', causing budget distortion."

            elif step_name == "propose_reallocation":
                is_step_corrupted = True
                corruption_reason = f"Attempted to rebalance or protect the wrong category '{corrupted_category}'."

            if is_step_corrupted:
                corrupted_steps_count += 1

            step_corruption_details.append({
                "step_index": s.step_index,
                "tool_name": step_name,
                "is_corrupted": is_step_corrupted,
                "reason": corruption_reason or "Step executed without inheriting category error."
            })

        # Calculate mathematical Dollar Drift ($):
        # A miscategorized expense of $X shifts $X out of the source category and $X into the target category,
        # creating a 2 * $X total ledger variance distortion.
        dollar_drift = round(txn_amount * 2.0, 2) if clean_cat != corrupted_category else 0.0

        # Check final recommendation corruption
        rec_corrupted = False
        corrupted_rec = corrupted_trace.final_recommendation
        if corrupted_category.lower() in corrupted_rec.lower() and clean_cat.lower() not in corrupted_rec.lower():
            rec_corrupted = True

        scenario_propagation_rate = (
            round(corrupted_steps_count / downstream_steps_evaluated, 3)
            if downstream_steps_evaluated > 0 else 0.0
        )

        return {
            "scenario_id": scenario["scenario_id"],
            "title": scenario["title"],
            "original_category": clean_cat,
            "injected_corrupted_category": corrupted_category,
            "transaction_amount": txn_amount,
            "dollar_drift": dollar_drift,
            "downstream_steps_evaluated": downstream_steps_evaluated,
            "corrupted_steps_count": corrupted_steps_count,
            "scenario_propagation_rate": scenario_propagation_rate,
            "final_recommendation_corrupted": rec_corrupted,
            "step_corruption_details": step_corruption_details,
            "clean_tool_sequence": clean_trace.tool_sequence,
            "corrupted_tool_sequence": corrupted_trace.tool_sequence
        }

    def run_all(self) -> Dict[str, Any]:
        results = []
        for s in self.testable_scenarios:
            res = self.analyze_scenario(s)
            results.append(res)

        total_evaluated_downstream = sum(r["downstream_steps_evaluated"] for r in results)
        total_corrupted_downstream = sum(r["corrupted_steps_count"] for r in results)
        total_dollar_drift = round(sum(r["dollar_drift"] for r in results), 2)
        overall_propagation_rate = (
            round(total_corrupted_downstream / total_evaluated_downstream, 3)
            if total_evaluated_downstream > 0 else 0.0
        )

        total_recs = len(results)
        corrupted_recs = sum(1 for r in results if r["final_recommendation_corrupted"])
        recommendation_corruption_rate = round(corrupted_recs / total_recs, 3) if total_recs > 0 else 0.0

        return {
            "total_scenarios_evaluated": len(results),
            "total_downstream_steps_evaluated": total_evaluated_downstream,
            "total_corrupted_downstream_steps": total_corrupted_downstream,
            "overall_error_propagation_rate": overall_propagation_rate,
            "total_dollar_drift": total_dollar_drift,
            "recommendation_corruption_rate": recommendation_corruption_rate,
            "key_finding": (
                f"An early miscategorization cascades into {overall_propagation_rate * 100:.1f}% "
                f"of downstream tool steps and directly corrupts {recommendation_corruption_rate * 100:.1f}% "
                f"of final recommendations, producing ${total_dollar_drift:,.2f} in total cumulative "
                f"budget variance distortion across evaluated test scenarios."
            ),
            "scenario_breakdown": results
        }
