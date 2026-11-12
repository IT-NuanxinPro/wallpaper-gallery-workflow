"""真实本机HTTP记录请求头，保证脚本身份明确且签名请求不携带业务凭据。"""
import hashlib
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from video.api import HTTPFailure, ServiceAPI, json_request, put_stream, read_signed


class RequestHeaderTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        captured = self.requests
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def reply(self, body, status=200):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_GET(self):
                captured.append((self.path, dict(self.headers)))
                if self.path.startswith("/object"):
                    return self.reply(b"signed-object")
                if self.path.startswith("/sensitive"):
                    return self.reply(b"remote-secret-response", 503)
                return self.reply(b"{}")
            def do_POST(self):
                captured.append((self.path, dict(self.headers)))
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.reply(b"remote-secret-response", 403)
            def do_PUT(self):
                captured.append((self.path, dict(self.headers)))
                self.rfile.read(int(self.headers["Content-Length"]))
                self.reply(b"")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_json_and_signed_transfers_identify_workflow_without_leaking_service_token(self):
        api = ServiceAPI(self.origin, "local-service-secret")
        api.get("/internal/video/jobs/job-123/source?leaseToken=local-lease-secret")
        with read_signed(self.origin + "/object?signature=local-signed-secret") as response:
            self.assertEqual(response.read(), b"signed-object")
        payload = b"signed-upload"
        put_stream(self.origin + "/object?signature=local-put-secret", io.BytesIO(payload), len(payload), "video/mp4", hashlib.sha256(payload).hexdigest())
        self.assertEqual(len(self.requests), 3)
        for _, headers in self.requests:
            self.assertEqual(headers.get("User-Agent"), "wallpaper-video-workflow/1.0")
        self.assertEqual(self.requests[0][1].get("X-Video-Service-Token"), "local-service-secret")
        for _, headers in self.requests[1:]:
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("X-Video-Service-Token", headers)

    def test_http_failure_identifies_only_whitelisted_method_and_stage(self):
        api = ServiceAPI(self.origin, "local-service-secret")
        with self.assertRaises(HTTPFailure) as captured:
            api.post("/internal/video/claim?signature=local-signed-secret", {"runId": "42"})
        message = str(captured.exception)
        self.assertIn("POST internal/video/claim", message)
        self.assertIn("403", message)
        for secret in [self.origin, "local-service-secret", "local-signed-secret", "remote-secret-response"]:
            self.assertNotIn(secret, message)
        with self.assertRaises(HTTPFailure) as unknown:
            json_request(self.origin + "/sensitive-path-secret?signature=local-signed-secret")
        self.assertNotIn("sensitive-path-secret", str(unknown.exception))


if __name__ == "__main__":
    unittest.main()
