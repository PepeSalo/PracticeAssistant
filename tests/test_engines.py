from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from conftest import FakeConnection, pump, signal

from practice_assistant.cascade import CascadeEngine, Sentences
from practice_assistant.config import Options, Settings
from practice_assistant.conversation import Conversation
from practice_assistant.live import LiveEngine
from practice_assistant.providers import ReplyEvent
from practice_assistant.report import Reports
from practice_assistant.resilience import AppError
from practice_assistant.session import Session
from practice_assistant.vad import AudioEvent


def make_session(options=None):
    settings = Settings()
    return Session(
        Conversation(settings),
        options or Options(),
        Reports(settings, None, None),
        output=lambda _: None,
    )


class FakeProvider:
    def __init__(self, transcript="I want coffee.", events=None):
        self.transcript = transcript
        self.events = events or [ReplyEvent("text", "Hello. How are you? ")]
        self.cancelled = False
        self.replies = 0

    async def transcribe(self, _wav):
        if isinstance(self.transcript, Exception):
            raise self.transcript
        return self.transcript

    async def reply(self, _conversation, _extra):
        self.replies += 1
        for event in self.events:
            yield event

    async def speech(self, _text, _settings):
        try:
            for _ in range(3):
                await asyncio.sleep(0.001)
                yield bytes(960)
        finally:
            self.cancelled = True


def utterance():
    now = time.perf_counter()
    return AudioEvent("end", now, signal(seconds=0.3), now - 0.4, now - 0.7)


def test_sentence_chunks():
    splitter = Sentences()
    assert splitter.feed("Hello") == []
    assert splitter.feed(". How are") == ["Hello."]
    assert splitter.feed(" you?") == ["How are you?"]
    assert splitter.feed("unfinished", final=True) == ["unfinished"]
    assert Sentences().feed("word " * 100)


def test_cascade_numeric_timings_and_five_turns():
    async def scenario():
        s = make_session(Options(benchmark_runs=5))
        s.bind_callbacks()
        p = FakeProvider()
        engine = CascadeEngine(s, p)
        stop = asyncio.Event()
        output = asyncio.create_task(pump(s.playback, stop))
        try:
            for _ in range(5):
                await engine.turn(utterance())
            assert s.ending
            assert all(t.successful for t in s.reports.turns)
            assert all(v is not None for v in s.reports.turns[0].values().values())
            assert "Median run" in s.reports.text()
            assert s.conversation.last_answer == "Hello. How are you?"
        finally:
            stop.set()
            await output

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "transcript,expected",
    [
        ("Let's practise Spanish", "es"),
        ("harjoitellaan suomi", "fi"),
    ],
)
def test_cascade_command_does_not_call_chat(transcript, expected):
    async def scenario():
        s = make_session()
        s.bind_callbacks()
        p = FakeProvider(transcript)
        engine = CascadeEngine(s, p)
        stop = asyncio.Event()
        output = asyncio.create_task(pump(s.playback, stop))
        try:
            await engine.turn(utterance())
            assert s.conversation.settings.practice_language == expected
            assert p.replies == 0
            assert s.reports.turns[0].command and not s.reports.turns[0].successful
        finally:
            stop.set()
            await output

    asyncio.run(scenario())


def test_cascade_goodbye_finishes_before_exit():
    async def scenario():
        s = make_session()
        s.bind_callbacks()
        engine = CascadeEngine(s, FakeProvider("end session"))
        stop = asyncio.Event()
        output = asyncio.create_task(pump(s.playback, stop))
        try:
            task = asyncio.create_task(engine.turn(utterance()))
            await asyncio.sleep(0.001)
            assert not s.ending
            await task
            assert s.ending and s.playback.idle()
        finally:
            stop.set()
            await output

    asyncio.run(scenario())


def test_cascade_failure_isolated():
    async def scenario():
        s = make_session()
        p = FakeProvider(AppError("offline"))
        engine = CascadeEngine(s, p)
        await engine.turn(utterance())
        assert not s.reports.turns[-1].successful and not s.ending
        p.transcript = "Hello"
        s.bind_callbacks()
        stop = asyncio.Event()
        output = asyncio.create_task(pump(s.playback, stop))
        try:
            await engine.turn(utterance())
            assert s.reports.turns[-1].successful
        finally:
            stop.set()
            await output

    asyncio.run(scenario())


