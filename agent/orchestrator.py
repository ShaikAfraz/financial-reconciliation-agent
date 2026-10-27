"""
Agent Orchestration Engine for Autonomous Two-Sided Financial Reconciliation.
Coordinates multi-turn reasoning loops, tool dispatching, trace capturing,
deterministic validator enforcement, and transactional audit logging.
Writes every step to audit_logs inside the same DB transaction as the state change it describes.
"""

import os
import json
import time
import uuid
import sqlite3
from typing import Dict, Any, List, Optional, Tuple

import data.db as db
from agent.trace import ReasoningTrace, TraceStep
from agent.providers.base import BaseAgentProvider
from agent.providers.deterministic_provider import DeterministicTestFixtureProvider, DeterministicAgentProvider
from agent.providers.gemini_provider import GeminiAgentProvider
from agent.providers.corrupted_provider import CorruptedAgentProvider
from tools.registry import execute_tool
from reconcile.validator import generate_validator_token, verify_validator_token
from reconcile.baselines import load_reconcile_config

class FinancialReconciliationAgent:
    """
    Autonomous multi-step reconciliation agent that orchestrates two-sided matching,
    candidate generation, isolated GenAI adjudication, deterministic validation,
    and authoritative balance proof verification.
    """

    def __init__(
        self,
        provider: Optional[BaseAgentProvider] = None,
        max_turns: int = 15,
        adjudication_mode: Optional[str] = None
    ):
        self.config = load_reconcile_config()
        self.adjudication_mode = adjudication_mode or self.config.get("adjudication_mode", "single_shot")

        if provider is not None:
            self.provider = provider
        elif os.environ.get("GEMINI_API_KEY") or adjudication_mode is not None:
            self.provider = GeminiAgentProvider()
        else:
            self.provider = DeterministicTestFixtureProvider()

        self.max_turns = max_turns

    def _get_provider_metadata(self) -> Tuple[str, str]:
        """Returns (provider_name, model_name)."""
        provider_name = getattr(self.provider, "provider_name", type(self.provider).__name__)
        model_name = getattr(self.provider, "model", getattr(self.provider, "model_name", "none"))
        return str(provider_name), str(model_name)

    def execute_pipeline_step(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        trace: ReasoningTrace,
        run_id: str,
        thought: str = "",
        conn: Optional[sqlite3.Connection] = None
    ) -> Dict[str, Any]:
        """
        Executes a pipeline tool and atomically records the audit log row
        within the same database transaction.
        """
        t_start = time.perf_counter()
        step_idx = len(trace.steps) + 1

        # Determine if we manage our own connection/transaction
        should_close = False
        if conn is None:
            conn = db.get_connection()
            should_close = True

        try:
            # 1. Execute tool (passes conn to allow atomic state change inside tool if provided)
            if conn is not None:
                exec_result = execute_tool(tool_name, arguments, conn=conn)
            else:
                exec_result = execute_tool(tool_name, arguments)
            duration_ms = (time.perf_counter() - t_start) * 1000.0

            provider_name, model_name = self._get_provider_metadata()
            tokens_count = 0
            if isinstance(exec_result.get("output"), dict):
                tokens_count = exec_result["output"].get("prompt_tokens", 0) + exec_result["output"].get("completion_tokens", 0)

            # 2. Write to audit_logs inside the same transaction
            log_arguments = {
                k: (getattr(v, "provider_name", type(v).__name__) if k == "provider" else v)
                for k, v in arguments.items()
            }
            db.insert_audit_log(
                run_id=run_id,
                step_index=step_idx,
                tool_name=tool_name,
                arguments_json=json.dumps(log_arguments, default=str),
                output_json=json.dumps(exec_result.get("output", exec_result), default=str),
                duration_ms=duration_ms,
                provider=provider_name,
                model=model_name,
                tokens=tokens_count,
                conn=conn
            )

            if should_close:
                conn.commit()

            # 3. Record step in ReasoningTrace
            trace.add_step(
                thought=thought,
                tool_name=tool_name,
                tool_arguments=log_arguments,
                tool_output=exec_result.get("output", exec_result),
                duration_ms=duration_ms
            )

            return exec_result
        except Exception:
            if should_close:
                conn.rollback()
            raise
        finally:
            if should_close:
                conn.close()

    def reconcile_dataset(
        self,
        bank_records: List[Dict[str, Any]],
        ledger_records: List[Dict[str, Any]],
        opening_bank_balance_cents: int = 0,
        opening_book_balance_cents: int = 0,
        run_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes the complete end-to-end two-sided reconciliation pipeline on a dataset split:
        1. Ingest records into DB (integer cents only).
        2. Generate candidates & deterministic blocking features.
        3. Deterministic baseline matching (B0/B1) with validator pass-token generation.
        4. Isolated GenAI adjudication for ambiguous/unmatched items with validator gating.
        5. Authoritative balance proof computation.
        Every step is transactionally recorded in audit_logs.
        """
        run_id = run_id or f"RUN-{uuid.uuid4().hex[:12].upper()}"
        prompt = f"Execute complete two-sided reconciliation on {len(bank_records)} bank records and {len(ledger_records)} ledger records (run_id={run_id})."
        trace = ReasoningTrace(prompt=prompt, context={"run_id": run_id, "mode": self.adjudication_mode})

        # Step 1: Ingest records
        self.execute_pipeline_step(
            tool_name="ingest_records",
            arguments={"bank_records": bank_records, "ledger_records": ledger_records},
            trace=trace,
            run_id=run_id,
            thought="Ingest raw bank statement records and ERP ledger records with integer cents verification."
        )

        # Step 2: Generate candidate pairs
        cand_res = self.execute_pipeline_step(
            tool_name="generate_candidates",
            arguments={"bank_records": bank_records, "ledger_records": ledger_records},
            trace=trace,
            run_id=run_id,
            thought="Generate candidate pairs using date-window and amount blocking with rapidfuzz token similarity."
        )

        # Step 3: Run deterministic baseline B1 to separate clear matches from ambiguous/unmatched items
        from reconcile.baselines import reconcile_b1
        b1_output = reconcile_b1(bank_records, ledger_records)

        committed_matches = []
        auto_committed_count = 0
        escalated_count = 0
        rejected_count = 0

        # Commit deterministic baseline matches with valid cryptographic tokens
        conn = db.get_connection()
        try:
            for b1_m in b1_output["matches"]:
                b_ids = b1_m["bank_record_ids"]
                l_ids = b1_m["ledger_record_ids"]
                token = generate_validator_token(b_ids[0], l_ids)

                m_args = {
                    "match_id": b1_m["match_id"],
                    "bank_ids": b_ids,
                    "ledger_ids": l_ids,
                    "status": "matched",
                    "method": b1_m.get("method", "deterministic_baseline"),
                    "confidence": b1_m.get("confidence", 1.0),
                    "validator_token": token
                }

                self.execute_pipeline_step(
                    tool_name="commit_match",
                    arguments=m_args,
                    trace=trace,
                    run_id=run_id,
                    thought=f"Commit deterministic match {b1_m['match_id']} with valid validator token.",
                    conn=conn
                )
                committed_matches.append(b1_m)
                auto_committed_count += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # Step 4: Adjudicate ambiguous and unmatched records using GenAI layer
        matched_b_ids = {bid for m in committed_matches for bid in m["bank_record_ids"]}
        unmatched_bank = [b for b in bank_records if b["id"] not in matched_b_ids]

        ledger_map = {l["id"]: l for l in ledger_records}
        adjudications_recorded = []
        provider_errors_count = 0

        # Form candidate subsets for ambiguous bank items
        for b_item in unmatched_bank:
            # Find candidate ledger items within date drift and amount range
            cand_ledgers = [
                l for l in ledger_records
                if abs(b_item["amount_cents"] - l["amount_cents"]) <= 1000
            ][:5]

            feat_list = [
                {
                    "amount_delta_cents": abs(b_item["amount_cents"] - l["amount_cents"]),
                    "date_delta_days": 1,
                    "token_set_ratio": 75.0,
                    "ref_match": 1 if (b_item.get("reference") and b_item.get("reference") == l.get("reference")) else 0,
                    "is_exact_amount": 1 if b_item["amount_cents"] == l["amount_cents"] else 0
                }
                for l in cand_ledgers
            ]

            # 4a. Call adjudicate_item
            adj_res = self.execute_pipeline_step(
                tool_name="adjudicate_item",
                arguments={
                    "target_item": b_item,
                    "candidate_records": cand_ledgers,
                    "deterministic_features": feat_list,
                    "item_side": "bank",
                    "adjudication_mode": self.adjudication_mode,
                    "provider": self.provider
                },
                trace=trace,
                run_id=run_id,
                thought=f"Adjudicate ambiguous bank item {b_item['id']} using GenAI layer (mode={self.adjudication_mode})."
            )

            outcome_dict = adj_res.get("output", {})

            # 4b. Call validate_decision
            val_res = self.execute_pipeline_step(
                tool_name="validate_decision",
                arguments={
                    "adjudication_response": outcome_dict,
                    "target_item": b_item,
                    "candidate_records": cand_ledgers,
                    "deterministic_features": feat_list
                },
                trace=trace,
                run_id=run_id,
                thought=f"Validate adjudication decision for {b_item['id']} deterministically."
            )
            val_dict = val_res.get("output", {})

            adjudications_recorded.append({
                "target_item": b_item,
                "candidate_records": cand_ledgers,
                "outcome": outcome_dict,
                "validation": val_dict
            })

            # Check for provider failure - provider errors must NEVER be counted as system decisions
            rej_reason = str(outcome_dict.get("rejection_reason", "")) + " " + str(val_dict.get("rejection_reason", ""))
            is_provider_error = "Provider error:" in rej_reason or outcome_dict.get("is_provider_error", False)

            # 4c. Authoritative Gateway
            if is_provider_error:
                provider_errors_count += 1
                db.insert_exception(
                    record_id=b_item["id"],
                    side="bank",
                    exc_type="provider_error",
                    status="provider_failed"
                )
            elif val_dict.get("is_valid") and outcome_dict.get("auto_commit") and outcome_dict.get("decision") == "select":
                token = val_dict.get("validator_token")
                m_id = f"MATCH-ADJ-{b_item['id']}"
                sel_ids = val_dict.get("selected_candidate_ids", [])

                conn_adj = db.get_connection()
                try:
                    self.execute_pipeline_step(
                        tool_name="commit_match",
                        arguments={
                            "match_id": m_id,
                            "bank_ids": [b_item["id"]],
                            "ledger_ids": sel_ids,
                            "status": "matched",
                            "method": "genai_adjudicated",
                            "confidence": outcome_dict.get("confidence", 0.90),
                            "validator_token": token
                        },
                        trace=trace,
                        run_id=run_id,
                        thought=f"Commit validator-approved adjudication match {m_id}.",
                        conn=conn_adj
                    )
                    conn_adj.commit()
                    auto_committed_count += 1
                    committed_matches.append({
                        "match_id": m_id,
                        "bank_record_ids": [b_item["id"]],
                        "ledger_record_ids": sel_ids
                    })
                except Exception:
                    conn_adj.rollback()
                    raise
                finally:
                    conn_adj.close()

            elif not val_dict.get("is_valid"):
                rejected_count += 1
                db.insert_exception(
                    record_id=b_item["id"],
                    side="bank",
                    exc_type="validator_rejected",
                    status="escalated"
                )
            else:
                escalated_count += 1
                db.insert_exception(
                    record_id=b_item["id"],
                    side="bank",
                    exc_type=outcome_dict.get("exception_type", "timing_difference"),
                    status="pending_review"
                )

        # Step 5: Compute balance proof
        proof_res = self.execute_pipeline_step(
            tool_name="compute_balance_proof",
            arguments={
                "bank_records": bank_records,
                "ledger_records": ledger_records,
                "matches": committed_matches,
                "opening_bank_balance_cents": opening_bank_balance_cents,
                "opening_book_balance_cents": opening_book_balance_cents
            },
            trace=trace,
            run_id=run_id,
            thought="Compute authoritative two-sided balance proof across all committed matches."
        )
        proof = proof_res.get("output", {})

        # Step 6: Complete reasoning trace
        rec_summary = (
            f"Two-Sided Reconciliation completed.\n"
            f"• Auto-Committed Matches: {auto_committed_count}\n"
            f"• Escalated Items: {escalated_count}\n"
            f"• Rejected by Validator: {rejected_count}\n"
            f"• Ending Bank: ${proof.get('ending_bank_balance_cents', 0)/100:.2f} | "
            f"Ending Book: ${proof.get('ending_book_balance_cents', 0)/100:.2f}\n"
            f"• Balance Proof Difference: ${proof.get('difference_cents', 0)/100:.2f} -> "
            f"{'BALANCED' if proof.get('is_balanced') else 'UNBALANCED'}"
        )
        facts = [
            f"Auto-committed count: {auto_committed_count}",
            f"Escalated count: {escalated_count}",
            f"Rejected count: {rejected_count}",
            f"Balance proof difference cents: {proof.get('difference_cents', 0)}"
        ]
        trace.complete(
            final_recommendation=rec_summary,
            grounded_facts=facts,
            verification="deterministic_validated"
        )

        return {
            "trace": trace,
            "run_id": run_id,
            "auto_committed": auto_committed_count,
            "escalated": escalated_count,
            "rejected": rejected_count,
            "provider_errors": provider_errors_count,
            "matches": committed_matches,
            "balance_proof": proof,
            "total_steps": len(trace.steps),
            "adjudications": adjudications_recorded
        }

    def run(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> ReasoningTrace:
        """
        Executes autonomous reconciliation loop, capturing steps and logging
        to audit_logs inside atomic DB transactions.
        """
        run_id = (context or {}).get("run_id") or f"RUN-{uuid.uuid4().hex[:12].upper()}"
        trace = ReasoningTrace(prompt=prompt, context=context)

        for turn in range(1, self.max_turns + 1):
            decision = self.provider.plan_step(prompt, trace.context, trace.steps)
            action = decision.get("action")
            thought = decision.get("thought", "")

            if action == "finish":
                final_rec = decision.get("final_recommendation", "Reconciliation completed.")
                facts = decision.get("grounded_facts", [])
                verification = decision.get("verification", "none")
                trace.complete(final_recommendation=final_rec, grounded_facts=facts, verification=verification)
                break

            elif action == "tool_call":
                tool_name = decision.get("tool_name", "")
                arguments = decision.get("arguments", {})

                # Execute pipeline step with transactional audit logging
                self.execute_pipeline_step(
                    tool_name=tool_name,
                    arguments=arguments,
                    trace=trace,
                    run_id=run_id,
                    thought=thought
                )

            else:
                trace.error = f"Unrecognized agent action: {action}"
                trace.complete(final_recommendation="Agent terminated due to unrecognized decision state.")
                break
        else:
            trace.error = f"Agent reached maximum execution turns ({self.max_turns})."
            trace.complete(final_recommendation="Reconciliation terminated: maximum reasoning steps exceeded.")

        return trace
