# Quarantined Benchmark Reports (Invalid / No Model)

## Context & Reason for Quarantine
The benchmark reports in this directory were produced during development runs evaluating systems `B3` (GenAI Single-Shot) and `B4` (GenAI Tool-Loop) without an active, working LLM model.

### Technical Evidence:
1. **SQLite Audit Log Analysis:**
   All 2,710 `GeminiAgentProvider` rows recorded in `audit_logs` failed in `0.05ms`–`0.24ms` with 0 tokens consumed and the immediate local exception:
   ```json
   "rejection_reason": "Provider error: Gemini API key is not configured or google-genai SDK is missing."
   ```
2. **Improper Error Propagation:**
   In early benchmark iterations, this provider initialization error was caught and improperly mapped to `decision = "escalate"`. The benchmark runner proceeded to score this error state as if it were a valid system decision, producing artificial metrics (F1, precision, recall, coverage, latencies) that did not reflect actual model performance.
3. **Quarantine Action:**
   Under FIX-1 policy, these files were relocated from `reports/` via `git mv` to prevent invalid model numbers from polluting verified baselines (`B0`, `B1`, `B2`).

## Relocated Files:
- `benchmark_20261027_023213.json`
- `benchmark_20261027_023213.md`
- `benchmark_20261027_023331.json`
- `benchmark_20261027_023331.md`