def test_cascade_barge_in_closes_generation_and_keeps_only_heard_sentence():
    async def scenario():
        s = make_session()

        class SlowProvider(FakeProvider):
            async def speech(self, text, _settings):
                try:
                    yield bytes(960)
                    if "second" in text:
                        await asyncio.sleep(100)
                finally:
                    self.cancelled = True

        p = SlowProvider(events=[ReplyEvent("text", "First sentence. Unheard second.")])
        engine = CascadeEngine(s, p)
        s.bind_callbacks()
        await engine.audio_event(utterance())
        while len(engine.spoken) < 2:
            s.playback.callback(bytearray(960), 480, None, None)
            await asyncio.sleep(0.001)
        s.playback.callback(bytearray(960), 480, None, None)
        await engine.interrupt(time.perf_counter())
        assert p.cancelled and engine.task is None and s.playback.pending == 0
        assert s.conversation.last_answer == "First sentence."
        assert s.timing.interrupted
        s.playback.callback(bytearray(960), 480, None, None)
        await asyncio.sleep(0)
        assert s.reports.interruptions

    asyncio.run(scenario())


def test_tool_json_retry_limit():
    async def scenario():
        s = make_session()
        p = FakeProvider(events=[ReplyEvent("tool", "{broken", "control", "call")])
        engine = CascadeEngine(s, p)
        await engine.turn(utterance())
        assert p.replies == 3 and not s.reports.turns[0].successful

    asyncio.run(scenario())


def live_engine():
    s = make_session()
    engine = LiveEngine(s, SimpleNamespace())
    engine.connection = FakeConnection()
    return s, engine


def event(kind, **kwargs):
    return SimpleNamespace(type=kind, **kwargs)


async def start_live_turn(engine, transcript="Hello"):
    await engine.audio_event(utterance())
    await engine.handle(event("input_audio_buffer.committed", item_id="input1"))
    await engine.handle(
        event(
            "conversation.item.input_audio_transcription.completed",
            item_id="input1",
            transcript=transcript,
        )
    )
    await engine.handle(
        event(
            "response.created",
            response=SimpleNamespace(
                id="r1", metadata={"generation": str(engine.generation)}
            ),
        )
    )


def test_live_configuration_disables_server_autoresponse():
    async def scenario():
        _, engine = live_engine()
        await engine.configure()
        config = engine.connection.session.update.calls[0]["session"]
        assert config["audio"]["input"]["turn_detection"] is None
        assert config["audio"]["input"]["transcription"]["model"]
        assert config["tools"]
        assert config["max_output_tokens"] == 2048
        from openai.types.realtime.realtime_session_create_request_param import (
            RealtimeSessionCreateRequestParam,
        )
        from pydantic import TypeAdapter

        TypeAdapter(RealtimeSessionCreateRequestParam).validate_python(
            config, strict=True
        )

    asyncio.run(scenario())


def test_live_partial_transcripts_do_not_control_session():
    async def scenario():
        s, engine = live_engine()
        await engine.audio_event(utterance())
        await engine.handle(event("input_audio_buffer.committed", item_id="input1"))
        await engine.handle(
            event(
                "conversation.item.input_audio_transcription.delta",
                item_id="input1",
                delta="quit",
            )
        )
        assert not s.ending and not engine.goodbye_requested
        await engine.handle(
            event(
                "conversation.item.input_audio_transcription.completed",
                item_id="input1",
                transcript="level B2",
            )
        )
        assert s.conversation.settings.level == "B2"
        assert s.timing.command

    asyncio.run(scenario())


