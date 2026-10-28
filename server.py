"""
Lightweight REST API and Web Server for the Autonomous Financial Reconciliation Agent.
Zero third-party framework dependencies: uses Python's built-in http.server.
Fully authenticated and persistent database integration with role-based access control,
CSRF protection, secure cookie handling, login throttling, and cross-user data isolation.
"""

import sys
import json
import os
import csv
import io
import hmac
import traceback
import urllib.parse
from typing import Any, Dict, Optional, Tuple
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from agent.orchestrator import FinancialReconciliationAgent
from evaluation.evaluator import BenchmarkEvaluator
from evaluation.error_propagation import ErrorPropagationAnalyzer
from tools.registry import get_tool_definitions, execute_tool
import reconcile.service as rec_service
import auth.service as auth_service
import core.csv_workflow as csv_workflow
import ai.reconciliation_ai as reconciliation_ai

WEB_DIR = PROJECT_ROOT / "web"
DATA_DIR = PROJECT_ROOT / "data"
MAX_PROMPT_LENGTH = 4096


class FinancialAgentServerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def _send_json(self, data: Any, status_code: int = 200, headers: Optional[Dict[str, str]] = None):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")

        # Restrict CORS to same-origin requests; never use wildcard '*'
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host:
            parsed_orig = urllib.parse.urlparse(origin)
            if parsed_orig.netloc == host:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CSRF-Token, Authorization")

        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_csv(self, csv_text: str, filename: str):
        body = csv_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _handle_exception(self, e: Exception):
        err_str = str(e)
        if err_str.startswith("{") and err_str.endswith("}"):
            try:
                data = json.loads(err_str)
                if "reasons" in data or data.get("reason") == "missing_source" or "already" in data.get("error", "").lower() or "blocked" in data.get("error", "").lower() or "duplicate" in data.get("error", "").lower():
                    self._send_json(data, 409)
                    return
                if "error" in data:
                    self._send_json(data, 400)
                    return
            except Exception:
                pass

        if isinstance(e, PermissionError):
            low = err_str.lower()
            if "not found" in low or "access denied" in low or "unknown" in low:
                self._send_json({"error": "Resource not found"}, 404)
            else:
                self._send_json({"error": err_str}, 403)
            return

        if isinstance(e, KeyError):
            self._send_json({"error": "Resource not found"}, 404)
            return

        if isinstance(e, ValueError):
            low = err_str.lower()
            if "not found" in low:
                self._send_json({"error": "Resource not found"}, 404)
            elif "already reconciled" in low or "missing_source" in low or "duplicate upload" in low or "active import batch" in low or "active bank batch" in low or "active ledger batch" in low or "already uploaded" in low or "blocked" in low or "already confirmed" in low:
                self._send_json({"error": err_str}, 409)
            else:
                self._send_json({"error": err_str}, 400)
            return

        print(f"[ERROR] Exception: {e}", file=sys.stderr)
        traceback.print_exc()
        self._send_json({"error": "Internal server error"}, 500)

    def do_OPTIONS(self):
        self.send_response(204)
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host:
            parsed_orig = urllib.parse.urlparse(origin)
            if parsed_orig.netloc == host:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CSRF-Token, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------------
    # Authentication & Security Helpers
    # ------------------------------------------------------------------

    def _get_client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "127.0.0.1"

    def _get_session_token(self) -> Optional[str]:
        # 1. Cookie header
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            for cookie in cookie_header.split(";"):
                parts = cookie.strip().split("=", 1)
                if len(parts) == 2 and parts[0] == "session_id":
                    return parts[1]
        # 2. Authorization header (Bearer token)
        auth_header = self.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        return None

    def _get_authenticated_user(self) -> Optional[Dict[str, Any]]:
        token = self._get_session_token()
        if not token:
            return None
        return auth_service.validate_session(token)

    def _validate_origin(self) -> bool:
        """Validates that request origin/referer matches Host."""
        host = self.headers.get("Host")
        if not host:
            return True

        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlparse(origin)
            if parsed.netloc and parsed.netloc != host:
                return False

        referer = self.headers.get("Referer")
        if referer:
            parsed = urllib.parse.urlparse(referer)
            if parsed.netloc and parsed.netloc != host:
                return False

        return True

    def _validate_csrf(self, session: Dict[str, Any]) -> bool:
        """CSRF token validation for state-changing requests using cookie authentication."""
        # Non-cookie Bearer tokens are exempt from CSRF checks
        auth_header = self.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return True

        csrf_header = self.headers.get("X-CSRF-Token")
        if not csrf_header or not session.get("csrf_token"):
            return False
        return hmac.compare_digest(csrf_header, session["csrf_token"])

    def _workflow_access(self, write: bool = False):
        """Authenticate and authorize workflow requests; return (session, error_response)."""
        session = self._get_authenticated_user()
        if not session:
            self._send_json({"error": "Unauthorized: authentication required"}, 401)
            return None
        if write and session.get("role") == "auditor":
            self._send_json({"error": "Forbidden: auditor role is read-only"}, 403)
            return None
        if write and (not self._validate_workflow_origin() or not self._validate_csrf(session)):
            self._send_json({"error": "Forbidden: invalid origin or CSRF token"}, 403)
            return None
        return session

    def _validate_workflow_origin(self) -> bool:
        """Require a same-host Origin or Referer on workflow mutations."""
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        referer = self.headers.get("Referer")
        if not host or not (origin or referer):
            return False
        source = origin or referer
        parsed = urllib.parse.urlparse(source)
        return bool(parsed.netloc and hmac.compare_digest(parsed.netloc, host))

    def _workflow_run(self, run_id: str, session: Dict[str, Any]):
        return csv_workflow.get_workflow_run(
            run_id, session["user_id"],
            session.get("role") in ("admin", "auditor")
        )

    def _workflow_batch(self, batch_id: str, session: Dict[str, Any]):
        return csv_workflow.verify_batch_ownership(
            batch_id, session["user_id"],
            session.get("role") in ("admin", "auditor")
        )

    def _build_auth_cookie(self, raw_token: str, max_age: int = 86400) -> str:
        parts = [
            f"session_id={raw_token}",
            "HttpOnly",
            "SameSite=Strict",
            "Path=/",
            f"Max-Age={max_age}"
        ]
        if self.headers.get("X-Forwarded-Proto") == "https":
            parts.append("Secure")
        return "; ".join(parts)

    def _build_clear_cookie(self) -> str:
        parts = [
            "session_id=",
            "HttpOnly",
            "SameSite=Strict",
            "Path=/",
            "Max-Age=0"
        ]
        if self.headers.get("X-Forwarded-Proto") == "https":
            parts.append("Secure")
        return "; ".join(parts)

    # ------------------------------------------------------------------
    # GET Request Handler
    # ------------------------------------------------------------------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Public static files
        if not path.startswith("/api/"):
            super().do_GET()
            return

        # Private routes require authentication
        session = self._get_authenticated_user()

        # Session restoration / Current user info
        if path == "/api/auth/me":
            if not session:
                self._send_json({"error": "Unauthorized: authentication required"}, 401)
                return
            self._send_json({
                "user": {
                    "id": session["user_id"],
                    "username": session["username"],
                    "email": session["email"],
                    "role": session["role"]
                },
                "csrf_token": session["csrf_token"]
            })
            return

        if not session:
            self._send_json({"error": "Unauthorized: authentication required"}, 401)
            return

        user_id = session["user_id"]
        role = session["role"]

        try:
            parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/")]
            query = urllib.parse.parse_qs(parsed.query)
            if parts == ["api", "runs"]:
                self._send_json({"runs": csv_workflow.list_workflow_runs(user_id, role in ("admin", "auditor"))})
                return
            if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                run_id = parts[2]
                run = self._workflow_run(run_id, session)
                if len(parts) == 3:
                    self._send_json(run)
                    return
                if parts[3] == "statement-checks" and len(parts) == 4:
                    side = query.get("side", [""])[0]
                    if side not in ("bank", "ledger"):
                        self._send_json({"error": "side must be bank or ledger"}, 400); return
                    self._send_json(csv_workflow.get_run_statement_checks(run_id, side)); return
                if len(parts) == 5 and parts[3] == "matches":
                    result = csv_workflow.get_run_results(run_id)
                    match = next((item for item in result["matches"] if item["id"] == parts[4]), None)
                    if not match:
                        raise KeyError("Match not found")
                    self._send_json(match); return
                if len(parts) == 4 and parts[3] == "ai-insights":
                    stored = reconciliation_ai.get_saved_insights(run_id, user_id)
                    result = stored or reconciliation_ai.rule_based_insights(run_id, csv_workflow.get_run_results(run_id))
                    self._send_json(result); return
                if len(parts) == 4 and parts[3] == "ai-report":
                    results = csv_workflow.get_run_results(run_id)
                    insights = reconciliation_ai.get_saved_insights(run_id, user_id)
                    if not insights:
                        insights = reconciliation_ai.rule_based_insights(run_id, results)
                    body = reconciliation_ai.render_report(run_id, results, insights).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Content-Disposition", f'attachment; filename="reconciliation-{run_id}-report.md"')
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                    self.end_headers(); self.wfile.write(body); return
                if parts[3] in ("results", "matches", "exceptions", "records"):
                    result = csv_workflow.get_run_results(run_id)
                    if parts[3] == "results":
                        self._send_json(result); return
                    key = "matches" if parts[3] == "matches" else "exceptions" if parts[3] == "exceptions" else None
                    if key:
                        values = result[key]
                    else:
                        from data.db import get_connection
                        conn = get_connection()
                        try:
                            values = []
                            for table, side in (("bank_records", "bank"), ("ledger_records", "ledger")):
                                conn.row_factory = None
                                cur = conn.cursor()
                                cur.execute(f"SELECT id, date, amount_cents, description, reference FROM {table} WHERE run_id = ? ORDER BY date, id", (run_id,))
                                values.extend({"side": side, "id": r[0], "date": r[1], "amount_cents": r[2], "description": r[3], "reference": r[4]} for r in cur.fetchall())
                        finally: conn.close()
                    status_filter = query.get("status", [""])[0].casefold()
                    search = query.get("search", [""])[0].strip().casefold()
                    if status_filter and key in ("matches", "exceptions"):
                        values = [v for v in values if str(v.get("status", "")).casefold() == status_filter]
                    if search:
                        values = [v for v in values if search in json.dumps(v, ensure_ascii=False).casefold()]
                    total_count = len(values)
                    page = max(1, int(query.get("page", [1])[0])); size = min(200, max(1, int(query.get("page_size", [50])[0])))
                    self._send_json({key or "records": values[(page-1)*size:page*size], "total_count": total_count, "page": page, "page_size": size}); return
                if parts[3:] == ["export", "matches.csv"]:
                    fname = "reconciliation-matches.csv"
                    self._send_csv(csv_workflow.export_matches_csv(run_id), fname); return
            if len(parts) == 4 and parts[:2] == ["api", "imports"] and parts[3] == "rows":
                self._workflow_batch(parts[2], session)
                self._send_json(csv_workflow.get_import_batch_rows(parts[2], int(query.get("page", [1])[0]), int(query.get("page_size", [50])[0]), query.get("status", [None])[0], query.get("search", [None])[0])); return
            if len(parts) == 4 and parts[:2] == ["api", "imports"] and parts[3] == "summary":
                self._workflow_batch(parts[2], session); self._send_json(csv_workflow.get_import_batch_summary(parts[2])); return
            if len(parts) == 3 and parts[:2] == ["api", "imports"]:
                batch = self._workflow_batch(parts[2], session)
                headers = []
                staged_path = csv_workflow.STAGING_DIR / f"{parts[2]}.csv"
                if batch["status"] == "staged" and staged_path.exists():
                    raw = staged_path.read_bytes()
                    csv_text = raw.decode("utf-8-sig")
                    delimiter = batch.get("delimiter") or csv_workflow.detect_delimiter(csv_text)
                    headers = next(csv.reader(io.StringIO(csv_text, newline=""), delimiter=delimiter), [])
                suggestion = csv_workflow.inspect_csv_document(raw, batch["filename"], batch["side"], batch.get("delimiter")) if batch["status"] == "staged" and staged_path.exists() else None
                self._send_json({"id": batch["id"], "batch_id": batch["id"], "run_id": batch["run_id"], "side": batch["side"], "filename": batch["filename"], "row_count": batch.get("row_count", 0), "status": batch["status"], "detected_headers": headers, "suggested_mapping": suggestion.get("mapping") if suggestion else None, "mapping": json.loads(batch["mapping_json"]) if batch.get("mapping_json") else None}); return
            if path == "/api/auth/users":
                if role != "admin":
                    self._send_json({"error": "Forbidden: administrator privileges required"}, 403)
                    return
                users = auth_service.list_users()
                self._send_json({"users": users})

            elif path == "/api/scenarios":
                with open(DATA_DIR / "ground_truth_scenarios.json", "r") as f:
                    scenarios = json.load(f)
                self._send_json({"scenarios": scenarios})

            elif path == "/api/tools":
                tools = get_tool_definitions()
                self._send_json({"tools": tools})

            elif path == "/api/budget-rules":
                with open(DATA_DIR / "budget_rules.json", "r") as f:
                    rules = json.load(f)
                self._send_json(rules)

            elif path == "/api/eval":
                evaluator = BenchmarkEvaluator()
                results = evaluator.run_all()
                self._send_json(results)

            elif path == "/api/error-analysis":
                analyzer = ErrorPropagationAnalyzer()
                results = analyzer.run_all()
                self._send_json(results)

            elif path == "/api/transactions":
                from data.db import get_connection
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                SELECT id, date, raw_descriptor, clean_merchant, category, amount, account_name, status, is_anomaly, anomaly_type
                FROM transactions
                ORDER BY date DESC
                """)
                rows = [dict(r) for r in cursor.fetchall()]
                conn.close()
                self._send_json({"transactions": rows, "total": len(rows)})

            elif path == "/api/reconciliation/status":
                proof = rec_service.get_balance_proof(user_id=user_id)
                self._send_json({"status": "active", "balance_proof": proof})

            elif path == "/api/reconciliation/records":
                query_params = urllib.parse.parse_qs(parsed.query)
                side = query_params.get("side", ["all"])[0]
                status_filter = query_params.get("status", ["all"])[0]
                search_query = query_params.get("search", [""])[0]
                limit = int(query_params.get("limit", [500])[0])
                records = rec_service.get_reconciliation_records(
                    side=side,
                    status_filter=status_filter,
                    search_query=search_query,
                    limit=limit,
                    user_id=user_id
                )
                self._send_json(records)

            elif path == "/api/reconciliation/balance-proof":
                proof = rec_service.get_balance_proof(user_id=user_id)
                self._send_json(proof)

            elif path == "/api/reconciliation/review-queue":
                queue = rec_service.get_review_queue(user_id=user_id)
                self._send_json({"items": queue, "total": len(queue)})

            elif path == "/api/reconciliation/audit-logs":
                query_params = urllib.parse.parse_qs(parsed.query)
                limit = int(query_params.get("limit", [100])[0])
                logs = rec_service.get_audit_logs(limit=limit, user_id=user_id)
                self._send_json({"audit_logs": logs, "total": len(logs)})

            elif path == "/api/reconciliation/tools":
                tools = rec_service.get_tools_metadata()
                self._send_json({"tools": tools, "total": len(tools)})

            elif path == "/api/reconciliation/runs":
                runs = rec_service.list_runs(user_id=user_id)
                self._send_json({"runs": runs, "total": len(runs)})

            else:
                self._send_json({"error": "Endpoint not found"}, 404)

        except Exception as e:
            self._handle_exception(e)

    # ------------------------------------------------------------------
    # POST Request Handler
    # ------------------------------------------------------------------

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. Validate request origin
        is_workflow_path = path.startswith("/api/runs") or path.startswith("/api/imports")
        is_registration_path = path == "/api/auth/register"
        origin_valid = self._validate_workflow_origin() if (is_workflow_path or is_registration_path) else self._validate_origin()
        if not origin_valid:
            self._send_json({"error": "Forbidden: invalid origin"}, 403)
            return

        # 2. Read request body
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send_json({"error": "Invalid Content-Length"}, 400)
            return
        is_inspect_upload = path == "/api/imports/inspect"
        max_body_size = csv_workflow.MAX_UPLOAD_BYTES if is_inspect_upload else 65536
        if content_length < 0 or content_length > max_body_size:
            self._send_json({"error": "Request body is too large"}, 413)
            return
        post_body = self.rfile.read(content_length) if content_length > 0 else b""

        if is_inspect_upload:
            req_data = {}
        elif post_body:
            try:
                req_data = json.loads(post_body.decode("utf-8"))
            except Exception:
                self._send_json({"error": "Invalid JSON in request payload"}, 400)
                return
        else:
            req_data = {}

        # 3. Handle Public Login Endpoint
        if path == "/api/auth/login":
            if not isinstance(req_data, dict):
                self._send_json({"error": "Request body must be a JSON object"}, 400)
                return

            username = req_data.get("username", "")
            password = req_data.get("password", "")
            ip_address = self._get_client_ip()

            if not username or not password:
                self._send_json({"error": "Invalid username or password"}, 401)
                return

            user, err_msg, status_code = auth_service.authenticate_user(username, password, ip_address=ip_address)
            if err_msg or not user:
                self._send_json({"error": err_msg or "Invalid username or password"}, status_code or 401)
                return

            # Generate session and CSRF token
            raw_token, csrf_token, expires_at = auth_service.create_session(user["id"])

            cookie_header = self._build_auth_cookie(raw_token)
            self._send_json(
                {
                    "user": {
                        "id": user["id"],
                        "username": user["username"],
                        "email": user["email"],
                        "role": user["role"]
                    },
                    "csrf_token": csrf_token
                },
                status_code=200,
                headers={"Set-Cookie": cookie_header}
            )
            return

        # Public signup creates analyst accounts only. Elevated roles remain admin-managed.
        if path == "/api/auth/register":
            ip_address = self._get_client_ip()
            if auth_service.check_registration_throttle(ip_address):
                self._send_json({"error": "Too many account creation attempts. Try again in 15 minutes."}, 429)
                return
            if not isinstance(req_data, dict):
                auth_service.record_registration_attempt(ip_address)
                self._send_json({"error": "Request body must be a JSON object"}, 400)
                return
            username = req_data.get("username")
            email = req_data.get("email")
            password = req_data.get("password")
            if not all(isinstance(value, str) for value in (username, email, password)):
                auth_service.record_registration_attempt(ip_address)
                self._send_json({"error": "Username, email, and password are required."}, 400)
                return
            try:
                user = auth_service.register_analyst(username, email, password)
                auth_service.record_registration_attempt(ip_address)
                raw_token, csrf_token, _expires_at = auth_service.create_session(user["id"])
                self._send_json(
                    {"user": {"id": user["id"], "username": user["username"], "email": user["email"], "role": "analyst"}, "csrf_token": csrf_token},
                    status_code=201,
                    headers={"Set-Cookie": self._build_auth_cookie(raw_token)}
                )
            except ValueError as error:
                auth_service.record_registration_attempt(ip_address)
                self._send_json({"error": str(error)}, 400)
            return

        # 4. Handle Logout Endpoint
        if path == "/api/auth/logout":
            token = self._get_session_token()
            if token:
                auth_service.invalidate_session(token)
            clear_cookie = self._build_clear_cookie()
            self._send_json(
                {"status": "success", "message": "Logged out successfully"},
                status_code=200,
                headers={"Set-Cookie": clear_cookie}
            )
            return

        # 5. Private POST Routes Require Authentication & CSRF
        session = self._get_authenticated_user()
        if not session:
            self._send_json({"error": "Unauthorized: authentication required"}, 401)
            return

        if not self._validate_csrf(session):
            self._send_json({"error": "Forbidden: invalid or missing CSRF token"}, 403)
            return

        user_id = session["user_id"]
        role = session["role"]

        try:
            parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/")]
            if is_inspect_upload:
                if role == "auditor": self._send_json({"error": "Forbidden: auditor role is read-only"}, 403); return
                query = urllib.parse.parse_qs(parsed.query)
                filename = query.get("filename", [""])[0]
                side = query.get("side", [""])[0]
                result = csv_workflow.inspect_csv_document(post_body, filename, side)
                self._send_json(result); return
            if path == "/api/runs/sample":
                if role == "auditor": self._send_json({"error": "Forbidden: auditor role is read-only"}, 403); return
                self._send_json(csv_workflow.create_sample_reconciliation(user_id), 201); return
            if path == "/api/runs":
                if role == "auditor": self._send_json({"error": "Forbidden: auditor role is read-only"}, 403); return
                res = csv_workflow.create_workflow_run(
                    user_id,
                    req_data.get("name", ""),
                    req_data.get("currency"),
                    req_data.get("period_start"),
                    req_data.get("period_end"),
                )
                self._send_json(res, 201); return
            if len(parts) == 4 and parts[:2] == ["api", "imports"] and parts[3] == "map":
                if role == "auditor": self._send_json({"error": "Forbidden"}, 403); return
                self._workflow_batch(parts[2], session)
                self._send_json(csv_workflow.map_and_process_import_batch(parts[2], req_data, user_id)); return
            if len(parts) == 4 and parts[:2] == ["api", "imports"] and parts[3] == "confirm":
                if role == "auditor": self._send_json({"error": "Forbidden"}, 403); return
                self._workflow_batch(parts[2], session)
                res = csv_workflow.confirm_import_batch(parts[2], user_id, role, req_data.get("override_reason"))
                self._send_json(res, 409 if res.get("error") else 200); return
            if len(parts) == 4 and parts[:2] == ["api", "imports"] and parts[3] == "discard":
                if role == "auditor": self._send_json({"error": "Forbidden"}, 403); return
                self._workflow_batch(parts[2], session); self._send_json(csv_workflow.discard_import_batch(parts[2], user_id)); return
            if len(parts) == 6 and parts[:2] == ["api", "imports"] and parts[3] == "rows":
                if role == "auditor": self._send_json({"error": "Forbidden"}, 403); return
                self._workflow_batch(parts[2], session)
                action = parts[5]
                if action not in ("reject", "restore", "include-duplicate"):
                    self._send_json({"error": "Not found"}, 404); return
                self._send_json(csv_workflow.set_import_row_status(parts[2], int(parts[4]), action.replace("-duplicate", "-duplicate"), user_id)); return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "reconcile":
                if role == "auditor": self._send_json({"error": "Forbidden"}, 403); return
                self._workflow_run(parts[2], session)
                res = csv_workflow.run_reconciliation_for_run(parts[2], user_id)
                if res.get("error") == "Missing confirmed bank statement":
                    self._send_json({"reason": "missing_source", "missing": "bank"}, 409)
                elif res.get("error") == "Missing confirmed ledger statement":
                    self._send_json({"reason": "missing_source", "missing": "ledger"}, 409)
                else: self._send_json(res)
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "ai-insights":
                self._workflow_run(parts[2], session)
                self._send_json(reconciliation_ai.generate_insights(parts[2], user_id))
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "ai-question":
                self._workflow_run(parts[2], session)
                if not isinstance(req_data, dict):
                    self._send_json({"error": "Request body must be a JSON object"}, 400); return
                self._send_json(reconciliation_ai.generate_assistant_answer(parts[2], user_id, req_data.get("question", "")))
                return
            # User management (admin only)
            if path == "/api/auth/users":
                if role != "admin":
                    self._send_json({"error": "Forbidden: administrator privileges required"}, 403)
                    return
                if not isinstance(req_data, dict):
                    self._send_json({"error": "Request body must be a JSON object"}, 400)
                    return
                new_username = req_data.get("username", "").strip()
                new_email = req_data.get("email", "").strip()
                new_password = req_data.get("password", "")
                new_role = req_data.get("role", "analyst").strip()

                if not new_username or not new_email or not new_password:
                    self._send_json({"error": "Fields 'username', 'email', and 'password' are required"}, 400)
                    return

                try:
                    new_user = auth_service.create_user(
                        username=new_username,
                        email=new_email,
                        password=new_password,
                        role=new_role
                    )
                    self._send_json({"user": new_user}, 201)
                except ValueError as e:
                    self._send_json({"error": str(e)}, 400)
                return

            elif path == "/api/auth/users/deactivate":
                if role != "admin":
                    self._send_json({"error": "Forbidden: administrator privileges required"}, 403)
                    return
                target_user_id = req_data.get("user_id")
                if not target_user_id:
                    self._send_json({"error": "Field 'user_id' is required"}, 400)
                    return
                try:
                    res = auth_service.update_user_status(target_user_id, is_active=False)
                    self._send_json(res, 200)
                except ValueError as e:
                    self._send_json({"error": str(e)}, 400)
                return

            elif path == "/api/run":
                if not isinstance(req_data, dict):
                    self._send_json({"error": "Request body must be a JSON object"}, 400)
                    return

                scenario_id = req_data.get("scenario_id")
                query = req_data.get("query")
                context = req_data.get("context", {})

                if scenario_id is not None:
                    if not isinstance(scenario_id, str):
                        self._send_json({"error": "Field 'scenario_id' must be a string"}, 400)
                        return
                    with open(DATA_DIR / "ground_truth_scenarios.json", "r") as f:
                        scenarios = json.load(f)
                    matched = next((s for s in scenarios if s.get("scenario_id") == scenario_id), None)
                    if not matched:
                        self._send_json({"error": f"Unknown scenario_id: {scenario_id!r}"}, 400)
                        return
                    query = matched["input_prompt"]
                    context = matched.get("context", {})
                else:
                    if query is None:
                        self._send_json({"error": "Missing required field: 'query' or 'scenario_id'"}, 400)
                        return
                    if not isinstance(query, str):
                        self._send_json({"error": "Field 'query' must be a string"}, 400)
                        return
                    if not query.strip():
                        self._send_json({"error": "Field 'query' cannot be empty"}, 400)
                        return

                if len(query) > MAX_PROMPT_LENGTH:
                    self._send_json({
                        "error": f"Prompt length ({len(query)}) exceeds maximum allowed limit of {MAX_PROMPT_LENGTH} characters"
                    }, 400)
                    return

                if context is not None and not isinstance(context, dict):
                    self._send_json({"error": "Field 'context' must be a JSON object"}, 400)
                    return

                agent = FinancialReconciliationAgent()
                trace = agent.run(query, context or {})
                self._send_json(trace.to_dict())

            elif path == "/api/test-tool":
                if os.environ.get("ENABLE_DEBUG_TOOLS", "0") != "1":
                    self._send_json({"error": "Endpoint not found"}, 404)
                    return
                if role != "admin":
                    self._send_json({"error": "Forbidden: administrator privileges required"}, 403)
                    return
                if not isinstance(req_data, dict):
                    self._send_json({"error": "Request body must be a JSON object"}, 400)
                    return

                tool_name = req_data.get("tool_name")
                arguments = req_data.get("arguments", {})
                result = execute_tool(tool_name, arguments)
                self._send_json(result)

            elif path == "/api/eval":
                evaluator = BenchmarkEvaluator()
                results = evaluator.run_all()
                self._send_json(results)

            elif path == "/api/error-analysis":
                analyzer = ErrorPropagationAnalyzer()
                results = analyzer.run_all()
                self._send_json(results)

            elif path == "/api/reconciliation/runs":
                if role == "auditor":
                    self._send_json({"error": "Forbidden: auditor role is read-only"}, 403)
                    return
                name = req_data.get("name")
                settings = req_data.get("settings", {})
                if not name:
                    self._send_json({"error": "Field 'name' is required"}, 400)
                    return
                run = rec_service.create_run(user_id=user_id, name=name, settings=settings)
                self._send_json(run, 201)

            elif path == "/api/reconciliation/upload":
                # The legacy importer writes directly to authoritative tables.
                # CSV records must enter only through the staged confirm workflow.
                self._send_json({"error": "Legacy direct import is disabled; use staged run imports."}, 404)

            elif path == "/api/reconciliation/confirm":
                if role == "auditor":
                    self._send_json({"error": "Forbidden: auditor role is read-only"}, 403)
                    return
                if not isinstance(req_data, dict):
                    self._send_json({"error": "Request body must be a JSON object"}, 400)
                    return
                target_id = req_data.get("target_id")
                candidate_id = req_data.get("candidate_id")
                match_id = req_data.get("match_id")
                reviewer_note = req_data.get("reviewer_note")

                if not target_id or not candidate_id:
                    self._send_json({"error": "Missing required fields: 'target_id' and 'candidate_id'"}, 400)
                    return

                try:
                    res = rec_service.confirm_match(
                        target_id=target_id,
                        candidate_id=candidate_id,
                        match_id=match_id,
                        reviewer_note=reviewer_note,
                        user_id=user_id
                    )
                    self._send_json(res, 200)
                except ValueError as e:
                    self._send_json({"error": str(e)}, 400)
                except PermissionError as e:
                    self._send_json({"error": str(e)}, 403)

            elif path == "/api/reconciliation/reject":
                if role == "auditor":
                    self._send_json({"error": "Forbidden: auditor role is read-only"}, 403)
                    return
                if not isinstance(req_data, dict):
                    self._send_json({"error": "Request body must be a JSON object"}, 400)
                    return
                match_id = req_data.get("match_id")
                target_id = req_data.get("target_id")
                reviewer_note = req_data.get("reviewer_note")

                if not match_id and not target_id:
                    self._send_json({"error": "Must provide 'match_id' or 'target_id' to reject"}, 400)
                    return

                try:
                    res = rec_service.reject_match(
                        match_id=match_id,
                        target_id=target_id,
                        reviewer_note=reviewer_note,
                        user_id=user_id
                    )
                    self._send_json(res, 200)
                except ValueError as e:
                    self._send_json({"error": str(e)}, 400)
                except PermissionError as e:
                    self._send_json({"error": str(e)}, 403)

            elif path == "/api/reconciliation/reassign":
                if role == "auditor":
                    self._send_json({"error": "Forbidden: auditor role is read-only"}, 403)
                    return
                if not isinstance(req_data, dict):
                    self._send_json({"error": "Request body must be a JSON object"}, 400)
                    return
                target_id = req_data.get("target_id")
                new_candidate_id = req_data.get("new_candidate_id")
                match_id = req_data.get("match_id")
                reviewer_note = req_data.get("reviewer_note")

                if not target_id or not new_candidate_id:
                    self._send_json({"error": "Missing required fields: 'target_id' and 'new_candidate_id'"}, 400)
                    return

                try:
                    res = rec_service.reassign_match(
                        target_id=target_id,
                        new_candidate_id=new_candidate_id,
                        match_id=match_id,
                        reviewer_note=reviewer_note,
                        user_id=user_id
                    )
                    self._send_json(res, 200)
                except ValueError as e:
                    self._send_json({"error": str(e)}, 400)
                except PermissionError as e:
                    self._send_json({"error": str(e)}, 403)

            elif path == "/api/reconciliation/load-split":
                # Benchmark seeding is not reachable from the product HTTP API.
                self._send_json({"error": "Benchmark loading is disabled in the product API."}, 404)

            else:
                self._send_json({"error": "Endpoint not found"}, 404)

        except Exception as e:
            self._handle_exception(e)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.strip("/").split("/")]
        if len(parts) != 5 or parts[:2] != ["api", "runs"] or parts[3] != "imports" or parts[4] not in ("bank", "ledger"):
            self._send_json({"error": "Endpoint not found"}, 404); return
        session = self._workflow_access(write=True)
        if not session: return
        try:
            self._workflow_run(parts[2], session)
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "text/csv":
                self._send_json({"error": "Content-Type must be text/csv"}, 415); return
            try: length = int(self.headers.get("Content-Length", "-1"))
            except ValueError: length = -1
            if length < 0 or length > csv_workflow.MAX_UPLOAD_BYTES:
                self._send_json({"error": "Invalid or oversized Content-Length"}, 413); return
            body = bytearray()
            remaining = min(length, csv_workflow.MAX_UPLOAD_BYTES)
            while remaining and len(body) < remaining:
                chunk = self.rfile.read(min(65536, remaining - len(body)))
                if not chunk: break
                body.extend(chunk)
            if len(body) != length or len(body) > csv_workflow.MAX_UPLOAD_BYTES:
                self._send_json({"error": "Upload size is invalid or exceeds 2 MB"}, 413); return
            query = urllib.parse.parse_qs(parsed.query)
            filename = query.get("filename", [""])[0]
            delimiter_choice = urllib.parse.parse_qs(parsed.query).get("delimiter", [None])[0]
            result = csv_workflow.ingest_raw_csv_upload(parts[2], parts[4], filename, bytes(body), session["user_id"], delimiter_choice)
            self._send_json(result, 201)
        except Exception as e: self._handle_exception(e)

    def do_PATCH(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.strip("/").split("/")]
        if len(parts) != 5 or parts[:2] != ["api", "imports"] or parts[3] != "rows":
            self._send_json({"error": "Endpoint not found"}, 404); return
        session = self._workflow_access(write=True)
        if not session: return
        try:
            self._workflow_batch(parts[2], session)
            try: length = int(self.headers.get("Content-Length", "0"))
            except ValueError: length = -1
            if length < 0 or length > 16384:
                self._send_json({"error": "Invalid request body size"}, 413); return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                self._send_json({"error": "Request body must be a JSON object"}, 400); return
            self._send_json(csv_workflow.edit_import_row(parts[2], int(parts[4]), payload, session["user_id"]))
        except Exception as e: self._handle_exception(e)


def run_server(port: Optional[int] = None, host: str = "127.0.0.1"):
    from data.db import init_database
    init_database()
    if port is None:
        port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", host)
    enable_debug = os.environ.get("ENABLE_DEBUG_TOOLS", "0") == "1"
    if enable_debug and host != "127.0.0.1":
        print("[SECURITY NOTICE] ENABLE_DEBUG_TOOLS=1 is active; forcing binding strictly to 127.0.0.1")
        host = "127.0.0.1"

    server_address = (host, port)
    httpd = HTTPServer(server_address, FinancialAgentServerHandler)
    print("=" * 76)
    print(f"FINANCIAL RECONCILIATION AGENT DASHBOARD RUNNING")
    print(f"URL: http://{host}:{port}")
    print(f"Directory: {WEB_DIR}")
    if enable_debug:
        print("DEBUG TOOLS: ENABLED (/api/test-tool active for authenticated admins)")
    else:
        print("DEBUG TOOLS: DISABLED (/api/test-tool returns 404)")
    print("=" * 76)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
