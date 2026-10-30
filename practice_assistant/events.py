"""Structured session events shared by the console and the web chat window.

Engines used to report progress only as formatted terminal text. The web UI needs the
same facts in machine-readable form, so this module adds a tiny thread-safe bus. The
console keeps receiving the exact strings it printed before; the bus is additive and
inert when nobody subscribes.

Design constraints:
- standard library only, no imports from the engines (avoids circular imports);
- audio callbacks run on a non-asyncio thread, so every method is thread-safe;
- per-subscriber queues are bounded: a slow browser must never stall audio;
- the bus never learns the API key or the UI token, so it cannot leak them.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

KINDS = frozenset(
    {
        "state",
        "transcript",
        "reply",
        "confirmation",
        "learning",
        "timing",
        "error",
        "session",
        "diagnostic",
        "notification",
    }
)

DEFAULT_QUEUE = 500
RECENT_LIMIT = 60
STATES = frozenset(
    {"starting", "listening", "thinking", "speaking", "interrupted", "ended", "error"}
)


def settings_payload(settings: Any) -> dict[str, Any]:
    """Field names the ``session`` event uses to refresh the header chips."""
    return {
        "practice_language": settings.practice_language,
        "learner_language": settings.learner_language,
        "level": settings.level,
        "scenario": settings.scenario,
        "correction": settings.correction,
        "speed": settings.speed,
    }


class Subscriber:
    """One browser connection: a bounded mailbox that drops the oldest on overflow."""

    def __init__(self, limit: int = DEFAULT_QUEUE) -> None:
        self.limit = limit
        self.items: deque[dict[str, Any]] = deque()
        self.lock = threading.Lock()
        self.dropped = 0
        self.closed = False
        self.wake = threading.Event()

    def offer(self, event: dict[str, Any]) -> None:
        with self.lock:
            if self.closed:
                return
            while len(self.items) >= self.limit:
                self.items.popleft()
                self.dropped += 1
            self.items.append(event)
        self.wake.set()

    def drain(self, timeout: float = 15.0) -> list[dict[str, Any]]:
        """Block until something is available or the timeout elapses (heartbeat)."""
        self.wake.wait(timeout)
        self.wake.clear()
        with self.lock:
            items = list(self.items)
            self.items.clear()
        return items

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.items.clear()
        self.wake.set()


class EventBus:
    """Fan-out plus a snapshot that lets a refreshed page rebuild the window."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.subscribers: set[Subscriber] = set()
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_LIMIT)
        self.snapshot: dict[str, Any] = {
            "state": "starting",
            "detail": "",
            "settings": {},
            "engine": "",
            "turns": [],
            "learning": {"vocabulary": [], "mistakes": []},
            "notifications": [],
            "ended": False,
            "summary": "",
            "dropped": 0,
        }

    # ---- publishing -------------------------------------------------------
    def emit(self, kind: str, **data: Any) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"Unknown event kind: {kind}")
        event = {
            "kind": kind,
            "at": time.time(),
            "seq": self._next_seq(),
            **{k: v for k, v in data.items() if v is not None},
        }
        with self.lock:
            self._apply(event)
            self.recent.append(event)
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            subscriber.offer(event)
        return event

    def _next_seq(self) -> int:
        with self.lock:
            self.snapshot["seq"] = self.snapshot.get("seq", 0) + 1
            return int(self.snapshot["seq"])

    def _apply(self, event: dict[str, Any]) -> None:
        """Fold an event into the snapshot. Called with the lock held."""
        kind = event["kind"]
        snapshot = self.snapshot
        if kind == "state":
            snapshot["state"] = event.get("state", snapshot["state"])
            snapshot["detail"] = event.get("detail", "")
        elif kind == "transcript":
            snapshot["turns"].append(
                {
                    "turn": event.get("turn"),
                    "role": "learner",
                    "text": event.get("text", ""),
                    "engine": event.get("engine", snapshot.get("engine", "")),
                }
            )
        elif kind == "reply":
            self._append_reply(event)
        elif kind == "confirmation":
            snapshot["notifications"].append(
                {
                    "level": "info",
                    "text": event.get("text", ""),
                    "at": event.get("at"),
                }
            )
            self._trim(snapshot["notifications"])
        elif kind == "learning":
            learning = snapshot["learning"]
            for name in ("vocabulary", "mistakes"):
                for value in event.get(name) or []:
                    if value and value not in learning[name]:
                        learning[name].append(value)
                del learning[name][50:]
        elif kind == "timing":
            self._apply_timing(event)
        elif kind == "error":
            snapshot["notifications"].append(
                {
                    "level": "error",
                    "text": event.get("message", ""),
                    "at": event.get("at"),
                }
            )
            self._trim(snapshot["notifications"])
        elif kind == "session":
            snapshot["ended"] = bool(event.get("ended", snapshot["ended"]))
            snapshot["summary"] = event.get("summary", snapshot["summary"])
            for name in ("practice_language", "level", "scenario"):
                if name in event:
                    snapshot["settings"][name] = event[name]
            for name in ("correction", "speed"):
                if name in event:
                    snapshot["settings"][name] = event[name]
        elif kind == "notification":
            snapshot["notifications"].append(
                {
                    "level": event.get("level", "info"),
                    "text": event.get("text", ""),
                    "at": event.get("at"),
                }
            )
            self._trim(snapshot["notifications"])
        elif kind == "diagnostic":
            diagnostics = snapshot.setdefault("diagnostics", {})
            diagnostics.update(
                {k: v for k, v in event.items() if k not in ("kind", "at", "seq")}
            )

    def _append_reply(self, event: dict[str, Any]) -> None:
        turns = self.snapshot["turns"]
        turn = event.get("turn")
        for index in range(len(turns) - 1, -1, -1):
            if turns[index].get("role") == "tutor" and turns[index].get("turn") == turn:
                turns[index]["text"] = event.get("text", "")
                turns[index]["interrupted"] = bool(event.get("interrupted", False))
                return
        turns.append(
            {
                "turn": turn,
                "role": "tutor",
                "text": event.get("text", ""),
                "interrupted": bool(event.get("interrupted", False)),
                "engine": event.get("engine", self.snapshot.get("engine", "")),
            }
        )

    def _apply_timing(self, event: dict[str, Any]) -> None:
        turn = event.get("turn")
        for entry in self.snapshot["turns"]:
            if entry.get("turn") == turn and entry.get("role") == "tutor":
                entry["timing"] = event.get("stages", {})
                entry["total_ms"] = event.get("total_ms")
                return
        self.snapshot["turns"].append(
            {
                "turn": turn,
                "role": "tutor",
                "text": "",
                "timing": event.get("stages", {}),
                "total_ms": event.get("total_ms"),
            }
        )

    @staticmethod
    def _trim(items: list[dict[str, Any]], limit: int = 40) -> None:
        del items[:-limit]

    # ---- subscriptions ----------------------------------------------------
    def subscribe(self, limit: int = DEFAULT_QUEUE) -> Subscriber:
        subscriber = Subscriber(limit)
        with self.lock:
            self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self.lock:
            self.subscribers.discard(subscriber)
        subscriber.close()

    def describe(
        self, settings: Any = None, options: Any = None, engine: str = ""
    ) -> dict[str, Any]:
        """Attach configuration to the snapshot (called once at startup)."""
        with self.lock:
            if engine:
                self.snapshot["engine"] = engine
            if settings is not None:
                self.snapshot["settings"].update(
                    {
                        "practice_language": settings.practice_language,
                        "learner_language": settings.learner_language,
                        "level": settings.level,
                        "scenario": settings.scenario,
                        "correction": settings.correction,
                        "speed": settings.speed,
                    }
                )
            if options is not None:
                self.snapshot["diagnostics"] = {
                    "voice": options.voice,
                    "silence_ms": options.silence_ms,
                    "echo_guard": options.echo_guard,
                    "live_model": options.live_model,
                    "input_device": options.input_device,
                    "output_device": options.output_device,
                    "log_dir": str(options.log_dir) if options.log_dir else "",
                    "report": str(options.report) if options.report else "",
                }
            return dict(self.snapshot)

    def state(self) -> dict[str, Any]:
        with self.lock:
            snapshot = {
                "state": self.snapshot["state"],
                "detail": self.snapshot.get("detail", ""),
                "settings": dict(self.snapshot["settings"]),
                "engine": self.snapshot.get("engine", ""),
                "turns": [dict(t) for t in self.snapshot["turns"]],
                "learning": {
                    name: list(values)
                    for name, values in self.snapshot["learning"].items()
                },
                "notifications": [dict(n) for n in self.snapshot["notifications"]],
                "ended": self.snapshot["ended"],
                "summary": self.snapshot.get("summary", ""),
                "diagnostics": dict(self.snapshot.get("diagnostics", {})),
                "dropped": sum(s.dropped for s in self.subscribers),
                "seq": self.snapshot.get("seq", 0),
            }
        return snapshot
