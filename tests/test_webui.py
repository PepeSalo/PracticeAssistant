from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from practice_assistant.config import Settings
from practice_assistant.events import EventBus
from practice_assistant.webui import WebUI


class FakeController:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.commands: list[tuple[str, str]] = []
        self.messages: list[str] = []
        self.stops = 0
        self.error: str | None = None

    def command(self, action: str, value: str) -> None:
        if self.error:
            raise RuntimeError(self.error)
        self.commands.append((action, value))
        if action == "level":
            self.bus.emit("session", level=value)

    def message(self, text: str) -> None:
        if self.error:
            raise RuntimeError(self.error)
        self.messages.append(text)

    def stop(self) -> None:
        if self.error:
            raise RuntimeError(self.error)
        self.stops += 1


@pytest.fixture
def ui():
    bus = EventBus()
    bus.describe(Settings(), None, "cascade")
    controller = FakeController(bus)
    server = WebUI(bus, controller, port=0)
    url = server.start()
    try:
        yield server, bus, controller, url.rstrip("/")
    finally:
        server.stop()


def get(url: str, token: str | None = None, headers: dict | None = None):
    request = urllib.request.Request(url)
    if token is not None:
        request.add_header("X-Session-Token", token)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def post(
    url: str, payload: dict, token: str | None = None, headers: dict | None = None
):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("X-Session-Token", token)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_index_serves_html_with_session_token(ui):
    server, _bus, _controller, url = ui
    status, headers, body = get(f"{url}/")
    text = body.decode("utf-8")
    assert status == 200
    assert "text/html" in headers["Content-Type"]
    assert server.token in text
    assert "__SESSION_TOKEN__" not in text
    assert "Content-Security-Policy" in headers


def test_static_assets_have_correct_content_types(ui):
    _server, _bus, _controller, url = ui
    status, headers, body = get(f"{url}/assets/app.css")
    assert status == 200
    assert headers["Content-Type"].startswith("text/css")
    assert b"--accent" in body
    status, headers, _body = get(f"{url}/assets/app.js")
    assert status == 200
    assert "javascript" in headers["Content-Type"]


def test_unknown_and_traversal_asset_requests_are_refused(ui):
    _server, _bus, _controller, url = ui
    assert get(f"{url}/assets/missing.js")[0] == 404
    assert get(f"{url}/assets/..%2f..%2fwebui.py")[0] == 404
    assert get(f"{url}/../webui.py")[0] == 404
    assert get(f"{url}/etc/passwd")[0] == 404


def test_state_requires_the_session_token(ui):
    server, _bus, _controller, url = ui
    assert get(f"{url}/api/state")[0] == 403
    assert get(f"{url}/api/state", token="wrong")[0] == 403
    status, _headers, body = get(f"{url}/api/state", token=server.token)
    assert status == 200
    payload = json.loads(body)
    assert payload["engine"] == "cascade"
    assert "settings" in payload


def test_foreign_host_header_is_rejected(ui):
    server, _bus, _controller, url = ui
    status, _headers, _body = get(
        f"{url}/api/state", token=server.token, headers={"Host": "evil.example"}
    )
    assert status == 403


def test_foreign_origin_is_rejected_on_post(ui):
    server, _bus, controller, url = ui
    status, _body = post(
        f"{url}/api/command",
        {"action": "level", "value": "B2"},
        token=server.token,
        headers={"Origin": "https://evil.example"},
    )
    assert status == 403
    assert controller.commands == []


def test_post_requires_the_session_token(ui):
    _server, _bus, controller, url = ui
    assert post(f"{url}/api/command", {"action": "level", "value": "B2"})[0] == 403
    assert controller.commands == []


def test_command_is_applied_and_snapshot_returned(ui):
    server, _bus, controller, url = ui
    status, body = post(
        f"{url}/api/command",
        {"action": "level", "value": "B2"},
        token=server.token,
    )
    assert status == 200
    assert controller.commands == [("level", "B2")]
    assert json.loads(body)["settings"]["level"] == "B2"


def test_message_is_forwarded_trimmed(ui):
    server, _bus, controller, url = ui
    status, _body = post(
        f"{url}/api/message", {"text": "  I like coffee.  "}, token=server.token
    )
    assert status == 200
    assert controller.messages == ["I like coffee."]


def test_stop_is_forwarded(ui):
    server, _bus, controller, url = ui
    assert post(f"{url}/api/stop", {}, token=server.token)[0] == 200
    assert controller.stops == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": 5, "value": "B2"},
        {"action": "level", "value": 2},
        {"action": "level", "value": "x" * 400},
    ],
)
def test_invalid_command_payloads_return_400(ui, payload):
    server, _bus, controller, url = ui
    status, _body = post(f"{url}/api/command", payload, token=server.token)
    assert status == 400
    assert controller.commands == []


def test_non_json_and_oversized_bodies_return_400(ui):
    server, _bus, _controller, url = ui
    request = urllib.request.Request(
        f"{url}/api/command", data=b"not json", method="POST"
    )
    request.add_header("X-Session-Token", server.token)
    request.add_header("Content-Type", "application/json")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 400

    status, _body = post(f"{url}/api/message", {"text": "x" * 9000}, token=server.token)
    assert status == 400


