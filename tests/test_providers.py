from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx2
import pytest
from conftest import AsyncItems, AsyncMethod
from openai import APIConnectionError, APIStatusError

from practice_assistant.config import Options, Settings
from practice_assistant.conversation import Conversation
from practice_assistant.providers import OpenAIProvider, get_provider, provider_error
from practice_assistant.resilience import AppError


def client_with(transcript="Hello", chunks=None, audio=None):
    stream = AsyncItems(chunks or [])
    speech_stream = AsyncItems([bytes(1024)] if audio is None else audio)
    client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(
                create=AsyncMethod(SimpleNamespace(text=transcript))
            ),
            speech=SimpleNamespace(
                with_streaming_response=SimpleNamespace(
                    create=lambda **_: speech_stream
                )
            ),
        ),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMethod(stream))),
        close=AsyncMethod(),
    )
    return client, stream, speech_stream


def chunk(text="", tools=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=tools or []))
        ]
    )


def status_error(status):
    return APIStatusError(
        "private server body not displayed",
        response=httpx2.Response(
            status,
            headers={"Retry-After": "0"},
            request=httpx2.Request("POST", "https://example.test"),
        ),
        body=None,
    )


def test_missing_key_and_unknown_provider():
    with pytest.raises(AppError, match="OPENAI_API_KEY"):
        OpenAIProvider("", Options())
    with pytest.raises(AppError, match="Unknown provider"):
        get_provider("other", "fake", Options())


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_status_error_classification(status):
    error = provider_error(status_error(status))
    assert error.retryable == (status == 429 or status >= 500)
    assert str(status) in str(error)
    assert "private" not in str(error)


def test_transcribe_success_and_retry():
    async def scenario():
        client, _, _ = client_with()
        provider = OpenAIProvider("fake", Options(max_retries=1), client=client)
        assert await provider.transcribe(b"wav") == "Hello"
        client.audio.transcriptions.create.errors.append(status_error(429))
        assert await provider.transcribe(b"wav") == "Hello"
        client.audio.transcriptions.create.errors.append(status_error(401))
        with pytest.raises(AppError, match="401"):
            await provider.transcribe(b"wav")
        await provider.close()
        assert len(client.close.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["", " ", None, 4])
def test_empty_transcript(text):
    async def scenario():
        client, _, _ = client_with(transcript=text)
        provider = OpenAIProvider("fake", Options(), client=client)
        with pytest.raises(AppError, match="usable text"):
            await provider.transcribe(b"wav")
        with pytest.raises(AppError, match="non-empty"):
            await provider.transcribe(b"")

    asyncio.run(scenario())


def test_streamed_reply_text_and_tool_fragments():
    async def scenario():
        calls = [
            SimpleNamespace(
                index=0,
                id="call_1",
                function=SimpleNamespace(
                    name="control", arguments='{"action":"language",'
                ),
            ),
            SimpleNamespace(
                index=0,
                id=None,
                function=SimpleNamespace(name=None, arguments='"value":"fi"}'),
            ),
        ]
        client, stream, _ = client_with(
            chunks=[
                chunk("Hello "),
                chunk("there."),
                chunk(tools=[calls[0]]),
                chunk(tools=[calls[1]]),
            ]
        )
        p = OpenAIProvider("fake", Options(), client=client)
        events = [e async for e in p.reply(Conversation(Settings()))]
        assert [e.text for e in events[:2]] == ["Hello ", "there."]
        assert events[-1].name == "control"
        assert events[-1].text == '{"action":"language","value":"fi"}'
        assert stream.closed
        request = client.chat.completions.create.calls[0]
        assert request["stream"] and request["tools"]

    asyncio.run(scenario())


def test_reply_failure_closes_stream_no_replay():
    async def scenario():
        client, stream, _ = client_with(
            chunks=[
                chunk("hello"),
                APIConnectionError(
                    request=httpx2.Request("POST", "https://example.test")
                ),
            ]
        )
        p = OpenAIProvider("fake", Options(max_retries=2), client=client)
        with pytest.raises(AppError, match="network"):
            _ = [e async for e in p.reply(Conversation(Settings()))]
        assert stream.closed
        assert len(client.chat.completions.create.calls) == 1

    asyncio.run(scenario())


def test_empty_reply():
    async def scenario():
        client, stream, _ = client_with()
        with pytest.raises(AppError, match="empty response"):
            _ = [
                e
                async for e in OpenAIProvider("fake", Options(), client=client).reply(
                    Conversation(Settings())
                )
            ]
        assert stream.closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("audio", "expected", "error"),
    [
        ([b"\x01", b"\x02\x03", b"\x04"], b"\x01\x02\x03\x04", None),
        ([], b"", "empty audio"),
        ([b"x"], b"", "incomplete PCM16"),
    ],
)
def test_pcm_first_complete_sample_and_validation(audio, expected, error):
    async def scenario():
        client, _, stream = client_with(audio=audio)
        p = OpenAIProvider("fake", Options(), client=client)
        if error:
            with pytest.raises(AppError, match=error):
                _ = [pcm async for pcm in p.speech("hello", Settings())]
        else:
            assert (
                b"".join([pcm async for pcm in p.speech("hello", Settings())])
                == expected
            )
        assert stream.closed
        with pytest.raises(AppError, match="non-empty"):
            _ = [pcm async for pcm in p.speech("", Settings())]

    asyncio.run(scenario())


def test_tts_cancellation_closes_stream():
    async def scenario():
        client, _, stream = client_with(audio=[bytes(1024)] * 3)
        p = OpenAIProvider("fake", Options(), client=client)
        generator = p.speech("hello", Settings())
        assert len(await anext(generator)) == 1024
        await generator.aclose()
        assert stream.closed

    asyncio.run(scenario())


def test_malformed_provider_shapes_close_streams():
    async def scenario():
        client, stream, _ = client_with(chunks=[SimpleNamespace(choices="invalid")])
        client.audio.transcriptions.create.result = SimpleNamespace()
        provider = OpenAIProvider("fake", Options(), client=client)
        with pytest.raises(AppError, match="usable text"):
            await provider.transcribe(b"wav")
        with pytest.raises(AppError, match="response shape"):
            _ = [e async for e in provider.reply(Conversation(Settings()))]
        assert stream.closed

    asyncio.run(scenario())


def test_tts_does_not_retry_or_duplicate_after_first_audio():
    async def scenario():
        client, _, stream = client_with(
            audio=[
                bytes(1024),
                APIConnectionError(
                    request=httpx2.Request("POST", "https://example.test")
                ),
            ]
        )
        provider = OpenAIProvider("fake", Options(max_retries=3), client=client)
        chunks = []
        with pytest.raises(AppError, match="network"):
            async for pcm in provider.speech("hello", Settings()):
                chunks.append(pcm)
        assert chunks == [bytes(1024)] and stream.closed

    asyncio.run(scenario())
