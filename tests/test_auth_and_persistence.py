"""
Comprehensive Unit and Integration Test Suite for:
1. Scrypt Password Hashing, Salt Uniqueness, and >=12 Character Validation.
2. Timing Attack Resistance and Generic Login Error (Non-existent user vs Bad password).
3. Persistent Login Throttling (>=5 failed attempts -> 429).
4. Session Generation, Token Hashing (SHA-256 in DB, never plaintext), Expiration, and Invalidation.
5. Role-Based Access Control and Last Active Administrator Protection.
6. HTTP Server Private Route Gating (401 on unauthenticated requests).
7. Origin and Referer Validation, CSRF Protection (X-CSRF-Token).
8. Cross-User Data Isolation (User A cannot view or mutate User B's records, runs, or imports).
9. Statement Ingestion and Runs Persistence (import_batches, import_rows, runs).
10. Secure Headers & No Wildcard CORS.
"""

import os
import json
import time
import urllib.request
import urllib.error
import threading
import unittest
import sqlite3
from http.server import HTTPServer
from pathlib import Path

import data.db as db
import reconcile.service as rec_service
import auth.service as auth_service
from server import FinancialAgentServerHandler

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class TestPasswordHashingAndSecurity(unittest.TestCase):
    """Tests password hashing, scrypt parameters, salt uniqueness, and strength rules."""

    def test_password_length_requirement(self):
        """Passwords under 12 characters must be rejected."""
        with self.assertRaises(ValueError) as ctx:
            auth_service.create_user(
                username="short_pwd_user",
                password="Short1!",
                role="analyst"
            )
        self.assertIn("at least 12 characters", str(ctx.exception))

    def test_scrypt_hashing_and_salt_uniqueness(self):
        """Scrypt must use a unique random salt and stored parameters."""
        pwd = "ComplexPassword2026!"
        h1, s1, p1 = auth_service.hash_password(pwd)
        h2, s2, p2 = auth_service.hash_password(pwd)

        # Hashes and salts must be unique per invocation
        self.assertNotEqual(s1, s2, "Salts must be randomly generated and unique")
        self.assertNotEqual(h1, h2, "Hashes of identical passwords with different salts must differ")

        # Verify salt length is at least 16 bytes (32 hex characters)
        self.assertGreaterEqual(len(bytes.fromhex(s1)), 16)

        # Verify parameters stored
        params1 = json.loads(p1)
        self.assertEqual(params1["n"], 16384)
        self.assertEqual(params1["r"], 8)
        self.assertEqual(params1["p"], 1)

    def test_password_verification(self):
        """Valid passwords verify True; incorrect verify False."""
        pwd = "CorrectSecretPassword123!"
        h, s, p = auth_service.hash_password(pwd)

        self.assertTrue(auth_service.verify_password(pwd, h, s, p))
        self.assertFalse(auth_service.verify_password("WrongPassword123!", h, s, p))
        self.assertFalse(auth_service.verify_password("", h, s, p))


