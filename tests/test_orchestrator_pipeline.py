"""
Unit and integration tests for Orchestrator Pipeline Wiring, Audit Logging, and Tool Safety:
1. Audit row count equals executed steps.
2. commit_match without a validator token raises PermissionError.
3. Read-only tool call hard cap (max 3 calls per item) is strictly enforced.
4. LLM cannot call commit_match.
5. A rolled-back commit transaction leaves no match rows.
"""

import json
import sqlite3
import unittest
import uuid
from pathlib import Path

import data.db as db
from agent.orchestrator import FinancialReconciliationAgent
from agent.providers.stub_provider import StubProvider
from tools.reconcile_tools import commit_match_tool
from reconcile.validator import generate_validator_token
from reconcile.adjudicator import execute_llm_read_only_tool

class TestOrchestratorPipeline(unittest.TestCase):

    def setUp(self):
        db.init_database()
        self.run_id = f"TEST-RUN-{uuid.uuid4().hex[:8].upper()}"
        self.bank_item = {
            "id": f"BANK-TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-08-15",
            "amount_cents": 15000,
            "description": "CLOUD HOSTING VENDOR",
            "reference": "INV-771"
        }
        self.ledger_item = {
            "id": f"LEDGER-TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-08-15",
            "amount_cents": 15000,
            "description": "Cloud Hosting Services",
            "reference": "INV-771"
        }

    def test_audit_row_count_equals_executed_steps(self):
        """Test 1: Verify that every executed pipeline step produces an audit_logs entry."""
        from agent.trace import ReasoningTrace
        stub = StubProvider()
        agent = FinancialReconciliationAgent(provider=stub)
        trace = ReasoningTrace(prompt="reconcile run", context={"run_id": self.run_id})

        # Ingest records
        agent.execute_pipeline_step(
            tool_name="ingest_records",
            arguments={"bank_records": [self.bank_item], "ledger_records": [self.ledger_item]},
            trace=trace,
            run_id=self.run_id,
            thought="Step 1: Ingest records."
        )

        # Generate candidates
        agent.execute_pipeline_step(
            tool_name="generate_candidates",
            arguments={"bank_records": [self.bank_item], "ledger_records": [self.ledger_item]},
            trace=trace,
            run_id=self.run_id,
            thought="Step 2: Generate candidates."
        )

        # Compute balance proof
        agent.execute_pipeline_step(
            tool_name="compute_balance_proof",
            arguments={"bank_records": [self.bank_item], "ledger_records": [self.ledger_item], "matches": []},
            trace=trace,
            run_id=self.run_id,
            thought="Step 3: Compute balance proof."
        )

        # Verify audit_logs count for this run_id equals 3
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE run_id = ?", (self.run_id,))
        count = cursor.fetchone()[0]

        cursor.execute("SELECT step_index, tool_name, provider, model FROM audit_logs WHERE run_id = ? ORDER BY step_index ASC", (self.run_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        self.assertEqual(count, 3, f"Expected 3 audit log rows, got {count}")
        self.assertEqual(rows[0]["step_index"], 1)
        self.assertEqual(rows[0]["tool_name"], "ingest_records")
        self.assertEqual(rows[1]["step_index"], 2)
        self.assertEqual(rows[1]["tool_name"], "generate_candidates")
        self.assertEqual(rows[2]["step_index"], 3)
        self.assertEqual(rows[2]["tool_name"], "compute_balance_proof")

    def test_commit_match_without_validator_token_raises(self):
        """Test 2: commit_match without a valid token raises PermissionError."""
        match_id = f"MATCH-NO-TOKEN-{uuid.uuid4().hex[:6]}"

        # Missing token
        with self.assertRaises(PermissionError) as ctx1:
            commit_match_tool(
                match_id=match_id,
                bank_ids=[self.bank_item["id"]],
                ledger_ids=[self.ledger_item["id"]],
                status="matched",
                method="test",
                confidence=1.0,
                validator_token=""  # empty token
            )
        self.assertIn("requires a valid validator-pass token", str(ctx1.exception))

        # None token
        with self.assertRaises(PermissionError):
            commit_match_tool(
                match_id=match_id,
                bank_ids=[self.bank_item["id"]],
                ledger_ids=[self.ledger_item["id"]],
                status="matched",
                method="test",
                confidence=1.0,
                validator_token=None  # type: ignore
            )

        # Forged token
        with self.assertRaises(PermissionError) as ctx2:
            commit_match_tool(
                match_id=match_id,
                bank_ids=[self.bank_item["id"]],
                ledger_ids=[self.ledger_item["id"]],
                status="matched",
                method="test",
                confidence=1.0,
                validator_token="deadbeefcafebabe1234567890abcdef"
            )
        self.assertIn("Invalid or forged validator-pass token", str(ctx2.exception))

    def test_read_only_tool_cap_enforced_and_commit_blocked_for_llm(self):
        """Test 3: LLM read-only inquiry tools enforce a hard cap of 3 calls per item and block mutating tools."""
        db.insert_bank_record(
            rec_id=self.bank_item["id"],
            date=self.bank_item["date"],
            amount_cents=self.bank_item["amount_cents"],
            description=self.bank_item["description"],
            reference=self.bank_item["reference"]
        )

        call_state = {"calls": 0}

        # Calls 1, 2, 3 succeed
        res1 = execute_llm_read_only_tool("get_record", {"record_id": self.bank_item["id"]}, call_state)
        self.assertTrue(res1["is_success"])
        self.assertEqual(call_state["calls"], 1)

        res2 = execute_llm_read_only_tool("get_counterparty_history", {"counterparty": "CLOUD"}, call_state)
        self.assertTrue(res2["is_success"])
        self.assertEqual(call_state["calls"], 2)

        res3 = execute_llm_read_only_tool("get_record", {"record_id": self.bank_item["id"]}, call_state)
        self.assertTrue(res3["is_success"])
        self.assertEqual(call_state["calls"], 3)

        # Call 4 fails due to hard cap
        with self.assertRaises(PermissionError) as ctx_cap:
            execute_llm_read_only_tool("get_record", {"record_id": self.bank_item["id"]}, call_state)
        self.assertIn("Tool call limit exceeded", str(ctx_cap.exception))

        # Attempt to call commit_match via LLM tool gateway is blocked
        with self.assertRaises(PermissionError) as ctx_sec:
            execute_llm_read_only_tool("commit_match", {"match_id": "HACK"}, call_state)
        self.assertIn("Security violation: LLM cannot call mutating tool 'commit_match'", str(ctx_sec.exception))

    def test_rolled_back_commit_leaves_no_match_rows(self):
        """Test 4: A rolled-back commit transaction leaves 0 rows in matches and match_members."""
        match_id = f"MATCH-ROLLBACK-{uuid.uuid4().hex[:6]}"
        token = generate_validator_token(self.bank_item["id"], [self.ledger_item["id"]])

        conn = db.get_connection()
        try:
            # Commit match within an active transaction
            commit_match_tool(
                match_id=match_id,
                bank_ids=[self.bank_item["id"]],
                ledger_ids=[self.ledger_item["id"]],
                status="matched",
                method="rollback_test",
                confidence=1.0,
                validator_token=token,
                conn=conn
            )
            # Intentionally rollback the transaction
            conn.rollback()
        finally:
            conn.close()

        # Query database on a fresh connection
        fresh_conn = db.get_connection()
        cursor = fresh_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM matches WHERE match_id = ?", (match_id,))
        match_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM match_members WHERE match_id = ?", (match_id,))
        member_count = cursor.fetchone()[0]
        fresh_conn.close()

        self.assertEqual(match_count, 0, "Rolled-back transaction must leave NO rows in matches table!")
        self.assertEqual(member_count, 0, "Rolled-back transaction must leave NO rows in match_members table!")

if __name__ == "__main__":
    unittest.main()