def test_runtime_error_becomes_a_friendly_409(ui):
    server, _bus, controller, url = ui
    controller.error = "Wait for the current answer first."
    status, body = post(
        f"{url}/api/command", {"action": "level", "value": "B2"}, token=server.token
    )
    assert status == 409
    assert json.loads(body)["error"] == "Wait for the current answer first."


def test_errors_never_leak_a_traceback(ui):
    server, _bus, controller, url = ui
    controller.error = "Traceback (most recent call last): secret"
    status, body = post(f"{url}/api/stop", {}, token=server.token)
    assert status == 409
    assert b'File "' not in body


def test_sse_stream_delivers_snapshot_then_events(ui):
    server, bus, _controller, url = ui
    frames: list[str] = []
    snapshot_seen = threading.Event()

    def reader() -> None:
        request = urllib.request.Request(f"{url}/api/events", method="GET")
        request.add_header("X-Session-Token", server.token)
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.headers["Content-Type"].startswith("text/event-stream")
            while True:
                line = response.readline().decode("utf-8")
                if not line:
                    break
                if line.startswith("event: snapshot"):
                    snapshot_seen.set()
                elif line.startswith("data: "):
                    frames.append(line[6:].strip())

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    assert snapshot_seen.wait(5)
    bus.emit("transcript", turn=1, text="Heard this", engine="cascade")
    for _ in range(100):
        if any(json.loads(f).get("kind") == "transcript" for f in frames):
            break
        threading.Event().wait(0.05)
    payloads = [json.loads(f) for f in frames]
    # The first frame is the snapshot (no "kind"); later frames are events.
    assert "settings" in payloads[0]
    assert any(p.get("kind") == "transcript" for p in payloads)
    assert any(p.get("text") == "Heard this" for p in payloads)


def test_sse_requires_the_token(ui):
    _server, _bus, _controller, url = ui
    assert get(f"{url}/api/events")[0] == 403


def test_stopping_the_server_releases_the_port(ui):
    server, _bus, _controller, url = ui
    port = server.httpd.server_address[1]
    server.stop()
    replacement = WebUI(EventBus(), FakeController(EventBus()), port=port)
    try:
        replacement.start()
    finally:
        replacement.stop()


def test_rejecting_a_consumed_body_does_not_hang(ui):
    """Regression: draining an already-read body blocked until the client timed out."""
    server, _bus, _controller, url = ui
    # An empty JSON object is a valid body but an invalid command payload, so the
    # handler fails after the body has been read.
    status, body = post(f"{url}/api/command", {}, token=server.token)
    assert status == 400
    assert json.loads(body)["error"]
    # A second request on a new connection must still work.
    assert post(f"{url}/api/stop", {}, token=server.token)[0] == 200


def test_body_cannot_be_read_twice(ui):
    server, _bus, _controller, url = ui
    for _ in range(3):
        assert post(f"{url}/api/command", {"action": 5}, token=server.token)[0] == 400


# ---- visible identity: version, developer, copyright ------------------
def test_index_shows_version_developer_and_copyright(ui):
    from practice_assistant import __author__, __copyright__, __url__, __version__

    _server, _bus, _controller, url = ui
    text = get(f"{url}/")[2].decode("utf-8")
    assert __version__ in text
    assert __author__ in text
    assert __copyright__ in text
    assert __url__ in text
    # No unreplaced template markers may reach the browser.
    assert "__APP_" not in text
    assert "__SESSION_TOKEN__" not in text


def test_version_appears_in_the_title_and_as_a_chip(ui):
    from practice_assistant import __version__

    _server, _bus, _controller, url = ui
    text = get(f"{url}/")[2].decode("utf-8")
    assert f"<title>Language Practice Assistant {__version__}</title>" in text
    assert f'id="app-version">{__version__}<' in text


def test_state_snapshot_reports_the_application_identity(ui):
    from practice_assistant import __author__, __version__

    server, _bus, _controller, url = ui
    payload = json.loads(get(f"{url}/api/state", token=server.token)[2])
    assert payload["app"]["version"] == __version__
    assert payload["app"]["author"] == __author__
    assert payload["app"]["copyright"].startswith("©")
    assert payload["app"]["url"].startswith("https://github.com/")


def test_sse_snapshot_also_carries_the_identity(ui):
    server, _bus, _controller, url = ui
    request = urllib.request.Request(f"{url}/api/events", method="GET")
    request.add_header("X-Session-Token", server.token)
    with urllib.request.urlopen(request, timeout=10) as response:
        for _ in range(40):
            line = response.readline().decode("utf-8")
            if line.startswith("data: "):
                payload = json.loads(line[6:].strip())
                if "settings" in payload:
                    assert payload["app"]["name"] == "Language Practice Assistant"
                    return
    raise AssertionError("no snapshot frame received")


def test_versions_never_disagree():
    """The UI, the package and pyproject must agree."""
    import tomllib

    from practice_assistant import __version__

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__
