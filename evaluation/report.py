"""
Reporting utilities for benchmark evaluations and error propagation findings.
"""

from typing import Dict, Any

def generate_markdown_benchmark_report(benchmark_results: Dict[str, Any]) -> str:
    metrics = benchmark_results["metrics"]
    lines = [
        "# Autonomous Financial Reconciliation Agent: Benchmark Evaluation Report",
        "",
        "## Core Evaluation Metrics (Section 7 PRD)",
        "",
        "| Metric | Target | Actual Score | Status |",
        "|---|---|---|---|",
        f"| **Tool-Selection Accuracy** | ≥ 85.0% | **{metrics['tool_selection_accuracy'] * 100:.1f}%** | `{metrics['tool_selection_status']}` |",
        f"| **Plan-Sequence Correctness** (Headline) | ≥ 70.0% | **{metrics['plan_sequence_correctness'] * 100:.1f}%** | `{metrics['plan_sequence_status']}` |",
        f"| **Recommendation Groundedness** | ≥ 90.0% | **{metrics['recommendation_groundedness'] * 100:.1f}%** | `{metrics['groundedness_status']}` |",
        "",
        f"**Total Scenarios Evaluated:** {benchmark_results['total_scenarios']}",
        "",
        "## Scenario Breakdown",
        "",
        "| Scenario ID | Title | Expected Sequence | Actual Sequence | Match | Tool Acc | Groundedness |",
        "|---|---|---|---|---|---|---|"
    ]

    for r in benchmark_results["scenario_results"]:
        exp_str = " → ".join(r["expected_sequence"]) if r["expected_sequence"] else "*(None / Clarify)*"
        act_str = " → ".join(r["actual_sequence"]) if r["actual_sequence"] else "*(None / Clarify)*"
        match_icon = "Match" if r["sequence_correctness"] == 1.0 else "Deviation"
        lines.append(
            f"| `{r['scenario_id']}` | {r['title']} | `{exp_str}` | `{act_str}` | {match_icon} | {r['tool_selection_accuracy'] * 100:.0f}% | {r['groundedness_score'] * 100:.0f}% |"
        )

    return "\n".join(lines)

def generate_markdown_error_report(error_results: Dict[str, Any]) -> str:
    lines = [
        "# Error Propagation & Failure-Mode Analysis Report",
        "",
        "## Executive Summary",
        "",
        f"> **Key Finding:** {error_results['key_finding']}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| **Scenarios Tested** | {error_results['total_scenarios_evaluated']} |",
        f"| **Downstream Steps Evaluated** | {error_results['total_downstream_steps_evaluated']} |",
        f"| **Corrupted Downstream Steps** | {error_results['total_corrupted_downstream_steps']} |",
        f"| **Overall Downstream Error Rate** | **{error_results['overall_error_propagation_rate'] * 100:.1f}%** |",
        f"| **Cumulative Ledger Dollar Drift** | **${error_results['total_dollar_drift']:,.2f}** |",
        f"| **Final Recommendation Corruption Rate** | **{error_results['recommendation_corruption_rate'] * 100:.1f}%** |",
        "",
        "## Downstream Cascade Breakdown",
        "",
        "| Scenario | Original | Injected Perturbation | Amount | Dollar Drift | Downstream Corrupted | Rec Status |",
        "|---|---|---|---|---|---|---|"
    ]

    for s in error_results["scenario_breakdown"]:
        rec_status = "Corrupted" if s["final_recommendation_corrupted"] else "Resilient"
        lines.append(
            f"| `{s['scenario_id']}` | `{s['original_category']}` | `{s['injected_corrupted_category']}` | ${s['transaction_amount']:.2f} | ${s['dollar_drift']:.2f} | {s['corrupted_steps_count']} / {s['downstream_steps_evaluated']} ({s['scenario_propagation_rate'] * 100:.0f}%) | {rec_status} |"
        )

    return "\n".join(lines)
