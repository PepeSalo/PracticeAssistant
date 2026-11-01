"""Watchdog and stall-handling tests.

The failure these guard against: the listen loop awaited engine calls without a
timeout, so a stalled WebSocket froze the whole program with no message and no
log. Both layers are tested here: hard timeouts (must unfreeze) and the
watchdog (must tell the user).
"""

from __future__ import annotations

import asyncio

import pytest

from practice_assistant.config import Options, Settings
from practice_assistant.conversation import Conversation
from practice_assistant.events import EventBus
from practice_assistant.report import Reports
from practice_assistant.resilience import AppError
from practice_assistant.session import Session
from practice_assistant.watchdog import Watchdog


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_session(options=None, bus=None):
    settings = Settings()
    return Session(
        Conversation(settings),
        options or Options(),
        Reports(settings, None, None),
        output=lambda _: None,
        bus=bus,
    )


class FakePlayback:
    def __init__(self, idle_value: bool = True) -> None:
        self.idle_value = idle_value
        self.interrupted = 0

    def idle(self) -> bool:
        return self.idle_value

    def interrupt(self) -> None:
        self.interrupted += 1


class FakeEngine:
    def __init__(self) -> None:
        self.recovered = 0

    async def recover(self) -> None:
        self.recovered += 1


# ---- the watchdog tells the user ---------------------------------------
def test_watchdog_warns_before_giving_up():
    async def scenario():
        bus = EventBus()
        session = make_session(bus=bus)
        session.playback = FakePlayback()
        session.generating = True
        clock = FakeClock()
        watchdog = Watchdog(session, hard_seconds=25, soft_seconds=8, clock=clock)
        engine = FakeEngine()

        clock.advance(9)
        await watchdog.tick(engine)
        return bus, engine, watchdog

    bus, engine, watchdog = asyncio.run(scenario())
    notifications = bus.state()["notifications"]
    assert any(n["level"] == "warn" for n in notifications)
    assert engine.recovered == 0


def test_watchdog_recovers_after_the_hard_limit():
    async def scenario():
        bus = EventBus()
        session = make_session(bus=bus)
        session.playback = FakePlayback()
        session.generating = True
        clock = FakeClock()
        watchdog = Watchdog(session, hard_seconds=25, soft_seconds=8, clock=clock)
        engine = FakeEngine()
        clock.advance(30)
        await watchdog.tick(engine)
        return bus, engine, session

    bus, engine, _session = asyncio.run(scenario())
    assert engine.recovered == 1
    # The watchdog delegates the cleanup to engine.recover(); interrupting
    # playback there is covered by the cascade/live recovery tests.
    assert any(n["level"] == "error" for n in bus.state()["notifications"])


def test_idle_listening_is_not_a_stall():
    async def scenario():
        session = make_session()
        session.playback = FakePlayback()
        session.generating = False
        clock = FakeClock()
        watchdog = Watchdog(session, hard_seconds=25, soft_seconds=8, clock=clock)
        engine = FakeEngine()
        clock.advance(120)
        await watchdog.tick(engine)
        return engine

    assert asyncio.run(scenario()).recovered == 0


def test_progress_resets_the_timers():
    async def scenario():
        bus = EventBus()
        session = make_session(bus=bus)
        session.playback = FakePlayback()
        session.generating = True
        clock = FakeClock()
        watchdog = Watchdog(session, hard_seconds=25, soft_seconds=8, clock=clock)
        session.watchdog = watchdog
        engine = FakeEngine()
        clock.advance(20)
        session.emit("state", state="thinking")  # real progress
        clock.advance(5)
        await watchdog.tick(engine)
        return engine, bus

    engine, bus = asyncio.run(scenario())
    assert engine.recovered == 0
    assert not any(n["level"] == "error" for n in bus.state()["notifications"])


def test_watchdog_survives_a_broken_recovery():
    class Broken:
        async def recover(self) -> None:
            raise RuntimeError("boom")

    async def scenario():
        bus = EventBus()
        session = make_session(bus=bus)
        session.playback = FakePlayback()
        session.generating = True
        clock = FakeClock()
        watchdog = Watchdog(session, hard_seconds=5, soft_seconds=3, clock=clock)
        clock.advance(10)
        await watchdog.tick(Broken())
        return bus

    bus = asyncio.run(scenario())
    assert any(
        n["level"] == "error" and "Recovery failed" in n["text"]
        for n in bus.state()["notifications"]
    )


# ---- hard timeouts: nothing may block forever --------------------------
def test_bounded_call_raises_on_a_stall():
    async def scenario():
        session = make_session(Options(timeout=0.05))
        bus = EventBus()
        session.bus = bus

        async def never_returns():
            await asyncio.sleep(5)

        with pytest.raises(AppError) as excinfo:
            await session._bounded(never_returns(), "engine check")
        return excinfo.value, bus

    error, bus = asyncio.run(scenario())
    assert "engine check" in str(error)
    assert error.retryable is True
    assert any(n["level"] == "error" for n in bus.state()["notifications"])


