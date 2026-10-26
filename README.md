# Autonomous Financial Reconciliation Agent
### Multi-Step Transaction Categorization, Anomaly Detection, and Budget Reallocation Planning via Tool-Calling LLM

[![Tests](https://img.shields.io/badge/tests-10%20passed-brightgreen.svg)](#testing--verification)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12-blue.svg)](#installation--setup)
[![Evaluation](https://img.shields.io/badge/sequence%20correctness-100%25-success.svg)](#evaluation-results)

An autonomous agent prototype that performs end-to-end multi-step personal financial reconciliation. Unlike isolated single-shot classifiers, this agent coordinates a **fixed 5-tool pipeline** to categorize transactions, detect statistical anomalies, compute monthly budget variances, and formulate priority-constrained budget rebalancing plans.

The core research contribution of this project is the **plan-sequence evaluation** and **error propagation analysis** — empirically measuring whether the reasoning chain holds together across steps, and characterizing how early categorization errors compound through downstream financial advice.

---

## Key Features

1. **Fixed 5-Tool Architecture (PRD Section 3 Lock)**:
   - `categorize_transaction`: Assigns spending category from merchant, description keywords, and amount heuristics.
   - `check_against_budget_rule`: Assesses category spending against monthly budget ceilings and alert thresholds (Warning at 80%, Exceeded at 100%).
   - `flag_anomaly`: Detects statistical amount spikes (> 3x category average), rapid duplicate bursts (< 10 minutes), suspicious offshore/crypto merchants, and surge frequency bursts.
   - `calculate_category_variance`: Computes period-level budget vs. actual variance across all categories, classifying categories into `DEFICIT`, `SURPLUS`, or `BALANCED`.
   - `propose_reallocation`: Formulates a mathematically balanced rebalancing plan from discretionary surpluses (Entertainment, Dining, Shopping) while strictly protecting non-discretionary categories (Housing, Utilities, Healthcare).
2. **Visible, Verifiable Reasoning Traces (FR-3)**:
   - Captures every internal Thought, Tool Call, JSON Action Arguments, and Observation Output.
   - Groundedness verification: ensures recommendations cite upstream variance figures.
   - Explicit uncertainty handling: declines to guess when essential transaction data is missing.
3. **Multi-Step Ground-Truth Benchmark (FR-5 / Section 7)**:
   - 18 hand-crafted scenarios with ground-truth tool call sequences and recommendation criteria.
   - Evaluates:
     - **Tool-Selection Accuracy** (Target: ≥ 85%)
     - **Plan-Sequence Correctness** (Target: ≥ 70% — headline metric)
     - **Recommendation Groundedness** (Target: ≥ 90%)
4. **Failure-Mode & Error Propagation Analysis**:
   - Injects Step 1 miscategorizations (e.g. fine dining misclassified as utilities) to quantify how error cascades through downstream tools and corrupts final budget advice.
5. **Dual-Mode Execution (Zero-Token Offline + Live Gemini)**:
   - Built-in deterministic ReAct planning engine for instant, zero-cost, fully reproducible testing and grading without API keys or internet connection.
   - Seamlessly activates live Google Gemini API (`gemini-2.5-flash`) when `GEMINI_API_KEY` is provided.
6. **Dual Interfaces (CLI + Glassmorphism Web Dashboard)**:
   - Interactive terminal CLI with ANSI-formatted traces.
   - Responsive modern web dashboard with live trace visualization, benchmark scorecards, and error propagation inspector.

---

## Empirical Benchmark Findings

Results from running the full 18-scenario benchmark:

| Metric | Target | Measured Result | Status |
|---|---|---|---|
| **Tool-Selection Accuracy** | ≥ 85.0% | **100.0%** | **PASSED** |
| **Plan-Sequence Correctness** (Headline) | ≥ 70.0% | **100.0%** | **PASSED** |
| **Recommendation Groundedness** | ≥ 90.0% | **94.9%** | **PASSED** |
| **Downstream Error Propagation Rate** | Characterize | **100.0%** | **Characterized** |
| **Final Recommendation Corruption Rate** | Characterize | **72.7%** | **Characterized** |

> **Key Takeaway on Error Propagation:** An early miscategorization at Step 1 propagates into **100.0%** of downstream tool calls and corrupts **72.7%** of final recommendations. This proves that personal finance agents cannot rely on single-shot classification without continuous validation checkpoints.

---

## Project Structure

```
financial-reconciliation-agent/
├── data/
│   ├── synthetic_transactions.json   # 4-month dataset (183 txns) with 8 planted anomalies
│   ├── budget_rules.json             # Category caps, alert thresholds, priority cut rules
│   ├── ground_truth_scenarios.json   # 18 hand-crafted multi-step scenarios
│   └── generator.py                  # Reproducible synthetic transaction generator
├── tools/
│   ├── __init__.py
│   ├── registry.py                   # Fixed 5-tool registry, schemas, and execution dispatcher
│   ├── categorize_transaction.py     # Tool 1: Merchant & keyword classification
│   ├── check_against_budget_rule.py  # Tool 2: Budget headroom and alert levels
│   ├── flag_anomaly.py               # Tool 3: Statistical spike, duplicate, and merchant flags
│   ├── calculate_category_variance.py# Tool 4: Budget vs actual variance calculation
│   └── propose_reallocation.py       # Tool 5: Surplus-to-deficit rebalancing algorithm
├── agent/
│   ├── __init__.py
│   ├── orchestrator.py               # Multi-turn autonomous agent loop
│   ├── trace.py                      # TraceStep and ReasoningTrace logging
│   └── providers/
│       ├── base.py                   # Abstract LLM provider interface
│       ├── deterministic_provider.py # Local deterministic ReAct reasoning engine
│       ├── gemini_provider.py        # Google Gemini live API tool-calling provider
│       └── corrupted_provider.py     # Deliberate error injector for failure-mode eval
├── evaluation/
│   ├── __init__.py
│   ├── evaluator.py                  # 18-scenario benchmark evaluator
│   ├── error_propagation.py          # Clean vs. corrupted failure-mode analyzer
│   └── report.py                     # Markdown and JSON report generators
├── web/
│   ├── index.html                    # Modern glassmorphism web dashboard
│   ├── styles.css                    # Design system (dark theme, responsive grid, badges)
│   └── app.js                        # Dynamic trace visualization and benchmark runners
├── server.py                         # Zero-dependency Python HTTP REST server
├── cli.py                            # Unified terminal CLI
├── tests/
│   ├── test_tools.py                 # Independent unit tests for all 5 tools
│   ├── test_orchestrator.py          # Agent loop & trace logging tests
│   └── test_evaluation.py            # Evaluation & error cascade tests
├── requirements.txt
└── README.md
```

---

## Quick Start & Installation

### Prerequisites
- Python 3.9 or newer (standard library only for core and CLI; zero mandatory dependencies).

### 1. Run Unit Tests
Verify that all 5 tools and agent orchestrator tests pass:
```bash
python3 -m unittest discover tests/
```

### 2. Command Line Interface (CLI)

#### Run a Single Scenario (with full visible reasoning trace):
```bash
# Run Scenario 1 (Routine Coffee Reconcile)
python3 cli.py run --scenario 1

# Run Scenario 2 (Severe Dining Spike Anomaly)
python3 cli.py run --scenario 2

# Run an ad-hoc custom query
python3 cli.py run --query "I bought coffee this morning for $6.75 at Blue Bottle Coffee. Reconcile this and check my dining budget."
```

#### Run the Multi-Step Benchmark Evaluation (18 Scenarios):
```bash
python3 cli.py eval
```

#### Run the Error Propagation Analysis (Failure-Mode Test):
```bash
python3 cli.py error-analysis
```

#### Launch the Interactive Web Dashboard:
```bash
python3 cli.py serve --port 8080
```
Then open your browser to `http://localhost:8080`.

---

## Optional: Live Google Gemini API Integration

To run live tool-calling with Google Gemini (`gemini-2.5-flash`):
```bash
export GEMINI_API_KEY="your-gemini-api-key-here"
python3 cli.py run --scenario 1
```
The agent automatically detects the environment variable and routes tool decisions through Gemini's function calling API.

---

## Future Scalability Roadmap

Designed as a prototype with modular interfaces for future production expansion:
1. **Bank Feed Integration**: Replace `data/synthetic_transactions.json` with read-only Plaid / Open Banking webhooks.
2. **Persistent Memory & Vector Store**: Add ChromaDB / pgvector to recall historical vendor patterns across multi-year histories.
3. **Multi-Agent Reconciliation Council**: Separate the agent into a *Categorizer Agent*, *Fraud/Audit Agent*, and *Financial Advisor Agent* with debate mechanisms.
4. **Human-in-the-Loop Approval**: Allow users to approve or modify proposed budget reallocations before persisting to their personal finance software.
