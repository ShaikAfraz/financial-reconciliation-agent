# Baseline Verification Report (Pre-Pivot State)

**Date of Audit:** October 27, 2026  
**Git Branch:** `pre-pivot-baseline`  
**Git Tag:** `pre-pivot-baseline`  
**Working Tree Status:** Clean (no source code modified)

---

## 1. Executive Summary & Verification Findings

This document records the empirical verification of architectural, security, and algorithmic claims regarding the initial implementation of the Autonomous Financial Reconciliation Agent prior to any structural refactoring.

All seven claims (a) through (g) have been verified against the source code:

| Claim | Topic | Verdict | Primary File:Line Evidence |
|---|---|---|---|
| **(a)** | API Key Exposure in URL | **CONFIRMED** | `agent/providers/gemini_provider.py:22` |
| **(b)** | Unverified Hardcoded Groundedness | **CONFIRMED** | `agent/providers/gemini_provider.py:117` |
| **(c)** | Unused `audit_logs` Database Table | **CONFIRMED** | `data/db.py:49` |
| **(d)** | Unauthenticated/Unvalidated Tool Execution | **CONFIRMED** | `server.py:125-129` |
| **(e)** | Float/Rounding Precision in Financial Math | **CONFIRMED** | Multiple locations across `tools/`, `data/`, `evaluation/` (enumerated below) |
| **(f)** | Unused `google-genai` Dependency | **CONFIRMED** | `requirements.txt:4` |
| **(g)** | Evaluation Path Bypasses LLM | **CONFIRMED** | `cli.py:65`, `evaluation/evaluator.py:24` |

---

## 2. Detailed Claim-by-Claim Verification & Evidence

