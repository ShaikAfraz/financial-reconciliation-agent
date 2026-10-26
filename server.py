"""
Lightweight REST API and Web Server for the Autonomous Financial Reconciliation Agent.
Zero third-party framework dependencies: uses Python's built-in http.server.
"""

import sys
import json
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

WEB_DIR = PROJECT_ROOT / "web"
DATA_DIR = PROJECT_ROOT / "data"

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

        else:
            # Fall back to static file serving from web directory
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length)

        try:
            req_data = json.loads(post_body.decode("utf-8")) if post_body else {}
        except Exception:
            req_data = {}

        if path == "/api/run":
            scenario_id = req_data.get("scenario_id")
            query = req_data.get("query")
            context = req_data.get("context", {})

            if scenario_id:
                with open(DATA_DIR / "ground_truth_scenarios.json", "r") as f:
                    scenarios = json.load(f)
                matched = next((s for s in scenarios if s["scenario_id"] == scenario_id), None)
                if matched:
                    query = matched["input_prompt"]
                    context = matched.get("context", {})

            if not query:
                self._send_json({"error": "Missing query or scenario_id"}, 400)
                return

            agent = FinancialReconciliationAgent()
            trace = agent.run(query, context)
            self._send_json(trace.to_dict())

        elif path == "/api/test-tool":
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

        else:
            self._send_json({"error": "Endpoint not found"}, 404)

def run_server(port: int = 8080):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, FinancialAgentServerHandler)
    print("=" * 76)
    print(f"FINANCIAL RECONCILIATION AGENT DASHBOARD RUNNING")
    print(f"URL: http://localhost:{port}")
    print(f"Directory: {WEB_DIR}")
    print("=" * 76)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        httpd.server_close()

if __name__ == "__main__":
    run_server()
