#!/usr/bin/env python3
"""
Autonomous Financial Reconciliation Agent - Command Line Interface.
Provides subcommands to run individual scenarios, execute benchmark evaluations,
conduct error propagation analysis, or launch the interactive web dashboard.
"""

import sys
import json
import argparse
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from agent.orchestrator import FinancialReconciliationAgent
from agent.providers.deterministic_provider import DeterministicAgentProvider
from evaluation.evaluator import BenchmarkEvaluator
from evaluation.error_propagation import ErrorPropagationAnalyzer
from evaluation.report import generate_markdown_benchmark_report, generate_markdown_error_report

def cmd_run(args):
    data_path = PROJECT_ROOT / "data" / "ground_truth_scenarios.json"
    with open(data_path, "r") as f:
        scenarios = json.load(f)

    scenario_to_run = None
    if args.scenario:
        # Match by ID or index number
        sc_arg = args.scenario.strip().upper()
        if sc_arg.isdigit():
            idx = int(sc_arg) - 1
            if 0 <= idx < len(scenarios):
                scenario_to_run = scenarios[idx]
        else:
            if not sc_arg.startswith("SCENARIO-"):
                sc_arg = f"SCENARIO-{int(args.scenario):02d}" if args.scenario.isdigit() else sc_arg
            for s in scenarios:
                if s["scenario_id"] == sc_arg:
                    scenario_to_run = s
                    break

    agent = FinancialReconciliationAgent()

    if scenario_to_run:
        print(f"\n[RUNNING SCENARIO] {scenario_to_run['scenario_id']}: {scenario_to_run['title']}")
        print(f"Description: {scenario_to_run['description']}")
        print(f"Expected Sequence: {' -> '.join(scenario_to_run['expected_tool_sequence']) or 'None (Clarify)'}\n")
        trace = agent.run(scenario_to_run["input_prompt"], scenario_to_run.get("context", {}))
    elif args.query:
        print(f"\n[RUNNING AD-HOC QUERY] \"{args.query}\"\n")
        trace = agent.run(args.query, {})
    else:
        print("Please specify either --scenario <id|number> or --query \"<your query>\"")
        return

    print(trace.format_ascii_trace())

def cmd_eval(args):
    print("\n" + "=" * 76)
    print("RUNNING MULTI-STEP RECONCILIATION BENCHMARK (18 SCENARIOS)")
    print("=" * 76)

    evaluator = BenchmarkEvaluator()
    results = evaluator.run_all()
    md_report = generate_markdown_benchmark_report(results)
    print(md_report)

    if args.output:
        out_file = Path(args.output)
        with open(out_file, "w") as f:
            if out_file.suffix == ".json":
                json.dump(results, f, indent=2)
            else:
                f.write(md_report)
        print(f"\nReport saved to: {out_file}")

def cmd_error_analysis(args):
    print("\n" + "=" * 76)
    print("RUNNING ERROR PROPAGATION & FAILURE-MODE ANALYSIS (FR-5 / Metric 4)")
    print("=" * 76)

    analyzer = ErrorPropagationAnalyzer()
    results = analyzer.run_all()
    md_report = generate_markdown_error_report(results)
    print(md_report)

    if args.output:
        out_file = Path(args.output)
        with open(out_file, "w") as f:
            if out_file.suffix == ".json":
                json.dump(results, f, indent=2)
            else:
                f.write(md_report)
        print(f"\nReport saved to: {out_file}")

def cmd_serve(args):
    from server import run_server
    port = args.port or 8080
    run_server(port=port)

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Financial Reconciliation Agent - CLI & Evaluation Benchmark"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # run
    p_run = subparsers.add_parser("run", help="Run a scenario or custom query with visible reasoning trace")
    p_run.add_argument("--scenario", "-s", help="Scenario ID (e.g. 1, SCENARIO-01, SCENARIO-02)")
    p_run.add_argument("--query", "-q", help="Custom reconciliation query string")

    # eval
    p_eval = subparsers.add_parser("eval", help="Run the full 18-scenario benchmark and compute all 4 metrics")
    p_eval.add_argument("--output", "-o", help="Optional path to save report (.md or .json)")

    # error-analysis
    p_err = subparsers.add_parser("error-analysis", help="Run deliberate error-injection failure-mode analysis")
    p_err.add_argument("--output", "-o", help="Optional path to save report (.md or .json)")

    # serve
    p_serve = subparsers.add_parser("serve", help="Launch interactive web UI dashboard")
    p_serve.add_argument("--port", "-p", type=int, default=8080, help="Port to listen on (default: 8080)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        cmd_run(args)
    elif args.command == "eval":
        cmd_eval(args)
    elif args.command == "error-analysis":
        cmd_error_analysis(args)
    elif args.command == "serve":
        cmd_serve(args)

if __name__ == "__main__":
    main()
