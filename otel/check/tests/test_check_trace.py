"""check_trace.py 的測試：用標準函式庫起一個假 OpenObserve。

跑法（repo 根目錄）：python3 -m unittest discover -s otel/check/tests
"""
import contextlib
import io
import json
import os
import re
import sys
import threading
import time
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_trace  # noqa: E402

TRACE = "0af7651916cd43dd8448eb211c80319c"
OTHER = "11111111111111111111111111111111"
AUTH = "Basic dGVzdDp0ZXN0"
SERVICES = ["demo-nginx", "demo-api", "demo-worker"]
STREAM_NOT_FOUND_400 = {"code": 20002, "message": "Search stream not found: nosuchstream",
                        "error_detail": "", "trace_id": ""}


class FakeOpenObserve:
    """模擬 /api/<org>/_search?type=traces|logs，照 SQL 裡的 trace id 依服務分組回傳。"""

    def __init__(self):
        self.rows = {"traces": [], "logs": []}
        self.missing_stream = None
        self.forced = []  # 依序先回這些 (狀態碼, body)，用完才照常查
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append((self.path, body))
                if self.headers.get("Authorization") != AUTH:
                    self._send(401, {"code": 401, "message": "Unauthorized Access"})
                    return
                if fake.forced:
                    self._send(*fake.forced.pop(0))
                    return
                m = re.match(r"^/api/demo/_search\?type=(traces|logs)$", self.path)
                if m and fake.missing_stream == m.group(1):
                    self._send(404, {"code": 404, "message": "Search stream not found"})
                    return
                if not m:
                    self._send(404, {"code": 404, "message": "not found"})
                    return
                tid = re.search(r"trace_id = '([0-9a-f]+)'", body["query"]["sql"]).group(1)
                counts = {}
                for r in fake.rows[m.group(1)]:
                    if r["trace_id"] == tid:
                        counts[r["service_name"]] = counts.get(r["service_name"], 0) + 1
                self._send(200, {"hits": [{"service_name": s, "n": n} for s, n in counts.items()]})

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.api = f"http://127.0.0.1:{self.server.server_address[1]}/api/demo"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, kind, service, trace_id=TRACE):
        self.rows[kind].append({"service_name": service, "trace_id": trace_id})

    def add_all(self, skip_span=None, skip_log=None):
        for s in SERVICES:
            if s != skip_span:
                self.add("traces", s)
            if s != skip_log:
                self.add("logs", s)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class CheckTraceTest(unittest.TestCase):
    def setUp(self):
        self.o2 = FakeOpenObserve()
        self.addCleanup(self.o2.close)

    def run_cli(self, *extra, auth=AUTH):
        argv = ["--url", self.o2.api, "--trace-id", TRACE, "--timeout", "0.5", "--interval", "0.05"]
        for s in SERVICES:
            argv += ["--span-service", s, "--log-service", s]
        out, err = io.StringIO(), io.StringIO()
        env = {"OPENOBSERVE_AUTH": auth}
        with unittest.mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_trace.main(argv + list(extra))
        return code, out.getvalue() + err.getvalue()

    def test_all_present_exit_0(self):
        self.o2.add_all()
        code, msg = self.run_cli()
        self.assertEqual(code, 0, msg)
        self.assertIn("全部到齊", msg)

    def test_missing_one_span_exit_1_and_names_service(self):
        self.o2.add_all(skip_span="demo-worker")
        code, msg = self.run_cli()
        self.assertEqual(code, 1, msg)
        self.assertIn("缺 span 的服務：demo-worker", msg)
        self.assertNotIn("缺 log", msg)
        self.assertNotIn("demo-api", msg)

    def test_missing_one_log_exit_1_and_names_service(self):
        self.o2.add_all(skip_log="demo-api")
        code, msg = self.run_cli()
        self.assertEqual(code, 1, msg)
        self.assertIn("缺 log 的服務：demo-api", msg)
        self.assertNotIn("缺 span", msg)

    def test_rows_of_other_trace_do_not_count(self):
        self.o2.add_all(skip_span="demo-nginx")
        self.o2.add("traces", "demo-nginx", trace_id=OTHER)
        code, msg = self.run_cli()
        self.assertEqual(code, 1, msg)
        self.assertIn("demo-nginx", msg)

    def test_polls_until_late_data_arrives(self):
        self.o2.add_all(skip_log="demo-worker")
        timer = threading.Timer(0.3, self.o2.add, ("logs", "demo-worker"))
        timer.start()
        self.addCleanup(timer.cancel)
        start = time.monotonic()
        result = check_trace.check_trace(self.o2.api, AUTH, TRACE, SERVICES, SERVICES,
                                         timeout=5, interval=0.05)
        self.assertTrue(result.ok, result.describe())
        self.assertLess(time.monotonic() - start, 4)
        self.assertGreater(len(self.o2.requests), 2)

    def test_stream_not_found_keeps_waiting(self):
        self.o2.add_all()
        self.o2.missing_stream = "logs"
        timer = threading.Timer(0.3, setattr, (self.o2, "missing_stream", None))
        timer.start()
        self.addCleanup(timer.cancel)
        result = check_trace.check_trace(self.o2.api, AUTH, TRACE, SERVICES, SERVICES,
                                         timeout=5, interval=0.05)
        self.assertTrue(result.ok, result.describe())

    def test_stream_not_found_400_keeps_waiting(self):
        # OpenObserve v1.1.2 實測：查不存在的 stream 回 400，traces 與 logs 相同
        self.o2.add_all()
        self.o2.forced = [(400, STREAM_NOT_FOUND_400)] * 2
        code, msg = self.run_cli("--timeout", "5")
        self.assertEqual(code, 0, msg)
        self.assertEqual(self.o2.forced, [])
        self.assertGreater(len(self.o2.requests), 2)

    def test_other_400_fails_fast_with_exit_2(self):
        self.o2.add_all()
        self.o2.forced = [(400, {"code": 20001, "message": "Search SQL not valid: syntax error"})]
        start = time.monotonic()
        code, msg = self.run_cli("--timeout", "10")
        self.assertEqual(code, 2, msg)
        self.assertIn("400", msg)
        self.assertLess(time.monotonic() - start, 5)

    def test_other_404_fails_fast_with_exit_2(self):
        code, msg = self.run_cli("--url", self.o2.api.replace("/demo", "/wrong-org"), "--timeout", "10")
        self.assertEqual(code, 2, msg)
        self.assertIn("404", msg)

    def test_all_missing_hints_at_config(self):
        code, msg = self.run_cli()
        self.assertEqual(code, 1, msg)
        self.assertIn("先查位址", msg)

    def test_wrong_auth_fails_fast_with_exit_2(self):
        self.o2.add_all()
        start = time.monotonic()
        code, msg = self.run_cli("--timeout", "10", auth="Basic d3Jvbmc6d3Jvbmc=")
        self.assertEqual(code, 2, msg)
        self.assertIn("401", msg)
        self.assertLess(time.monotonic() - start, 5)

    def test_invalid_trace_id_rejected(self):
        with self.assertRaises(check_trace.CheckError):
            check_trace.check_trace(self.o2.api, AUTH, "' OR 1=1 --", SERVICES, [])
        self.assertEqual(self.o2.requests, [])

    def test_sends_auth_and_time_range(self):
        self.o2.add_all()
        check_trace.check_trace(self.o2.api, AUTH, TRACE, ["demo-api"], [], timeout=0)
        path, body = self.o2.requests[0]
        self.assertEqual(path, "/api/demo/_search?type=traces")
        q = body["query"]
        self.assertLess(q["start_time"], q["end_time"])
        self.assertIn(f"trace_id = '{TRACE}'", q["sql"])


if __name__ == "__main__":
    unittest.main()
