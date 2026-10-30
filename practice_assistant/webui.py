"""Local web chat window: a tiny HTTP server plus Server-Sent Events.

The browser is a controller and transcript view only. Microphone and speaker stay
native on this PC, so no audio ever travels through HTTP.

Security model (a personal desktop app, not a public service):
- bind to loopback only;
- every request must carry a per-run random token that the page receives in its HTML;
- the Host header must match the bound address, and any Origin must be same-origin,
  which blocks DNS-rebinding and cross-site POSTs;
- static files come from a fixed allowlist, so no path is ever joined from the request;
- errors return a plain message, never a traceback or a key.
"""

from __future__ import annotations

import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

MAX_BODY = 8192
ASSETS = Path(__file__).with_name("assets") / "ui"
STATIC = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


class Controller(Protocol):
    """Applied on the asyncio loop by the owner, so no engine state is touched here."""

    def command(self, action: str, value: str) -> None: ...

    def message(self, text: str) -> None: ...

    def stop(self) -> None: ...


class _Handler(BaseHTTPRequestHandler):
    server_version = "PracticeAssistantUI"
    protocol_version = "HTTP/1.1"

    # Injected by WebUI.
    bus: Any
    controller: Controller
    token: str
    allowed_hosts: set[str]

    def log_message(self, *_args: Any) -> None:
        """Keep the terminal clean; problems surface in the UI instead."""

    # ---- plumbing --------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status >= 400:
            # A rejected body may still be unread; closing avoids corrupting
            # the next request on a kept-alive connection.
            self.close_connection = True
            self.send_header("Connection", "close")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _fail(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").casefold() in self.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = (self.headers.get("Host") or "").casefold()
        return urlsplit(origin).netloc.casefold() == host

    def _token_ok(self, query: dict[str, list[str]]) -> bool:
        supplied = (
            self.headers.get("X-Session-Token") or (query.get("token") or [""])[0]
        )
        return secrets.compare_digest(supplied, self.token)

    def _body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            if 0 < length <= 10 * MAX_BODY:
                # Drain what is already on the wire before answering.
                self.rfile.read(length)
            return None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    # ---- routing ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._host_ok():
            self._fail(HTTPStatus.FORBIDDEN, "Unexpected Host header.")
            return
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        if path in ("/", "/index.html"):
            self._serve_index()
            return
        if path.startswith("/assets/"):
            self._serve_static(path.rsplit("/", 1)[-1])
            return
        if path == "/api/state":
            if not self._token_ok(query):
                self._fail(HTTPStatus.FORBIDDEN, "Missing or wrong session token.")
                return
            self._json(HTTPStatus.OK, self.bus.state())
            return
        if path == "/api/events":
            if not self._token_ok(query):
                self._fail(HTTPStatus.FORBIDDEN, "Missing or wrong session token.")
                return
            self._stream()
            return
        self._fail(HTTPStatus.NOT_FOUND, "Unknown path.")

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._host_ok() or not self._origin_ok():
            self._fail(HTTPStatus.FORBIDDEN, "Cross-origin request blocked.")
            return
        parts = urlsplit(self.path)
        if not self._token_ok(parse_qs(parts.query)):
            self._fail(HTTPStatus.FORBIDDEN, "Missing or wrong session token.")
            return
        payload = self._body()
        if payload is None:
            self._fail(HTTPStatus.BAD_REQUEST, "Expected a small JSON object body.")
            return
        try:
            if parts.path == "/api/command":
                action = payload.get("action")
                value = payload.get("value", "")
                if not isinstance(action, str) or not isinstance(value, str):
                    raise ValueError("action and value must be strings")
                if len(action) > 40 or len(value) > 300:
                    raise ValueError("value is too long")
                self.controller.command(action, value)
            elif parts.path == "/api/message":
                text = payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("text must be a non-empty string")
                if len(text) > 2000:
                    raise ValueError("text is too long")
                self.controller.message(text.strip())
            elif parts.path == "/api/stop":
                self.controller.stop()
            else:
                self._fail(HTTPStatus.NOT_FOUND, "Unknown path.")
                return
        except ValueError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except RuntimeError as exc:
            self._fail(HTTPStatus.CONFLICT, str(exc))
            return
        self._json(HTTPStatus.OK, self.bus.state())

    # ---- responses -------------------------------------------------------
    def _serve_index(self) -> None:
        try:
            html = (ASSETS / "index.html").read_text(encoding="utf-8")
        except OSError:
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, "UI files are missing.")
            return
        document = html.replace("__SESSION_TOKEN__", self.token)
        body = document.encode("utf-8")
        self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")

    def _serve_static(self, name: str) -> None:
        content_type = STATIC.get(name)
        if content_type is None:
            self._fail(HTTPStatus.NOT_FOUND, "Unknown asset.")
            return
        try:
            body = (ASSETS / name).read_bytes()
        except OSError:
            self._fail(HTTPStatus.NOT_FOUND, "Unknown asset.")
            return
        self._send(HTTPStatus.OK, body, content_type)

    def _stream(self) -> None:
        subscriber = self.bus.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self._write_frame(self.bus.state(), event="snapshot")
            while True:
                events = subscriber.drain(15.0)
                for event in events:
                    self._write_frame(event)
                if not events:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.bus.unsubscribe(subscriber)

    def _write_frame(self, payload: dict[str, Any], event: str | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        frame = f"event: {event}\n" if event else ""
        frame += f"data: {data}\n\n"
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()


class WebUI:
    """Owns the HTTP server thread; call ``start`` then ``stop``."""

    def __init__(
        self,
        bus: Any,
        controller: Controller,
        port: int = 8770,
        host: str = "127.0.0.1",
    ) -> None:
        self.bus = bus
        self.controller = controller
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def _make_server(self) -> ThreadingHTTPServer:
        handler = type(
            "BoundHandler",
            (_Handler,),
            {
                "bus": self.bus,
                "controller": self.controller,
                "token": self.token,
                "allowed_hosts": set(),
            },
        )
        port = self.port
        try:
            httpd = ThreadingHTTPServer((self.host, port), handler)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot open http://{self.host}:{port} ({exc}). "
                "Choose another port with --ui-port."
            ) from exc
        bound = httpd.server_address[1]
        handler.allowed_hosts = {
            f"127.0.0.1:{bound}",
            f"localhost:{bound}",
            f"[::1]:{bound}",
        }
        httpd.daemon_threads = True
        return httpd

    def start(self) -> str:
        self.httpd = self._make_server()
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, name="webui", daemon=True
        )
        self.thread.start()
        return self.url

    @property
    def url(self) -> str:
        port = self.httpd.server_address[1] if self.httpd else self.port
        return f"http://{self.host}:{port}/"

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.thread = None
