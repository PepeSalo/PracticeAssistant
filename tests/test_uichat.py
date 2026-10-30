from __future__ import annotations

import asyncio

import pytest
from conftest import pump

from practice_assistant.cascade import CascadeEngine
from practice_assistant.config import Options, Settings
from practice_assistant.conversation import Conversation
from practice_assistant.events import EventBus
from practice_assistant.providers import ReplyEvent
from practice_assistant.report import Reports
from practice_assistant.resilience import AppError
from practice_assistant.session import Session


class FakeProvider:
    """Cascade-shaped fake: transcript, streamed reply, streamed PCM."""

    def __init__(self, reply_text="Hello. How are you? ") -> None:
        self.reply_text = reply_text
        self.spoken: list[str] = []
        self.replied = 0

    async def transcribe(self, _wav):
        return "I want coffee."

    async def reply(self, _conversation, _extra=None):
        self.replied += 1
        for piece in self.reply_text.split(" "):
            yield ReplyEvent("text", piece + " ")

    async def speech(self, text, _settings):
        self.spoken.append(text)
        yield b"\0\0" * 2400


class Playback:
    """Drives the output callback so hidden drains cannot hang the test."""

    def __init__(self, playback):
        self.playback = playback
        self.stop = asyncio.Event()
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(pump(self.playback, self.stop))
        return self

    async def __aexit__(self, *_exc):
        self.stop.set()
        await asyncio.gather(self.task, return_exceptions=True)


def make(options=None, bus=None):
    settings = Settings()
    session = Session(
        Conversation(settings),
        options or Options(),
        Reports(settings, None, None),
        output=lambda _: None,
        bus=bus,
    )
    return session, CascadeEngine(session, FakeProvider())


def test_typed_turn_produces_transcript_reply_and_timing():
    async def scenario():
        bus = EventBus()
        session, engine = make(bus=bus)
        async with Playback(session.playback):
            engine.submit_text("I would like a coffee.")
            assert engine.task is not None
            await asyncio.wait_for(engine.task, timeout=5)
        return bus

    bus = asyncio.run(scenario())
    kinds = [turn["role"] for turn in bus.state()["turns"]]
    assert "learner" in kinds
    learner = next(t for t in bus.state()["turns"] if t["role"] == "learner")
    assert learner["text"] == "I would like a coffee."
    tutor = next(t for t in bus.state()["turns"] if t["role"] == "tutor")
    assert tutor["text"]
    assert tutor["total_ms"] is not None


def test_typed_turn_reuses_local_command_parsing():
    async def scenario():
        bus = EventBus()
        session, engine = make(bus=bus)
        async with Playback(session.playback):
            engine.submit_text("level B2")
            await asyncio.wait_for(engine.task, timeout=5)
        return session, bus

    session, bus = asyncio.run(scenario())
    assert session.conversation.settings.level == "B2"
    assert any(
        note["text"].startswith("Level is now B2")
        for note in bus.state()["notifications"]
    )


def test_second_typed_turn_is_refused_while_one_is_in_flight():
    async def scenario():
        session, engine = make()
        async with Playback(session.playback):
            engine.submit_text("First message.")
            with pytest.raises(RuntimeError):
                engine.submit_text("Second message.")
            await asyncio.wait_for(engine.task, timeout=5)

    asyncio.run(scenario())


def test_typed_turn_failure_is_reported_and_engine_recovers():
    class Broken(FakeProvider):
        async def reply(self, _conversation, _extra=None):
            raise AppError("Chat is unavailable.")
            yield ReplyEvent("text", "")  # pragma: no cover

    async def scenario():
        bus = EventBus()
        settings = Settings()
        session = Session(
            Conversation(settings),
            Options(),
            Reports(settings, None, None),
            output=lambda _: None,
            bus=bus,
        )
        engine = CascadeEngine(session, Broken())
        async with Playback(session.playback):
            engine.submit_text("Hello?")
            await asyncio.wait_for(engine.task, timeout=5)
        return bus

    bus = asyncio.run(scenario())
    assert any(
        note["level"] == "error" and "unavailable" in note["text"]
        for note in bus.state()["notifications"]
    )


def test_no_bus_means_no_events_and_unchanged_behaviour():
    async def scenario():
        session, engine = make(bus=None)
        async with Playback(session.playback):
            engine.submit_text("Just checking.")
            await asyncio.wait_for(engine.task, timeout=5)
        return session

    session = asyncio.run(scenario())
    assert session.bus is None
    assert session.conversation.history


def test_cli_exposes_ui_flags_with_safe_defaults():
    from practice_assistant.cli import parser

    args = parser().parse_args([])
    assert args.ui is False
    assert args.ui_port == 8770
    assert args.no_browser is False
    args = parser().parse_args(["--ui", "--ui-port", "9000", "--no-browser"])
    assert args.ui is True
    assert args.ui_port == 9000
    assert args.no_browser is True


def test_cli_rejects_an_invalid_ui_port(monkeypatch, capsys):
    from practice_assistant.cli import main

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    assert main(["--ui", "--ui-port", "80", "--no-browser"]) == 1
    assert "UI port" in capsys.readouterr().out


def test_missing_key_is_reported_before_the_window_opens(monkeypatch, capsys):
    from practice_assistant.cli import main

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["--ui", "--no-browser"]) == 1
    output = capsys.readouterr().out
    assert "OPENAI_API_KEY" in output
    assert "Chat window" not in output
