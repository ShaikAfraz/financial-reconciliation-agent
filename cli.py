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
PROJECT_ROOT = Path(__file__).parent.absolute()
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

def cmd_gen_benchmark(args):
    from evaluation.dataset_generator import generate_benchmark
    seed = args.seed if args.seed is not None else 42
    out_dir = Path(args.output_dir) if args.output_dir else None

    print("\n" + "=" * 76)
    print(f"GENERATING SEEDED TWO-SIDED RECONCILIATION BENCHMARK (SEED: {seed})")
    print("=" * 76)

    dev_path, test_path, sha_path = generate_benchmark(seed=seed, output_dir=out_dir)

    with open(dev_path, "r", encoding="utf-8") as f:
        dev_data = json.load(f)
    with open(test_path, "r", encoding="utf-8") as f:
        test_data = json.load(f)
    with open(sha_path, "r", encoding="utf-8") as f:
        sha256 = f.read().strip()

    print(f"Generated dev split:  {dev_path} ({len(dev_data['ground_truth'])} groups)")
    print(f"Generated test split: {test_path} ({len(test_data['ground_truth'])} groups)")
    print(f"Test SHA-256:         {sha256}")
    print("\nSlice Counts Breakdown (Stratified 50/50):")
    print("-" * 76)
    print(f"{'Slice Name':<25} | {'Dev Count':<10} | {'Test Count':<10} | {'Total':<8} | {'Share':<6}")
    print("-" * 76)
    dev_slices = dev_data["metadata"]["slice_counts"]
    test_slices = test_data["metadata"]["slice_counts"]
    all_slices = sorted(set(list(dev_slices.keys()) + list(test_slices.keys())))
    total_all = len(dev_data['ground_truth']) + len(test_data['ground_truth'])
    for s in all_slices:
        d_cnt = dev_slices.get(s, 0)
        t_cnt = test_slices.get(s, 0)
        tot = d_cnt + t_cnt
        pct = (tot / total_all) * 100
        print(f"{s:<25} | {d_cnt:<10} | {t_cnt:<10} | {tot:<8} | {pct:>5.1f}%")
    print("-" * 76)
    print(f"{'TOTAL':<25} | {len(dev_data['ground_truth']):<10} | {len(test_data['ground_truth']):<10} | {total_all:<8} | 100.0%")
    print("=" * 76)

def cmd_tune(args):
    from reconcile.tune import tune_b1_on_dev, tune_b2_on_dev
    target = (args.baseline or "all").lower()
    if target in ("b1", "all"):
        tune_b1_on_dev()
    if target in ("b2", "all"):
        tune_b2_on_dev()

def cmd_benchmark(args):
    split = (args.split or "test").lower().strip()

    if args.systems:
        systems = [s.strip().upper() for s in args.systems.split(",") if s.strip()]
    elif args.system:
        systems = [args.system.strip().upper()]
    else:
        systems = ["B0", "B1", "B2", "B3", "B4"]

    repeats = args.repeats if args.repeats is not None else 5

    from evaluation.benchmark_runner import BenchmarkRunner
    runner = BenchmarkRunner(
        split=split,
        systems=systems,
        repeats=repeats,
        project_root=PROJECT_ROOT
    )
    bundle = runner.run_benchmark()

    # Print report
    md_content = runner.generate_markdown_report(bundle)
    print("\n" + md_content)

    if args.output:
        out_p = Path(args.output)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2)
        print(f"Saved benchmark results to {out_p}")


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

    # gen-benchmark
    p_gen = subparsers.add_parser("gen-benchmark", help="Generate seeded two-sided reconciliation benchmark datasets")
    p_gen.add_argument("--seed", type=int, default=42, help="Deterministic random seed (default: 42)")
    p_gen.add_argument("--output-dir", help="Target output directory (default: data/benchmark)")

    # tune
    p_tune = subparsers.add_parser("tune", help="Tune baseline hyperparameter thresholds on dev split only")
    p_tune.add_argument("--baseline", choices=["b1", "b2", "all"], default="all", help="Baseline to tune on dev (default: all)")

    # benchmark
    p_bm = subparsers.add_parser("benchmark", help="Run two-sided reconciliation benchmark evaluation")
    p_bm.add_argument("--split", choices=["dev", "test"], default="test", help="Dataset split to evaluate (default: test)")
    p_bm.add_argument("--systems", help="Comma-separated reconciliation systems to evaluate (e.g. B0,B1,B2,B3,B4)")
    p_bm.add_argument("--system", help="Single system alias (for backward compatibility)")
    p_bm.add_argument("--repeats", type=int, default=5, help="Number of repeat benchmark iterations (default: 5)")
    p_bm.add_argument("--output", "-o", help="Optional path to save JSON results")

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
    elif args.command == "gen-benchmark":
        cmd_gen_benchmark(args)
    elif args.command == "tune":
        cmd_tune(args)
    elif args.command == "benchmark":
        cmd_benchmark(args)

if __name__ == "__main__":
    main()
