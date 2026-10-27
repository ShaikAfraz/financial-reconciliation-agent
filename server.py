"""
Lightweight REST API and Web Server for the Autonomous Financial Reconciliation Agent.
Zero third-party framework dependencies: uses Python's built-in http.server.
"""

import sys
import json
import os
import urllib.parse
from typing import Any, Dict
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from agent.orchestrator import FinancialReconciliationAgent
from agent.providers.deterministic_provider import DeterministicAgentProvider
from agent.providers.gemini_provider import GeminiAgentProvider
from evaluation.evaluator import BenchmarkEvaluator
from evaluation.error_propagation import ErrorPropagationAnalyzer
from tools.registry import get_tool_definitions, execute_tool
import reconcile.service as rec_service

WEB_DIR = PROJECT_ROOT / "web"
DATA_DIR = PROJECT_ROOT / "data"
MAX_PROMPT_LENGTH = 4096

class FinancialAgentServerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def _send_json(self, data: Any, status_code: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/scenarios":
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
            proof = rec_service.get_balance_proof()
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
                limit=limit
            )
            self._send_json(records)

        elif path == "/api/reconciliation/balance-proof":
            proof = rec_service.get_balance_proof()
            self._send_json(proof)

        elif path == "/api/reconciliation/review-queue":
            queue = rec_service.get_review_queue()
            self._send_json({"items": queue, "total": len(queue)})

        elif path == "/api/reconciliation/audit-logs":
            query_params = urllib.parse.parse_qs(parsed.query)
            limit = int(query_params.get("limit", [100])[0])
            logs = rec_service.get_audit_logs(limit=limit)
            self._send_json({"audit_logs": logs, "total": len(logs)})

        elif path == "/api/reconciliation/tools":
            tools = rec_service.get_tools_metadata()
            self._send_json({"tools": tools, "total": len(tools)})

        else:
            # Fall back to static file serving from web directory
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length)

        if post_body:
            try:
                req_data = json.loads(post_body.decode("utf-8"))
            except Exception:
                self._send_json({"error": "Invalid JSON in request payload"}, 400)
                return
        else:
            req_data = {}

        if path == "/api/run":
            if not isinstance(req_data, dict):
                self._send_json({"error": "Request body must be a JSON object"}, 400)
                return

            scenario_id = req_data.get("scenario_id")
            query = req_data.get("query")
            context = req_data.get("context", {})

            # Validate scenario_id if provided
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

            # If scenario_id was not provided, validate query
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

            # Check prompt length
            if len(query) > MAX_PROMPT_LENGTH:
                self._send_json({
                    "error": f"Prompt length ({len(query)}) exceeds maximum allowed limit of {MAX_PROMPT_LENGTH} characters"
                }, 400)
                return

            # Validate context type if provided
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

        elif path == "/api/reconciliation/confirm":
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
                    reviewer_note=reviewer_note
                )
                self._send_json(res, 200)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                self._send_json({"error": f"Internal error: {str(e)}"}, 500)

        elif path == "/api/reconciliation/reject":
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
                    reviewer_note=reviewer_note
                )
                self._send_json(res, 200)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                self._send_json({"error": f"Internal error: {str(e)}"}, 500)

        elif path == "/api/reconciliation/reassign":
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
                    reviewer_note=reviewer_note
                )
                self._send_json(res, 200)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                self._send_json({"error": f"Internal error: {str(e)}"}, 500)

        elif path == "/api/reconciliation/load-split":
            if not isinstance(req_data, dict):
                self._send_json({"error": "Request body must be a JSON object"}, 400)
                return
            split = req_data.get("split", "dev")
            if split not in ("dev", "test"):
                self._send_json({"error": "Field 'split' must be either 'dev' or 'test'"}, 400)
                return

            try:
                res = rec_service.seed_reconciliation_from_benchmark(split=split)
                self._send_json(res, 200)
            except Exception as e:
                self._send_json({"error": f"Failed to load split: {str(e)}"}, 500)

        else:
            self._send_json({"error": "Endpoint not found"}, 404)

def run_server(port: int = 8080, host: str = "127.0.0.1"):
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
        print("DEBUG TOOLS: ENABLED (/api/test-tool active)")
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
