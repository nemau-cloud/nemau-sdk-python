"""Test server, and a client aimed at it."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

from nemau import NemauClient, RetryPolicy


class RecordedRequest:
    """What the stub saw go past."""

    def __init__(self, method: str, path: str, query: str, headers: dict[str, str], body: bytes):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None

    def __repr__(self) -> str:
        return f"{self.method} {self.path}?{self.query}"


#: A handler returns (status, body) or (status, body, response_headers). The
#: body is JSON-encoded unless it is already a string.
Handler = Callable[[RecordedRequest], tuple[Any, ...]]


class Stub:
    """Ephemeral HTTP server that records the requests it receives."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[RecordedRequest] = []
        stub = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                parsed = urlparse(self.path)
                recorded = RecordedRequest(
                    self.command, parsed.path, parsed.query, dict(self.headers), body
                )
                stub.requests.append(recorded)

                result = handler(recorded)
                status, payload = result[0], result[1]
                extra_headers: dict[str, str] = result[2] if len(result) > 2 else {}  # type: ignore[misc]
                raw = payload if isinstance(payload, bytes) else None
                if raw is None:
                    text = payload if isinstance(payload, str) else json.dumps(payload)
                    raw = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for name, value in extra_headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(raw)

            do_GET = _handle
            do_POST = _handle
            do_PUT = _handle
            do_PATCH = _handle
            do_DELETE = _handle

            def log_message(self, *args: Any) -> None:
                """Disables the request log."""

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> Stub:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def client_for(stub: Stub, **kwargs: Any) -> NemauClient:
    """Client aimed at the stub, with retries and polling sped up."""
    options: dict[str, Any] = {
        "base_url": stub.url,
        "retry": RetryPolicy(max_attempts=3, base_delay=0.001, max_delay=0.002),
        "poll_interval": 0.001,
        "env": {},
    }
    options.update(kwargs)
    return NemauClient(**options)


def routes(table: dict[str, Any], on_missing: Handler | None = None) -> Handler:
    """Handler driven by a "METHOD /path" to body table.

    A missing route answers 404 with an explicit message: in a test, that means
    the SDK is calling an endpoint the API does not have.
    """

    def handler(request: RecordedRequest) -> tuple[int, Any]:
        key = f"{request.method} {request.path}"
        if key in table:
            entry = table[key]
            if isinstance(entry, tuple):
                return entry
            return 200, entry
        if on_missing is not None:
            return on_missing(request)
        return 404, {"error": f"no stub for {key}"}

    return handler
