from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from practice_assistant.cli import main
from practice_assistant.commands import (
    Command,
    apply_command,
    extract_json,
    parse_command,
    tool_command,
)
from practice_assistant.config import Options, Settings, language
from practice_assistant.conversation import Conversation
from practice_assistant.evaluation import word_error_rate
from practice_assistant.resilience import AppError, backoff, retry, retry_after


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("Please stop!", "stop"),
        ("wait", "stop"),
        ("bus stop", None),
        ("I said goodbye to my friend", None),
        ("repeat that", "repeat"),
        ("say it again please", "repeat"),
        ("toista", "repeat"),
        ("puhu hitaammin", "speed"),
        ("speak faster", "speed"),
        ("make it easier", "level"),
        ("level B2", "level"),
        ("taso a1", "level"),
        ("Let's practise Spanish!", "language"),
        ("harjoitellaan suomi", "language"),
        ("role-play ordering at a café", "scenario"),
        ("työhaastattelu", "scenario"),
        ("correct my mistakes", "correction"),
        ("don't correct me", "correction"),
        ("älä korjaa minua", "correction"),
        ("end session", "end"),
        ("näkemiin", "end"),
        ("what does hello mean", None),
        ("how do you say stop", None),
        ("practice language unknown", None),
    ],
)
def test_commands(text, action):
    command = parse_command(text)
    assert (command.action if command else None) == action
    assert parse_command(text, final=False) is None


@pytest.mark.parametrize(
    ("command", "field", "expected"),
    [
        (Command("level", "easier"), "level", "A2"),
        (Command("level", "harder"), "level", "B2"),
        (Command("level", "C1"), "level", "C1"),
        (Command("language", "es"), "practice_language", "es"),
        (Command("learner_language", "fi"), "learner_language", "fi"),
        (Command("correction", "off"), "correction", False),
        (Command("speed", "slower"), "speed", 0.85),
        (Command("scenario", "café"), "scenario", "café"),
    ],
)
def test_apply(command, field, expected):
    settings = Settings()
    assert apply_command(command, settings)
    assert getattr(settings, field) == expected


@pytest.mark.parametrize(
    "command",
    [
        Command("unknown"),
        Command("level", "Z4"),
        Command("language", "english"),
        Command("speed", "4"),
        Command("correction", "yes"),
        Command("scenario", ""),
    ],
)
def test_invalid_commands_leave_settings_unchanged(command):
    settings = Settings()
    old = replace(settings)
    with pytest.raises(ValueError):
        apply_command(command, settings)
    assert old == settings


@pytest.mark.parametrize(
    "raw",
    [
        '{"action":"repeat","value":""}',
        '```json\n{"action":"repeat","value":""}\n```',
        '```\n{"action":"repeat","value":""}\n```',
    ],
)
def test_json_tool_formats(raw):
    assert tool_command(raw) == Command("repeat")
    assert extract_json(raw)["action"] == "repeat"


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "```oops",
        '{"action":"end"}',
        '{"action":3,"value":""}',
        '{"action":"end","value":"","extra":true}',
        '{"action":"unknown","value":""}',
        "{bad json}",
    ],
)
def test_invalid_tool_schema(raw):
    with pytest.raises(ValueError):
        tool_command(raw)


def test_conversation_bounded_and_prompt():
    c = Conversation(Settings(practice_language="fi", level="C1"), max_messages=4)
    for n in range(20):
        c.add("user" if n % 2 == 0 else "assistant", f"Message {n}")
    assert len(c.history) == 4
    assert "Message 0" in c.rolling_summary
    assert "Practice language: fi" in c.prompt() and "C1" in c.prompt()
    assert c.last_answer == "Message 19"
    c.record_learning('{"vocabulary":["kiitos"],"mistakes":["word order"]}')
    c.record_learning('{"vocabulary":["kiitos"],"mistakes":[]}')
    assert "kiitos" in c.summary() and "word order" in c.summary()
    assert c.vocabulary["kiitos"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"vocabulary": [], "mistakes": []},
        {"vocabulary": ["hello"], "mistakes": ["tense"]},
    ],
)
def test_learning_valid(payload):
    assert Conversation(Settings()).record_learning(json.dumps(payload))


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        '{"vocabulary":[""],"mistakes":[]}',
        '{"vocabulary":{},"mistakes":[]}',
        '{"vocabulary":[1],"mistakes":[]}',
        '{"vocabulary":[],"mistakes":[],"extra":1}',
    ],
)
def test_learning_invalid(raw):
    with pytest.raises(ValueError):
        Conversation(Settings()).record_learning(raw)


@pytest.mark.parametrize(
    "changes",
    [
        {"benchmark_runs": 4},
        {"silence_ms": 10},
        {"timeout": 0},
        {"max_retries": -1},
        {"vad_threshold": -1},
        {"log_transcripts": True},
        {"engine": "bad"},
        {"voice": "bad"},
        {"echo_guard": "bad"},
        {"live_max_output_tokens": 350},
        {"live_max_output_tokens": 4097},
    ],
)
def test_options_invalid(changes):
    with pytest.raises(ValueError):
        replace(Options(), **changes).validate()


def test_same_language_is_allowed_and_auto_is_not_a_practice_language():
    Settings(practice_language="en", learner_language="en").validate()
    with pytest.raises(ValueError):
        language("auto")


def test_cli_help_and_missing_key(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "practice-language" in capsys.readouterr().out
    assert main([]) == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().out
    assert main(["--benchmark-runs", "4"]) == 1


def test_retry_and_exhaustion():
    async def scenario():
        calls = 0
        sleeps = []

        async def operation():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise AppError("retry", retryable=True, retry_after=2)
            return 42

        async def sleep(delay):
            sleeps.append(delay)

        assert await retry(operation, 2, lambda _: None, sleep) == 42
        assert calls == 3 and sleeps == [2, 2]
        calls = 0
        with pytest.raises(AppError):
            await retry(operation, 1, lambda _: None, sleep)
        assert calls == 2

        async def permanent():
            raise AppError("401")

        with pytest.raises(AppError):
            await retry(permanent, 5, lambda _: None, sleep)

    asyncio.run(scenario())


def test_backoff_and_retry_after():
    assert backoff(0, lambda: 0) == 0.5
    assert backoff(20, lambda: 1) == 8.25
    assert retry_after("3") == 3
    assert retry_after("invalid") is None
    assert retry_after("1000") == 60


def test_wer():
    assert word_error_rate("Hello, world!", "hello world") == 0
    assert word_error_rate("one two", "one three") == 0.5
    assert word_error_rate("one two", "") == 1
    assert word_error_rate("one", "one two three") == 2
    with pytest.raises(ValueError):
        word_error_rate("", "something")
