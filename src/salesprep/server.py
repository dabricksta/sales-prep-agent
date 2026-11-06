"""Webhook trigger: a signed POST starts an agent run for a new lead.

Auth: HMAC-SHA256 over f"{timestamp}.{body}" with a shared secret, a timestamp window
to limit replay, a body-size cap, and strict lead_id validation. Runs are executed on a
small worker pool so the HTTP response (202) returns immediately.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping

MAX_BODY_BYTES = 4096
_LEAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(
    secret: bytes,
    timestamp: str,
    body: bytes,
    signature: str,
    now: float,
    max_skew: int,
) -> bool:
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(now - ts) > max_skew:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature or "")


class WebhookApp:
    def __init__(
        self,
        secret: str,
        lead_exists: Callable[[str], bool],
        run_agent: Callable[[str, str], Any],
        max_skew: int = 300,
        workers: int = 2,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not secret:
            raise ValueError(
                "WEBHOOK_SECRET must be set; refusing to start an unauthenticated webhook"
            )
        self._secret = secret.encode()
        self._lead_exists = lead_exists
        self._run_agent = run_agent
        self._max_skew = max_skew
        self._clock = clock
        self._pool = ThreadPoolExecutor(max_workers=workers)

    def handle(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, dict[str, Any]]:
        if method != "POST" or path != "/webhook/new-lead":
            return 404, {"error": "not found"}
        if len(body) > MAX_BODY_BYTES:
            return 413, {"error": "body too large"}

        lowered = {k.lower(): v for k, v in headers.items()}
        if not verify(
            self._secret,
            lowered.get("x-timestamp", ""),
            body,
            lowered.get("x-signature", ""),
            self._clock(),
            self._max_skew,
        ):
            return 401, {"error": "invalid signature"}

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return 400, {"error": "invalid JSON"}
        lead_id = payload.get("lead_id") if isinstance(payload, dict) else None
        if not isinstance(lead_id, str) or not _LEAD_ID_RE.match(lead_id):
            return 400, {"error": "lead_id must match [A-Za-z0-9_-]{1,40}"}
        if not self._lead_exists(lead_id):
            return 404, {"error": "unknown lead"}

        run_id = uuid.uuid4().hex[:12]
        self._pool.submit(self._run_agent, lead_id, run_id)
        return 202, {"accepted": True, "run_id": run_id}

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


def serve(app: WebhookApp, host: str = "127.0.0.1", port: int = 8787) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            body = self.rfile.read(min(length, MAX_BODY_BYTES + 1))
            status, payload = app.handle("POST", self.path, dict(self.headers), body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            status, payload = app.handle("GET", self.path, {}, b"")
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: Any) -> None:  # keep stdout quiet
            return

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Listening on http://{host}:{port}/webhook/new-lead (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.shutdown(wait=False)
