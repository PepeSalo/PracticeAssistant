from __future__ import annotations

import asyncio
import io
import json
import wave
from types import SimpleNamespace

import pytest
from conftest import echo_signal, signal

from practice_assistant.audio_io import AudioIO, Playback, wav_bytes
from practice_assistant.config import Options, Settings
from practice_assistant.conversation import Conversation
from practice_assistant.metrics import TurnTiming
from practice_assistant.report import Reports, format_report
from practice_assistant.resilience import AppError
from practice_assistant.session import Session
from practice_assistant.vad import AudioEvent, EndpointDetector


def test_vad_start_end_and_hysteresis():
    vad = EndpointDetector(16000, silence_ms=400, threshold=500)
    events = []
    for i in range(40):
        events.extend(vad.feed(signal() if i < 12 else signal(0), (i + 1) * 0.02))
    assert [e.kind for e in events].count("start") == 1
    ends = [e for e in events if e.kind == "end"]
    assert len(ends) == 1
    assert ends[0].speech_end == pytest.approx(0.24)
    assert ends[0].at - ends[0].speech_end == pytest.approx(0.4)
    assert len(ends[0].pcm) < 16000 * 2


def test_vad_auto_noise_and_short_clicks():
    vad = EndpointDetector(16000)
    for i in range(100):
        assert not vad.feed(signal(30, noise=True, seed=i), i * 0.02)
    assert vad.threshold == 150
    for i in range(3):
        vad.feed(signal(), 2 + i * 0.02)
    events = []
    for i in range(40):
        events.extend(vad.feed(signal(0), 2.1 + i * 0.02))
    assert any(e.kind == "discard" for e in events)
    assert not any(e.kind == "end" for e in events)


def test_vad_max_length_and_bad_pcm():
    vad = EndpointDetector(16000, threshold=500, max_seconds=0.3)
    events = []
    for i in range(20):
        events.extend(vad.feed(signal(), i * 0.02))
    assert any(e.kind == "end" for e in events)
    with pytest.raises(ValueError):
        vad.feed(b"x", 1)
    assert vad.feed(b"", 1) == []
    assert echo_signal(signal())[:640] == bytes(640)


def test_playback_bounds_partial_and_interrupt():
    first = []
    silent = []
    p = Playback(
        rate=100,
        max_seconds=0.1,
        on_first=lambda k, t: first.append(k),
        on_silent=silent.append,
    )
    assert p.enqueue("a", bytes(20))
    assert not p.enqueue("b", bytes(2))
    out = bytearray(10)
    p.callback(out, 5, None, None)
    assert first == ["a"] and p.played_ms("a") == 50
    assert not p.complete("a")
    assert p.interrupt() == {"a": 5}
    p.callback(out, 5, None, None)
    assert out == bytes(10) and len(silent) == 1
    with pytest.raises(AppError):
        p.enqueue("b", b"x")
    with pytest.raises(AppError):
        p.enqueue("b", bytes(22))


def test_playback_timeout():
    async def scenario():
        p = Playback(rate=100, max_seconds=0.1)
        p.enqueue("a", bytes(20))
        with pytest.raises(AppError, match="stalled"):
            await p.put("b", bytes(2), 0.01)
        with pytest.raises(AppError, match="timed out"):
            await p.drain(0.01)

    asyncio.run(scenario())


def test_wav_format():
    pcm = signal()
    with wave.open(io.BytesIO(wav_bytes(pcm, 16000)), "rb") as reader:
        assert reader.getframerate() == 16000
        assert reader.getsampwidth() == 2 and reader.getnchannels() == 1
        assert reader.readframes(reader.getnframes()) == pcm
    with pytest.raises(AppError):
        wav_bytes(b"x", 16000)


def test_audio_startup_cleanup_and_status():
    async def scenario():
        calls = []

        class Stream:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                calls.append(self)

            def start(self):
                pass

            def stop(self):
                self.stopped = True

            def close(self):
                self.closed = True

        backend = SimpleNamespace(
            PortAudioError=RuntimeError,
            check_input_settings=lambda **_: None,
            check_output_settings=lambda **_: None,
            RawInputStream=Stream,
            RawOutputStream=Stream,
        )
        audio = AudioIO(16000, Playback(), backend=backend)
        audio.start()
        calls[1].kwargs["callback"](bytes(640), 320, None, None)
        pcm, _ = await audio.read()
        assert len(pcm) == 640
        calls[1].kwargs["callback"](bytes(640), 320, None, "overflow")
        with pytest.raises(AppError, match="Microphone"):
            await audio.read()
        audio.close()
        audio.close()
        assert all(s.closed and s.stopped for s in calls)

        def invalid(**_):
            raise RuntimeError("Unsupported rate")

        backend.check_input_settings = invalid
        with pytest.raises(AppError, match="16000/24000"):
            AudioIO(16000, Playback(), backend=backend).start()

    asyncio.run(scenario())


def test_callback_cancelled_read_does_not_leave_tasks():
    async def scenario():
        audio = AudioIO(16000, Playback())
        audio.error = asyncio.get_running_loop().create_future()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(audio.read(), 0.01)
        assert len(asyncio.all_tasks()) == 1
        audio.close()

    asyncio.run(scenario())


def measured(turn=1, engine="cascade", total=1.0):
    timing = TurnTiming(turn, engine, 0, 0.4)
    for name, at in (
        ("transcript", 0.6),
        ("token", 0.8),
        ("audio", 0.9),
        ("playback", total),
        ("finished", total + 0.1),
    ):
        timing.mark(name, at)
    return timing