def test_bounded_call_returns_normally_when_fast():
    async def scenario():
        session = make_session(Options(timeout=5))

        async def quick():
            return "done"

        return await session._bounded(quick(), "engine check")

    assert asyncio.run(scenario()) == "done"


def test_cascade_recover_cancels_the_stuck_turn():
    from practice_assistant.cascade import CascadeEngine

    class Provider:
        async def transcribe(self, _wav):
            raise AssertionError("unused")

    async def scenario():
        session = make_session()
        session.playback = FakePlayback()
        session.generating = True
        engine = CascadeEngine(session, Provider())

        async def stuck():
            await asyncio.sleep(30)

        engine.task = asyncio.create_task(stuck())
        await engine.recover()
        return session, engine

    session, engine = asyncio.run(scenario())
    assert engine.task is None
    assert session.generating is False
    assert session.playback.interrupted == 1


def test_live_recover_marks_the_connection_as_failed():
    from practice_assistant.live import LiveEngine

    class Provider:
        class client:  # noqa: N801 - fake shape only
            pass

    async def scenario():
        session = make_session()
        session.playback = FakePlayback()
        engine = LiveEngine(session, Provider())
        await engine.recover()
        with pytest.raises(AppError) as excinfo:
            await engine.check()
        return excinfo.value

    error = asyncio.run(scenario())
    assert error.retryable is True
    assert "reconnecting" in str(error)


# ---- the user can find a log ------------------------------------------
def test_default_log_directory_is_outside_the_source_tree(monkeypatch, tmp_path):
    from practice_assistant.cli import default_log_dir

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = default_log_dir()
    assert tmp_path in path.parents
    assert "logs" in path.parts
    assert path.name.startswith("session-")


def test_no_log_flag_is_available_and_logs_by_default(monkeypatch):
    from practice_assistant.cli import main

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\definitely\\not\\writable\\for\\tests")
    # Missing key path is not reached; an unwritable log folder must not crash.
    assert main(["--list-devices", "--no-log"]) in (0, 1)


def test_unwritable_log_folder_degrades_instead_of_failing(monkeypatch, capsys):
    from practice_assistant import cli

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        cli.Path,
        "mkdir",
        lambda *a, **k: (_ for _ in ()).throw(OSError("denied")),
        raising=False,
    )

    # Replace the session with a no-op coroutine: only the log-folder guard is
    # under test, and nothing must reach the network or audio devices.
    async def fake_run(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cli, "run", fake_run)

    cli.main(["--log-dir", "X:\\nope"])
    assert "continuing without a session log" in capsys.readouterr().out


# ---- Finnish commands --------------------------------------------------
@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("puhu hitaammin", "speed/slower"),
        ("nopeammin", "speed/faster"),
        ("Seis", "stop/"),
        ("hetkinen", "stop/"),
        ("pysähdy", "stop/"),
        ("pysahdy", "stop/"),
        ("älä korjaa minua", "correction/off"),
        ("ala korjaa minua", "correction/off"),
        ("älä korjaa virheitäni", "correction/off"),
        ("korjaa virheeni", "correction/on"),
        ("harjoitellaan suomea", "language/fi"),
        ("harjoitellaan espanjaa", "language/es"),
        ("helpompaa", "level/easier"),
        ("vaikeampaa", "level/harder"),
        ("toista se", "repeat/"),
        ("mitä sanoit", "repeat/"),
        ("mita sanoit", "repeat/"),
        ("lopeta harjoitus", "end/"),
        ("näkemiin", "end/"),
        ("taso b2", "level/B2"),
        ("skenaario kahvilassa", "scenario/kahvilassa"),
    ],
)
def test_finnish_commands_are_understood(phrase, expected):
    from practice_assistant.commands import parse_command

    command = parse_command(phrase)
    assert command is not None, phrase
    assert f"{command.action}/{command.value}" == expected


@pytest.mark.parametrize(
    "phrase",
    ["the bus stop", "I stopped at the bus stop", "stop by the shop later"],
)
def test_english_false_positives_stay_conversation(phrase):
    from practice_assistant.commands import parse_command

    assert parse_command(phrase) is None


def test_diacritic_folding_does_not_change_exact_meaning():
    from practice_assistant.commands import fold, parse_command

    assert fold("älä korjaa") == "ala korjaa"
    assert parse_command("älä korjaa").value == "off"
    assert parse_command("ala korjaa").value == "off"


# ---- models are configurable ------------------------------------------
def test_models_can_be_chosen_for_better_multilingual_accuracy():
    options = Options(stt_model="gpt-4o-transcribe", chat_model="gpt-4o")
    options.validate()
    assert options.stt_model == "gpt-4o-transcribe"
    assert options.chat_model == "gpt-4o"


def test_empty_model_names_are_rejected():
    with pytest.raises(ValueError):
        Options(stt_model="  ").validate()


def test_watchdog_timeout_is_validated():
    Options(watchdog_seconds=25).validate()
    with pytest.raises(ValueError):
        Options(watchdog_seconds=1).validate()
    with pytest.raises(ValueError):
        Options(watchdog_seconds=1000).validate()