def test_live_audio_response_finishes_and_barge_in_truncates_played_ms():
    async def scenario():
        s, engine = live_engine()
        s.bind_callbacks()
        await start_live_turn(engine)
        import base64

        await engine.handle(
            event(
                "response.output_audio.delta",
                response_id="r1",
                item_id="audio1",
                content_index=0,
                delta=base64.b64encode(bytes(1920)).decode(),
            )
        )
        s.playback.callback(bytearray(960), 480, None, None)
        await asyncio.sleep(0)
        await engine.handle(
            event(
                "response.output_audio_transcript.delta",
                response_id="r1",
                item_id="audio1",
                delta="This is unplayed text.",
            )
        )
        await engine.interrupt(time.perf_counter())
        assert engine.connection.response.cancel.calls[0]["response_id"] == "r1"
        truncated = engine.connection.conversation.item.truncate.calls[0]
        assert truncated["audio_end_ms"] == 20 and truncated["item_id"] == "audio1"
        assert not s.conversation.last_answer
        assert s.timing.interrupted
        await engine.handle(
            event(
                "response.output_audio.delta",
                response_id="r1",
                item_id="audio1",
                content_index=0,
                delta=base64.b64encode(bytes(960)).decode(),
            )
        )
        assert s.playback.pending == 0

    asyncio.run(scenario())


def test_live_completed_audio_records_timings():
    async def scenario():
        s, engine = live_engine()
        s.bind_callbacks()
        await start_live_turn(engine)
        import base64

        await engine.handle(
            event(
                "response.output_audio.delta",
                response_id="r1",
                item_id="audio1",
                content_index=0,
                delta=base64.b64encode(bytes(960)).decode(),
            )
        )
        await engine.handle(
            event(
                "response.output_audio_transcript.done",
                response_id="r1",
                item_id="audio1",
                transcript="Hello, how are you?",
            )
        )
        s.playback.callback(bytearray(960), 480, None, None)
        await asyncio.sleep(0)
        await engine.handle(
            event(
                "response.done",
                response=SimpleNamespace(id="r1", status="completed", output=[]),
            )
        )
        await engine.finisher
        assert s.timing.successful and "Hello" in s.conversation.last_answer
        assert s.timing.values()["Translation/LLM delay"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["max_output_tokens", "content_filter", None])
def test_incomplete_reply_does_not_disconnect_or_execute_partial_tools(reason):
    async def scenario():
        s, engine = live_engine()
        await start_live_turn(engine)
        call = SimpleNamespace(
            type="function_call",
            id="partial_tool",
            name="control",
            arguments='{"action":"end","value":""}',
            call_id="call1",
        )
        await engine.handle(
            event(
                "response.done",
                response=SimpleNamespace(
                    id="r1",
                    status="incomplete",
                    status_details=SimpleNamespace(reason=reason, error=None),
                    output=[call],
                ),
            )
        )
        assert engine.finisher is not None
        await engine.finisher
        await engine.check()
        assert s.timing.interrupted and not s.timing.successful
        assert not engine.goodbye_requested and not s.ending and not s.generating
        assert engine.connection.conversation.item.delete.calls == [
            {"item_id": "partial_tool"}
        ]
        await start_live_turn(engine, "level B2")
        assert s.conversation.settings.level == "B2"

    asyncio.run(scenario())


def test_response_diagnostic_log_has_reason_without_transcripts(tmp_path):
    async def scenario():
        s, engine = live_engine()
        s.reports = Reports(s.conversation.settings, None, tmp_path)
        await start_live_turn(engine, "private learner text")
        await engine.handle(
            event(
                "response.done",
                response=SimpleNamespace(
                    id="r1",
                    status="incomplete",
                    status_details=SimpleNamespace(
                        reason="max_output_tokens", error=None
                    ),
                    output=[],
                ),
            )
        )
        await engine.finisher
        import json

        records = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text().splitlines()
        ]
        done = next(row for row in records if row["event"] == "response_done")
        assert done["reason"] == "max_output_tokens"
        assert done["max_output_tokens"] == 2048
        assert done["session_id"] and done["timestamp"]
        assert "private learner text" not in (tmp_path / "events.jsonl").read_text()

    asyncio.run(scenario())


def test_live_failed_status_retries_only_service_failures():
    async def scenario():
        for code, retryable in (
            ("server_error", True),
            ("rate_limit_exceeded", True),
            ("invalid_api_key", False),
            (None, False),
        ):
            s, engine = live_engine()
            await start_live_turn(engine)
            with pytest.raises(AppError) as exc:
                await engine.handle(
                    event(
                        "response.done",
                        response=SimpleNamespace(
                            id="r1",
                            status="failed",
                            status_details=SimpleNamespace(
                                reason=None, error=SimpleNamespace(code=code)
                            ),
                            output=[],
                        ),
                    )
                )
            assert exc.value.retryable == retryable
            assert s.timing.interrupted

    asyncio.run(scenario())