def test_metrics_report_five_runs_and_exclusions():
    turns = [measured(i, total=n) for i, n in enumerate((1.0, 2.0, 3.0, 4.0, 5.0), 1)]
    interrupted = measured(6, total=9)
    interrupted.interrupted = True
    command = measured(7, total=20)
    command.command = True
    report = format_report(turns + [interrupted, command], Settings(), [10, 20])
    assert "Median run: 3000.0 ms" in report
    assert "Fastest run: 1000.0 ms" in report
    assert "Slowest run: 5000.0 ms" in report
    assert "Successful real turns: 5" in report
    assert "warm proxy): 3500.0 ms" in report
    assert "Largest measured median component" in report
    assert "stop delay median: 15.0 ms" in report
    assert "Median run: 1500.0 ms" in format_report(turns[:2], Settings(), [])


def test_live_unavailable_and_incremental_privacy(tmp_path):
    reports = Reports(Settings(), tmp_path / "report.txt", tmp_path / "logs")
    reports.turns.append(measured(engine="live"))
    reports.save()
    text = reports.path.read_text()
    assert "INCOMPLETE" in text
    assert "Translation/LLM delay: unavailable" in text
    assert "native model's internal STT" in text
    reports.event("transcript", text="private words", turn=1)
    logged = json.loads((reports.log_dir / "events.jsonl").read_text())
    assert "text" not in logged
    assert logged["session_id"] == reports.session_id
    assert logged["timestamp"].endswith("+00:00")
    reports.log_transcripts = True
    reports.event("transcript", text="explicitly allowed")
    assert "explicitly allowed" in (reports.log_dir / "events.jsonl").read_text()
    with pytest.raises(AppError, match="Cannot save"):
        Reports(Settings(), tmp_path / "missing" / "report.txt", None)


def test_session_ignores_stale_callbacks_and_echo():
    s = Session(Conversation(Settings()), Options(), Reports(Settings(), None, None))
    s.new_turn(AudioEvent("end", 1, b"aa", 0.6), "cascade")
    s.register_playback("1:sentence")
    s.first_playback("old", 0.8)
    assert "playback" not in s.timing.marks
    s.first_playback("1:sentence", 1.2)
    assert s.timing.values()["Total end-of-speech-to-playback delay"] == pytest.approx(
        600
    )
    s.echo_delay = 0.2
    s.echo_answer = "Hello how are you?"
    assert s.echo_suspected("hello how are you")
    s.echo_delay = 0.7
    assert not s.echo_suspected("hello how are you")


def test_backpressure_cannot_reenqueue_interrupted_live_chunk():
    async def scenario():
        p = Playback(rate=100, max_seconds=0.1)
        p.enqueue("old", bytes(20))
        valid = True
        task = asyncio.create_task(p.put("late", bytes(10), 1, lambda: valid))
        await asyncio.sleep(0.001)
        valid = False
        p.interrupt()
        assert not await task
        assert p.pending == 0

    asyncio.run(scenario())


def test_session_half_duplex_and_recording_notice():
    async def scenario():
        settings = Settings()
        s = Session(
            Conversation(settings),
            Options(echo_guard="half-duplex", vad_threshold=500),
            Reports(settings, None, None),
        )
        messages = []
        s.output = messages.append

        class FakeAudio:
            def __init__(self):
                self.count = 0
                self.started = False
                self.closed = False

            def start(self):
                self.started = True

            async def read(self):
                assert self.started
                self.count += 1
                return signal(), self.count * 0.02

            def close(self):
                self.closed = True

        class FakeEngine:
            def __init__(self):
                self.events = []
                self.stopped = False

            async def check(self):
                if s.audio.count >= 10:
                    s.ending = True

            async def stop(self):
                self.stopped = True

            async def interrupt(self, _):
                pass

            async def audio_event(self, e):
                self.events.append(e)

            def echo_text(self):
                return ""

        s.audio = FakeAudio()
        s.generating = True
        engine = FakeEngine()
        await s.listen(engine, 16000)
        assert s.audio.closed and engine.stopped
        assert messages[0].startswith("recording")
        assert not engine.events

    asyncio.run(scenario())


def test_audio_queue_overflow_and_constructor_failure_clean_output():
    async def scenario():
        streams = []

        class Stream:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.closed = False
                streams.append(self)

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                self.closed = True

        backend = SimpleNamespace(
            PortAudioError=RuntimeError,
            check_input_settings=lambda **_: None,
            check_output_settings=lambda **_: None,
            RawInputStream=Stream,
            RawOutputStream=Stream,
        )
        audio = AudioIO(16000, Playback(), backend=backend)
        audio.start()
        callback = streams[1].kwargs["callback"]
        for _ in range(101):
            callback(bytes(640), 320, None, None)
        await asyncio.sleep(0)
        with pytest.raises(AppError, match="overflow"):
            await audio.read()
        audio.close()

        def fail(**_):
            raise RuntimeError("input failed")

        backend.RawInputStream = fail
        with pytest.raises(AppError):
            AudioIO(16000, Playback(), backend=backend).start()
        assert streams[-1].closed

    asyncio.run(scenario())


def test_report_failure_preserves_last_saved_report(tmp_path, monkeypatch):
    from pathlib import Path

    path = tmp_path / "report.txt"
    reports = Reports(Settings(), path, None)
    old = path.read_text()
    reports.turns.append(measured())

    def fail(_self, _target):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(AppError, match="Cannot save"):
        reports.save()
    assert path.read_text() == old
    assert list(tmp_path.iterdir()) == [path]