### Claim (a): `gemini_provider.py puts the API key in the URL query string.`
* **Verdict:** **CONFIRMED**
* **File & Line Evidence:** [`agent/providers/gemini_provider.py:22`](agent/providers/gemini_provider.py#L22)
* **Code Excerpt:**
  ```python
  class GeminiAgentProvider(BaseAgentProvider):
      def __init__(self, api_key: str = None, model: str = "gemini-2.5-flash"):
          self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
          self.model = model
          self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
  ```
* **Analysis:** The `endpoint` URL string directly interpolates `self.api_key` as a cleartext URL query parameter (`?key=...`). When outgoing HTTP requests are dispatched via `urllib.request.Request(self.endpoint, ...)`, this query string is exposed in proxy logs, HTTP request headers, browser history, and network packet traces rather than using an `Authorization: Bearer <token>` or `x-goog-api-key` HTTP header.

---

### Claim (b): `gemini_provider.py hardcodes grounded_facts "LLM synthesis verified." without checking anything.`
* **Verdict:** **CONFIRMED**
* **File & Line Evidence:** [`agent/providers/gemini_provider.py:111-118`](agent/providers/gemini_provider.py#L111-L118)
* **Code Excerpt:**
  ```python
              elif "text" in part:
                  text = part["text"]
                  return {
                      "action": "finish",
                      "thought": "LLM finalized reconciliation plan.",
                      "final_recommendation": text,
                      "grounded_facts": ["LLM synthesis verified."]
                  }
  ```
* **Analysis:** When the Gemini model returns a textual response (indicating it wishes to finish the reasoning turn), the provider returns `"grounded_facts": ["LLM synthesis verified."]`. No inspection, token extraction, numerical cross-check, or consistency validation against upstream tool outputs (e.g. variances, amounts, budget limits) is performed. The string is a static, unverified literal.

---

### Claim (c): `audit_logs is created in data/db.py but never written to anywhere.`
* **Verdict:** **CONFIRMED**
* **File & Line Evidence:** [`data/db.py:49-58`](data/db.py#L49-L58)
* **Code Excerpt:**
  ```python
      CREATE TABLE IF NOT EXISTS audit_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
          run_id TEXT NOT NULL,
          step_index INTEGER NOT NULL,
          tool_name TEXT NOT NULL,
          arguments_json TEXT,
          output_json TEXT,
          duration_ms REAL
      );
  ```
* **Analysis:** A global search across the entire codebase (`grep -rn "audit_logs" .`) returns exactly one hit: the schema definition at `data/db.py:49`. There is no `INSERT INTO audit_logs` anywhere in `data/`, `agent/`, `tools/`, or `evaluation/`. The table is created upon database initialization but remains permanently empty at runtime.

---

### Claim (d): `server.py exposes /api/test-tool with no authentication or validation.`
* **Verdict:** **CONFIRMED**
* **File & Line Evidence:** [`server.py:125-129`](server.py#L125-L129)
* **Code Excerpt:**
  ```python
          elif path == "/api/test-tool":
              tool_name = req_data.get("tool_name")
              arguments = req_data.get("arguments", {})
              result = execute_tool(tool_name, arguments)
              self._send_json(result)
  ```
* **Analysis:** The handler parses `req_data` directly from incoming POST bodies. It contains:
  1. **Zero Authentication:** No API keys, JWT tokens, cookies, or authorization headers are inspected.
  2. **Zero Authorization:** Anyone who can reach port 8080 can invoke arbitrary tool logic.
  3. **Zero Validation:** `tool_name` and `arguments` are passed directly to `execute_tool(tool_name, arguments)` without schema checks or validation.

---

### Claim (e): `Any monetary amount in tools/, data/, or evaluation/ is handled as a Python float or rounded with round(..., 2) rather than integer cents/Decimal. List every location.`
* **Verdict:** **CONFIRMED**
* **Analysis:** The `decimal` module / `Decimal` type is **never imported or instantiated** anywhere in the codebase. Every monetary quantity is stored as a SQLite `REAL` or Python `float`, and rounded using standard binary floating-point `round(..., 2)`.

#### Comprehensive Enumeration of Locations:

#### 1. In `tools/`
* **`tools/categorize_transaction.py`**:
  * Line 61: `def categorize_transaction(merchant: str, description: str = "", amount: float = 0.0) -> Dict[str, Any]:` (parameter type annotation and default value)
* **`tools/check_against_budget_rule.py`**:
  * Line 9: `def check_against_budget_rule(category: str, additional_spend: float = 0.0, month: str = "2026-08")`
  * Line 31: `monthly_limit = float(rule["monthly_limit"])`
  * Line 33: `flex_weight = float(rule["flexibility_weight"])`
  * Line 37: `SELECT COALESCE(SUM(amount), 0.0) AS total_actual`
  * Line 42: `existing_spend = round(float(row["total_actual"]), 2) if row else 0.0`
  * Line 45: `total_spend = round(existing_spend + float(additional_spend), 2)`
  * Line 46: `utilization_pct = round((total_spend / monthly_limit) * 100, 1) if monthly_limit > 0 else 0.0`
  * Line 48: `overage = max(0.0, round(total_spend - monthly_limit, 2))`
  * Line 49: `remaining = max(0.0, round(monthly_limit - total_spend, 2))`
  * Line 64: `"existing_spend": existing_spend` (float)
  * Line 65: `"additional_spend": round(float(additional_spend), 2)`
  * Line 66: `"total_spend": total_spend` (float)
  * Line 67: `"budget_limit": monthly_limit` (float)
  * Line 70: `"remaining_budget": remaining` (float)
  * Line 71: `"overage_amount": overage` (float)
* **`tools/flag_anomaly.py`**:
  * Line 15: `amount: float` (function signature)
  * Line 23: `amt = float(amount or 0.0)`
  * Line 70: `if "uber" in m_clean and amt >= 70.0 and date:`
  * Line 89: `cat_avg = float(row["avg_amt"]) if (row and row["avg_amt"]) else 45.0`
  * Line 91: `spike_threshold = cat_avg * 3.0`
  * Line 93: `if amt >= spike_threshold and amt >= 100.0:`
  * Line 94: `multiplier = round(amt / cat_avg, 1)`
  * Line 103: `"category_mean": round(cat_avg, 2)`
* **`tools/calculate_category_variance.py`**:
  * Line 22, 28: `COALESCE(SUM(t.amount), 0.0)`
  * Line 35: `total_deficits = 0.0`
  * Line 36: `total_surpluses = 0.0`
  * Line 39: `total_budget = 0.0`
  * Line 40: `total_actual = 0.0`
  * Line 44: `limit = float(r["monthly_limit"])`
  * Line 45: `actual = round(float(r["actual_spend"]), 2)`
  * Line 46: `variance = round(actual - limit, 2)`
  * Line 47: `variance_pct = round(((actual - limit) / limit) * 100, 1) if limit > 0 else 0.0`
  * Line 49: `total_budget += limit`
  * Line 50: `total_actual += actual`
  * Line 52: `if variance > 0.01:`
  * Line 54: `total_deficits += variance`
  * Line 56: `elif variance < -0.01:`
  * Line 58: `total_surpluses += abs(variance)`
  * Line 66: `"flexibility_weight": float(r["flexibility_weight"])`
  * Line 67: `"budget_limit": limit` (float)
  * Line 68: `"actual_spend": actual` (float)
  * Line 69: `"variance_amount": variance` (float)
  * Line 72: `"surplus_headroom": max(0.0, round(limit - actual, 2))`
  * Line 73: `"deficit_overage": max(0.0, round(actual - limit, 2))`
  * Line 76: `net_variance = round(total_actual - total_budget, 2)`
  * Line 80: `"total_budget": round(total_budget, 2)`
  * Line 81: `"total_actual": round(total_actual, 2)`
  * Line 82: `"net_variance": net_variance` (float)
  * Line 83: `"total_deficits": round(total_deficits, 2)`
  * Line 84: `"total_surpluses": round(total_surpluses, 2)`
* **`tools/propose_reallocation.py`**:
  * Line 27: `'flex': float(r['flexibility_weight'])`
  * Line 35: `if item["status"] == "DEFICIT" and item["deficit_overage"] > 0.0:`
  * Line 37: `elif item["status"] == "SURPLUS" and item["surplus_headroom"] > 0.0:`
  * Line 48: `"total_deficit_absorbed": 0.0`
  * Line 49: `"remaining_uncovered_deficit": 0.0`
  * Line 54: `total_deficit_needed = sum(deficits.values())` (sum of floats)
  * Line 59: `meta.get(c, {}).get("flex", 0.0)`
  * Line 64: `absorbed_amount = 0.0`
  * Line 71: `avail = remaining_surpluses.get(don_cat, 0.0)`
  * Line 72: `if avail <= 0.01 or needed <= 0.01:`
  * Line 75: `transfer_amt = min(needed, avail)`
  * Line 76: `transfer_amt = round(transfer_amt, 2)`
  * Line 89: `needed = round(needed - transfer_amt, 2)`
  * Line 90: `remaining_surpluses[don_cat] = round(remaining_surpluses[don_cat] - transfer_amt, 2)`
  * Line 91: `absorbed_amount = round(absorbed_amount + transfer_amt, 2)`
  * Line 95: `remaining_uncovered = round(sum(remaining_deficits.values()), 2)`
  * Line 96: `is_fully_covered = remaining_uncovered <= 0.01`
  * Line 114: `"initial_total_deficit": round(total_deficit_needed, 2)`
  * Line 115: `"total_deficit_absorbed": absorbed_amount` (float)
  * Line 116: `"remaining_uncovered_deficit": remaining_uncovered` (float)

#### 2. In `data/`
* **`data/db.py`**:
  * Line 29: `monthly_limit REAL NOT NULL` (SQLite schema)
  * Line 31: `flexibility_weight REAL NOT NULL` (SQLite schema)
  * Line 41: `amount REAL NOT NULL` (SQLite schema)
  * Line 57: `duration_ms REAL` (SQLite schema)
  * Lines 65-72: Float literals in budget rules table seeding: `("Housing", 1800.0, ...), ("Utilities", 250.0, ...), ("Healthcare", 150.0, ...), ("Groceries", 550.0, ...), ("Transport", 200.0, ...), ("Shopping", 300.0, ...), ("Dining", 400.0, ...), ("Entertainment", 150.0, ...)`
  * Lines 93-130: Float base amounts in template tuples: `1800.00`, `114.50`, `42.80`, `79.99`, `34.20`, `78.40`, `112.50`, `185.00`, `36.00`, `16.50`, `14.80`, `6.75`, `72.00`, `19.20`, `12.00`, `34.00`, `24.50`, `21.00`, `48.00`, `28.50`, `22.00`, `40.00`, `19.99`, `11.99`, `36.50`, `49.99`, `59.99`, `58.20`, `64.50`, `85.00`, `149.00`
  * Line 152: `1800.00` (float)
  * Line 166: `19.99` (float)
  * Line 179: `79.99` (float)
  * Line 190: `amt_var = round(template[3] * random.uniform(0.85, 1.20), 2)`
  * Lines 211-229: Float literals in anomaly entries: `745.00`, `59.99`, `289.50`, `490.80`, `412.50`, `26.50`, `10.00`, `78.00`, `1299.00`, `55.00`
* **`data/generator.py`**:
  * Line 132: `amt = max(3.50, round(random.normalvariate(mean_amt, max(1.0, std_amt)), 2))`
  * Lines 157, 170, 183, 196, 209, 222, 235, 248: Planted anomaly float amounts: `745.00`, `59.99`, `289.50`, `490.80`, `412.50`, `114.50`, `1299.00`, `55.00`
* **`data/budget_rules.json`**:
  * Floating-point numbers for `"monthly_limit"`: `1800.0`, `250.0`, `150.0`, `550.0`, `200.0`, `300.0`, `400.0`, `150.0`
* **`data/synthetic_transactions.json`** & **`data/ground_truth_scenarios.json`**:
  * Floating-point numbers for all `"amount"` fields (e.g. `6.75`, `340.0`, `59.99`, `120.0`)

#### 3. In `evaluation/`
* **`evaluation/error_propagation.py`**:
  * Line 39: `txn_amount = float(context.get("transaction", {}).get("amount", 50.0))`
  * Line 108: `dollar_drift = round(txn_amount * 2.0, 2) if clean_cat != corrupted_category else 0.0`
  * Line 117: `round(corrupted_steps_count / downstream_steps_evaluated, 3)`
  * Line 145: `total_dollar_drift = round(sum(r["dollar_drift"] for r in results), 2)`
  * Line 147: `round(total_corrupted_downstream / total_evaluated_downstream, 3)`
  * Line 153: `recommendation_corruption_rate = round(corrupted_recs / total_recs, 3)`
* **`evaluation/evaluator.py`**:
  * Line 70: `"amount": txn_ctx.get("amount", None)` (passed as float)

---

### Claim (f): `requirements.txt declares google-genai but nothing imports it.`
* **Verdict:** **CONFIRMED**
* **File & Line Evidence:** [`requirements.txt:4`](requirements.txt#L4)
* **Code Excerpt:**
  ```text
  google-genai>=1.0.0
  pytest>=7.0.0
  ```
* **Analysis:** While `google-genai` is listed in `requirements.txt`, grepping the entire repository for `google_genai`, `google.genai`, or `from google` reveals zero import statements. The live provider in `agent/providers/gemini_provider.py:8` directly implements raw HTTP REST requests via standard library `urllib.request`.

---

### Claim (g): `The default cli.py eval path never calls an LLM.`
* **Verdict:** **CONFIRMED**
* **File & Line Evidence:**
  * [`cli.py:65`](cli.py#L65):
    ```python
    def cmd_eval(args):
        ...
        evaluator = BenchmarkEvaluator()
        results = evaluator.run_all()
    ```
  * [`evaluation/evaluator.py:23-24`](evaluation/evaluator.py#L23-L24):
    ```python
    class BenchmarkEvaluator:
        def __init__(self, agent: FinancialReconciliationAgent = None):
            self.agent = agent or FinancialReconciliationAgent(provider=DeterministicAgentProvider())
    ```
* **Analysis:** The `cmd_eval` function calls `BenchmarkEvaluator()` with no arguments. `BenchmarkEvaluator.__init__` explicitly assigns `self.agent` to a `FinancialReconciliationAgent` backed by `DeterministicAgentProvider()`. Even if `GEMINI_API_KEY` is present in the shell environment, `BenchmarkEvaluator` bypasses the API-key check in `FinancialReconciliationAgent.__init__` because an explicit provider (`DeterministicAgentProvider()`) is supplied. Consequently, `cli.py eval` is 100% deterministic, rule-based, local, and never calls an LLM.

---

## 3. Raw Execution Output of Test & Evaluation Suites

### 3.1. Unit Test Suite: `python3 -m unittest discover tests/`

```text
..........
----------------------------------------------------------------------
Ran 10 tests in 0.028s

OK
```

---

### 3.2. Benchmark Evaluation Suite: `python3 cli.py eval`

```text

============================================================================
RUNNING MULTI-STEP RECONCILIATION BENCHMARK (18 SCENARIOS)
============================================================================
# Autonomous Financial Reconciliation Agent: Benchmark Evaluation Report

## Core Evaluation Metrics (Section 7 PRD)

| Metric | Target | Actual Score | Status |
|---|---|---|---|
| **Tool-Selection Accuracy** | ≥ 85.0% | **100.0%** | `PASSED` |
| **Plan-Sequence Correctness** (Headline) | ≥ 70.0% | **100.0%** | `PASSED` |
| **Recommendation Groundedness** | ≥ 90.0% | **94.9%** | `PASSED` |

**Total Scenarios Evaluated:** 18

## Scenario Breakdown

| Scenario ID | Title | Expected Sequence | Actual Sequence | Match | Tool Acc | Groundedness |
|---|---|---|---|---|---|---|
| `SCENARIO-01` | Standard Coffee & Cafe Purchase Reconciliation | `categorize_transaction → flag_anomaly → check_against_budget_rule` | `categorize_transaction → flag_anomaly → check_against_budget_rule` | Match | 100% | 100% |
| `SCENARIO-02` | Severe Restaurant Spike Anomaly Detection | `categorize_transaction → flag_anomaly → check_against_budget_rule` | `categorize_transaction → flag_anomaly → check_against_budget_rule` | Match | 100% | 100% |
| `SCENARIO-03` | Rapid Duplicate Subscription Charge | `categorize_transaction → flag_anomaly` | `categorize_transaction → flag_anomaly` | Match | 100% | 67% |
| `SCENARIO-04` | Monthly Category Variance Overview | `calculate_category_variance` | `calculate_category_variance` | Match | 100% | 100% |
| `SCENARIO-05` | Month-End Budget Reallocation Plan | `calculate_category_variance → propose_reallocation` | `calculate_category_variance → propose_reallocation` | Match | 100% | 100% |
| `SCENARIO-06` | Mid-Month Dining Overspend and Rebalancing | `categorize_transaction → check_against_budget_rule → calculate_category_variance → propose_reallocation` | `categorize_transaction → check_against_budget_rule → calculate_category_variance → propose_reallocation` | Match | 100% | 67% |
| `SCENARIO-07` | Unrecognized Foreign Crypto POS Merchant | `categorize_transaction → flag_anomaly` | `categorize_transaction → flag_anomaly` | Match | 100% | 75% |
| `SCENARIO-08` | Major Electricity Bill Spike Reconcile | `categorize_transaction → flag_anomaly → check_against_budget_rule` | `categorize_transaction → flag_anomaly → check_against_budget_rule` | Match | 100% | 100% |
| `SCENARIO-09` | Severe Deficit Reallocation Request | `calculate_category_variance → propose_reallocation` | `calculate_category_variance → propose_reallocation` | Match | 100% | 100% |
| `SCENARIO-10` | Insufficient Information Handling | `*(None / Clarify)*` | `*(None / Clarify)*` | Match | 100% | 100% |
| `SCENARIO-11` | Routine Grocery Purchase | `categorize_transaction → flag_anomaly → check_against_budget_rule` | `categorize_transaction → flag_anomaly → check_against_budget_rule` | Match | 100% | 100% |
| `SCENARIO-12` | Balanced Month Audit with Zero Reallocation | `calculate_category_variance → propose_reallocation` | `calculate_category_variance → propose_reallocation` | Match | 100% | 100% |
| `SCENARIO-13` | Burst Rideshare Surge Anomaly | `categorize_transaction → flag_anomaly → check_against_budget_rule` | `categorize_transaction → flag_anomaly → check_against_budget_rule` | Match | 100% | 100% |
| `SCENARIO-14` | Protected Non-Discretionary Category Reallocation Rule | `calculate_category_variance → propose_reallocation` | `calculate_category_variance → propose_reallocation` | Match | 100% | 100% |
| `SCENARIO-15` | Extraordinary Electronics Purchase Reconciliation | `categorize_transaction → flag_anomaly → check_against_budget_rule → calculate_category_variance → propose_reallocation` | `categorize_transaction → flag_anomaly → check_against_budget_rule → calculate_category_variance → propose_reallocation` | Match | 100% | 100% |
| `SCENARIO-16` | Rapid Double Gas Station Swipe Anomaly | `categorize_transaction → flag_anomaly` | `categorize_transaction → flag_anomaly` | Match | 100% | 100% |
| `SCENARIO-17` | Cross-Category Rebalancing to Cover Groceries | `calculate_category_variance → propose_reallocation` | `calculate_category_variance → propose_reallocation` | Match | 100% | 100% |
| `SCENARIO-18` | Ambiguous Vendor Description Classification | `categorize_transaction → flag_anomaly` | `categorize_transaction → flag_anomaly` | Match | 100% | 100% |
```

---

### 3.3. Failure-Mode & Error Propagation Suite: `python3 cli.py error-analysis`

```text

============================================================================
RUNNING ERROR PROPAGATION & FAILURE-MODE ANALYSIS (FR-5 / Metric 4)
============================================================================
# Error Propagation & Failure-Mode Analysis Report

## Executive Summary

> **Key Finding:** An early miscategorization cascades into 100.0% of downstream tool steps and directly corrupts 72.7% of final recommendations, producing $6,353.08 in total cumulative budget variance distortion across evaluated test scenarios.

| Metric | Value |
|---|---|
| **Scenarios Tested** | 11 |
| **Downstream Steps Evaluated** | 21 |
| **Corrupted Downstream Steps** | 21 |
| **Overall Downstream Error Rate** | **100.0%** |
| **Cumulative Ledger Dollar Drift** | **$6,353.08** |
| **Final Recommendation Corruption Rate** | **72.7%** |

## Downstream Cascade Breakdown

| Scenario | Original | Injected Perturbation | Amount | Dollar Drift | Downstream Corrupted | Rec Status |
|---|---|---|---|---|---|---|
| `SCENARIO-01` | `Dining` | `Utilities` | $6.75 | $13.50 | 2 / 2 (100%) | Corrupted |
| `SCENARIO-02` | `Dining` | `Shopping` | $745.00 | $1490.00 | 2 / 2 (100%) | Resilient |
| `SCENARIO-03` | `Entertainment` | `Healthcare` | $59.99 | $119.98 | 1 / 1 (100%) | Corrupted |
| `SCENARIO-06` | `Dining` | `Groceries` | $120.00 | $240.00 | 3 / 3 (100%) | Resilient |
| `SCENARIO-07` | `Shopping` | `Utilities` | $289.50 | $579.00 | 1 / 1 (100%) | Corrupted |
| `SCENARIO-08` | `Utilities` | `Entertainment` | $412.50 | $825.00 | 2 / 2 (100%) | Corrupted |
| `SCENARIO-11` | `Groceries` | `Dining` | $78.40 | $156.80 | 2 / 2 (100%) | Corrupted |
| `SCENARIO-13` | `Transport` | `Entertainment` | $78.00 | $156.00 | 2 / 2 (100%) | Corrupted |
| `SCENARIO-15` | `Shopping` | `Healthcare` | $1299.00 | $2598.00 | 4 / 4 (100%) | Resilient |
| `SCENARIO-16` | `Transport` | `Groceries` | $55.00 | $110.00 | 1 / 1 (100%) | Corrupted |
| `SCENARIO-18` | `Shopping` | `Transport` | $32.40 | $64.80 | 1 / 1 (100%) | Corrupted |
```