def test_live_barge_in_cancels_playback_finisher_without_cancelling_session():
    async def scenario():
        s, engine = live_engine()
        await start_live_turn(engine)
        import base64

        await engine.handle(
            event(
                "response.output_audio.delta",
                response_id="r1",
                item_id="audio1",
                content_index=0,
                delta=base64.b64encode(bytes(960)).decode(),
            )
        )
        await engine.handle(
            event(
                "response.done",
                response=SimpleNamespace(id="r1", status="completed", output=[]),
            )
        )
        await asyncio.sleep(0)
        assert engine.finisher is not None
        await engine.interrupt(time.perf_counter())
        await engine.check()
        assert engine.finisher is None
        assert s.timing.interrupted
        assert len(asyncio.all_tasks()) == 1

    asyncio.run(scenario())


def test_live_cancelled_tool_not_applied_and_bad_audio():
    async def scenario():
        s, engine = live_engine()
        await start_live_turn(engine)
        call = SimpleNamespace(
            type="function_call",
            name="control",
            arguments='{"action":"end","value":""}',
            call_id="call1",
        )
        await engine.handle(
            event(
                "response.done",
                response=SimpleNamespace(id="r1", status="cancelled", output=[call]),
            )
        )
        assert not engine.goodbye_requested and not s.ending
        with pytest.raises(AppError, match="base64"):
            await engine.handle(
                event(
                    "response.output_audio.delta",
                    response_id="r1",
                    item_id="audio1",
                    content_index=0,
                    delta="???",
                )
            )

    asyncio.run(scenario())


def test_live_multilingual_tool_control_and_validation():
    async def scenario():
        s, engine = live_engine()
        await start_live_turn(engine, "Hablemos español")
        call = SimpleNamespace(
            type="function_call",
            name="control",
            arguments='{"action":"language","value":"es"}',
            call_id="call1",
        )
        await engine.handle(
            event(
                "response.done",
                response=SimpleNamespace(id="r1", status="completed", output=[call]),
            )
        )
        assert s.conversation.settings.practice_language == "es"
        assert engine.connection.conversation.item.create.calls
        assert engine.connection.response.create.calls

    asyncio.run(scenario())


def test_live_error_and_timeout_are_explicit():
    async def scenario():
        _, engine = live_engine()
        with pytest.raises(AppError, match="rate_limit"):
            await engine.handle(
                event("error", error=SimpleNamespace(code="rate_limit_exceeded"))
            )
        engine.pending_at = time.perf_counter() - 100
        with pytest.raises(AppError, match="timed out"):
            await engine.check()

    asyncio.run(scenario())


def test_live_reconnect_seeds_recent_context_and_stops_tasks(monkeypatch):
    async def scenario():
        s = make_session(Options(max_retries=1))
        s.conversation.add("user", "I want to practise ordering coffee.")
        s.conversation.add("assistant", "What would you like to order?")
        connections = []

        class Manager:
            async def __aenter__(self):
                self.connection = FakeConnection()
                self.connection.events.put_nowait(event("session.updated"))
                connections.append(self.connection)
                return self.connection

            async def __aexit__(self, *_):
                pass

        provider = SimpleNamespace(
            client=SimpleNamespace(
                realtime=SimpleNamespace(connect=lambda **_: Manager())
            )
        )
        engine = LiveEngine(s, provider)
        listened = 0

        async def listen(active_engine, rate):
            nonlocal listened
            assert rate == 24000
            listened += 1
            try:
                if listened == 1:
                    raise AppError("connection dropped", retryable=True)
                s.ending = True
            finally:
                await active_engine.stop()

        original_sleep = asyncio.sleep

        async def fast_sleep(_seconds):
            await original_sleep(0)

        monkeypatch.setattr("practice_assistant.live.asyncio.sleep", fast_sleep)
        s.listen = listen
        await engine.run()
        assert listened == 2 and len(connections) == 2
        for c in connections:
            seed = c.conversation.item.create.calls
            assert seed[0]["item"]["content"][0]["text"].startswith("I want")
            assert len(seed) == 2
        assert len(asyncio.all_tasks()) == 1

    asyncio.run(scenario())