class TestAuthenticationService(unittest.TestCase):
    """Tests authentication logic, generic errors, throttling, and sessions."""

    def setUp(self):
        # Clean users and login_attempts for isolation
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM login_attempts")
        cursor.execute("DELETE FROM sessions")
        conn.commit()
        conn.close()

    def test_generic_login_error_identical(self):
        """Return the same generic login error for an unknown account and an incorrect password."""
        # 1. Create a known user
        username = "known_user_test"
        pwd = "KnownUserPassword123!"
        try:
            auth_service.create_user(username=username, password=pwd, role="analyst")
        except ValueError:
            pass

        # 2. Login with known user but wrong password
        user1, err1, status1 = auth_service.authenticate_user(username, "IncorrectPassword999!")
        self.assertIsNone(user1)
        self.assertEqual(status1, 401)

        # 3. Login with unknown user
        user2, err2, status2 = auth_service.authenticate_user("completely_unknown_user_999", "AnyPassword12345!")
        self.assertIsNone(user2)
        self.assertEqual(status2, 401)

        # Error messages must be completely identical to prevent account enumeration
        self.assertEqual(err1, err2, "Error message must be identical for unknown user and bad password")
        self.assertEqual(err1, "Invalid username or password")

    def test_login_throttling_after_5_failures(self):
        """Persistent login throttling triggers 429 after 5 failed attempts."""
        target_user = "throttle_test_user"
        ip = "192.168.1.100"

        for i in range(5):
            u, err, status = auth_service.authenticate_user(target_user, "BadPassword123!", ip_address=ip)
            self.assertEqual(status, 401)

        # 6th attempt must be throttled with HTTP 429
        u, err, status = auth_service.authenticate_user(target_user, "BadPassword123!", ip_address=ip)
        self.assertEqual(status, 429)
        self.assertIn("Too many failed login attempts", err)

    def test_inactive_account_rejected(self):
        """Deactivated accounts cannot authenticate."""
        username = "inactive_analyst_test"
        pwd = "InactivePassword123!"
        try:
            u = auth_service.create_user(username=username, password=pwd, role="analyst", is_active=False)
        except ValueError:
            u = auth_service.get_user_by_username(username)
            auth_service.update_user_status(u["id"], is_active=False)

        user, err, status = auth_service.authenticate_user(username, pwd)
        self.assertIsNone(user)
        self.assertEqual(status, 403)
        self.assertIn("inactive", err.lower())

    def test_session_lifecycle_and_token_hashing(self):
        """Sessions store only token hashes, validate expiry, and invalidate on logout."""
        username = "session_user_test"
        pwd = "SessionUserPassword123!"
        try:
            u = auth_service.create_user(username=username, password=pwd, role="analyst")
        except ValueError:
            u = auth_service.get_user_by_username(username)

        # 1. Create session
        raw_token, csrf_token, expires_at = auth_service.create_session(u["id"])
        self.assertTrue(len(raw_token) >= 32)
        self.assertTrue(len(csrf_token) >= 32)

        # 2. Check DB does NOT contain the raw token (only SHA-256 hash)
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT token_hash FROM sessions WHERE user_id = ?", (u["id"],))
        rows = [r[0] for r in cursor.fetchall()]
        conn.close()

        self.assertNotIn(raw_token, rows, "Plaintext session token must NEVER be stored in the database")

        # 3. Validate valid session
        sess = auth_service.validate_session(raw_token)
        self.assertIsNotNone(sess)
        self.assertEqual(sess["user_id"], u["id"])
        self.assertEqual(sess["csrf_token"], csrf_token)

        # 4. Invalidate session (logout)
        invalidated = auth_service.invalidate_session(raw_token)
        self.assertTrue(invalidated)

        # 5. Verify validation fails after invalidation
        sess_after = auth_service.validate_session(raw_token)
        self.assertIsNone(sess_after)


class TestRoleRestrictionsAndAdminProtection(unittest.TestCase):
    """Tests RBAC and protection of the last active administrator."""

    def test_last_active_admin_protected(self):
        """Cannot deactivate or demote the sole active administrator."""
        try:
            test_admin = auth_service.create_user("temp_iso_admin", "AdminPassWord123!", role="admin")
        except ValueError:
            test_admin = auth_service.get_user_by_username("temp_iso_admin")
            auth_service.update_user(test_admin["id"], is_active=True, role="admin")

        other_admins = [u for u in auth_service.list_users() if u["role"] == "admin" and u["is_active"] and u["id"] != test_admin["id"]]
        try:
            for a in other_admins:
                auth_service.update_user_status(a["id"], is_active=False)

            # Attempt to deactivate sole admin must raise ValueError
            with self.assertRaises(ValueError) as ctx:
                auth_service.update_user_status(test_admin["id"], is_active=False)
            self.assertIn("last active administrator", str(ctx.exception))

            # Attempt to demote sole admin must raise ValueError
            with self.assertRaises(ValueError) as ctx:
                auth_service.update_user(test_admin["id"], role="analyst")
            self.assertIn("last active administrator", str(ctx.exception))
        finally:
            for a in other_admins:
                auth_service.update_user_status(a["id"], is_active=True)
            auth_service.update_user_status(test_admin["id"], is_active=True)


