"""Isolated regression coverage for staged CSV imports and confirmation gates."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import csv
from decimal import Decimal
from pathlib import Path

import auth.service as auth
import core.csv_workflow as workflow
import data.db as db


class CsvWorkflowRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protected_db = Path(__file__).resolve().parent.parent / "data" / "reconciliation.db"
        cls.app_db_hash = hashlib.sha256(cls.protected_db.read_bytes()).hexdigest() if cls.protected_db.exists() else None

    @classmethod
    def tearDownClass(cls):
        final_hash = hashlib.sha256(cls.protected_db.read_bytes()).hexdigest() if cls.protected_db.exists() else None
        assert cls.app_db_hash == final_hash, "The suite changed data/reconciliation.db"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_path = root / "workflow.sqlite"
        db.set_active_db(self.db_path)
        db.init_database(self.db_path)
        workflow.STAGING_DIR = root / "staging"
        workflow.STAGING_DIR.mkdir()
        self.user = auth.create_user("workflow_test", "WorkflowTestPassword2026!", role="analyst")
        self.run = workflow.create_workflow_run(
            self.user["id"], "Fixture", "USD", "2026-01-01", "2026-12-31",
            "0.00", "10.00", "0.00", "10.00"
        )

    def tearDown(self):
        db.use_app_db()
        self.temp.cleanup()

    def test_public_signup_is_analyst_only_and_rejects_duplicate_identity(self):
        user = auth.register_analyst("New_Analyst", "New.Analyst@Example.test", "SecureSignupPassword2026!")
        self.assertEqual(user["username"], "new_analyst")
        self.assertEqual(user["email"], "new.analyst@example.test")
        self.assertEqual(user["role"], "analyst")
        with self.assertRaisesRegex(ValueError, "already exists"):
            auth.register_analyst("another_user", "NEW.ANALYST@example.test", "DifferentPassword2026!")
        with self.assertRaisesRegex(ValueError, "at least 12 characters"):
            auth.register_analyst("short_pwd", "short@example.test", "too-short")

    def _mapped_bank(self, content=b"Date,Description,Amount\n2026-02-01,Deposit,10.00\n"):
        uploaded = workflow.ingest_raw_csv_upload(self.run["id"], "bank", "fixture.csv", content, self.user["id"])
        workflow.map_and_process_import_batch(uploaded["batch_id"], {
            "date_column": "Date", "amount_mode": "signed", "amount_column": "Amount",
            "decimal_separator": ".", "description_column": "Description"
        }, self.user["id"])
        return uploaded["batch_id"]

    def test_staging_gate_confirm_idempotency_and_database_hash(self):
        batch_id = self._mapped_bank()
        conn = db.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM bank_records WHERE run_id=?", (self.run["id"],)).fetchone()[0], 0)
        conn.close()
        first = workflow.confirm_import_batch(batch_id, self.user["id"], "analyst")
        second = workflow.confirm_import_batch(batch_id, self.user["id"], "analyst")
        self.assertEqual(first["confirmed_count"], 1)
        self.assertEqual(second["confirmed_records"], 1)
        conn = db.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM bank_records WHERE run_id=?", (self.run["id"],)).fetchone()[0], 1)
        conn.close()

    def test_amount_validation_and_currency_symbol(self):
        self.assertEqual(workflow.parse_csv_amount("1,234.56", ".", {}), (123456, None))
        self.assertEqual(workflow.parse_csv_amount("1.234,56", ",", {}), (123456, None))
        self.assertEqual(workflow.parse_csv_amount("(12.30)", ".", {"parentheses": True}), (-1230, None))
        for value in ("--", "1.234", "1e3", "12.00-"):
            cents, issue = workflow.parse_csv_amount(value, ".", {})
            self.assertIsNone(cents, value)
            self.assertTrue(issue)
        self.assertIn("conflicts", workflow.parse_csv_amount("€12.00", ".", {}, "USD")[1])

    def test_downloadable_synthetic_statement_pair_balances_and_has_differences(self):
        sample_root = Path(__file__).resolve().parent.parent / "sample_data"
        def read(name):
            with (sample_root / name).open(newline="", encoding="utf-8") as stream:
                return list(csv.DictReader(stream))
        bank = read("bank_statement.csv"); ledger = read("ledger_statement.csv")
        bank_total = sum((Decimal(row["Amount"]) for row in bank), Decimal("0"))
        ledger_total = sum((Decimal(row["Amount"]) for row in ledger), Decimal("0"))
        self.assertEqual(bank_total, Decimal("3350.37"))
        self.assertEqual(ledger_total, Decimal("3352.87"))
        self.assertEqual(len(bank), 12); self.assertEqual(len(ledger), 11)
        self.assertNotEqual(bank_total, ledger_total)
        run = workflow.create_workflow_run(self.user["id"], "Downloadable fixture", "USD", "2026-10-01", "2026-10-31", "10000.00", "13350.37", "10000.00", "13352.87")
        mapping = {"date_column":"Date", "amount_mode":"signed", "amount_column":"Amount", "decimal_separator":".", "description_column":"Description", "reference_column":"Reference"}
        ids = []
        for side, filename in (("bank", "bank_statement.csv"), ("ledger", "ledger_statement.csv")):
            raw = (sample_root / filename).read_bytes()
            batch = workflow.ingest_raw_csv_upload(run["id"], side, filename, raw, self.user["id"])
            ids.append(batch["batch_id"])
            workflow.map_and_process_import_batch(batch["batch_id"], {**mapping, **({"ledger_perspective":"cash_account"} if side == "ledger" else {})}, self.user["id"])
            self.assertEqual(workflow.confirm_import_batch(batch["batch_id"], self.user["id"], "analyst")["status"], "confirmed")
        result = workflow.run_reconciliation_for_run(run["id"], self.user["id"])
        self.assertEqual(result["status"], "reconciled")
        output = workflow.get_run_results(run["id"])
        self.assertEqual(output["arithmetic_consistency_check"]["bank"]["difference_cents"], 0)
        self.assertEqual(output["arithmetic_consistency_check"]["ledger"]["difference_cents"], 0)
        self.assertTrue(output["matches"])
        self.assertTrue(output["exceptions"])

    def test_sample_reconciliation_runs_without_opening_or_closing_balances(self):
        run = workflow.create_sample_reconciliation(self.user["id"])
        self.assertEqual(run["status"], "reconciled")
        self.assertIsNone(run["bank_opening_cents"])
        self.assertIsNone(run["bank_closing_cents"])
        self.assertIsNone(run["ledger_opening_cents"])
        self.assertIsNone(run["ledger_closing_cents"])
        self.assertEqual({batch["side"] for batch in run["batches"]}, {"bank", "ledger"})
        self.assertTrue(all(batch["status"] == "confirmed" for batch in run["batches"]))

        results = workflow.get_run_results(run["id"])
        self.assertTrue(results["matches"])
        self.assertTrue(results["exceptions"])
        self.assertIsNone(results["arithmetic_consistency_check"]["bank"]["difference_cents"])
        self.assertIsNone(results["arithmetic_consistency_check"]["ledger"]["difference_cents"])

        second = workflow.create_sample_reconciliation(self.user["id"])
        self.assertEqual(second["id"], run["id"], "sample creation should be idempotent per user")

    def test_ambiguous_dates_require_explicit_mapping_and_multiline_raw_line_is_preserved(self):
        raw = b'Date,Description,Amount\n01/02/2026,"First line\nsecond line",10.00\n'
        uploaded = workflow.ingest_raw_csv_upload(self.run["id"], "bank", "fixture.csv", raw, self.user["id"])
        mapping = {"date_column": "Date", "amount_mode": "signed", "amount_column": "Amount", "decimal_separator": ".", "description_column": "Description"}
        result = workflow.map_and_process_import_batch(uploaded["batch_id"], mapping, self.user["id"])
        self.assertEqual(result["ambiguous_date"], 1)
        mapping["date_format"] = "DD/MM/YYYY"
        workflow.map_and_process_import_batch(uploaded["batch_id"], mapping, self.user["id"])
        rows = workflow.get_import_batch_rows(uploaded["batch_id"])["rows"]
        self.assertEqual(rows[0]["raw_line"], '01/02/2026,"First line\nsecond line",10.00\n')

    def test_inferred_date_format_requires_confirmation(self):
        uploaded = workflow.ingest_raw_csv_upload(self.run["id"], "bank", "fixture.csv", b"Date,Description,Amount\n25/01/2026,Payment,10.00\n", self.user["id"])
        result = workflow.map_and_process_import_batch(uploaded["batch_id"], {
            "date_column": "Date", "amount_mode": "signed", "amount_column": "Amount",
            "decimal_separator": ".", "description_column": "Description"
        }, self.user["id"])
        self.assertTrue(result["date_format_confirmation_required"])

    def test_ragged_and_invalid_csv_rejected_before_batch_creation(self):
        for raw in (b"Date,Amount\n2026-01-01\n", b"Date,Amount\n2026-01-01,1.00\x00\n", b"Date,Amount\n\xff,1\n"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                workflow.ingest_raw_csv_upload(self.run["id"], "bank", "fixture.csv", raw, self.user["id"])

    def test_confirm_copy_is_atomic_on_insert_failure(self):
        batch_id = self._mapped_bank()
        conn = db.get_connection()
        conn.execute("CREATE TRIGGER fail_bank_insert BEFORE INSERT ON bank_records BEGIN SELECT RAISE(ABORT, 'injected'); END")
        conn.commit(); conn.close()
        with self.assertRaises(Exception):
            workflow.confirm_import_batch(batch_id, self.user["id"], "analyst")
        conn = db.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM bank_records WHERE run_id=?", (self.run["id"],)).fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT status FROM import_batches WHERE id=?", (batch_id,)).fetchone()[0], "staged")
        conn.close()

    def test_duplicate_is_flagged_until_explicit_reject_or_include(self):
        content = b"Date,Description,Amount\n2026-02-01,Deposit,5.00\n2026-02-01,Deposit,5.00\n"
        batch_id = self._mapped_bank(content)
        rows = workflow.get_import_batch_rows(batch_id)["rows"]
        self.assertEqual([r["status"] for r in rows], ["valid", "duplicate_flagged"])
        workflow.set_import_row_status(batch_id, rows[1]["id"], "reject", self.user["id"])
        self.assertEqual(workflow.get_import_batch_summary(batch_id)["counts"]["rejected"], 1)

    def test_persistent_http_workflow_across_server_restarts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "server.sqlite"
            stage_path = root / "stage"
            stage_path.mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
            env = os.environ.copy()
            env.update({"RECONCILIATION_DB_PATH": str(db_path), "CSV_STAGING_DIR": str(stage_path), "PORT": str(port), "HOST": "127.0.0.1"})
            project = Path(__file__).resolve().parent.parent
            env["PYTHONPATH"] = str(project)
            subprocess.run([sys.executable, "-c", "import data.db as d; d.init_database(); import auth.service as a; a.create_user('persist_admin','PersistTestPassword2026!',role='admin'); a.create_user('persist_analyst','PersistTestPassword2026!',role='analyst')"], cwd=project, env=env, check=True, capture_output=True, text=True)
            base = f"http://127.0.0.1:{port}"
            proc = None

            def start_server():
                nonlocal proc
                proc = subprocess.Popen([sys.executable, "server.py"], cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(100):
                    if proc.poll() is not None:
                        self.fail("HTTP server exited during startup")
                    try:
                        urllib.request.urlopen(base + "/api/runs", timeout=0.2).close()
                        break
                    except urllib.error.HTTPError as e:
                        e.close()
                        if e.code == 401: break
                    except Exception:
                        time.sleep(0.05)
                else: self.fail("HTTP server did not start")

            def stop_server():
                nonlocal proc
                if proc and proc.poll() is None:
                    proc.terminate(); proc.wait(timeout=5)
                proc = None

            self.addCleanup(stop_server)

            start_server()
            signup_req = urllib.request.Request(
                base + "/api/auth/register",
                data=json.dumps({"username":"self_signup","email":"self_signup@example.test","password":"SelfSignupPassword2026!","role":"admin"}).encode(),
                headers={"Content-Type":"application/json", "Origin":base}
            )
            with urllib.request.urlopen(signup_req, timeout=4) as response:
                self.assertEqual(response.status, 201)
                signup = json.loads(response.read())
                signup_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            self.assertEqual(signup["user"]["role"], "analyst")
            self.assertNotIn("password", signup["user"])
            with urllib.request.urlopen(urllib.request.Request(base + "/api/auth/me", headers={"Cookie":signup_cookie}), timeout=4) as response:
                self.assertEqual(json.loads(response.read())["user"]["username"], "self_signup")
            with urllib.request.urlopen(base + "/") as response:
                page = response.read().decode("utf-8")
            self.assertIn("Veritas Treasury", page)
            self.assertIn('data-page-view="import"', page)
            self.assertIn('data-page-view="worklist"', page)
            self.assertNotIn("Dev Split", page)
            with urllib.request.urlopen(base + "/app.js") as response:
                app_script = response.read().decode("utf-8")
            self.assertIn("/api/runs", app_script)
            with urllib.request.urlopen(base + "/styles.css") as response:
                styles = response.read().decode("utf-8")
            for breakpoint in ("1024px", "767px", "390px"):
                self.assertIn(breakpoint, styles)
            self.assertIn("prefers-reduced-motion", styles)
            with urllib.request.urlopen(base + "/sample_data/bank_statement.csv") as response:
                self.assertIn(b"PAY-1001", response.read())
            def assert_status(method, path, expected, headers=None, body=b"{}", content_type="application/json"):
                request_headers = {"Origin": base, "Content-Type": content_type}
                request_headers.update(headers or {})
                request = urllib.request.Request(base + path, data=body if method != "GET" else None, method=method, headers=request_headers)
                try:
                    response = urllib.request.urlopen(request, timeout=3)
                    actual = response.status; response.close()
                except urllib.error.HTTPError as error:
                    actual = error.code; error.close()
                self.assertEqual(actual, expected, f"{method} {path}")

            for route in ("/api/runs", "/api/runs/no-such-run", "/api/runs/no-such-run/statement-checks?side=bank", "/api/runs/no-such-run/results", "/api/runs/no-such-run/matches", "/api/runs/no-such-run/exceptions", "/api/runs/no-such-run/records", "/api/runs/no-such-run/export/matches.csv", "/api/imports/no-such-batch/rows", "/api/imports/no-such-batch/summary"):
                assert_status("GET", route, 401)
            for route in ("/api/runs", "/api/imports/no-such-batch/map", "/api/imports/no-such-batch/confirm", "/api/imports/no-such-batch/discard", "/api/imports/no-such-batch/rows/1/reject", "/api/imports/no-such-batch/rows/1/restore", "/api/imports/no-such-batch/rows/1/include-duplicate", "/api/runs/no-such-run/reconcile"):
                assert_status("POST", route, 401)
            assert_status("POST", "/api/auth/register", 403, body=json.dumps({"username":"evil_origin","email":"evil@example.test","password":"SecureSignupPassword2026!"}).encode(), headers={"Origin":"http://evil.invalid"})
            for i in range(4):
                assert_status("POST", "/api/auth/register", 400, body=json.dumps({"username":f"short{i}","email":f"short{i}@example.test","password":"short"}).encode())
            assert_status("POST", "/api/auth/register", 429, body=json.dumps({"username":"throttled_signup","email":"throttled@example.test","password":"LongEnoughSignupPassword2026!"}).encode())
            assert_status("PUT", "/api/runs/no-such-run/imports/bank?filename=x.csv", 401, body=b"x", content_type="text/csv")
            assert_status("PATCH", "/api/imports/no-such-batch/rows/1", 401)
            login_req = urllib.request.Request(base + "/api/auth/login", data=json.dumps({"username":"persist_admin","password":"PersistTestPassword2026!"}).encode(), headers={"Content-Type":"application/json", "Origin":base})
            with urllib.request.urlopen(login_req) as response:
                login = json.loads(response.read())
                cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            csrf = login["csrf_token"]

            assert_status("POST", "/api/runs", 403, {"Cookie": cookie}, json.dumps({}).encode())
            assert_status("POST", "/api/runs", 403, {"Cookie": cookie, "X-CSRF-Token": csrf, "Origin": "http://evil.invalid"}, json.dumps({}).encode())

            def call(method, path, value=None, content_type="application/json"):
                data = value if isinstance(value, bytes) else json.dumps(value).encode() if value is not None else None
                headers = {"Cookie":cookie, "Origin":base}
                if method != "GET": headers["X-CSRF-Token"] = csrf
                if content_type: headers["Content-Type"] = content_type
                req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
                with urllib.request.urlopen(req, timeout=4) as response:
                    return json.loads(response.read()) if "json" in response.headers.get("Content-Type", "") else response.read()

            run = call("POST", "/api/runs", {"name":"Restart acceptance", "currency":"USD", "period_start":"2026-01-01", "period_end":"2026-12-31", "bank_opening":"0.00", "bank_closing":"10.00", "ledger_opening":"0.00", "ledger_closing":"10.00"})
            run_id = run["id"]
            analyst_login = urllib.request.Request(base + "/api/auth/login", data=json.dumps({"username":"persist_analyst","password":"PersistTestPassword2026!"}).encode(), headers={"Content-Type":"application/json", "Origin":base})
            with urllib.request.urlopen(analyst_login) as response:
                analyst_session = json.loads(response.read())
                analyst_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            privacy_req = urllib.request.Request(base + f"/api/runs/{run_id}", headers={"Cookie": analyst_cookie})
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(privacy_req)
            self.assertEqual(denied.exception.code, 404); denied.exception.close()
            bank = b"Date,Description,Amount,Reference\n2026-02-01,Deposit,10.00,R1\n2026-02-02,Fix me,bad,R2\n2026-02-03,Reject me,5.00,R3\n"
            batch = call("PUT", f"/api/runs/{run_id}/imports/bank?filename=bank.csv", bank, "text/csv")["batch_id"]
            call("POST", f"/api/imports/{batch}/map", {"date_column":"Date","amount_mode":"signed","amount_column":"Amount","decimal_separator":".","description_column":"Description","reference_column":"Reference"})
            rows = call("GET", f"/api/imports/{batch}/rows")["rows"]
            call("PATCH", f"/api/imports/{batch}/rows/{rows[1]['id']}", {"amount":"0.00"})
            call("POST", f"/api/imports/{batch}/rows/{rows[2]['id']}/reject", {})
            call("POST", f"/api/imports/{batch}/confirm", {})
            stop_server(); start_server()
            restored_records = call("GET", f"/api/runs/{run_id}/records")["records"]
            self.assertEqual(len(restored_records), 2)
            self.assertEqual(sorted(r["amount_cents"] for r in restored_records if r["side"] == "bank"), [0, 1000])
            ledger = b"Date,Description,Amount,Reference\n2026-02-01,Deposit,10.00,R1\n"
            lb = call("PUT", f"/api/runs/{run_id}/imports/ledger?filename=ledger.csv", ledger, "text/csv")["batch_id"]
            call("POST", f"/api/imports/{lb}/map", {"date_column":"Date","amount_mode":"signed","amount_column":"Amount","decimal_separator":".","description_column":"Description","reference_column":"Reference","ledger_perspective":"cash_account"})
            call("POST", f"/api/imports/{lb}/confirm", {})
            call("POST", f"/api/runs/{run_id}/reconcile", {})
            stop_server(); start_server()
            result = call("GET", f"/api/runs/{run_id}/results")
            # With no user-entered opening/closing balances, the balance check
            # is intentionally unavailable rather than fabricated as a zero.
            self.assertIsNone(result["arithmetic_consistency_check"]["bank"]["difference_cents"])
            self.assertEqual(result["matches"][0]["features"]["amount_delta_cents"], 0)
            self.assertNotIn("ai_rationale", result["matches"][0])
            stop_server()


if __name__ == "__main__":
    unittest.main()