def test_live_retry_exhaustion_and_permanent_errors(monkeypatch):
    async def scenario():
        for retryable, expected in ((True, 3), (False, 1)):
            s = make_session(Options(max_retries=2))
            attempts = 0

            class FailedManager:
                retryable_failure = retryable

                async def __aenter__(self):
                    nonlocal attempts
                    attempts += 1
                    raise AppError("failed connect", retryable=self.retryable_failure)

                async def __aexit__(self, *_):
                    pass

            provider = SimpleNamespace(
                client=SimpleNamespace(
                    realtime=SimpleNamespace(connect=lambda **_: FailedManager())
                )
            )
            engine = LiveEngine(s, provider)
            with pytest.raises(AppError, match="failed connect"):
                await engine.run()
            assert attempts == expected

    async def no_sleep(_):
        pass

    monkeypatch.setattr("practice_assistant.live.asyncio.sleep", no_sleep)
    asyncio.run(scenario())


def test_late_transcript_cannot_start_response_during_new_speech():
    async def scenario():
        s, engine = live_engine()
        await engine.audio_event(utterance())
        await engine.interrupt(time.perf_counter())
        await engine.handle(event("input_audio_buffer.committed", item_id="old"))
        await engine.handle(
            event(
                "conversation.item.input_audio_transcription.completed",
                item_id="old",
                transcript="end session",
            )
        )
        assert not engine.goodbye_requested
        assert not engine.connection.response.create.calls
        assert s.timing.interrupted

    asyncio.run(scenario())


def test_live_late_commit_is_not_paired_with_new_timing():
    async def scenario():
        s, engine = live_engine()
        await engine.audio_event(utterance())
        await engine.interrupt(time.perf_counter())
        await engine.audio_event(utterance())
        await engine.handle(event("input_audio_buffer.committed", item_id="old"))
        await engine.handle(
            event(
                "conversation.item.input_audio_transcription.completed",
                item_id="old",
                transcript="goodbye",
            )
        )
        assert not engine.goodbye_requested and "transcript" not in s.timing.marks
        await engine.handle(event("input_audio_buffer.committed", item_id="new"))
        await engine.handle(
            event(
                "conversation.item.input_audio_transcription.completed",
                item_id="new",
                transcript="level C1",
            )
        )
        assert s.conversation.settings.level == "C1"

    asyncio.run(scenario())


def test_live_generation_guard_after_settings_update():
    async def scenario():
        s, engine = live_engine()
        await start_live_turn(engine)
        previous = engine.generation
        await engine.interrupt(time.perf_counter())
        count = len(engine.connection.response.create.calls)
        await engine.request_response(expected_generation=previous)
        assert len(engine.connection.response.create.calls) == count
        assert not s.generating

    asyncio.run(scenario())


def test_cascade_partial_generated_sentence_is_not_stored():
    async def scenario():
        s = make_session()

        class BlockingProvider(FakeProvider):
            async def speech(self, _text, _settings):
                yield bytes(960)
                await asyncio.sleep(100)

        engine = CascadeEngine(s, BlockingProvider())
        await engine.audio_event(utterance())
        while s.playback.pending == 0:
            await asyncio.sleep(0.001)
        s.playback.callback(bytearray(960), 480, None, None)
        await engine.interrupt(time.perf_counter())
        assert not s.conversation.last_answer
        assert len(asyncio.all_tasks()) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("status,attempts", [(401, 1), (429, 2), (503, 2)])
def test_live_http_handshake_failure(status, attempts, monkeypatch):
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response

    async def scenario():
        s = make_session(Options(max_retries=1))
        count = 0

        class Manager:
            async def __aenter__(self):
                nonlocal count
                count += 1
                raise InvalidStatus(Response(status, "test", Headers()))

            async def __aexit__(self, *_):
                pass

        provider = SimpleNamespace(
            client=SimpleNamespace(
                realtime=SimpleNamespace(connect=lambda **_: Manager())
            )
        )
        with pytest.raises(AppError, match=str(status)):
            await LiveEngine(s, provider).run()
        assert count == attempts

    async def no_sleep(_):
        pass

    monkeypatch.setattr("practice_assistant.live.asyncio.sleep", no_sleep)
    asyncio.run(scenario())