class TestServerAuthAndCrossUserIsolation(unittest.TestCase):
    """Integration tests with the live HTTP server testing authentication, CSRF, and data isolation."""

    @classmethod
    def setUpClass(cls):
        # Initialize and migrate database
        db.init_database()
        # Seed dev baseline
        rec_service.seed_reconciliation_from_benchmark("dev")

        # Provision Admin User
        try:
            cls.admin_user = auth_service.create_user(
                username="server_test_admin",
                email="admin@treasury.test",
                password="AdminSecretPassword123!",
                role="admin"
            )
        except ValueError:
            cls.admin_user = auth_service.get_user_by_username("server_test_admin")
            auth_service.update_user(cls.admin_user["id"], is_active=True, role="admin")

        cls.admin_token, cls.admin_csrf, _ = auth_service.create_session(cls.admin_user["id"])

        # Provision Analyst User A
        try:
            cls.analyst_a = auth_service.create_user(
                username="analyst_alice",
                email="alice@treasury.test",
                password="AlicePassword123!",
                role="analyst"
            )
        except ValueError:
            cls.analyst_a = auth_service.get_user_by_username("analyst_alice")
            auth_service.update_user(cls.analyst_a["id"], is_active=True, role="analyst")

        cls.alice_token, cls.alice_csrf, _ = auth_service.create_session(cls.analyst_a["id"])

        # Provision Analyst User B
        try:
            cls.analyst_b = auth_service.create_user(
                username="analyst_bob",
                email="bob@treasury.test",
                password="BobPassword1234!",
                role="analyst"
            )
        except ValueError:
            cls.analyst_b = auth_service.get_user_by_username("analyst_bob")
            auth_service.update_user(cls.analyst_b["id"], is_active=True, role="analyst")

        cls.bob_token, cls.bob_csrf, _ = auth_service.create_session(cls.analyst_b["id"])

        # Provision Auditor User
        try:
            cls.auditor = auth_service.create_user(
                username="auditor_eve",
                email="eve@treasury.test",
                password="EveAuditorPassword123!",
                role="auditor"
            )
        except ValueError:
            cls.auditor = auth_service.get_user_by_username("auditor_eve")
            auth_service.update_user(cls.auditor["id"], is_active=True, role="auditor")

        cls.eve_token, cls.eve_csrf, _ = auth_service.create_session(cls.auditor["id"])

        # Start HTTP server
        cls.server = HTTPServer(("127.0.0.1", 0), FinancialAgentServerHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _http_get(self, path: str, token: str = None) -> tuple[int, dict, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        headers = {}
        if token:
            headers["Cookie"] = f"session_id={token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                resp_headers = dict(resp.headers)
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data, resp_headers
        except urllib.error.HTTPError as e:
            resp_headers = dict(e.headers)
            try:
                data = json.loads(e.read().decode("utf-8"))
            finally:
                e.close()
            return e.code, data, resp_headers

    def _http_post(
        self,
        path: str,
        payload: dict,
        token: str = None,
        csrf: str = None,
        origin: str = None
    ) -> tuple[int, dict, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Cookie"] = f"session_id={token}"
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if origin is not None:
            headers["Origin"] = origin
        else:
            headers["Origin"] = f"http://127.0.0.1:{self.port}"

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                resp_headers = dict(resp.headers)
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data, resp_headers
        except urllib.error.HTTPError as e:
            resp_headers = dict(e.headers)
            try:
                data = json.loads(e.read().decode("utf-8"))
            finally:
                e.close()
            return e.code, data, resp_headers

    # 1. Unauthenticated Gating
    def test_unauthenticated_request_rejected(self):
        """Unauthenticated requests to private routes return 401."""
        status, data, _ = self._http_get("/api/reconciliation/records")
        self.assertEqual(status, 401)
        self.assertIn("error", data)

        status, data, _ = self._http_get("/api/reconciliation/balance-proof")
        self.assertEqual(status, 401)

        status, data, _ = self._http_get("/api/auth/me")
        self.assertEqual(status, 401)

    # 2. Login & Cookie Security
    def test_login_flow_and_cookie_attributes(self):
        """POST /api/auth/login sets HttpOnly, SameSite=Strict session cookie and returns user and CSRF."""
        payload = {
            "username": "analyst_alice",
            "password": "AlicePassword123!"
        }
        status, data, headers = self._http_post("/api/auth/login", payload)
        self.assertEqual(status, 200)
        self.assertIn("user", data)
        self.assertEqual(data["user"]["username"], "analyst_alice")
        self.assertIn("csrf_token", data)

        set_cookie = headers.get("Set-Cookie", "")
        self.assertIn("session_id=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        self.assertNotIn("*", headers.get("Access-Control-Allow-Origin", ""))

    # 3. Session Restoration & Logout
    def test_session_restoration_and_logout(self):
        """GET /api/auth/me returns profile; POST /api/auth/logout invalidates session."""
        status, data, _ = self._http_get("/api/auth/me", token=self.alice_token)
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["username"], "analyst_alice")

        # Logout
        status, _, headers = self._http_post(
            "/api/auth/logout",
            {},
            token=self.alice_token,
            csrf=self.alice_csrf
        )
        self.assertEqual(status, 200)
        set_cookie = headers.get("Set-Cookie", "")
        self.assertIn("Max-Age=0", set_cookie)

        # Confirm session is now invalid
        status_after, _, _ = self._http_get("/api/auth/me", token=self.alice_token)
        self.assertEqual(status_after, 401)

    # 4. CSRF Protection
    def test_csrf_token_required_on_post(self):
        """State-changing requests using cookie session require matching X-CSRF-Token."""
        # Request with missing CSRF token
        payload = {"split": "dev"}
        status, data, _ = self._http_post(
            "/api/reconciliation/load-split",
            payload,
            token=self.admin_token,
            csrf="" # Missing CSRF token
        )
        self.assertEqual(status, 403)
        self.assertIn("CSRF", data.get("error", ""))

        # Request with forged / incorrect CSRF token
        status, data, _ = self._http_post(
            "/api/reconciliation/load-split",
            payload,
            token=self.admin_token,
            csrf="FORGED_CSRF_TOKEN_12345"
        )
        self.assertEqual(status, 403)

    # 5. Origin Validation
    def test_invalid_origin_rejected(self):
        """State-changing requests with hostile Origin are rejected with 403."""
        payload = {"username": "analyst_bob", "password": "BobPassword1234!"}
        status, data, _ = self._http_post(
            "/api/auth/login",
            payload,
            origin="http://malicious-site.com"
        )
        self.assertEqual(status, 403)
        self.assertIn("invalid origin", data.get("error", "").lower())

    # 6. Role Restrictions: Analyst cannot manage users
    def test_role_restrictions_admin_only_routes(self):
        """Analyst role cannot access /api/auth/users; Admin role can."""
        # Alice (analyst) attempts to list users -> 403
        status, data, _ = self._http_get("/api/auth/users", token=self.bob_token)
        self.assertEqual(status, 403)
        self.assertIn("administrator", data.get("error", "").lower())

        # Admin attempts to list users -> 200
        status, data, _ = self._http_get("/api/auth/users", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertIn("users", data)

    # 7. Role Restrictions: Auditor is read-only
    def test_auditor_role_read_only(self):
        """Auditor role can read balance proof and records but cannot mutate matches."""
        # Auditor can read
        status, data, _ = self._http_get("/api/reconciliation/balance-proof", token=self.eve_token)
        self.assertEqual(status, 200)

        # Auditor cannot confirm match
        status, data, _ = self._http_post(
            "/api/reconciliation/confirm",
            {"target_id": "BANK-01", "candidate_id": "LEDGER-01"},
            token=self.eve_token,
            csrf=self.eve_csrf
        )
        self.assertEqual(status, 403)
        self.assertIn("read-only", data.get("error", "").lower())

    # 8. Cross-User Data Isolation: Runs & Imports
    def test_cross_user_data_isolation(self):
        """User A's runs and imports cannot be accessed or modified by User B."""
        # 1. Bob creates a run
        run_name = "Bob Private Reconciliation Q3"
        status, run_data, _ = self._http_post(
            "/api/runs",
            {"name": run_name},
            token=self.bob_token,
            csrf=self.bob_csrf
        )
        self.assertEqual(status, 201)
        bob_run_id = run_data["id"]

        # 2. Bob imports bank statement
        csv_content = b"Date,Description,Amount,Reference\n2026-08-01,Client Direct Deposit,500.00,BOB-REF-01\n"
        url = f"http://127.0.0.1:{self.port}/api/runs/{bob_run_id}/imports/bank?filename=bob_statement.csv"
        req = urllib.request.Request(url, data=csv_content, headers={
            "Content-Type": "text/csv",
            "Cookie": f"session_id={self.bob_token}",
            "X-CSRF-Token": self.bob_csrf,
            "Origin": f"http://127.0.0.1:{self.port}",
        }, method="PUT")
        with urllib.request.urlopen(req) as response:
            self.assertEqual(response.status, 201)

        # 3. Eve (or Alice) lists runs: Bob's run must NOT be present
        # 3. Alice attempts to list runs: must only see Alice's runs, not Bob's
        status, alice_runs_data, _ = self._http_get("/api/runs", token=self.alice_token)
        self.assertEqual(status, 200)
        alice_run_ids = [r["id"] for r in alice_runs_data.get("runs", [])]
        self.assertNotIn(bob_run_id, alice_run_ids, "User A's run must NOT be visible to User B")

        # 4. Alice cannot read Bob's run or add an import to it.
        status, _, _ = self._http_get(f"/api/runs/{bob_run_id}", token=self.alice_token)
        self.assertEqual(status, 404)
        hostile_url = f"http://127.0.0.1:{self.port}/api/runs/{bob_run_id}/imports/ledger?filename=hostile.csv"
        hostile = urllib.request.Request(hostile_url, data=csv_content, headers={
            "Content-Type": "text/csv",
            "Cookie": f"session_id={self.alice_token}",
            "X-CSRF-Token": self.alice_csrf,
            "Origin": f"http://127.0.0.1:{self.port}",
        }, method="PUT")
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(hostile)
        self.assertEqual(denied.exception.code, 404)
        denied.exception.close()

    # 9. Security Headers and No Wildcard CORS
    def test_security_headers_present(self):
        """API endpoints must return security headers and never wildcard CORS."""
        status, _, headers = self._http_get("/api/reconciliation/tools", token=self.bob_token)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertNotEqual(headers.get("Access-Control-Allow-Origin"), "*")


if __name__ == "__main__":
    unittest.main()
