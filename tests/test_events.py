from __future__ import annotations

import json
import threading

import pytest

from practice_assistant.config import Settings
from practice_assistant.events import EventBus, Subscriber, settings_payload


def test_every_kind_serializes_and_is_ordered():
    bus = EventBus()
    subscriber = bus.subscribe()
    bus.emit("state", state="listening", detail="Listening…")
    bus.emit("transcript", turn=1, text="I want coffee.", engine="cascade")
    events = subscriber.drain(timeout=0.1)
    assert [event["kind"] for event in events] == ["state", "transcript"]
    assert events[1]["seq"] < bus.state()["seq"] + 1
    for event in events:
        json.dumps(event)  # must not raise


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        EventBus().emit("nonsense")


def test_none_values_are_dropped_from_payloads():
    bus = EventBus()
    subscriber = bus.subscribe()
    bus.emit("timing", turn=1, total_ms=None, stages={"STT delay": 12.0})
    payload = subscriber.drain(timeout=0.1)[0]
    assert "total_ms" not in payload
    assert payload["stages"] == {"STT delay": 12.0}


def test_slow_subscriber_drops_oldest_without_blocking_sender():
    bus = EventBus()
    subscriber = bus.subscribe(limit=3)
    for index in range(10):
        bus.emit("notification", text=f"note {index}")
    assert subscriber.dropped == 7
    remaining = subscriber.drain(timeout=0.1)
    assert [event["text"] for event in remaining] == ["note 7", "note 8", "note 9"]
    assert bus.state()["dropped"] == 7


def test_unsubscribe_stops_delivery_and_is_the_audio_loop_safe_option():
    bus = EventBus()
    subscriber = bus.subscribe()
    bus.unsubscribe(subscriber)
    bus.emit("notification", text="after close")
    assert subscriber.drain(timeout=0.1) == []


def test_emit_from_many_threads_stays_consistent():
    bus = EventBus()
    subscriber = bus.subscribe(limit=10_000)

    def worker(start: int) -> None:
        for index in range(50):
            bus.emit("notification", text=f"{start}-{index}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(subscriber.drain(timeout=0.1)) == 200


def test_snapshot_rebuilds_conversation_after_refresh():
    bus = EventBus()
    bus.emit("transcript", turn=1, text="Hello there", engine="cascade")
    bus.emit("reply", turn=1, text="Hi! How are you?", engine="cascade")
    bus.emit("timing", turn=1, stages={"STT delay": 120.0}, total_ms=900.0)
    state = bus.state()
    assert state["turns"][0]["role"] == "learner"
    assert state["turns"][1]["role"] == "tutor"
    assert state["turns"][1]["total_ms"] == 900.0
    assert state["turns"][1]["timing"] == {"STT delay": 120.0}


def test_repeated_reply_updates_the_same_bubble():
    bus = EventBus()
    bus.emit("reply", turn=2, text="First draft.", engine="live")
    bus.emit("reply", turn=2, text="Final text.", engine="live")
    tutors = [t for t in bus.state()["turns"] if t["role"] == "tutor"]
    assert len(tutors) == 1
    assert tutors[0]["text"] == "Final text."


def test_interrupted_flag_is_recorded():
    bus = EventBus()
    bus.emit("reply", turn=1, text="Half an ans", interrupted=True)
    assert bus.state()["turns"][0]["interrupted"] is True


def test_learning_notes_accumulate_without_duplicates():
    bus = EventBus()
    bus.emit("learning", vocabulary=["kiitos"], mistakes=["word order"])
    bus.emit("learning", vocabulary=["kiitos", "kahvi"], mistakes=[])
    learning = bus.state()["learning"]
    assert learning["vocabulary"] == ["kiitos", "kahvi"]
    assert learning["mistakes"] == ["word order"]


def test_errors_and_confirmations_become_notifications():
    bus = EventBus()
    bus.emit("confirmation", text="Level is now B2.")
    bus.emit("error", message="Network lost.", retryable=True)
    notifications = bus.state()["notifications"]
    info = {"level": "info", "text": "Level is now B2."}
    error = {"level": "error", "text": "Network lost."}
    assert info.items() <= notifications[0].items()
    assert error.items() <= notifications[1].items()
    assert notifications[0]["at"] is not None


def test_settings_flow_into_the_snapshot():
    bus = EventBus()
    settings = Settings(practice_language="es", level="A2", scenario="cafe")
    bus.describe(settings, None, "cascade")
    bus.emit("session", **settings_payload(settings))
    state = bus.state()
    assert state["settings"]["practice_language"] == "es"
    assert state["settings"]["level"] == "A2"
    assert state["engine"] == "cascade"


def test_no_api_key_or_token_can_reach_the_snapshot():
    bus = EventBus()
    settings = Settings()
    bus.describe(settings, None, "live")
    blob = json.dumps(bus.state(), ensure_ascii=False)
    assert "sk-" not in blob
    assert "OPENAI_API_KEY" not in blob
    assert "api_key" not in blob
    assert "token" not in blob


def test_end_of_session_records_summary():
    bus = EventBus()
    bus.emit("session", ended=True, summary="Practice complete. Goodbye!")
    state = bus.state()
    assert state["ended"] is True
    assert state["summary"] == "Practice complete. Goodbye!"


def test_diagnostic_values_merge_into_diagnostics():
    bus = EventBus()
    bus.emit("diagnostic", calibrated_rms=312.4)
    bus.emit("diagnostic", reconnects=1)
    assert bus.state()["diagnostics"] == {"calibrated_rms": 312.4, "reconnects": 1}


def test_describe_stores_safe_diagnostics():
    from practice_assistant.config import Options

    bus = EventBus()
    bus.describe(Settings(), Options(engine="cascade", voice="alloy"), "cascade")
    diagnostics = bus.state()["diagnostics"]
    assert diagnostics["voice"] == "alloy"
    assert diagnostics["silence_ms"] == 700
    assert diagnostics["log_dir"] == ""


def test_subscriber_close_wakes_a_waiting_drain():
    subscriber = Subscriber()

    def close_soon() -> None:
        subscriber.close()

    threading.Timer(0.05, close_soon).start()
    assert subscriber.drain(timeout=5.0) == []
    subscriber.offer({"kind": "notification"})
    assert subscriber.items == __import__("collections").deque()
