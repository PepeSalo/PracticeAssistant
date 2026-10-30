from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam

from .commands import TOOLS
from .config import Options, Settings
from .conversation import Conversation
from .resilience import AppError, backoff, retry, retry_after


def provider_error(exc: APIConnectionError | APIStatusError) -> AppError:
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        detail = (
            "Check OPENAI_API_KEY and model access."
            if status in (401, 403)
            else (
                "Check model/settings."
                if status < 500 and status != 429
                else "Service is temporarily unavailable or rate limited."
            )
        )
        return AppError(
            f"OpenAI HTTP {status}. {detail}",
            retryable=status == 429 or status >= 500,
            retry_after=retry_after(exc.response.headers.get("Retry-After")),
        )
    return AppError(
        "OpenAI network/timeout error; check your connection.", retryable=True
    )


@dataclass(frozen=True)
class ReplyEvent:
    kind: str
    text: str = ""
    name: str = ""
    call_id: str = ""


def chat_tools() -> list[ChatCompletionToolUnionParam]:
    return [
        {
            "type": "function",
            "function": {
                "name": str(tool["name"]),
                "description": str(tool["description"]),
                "parameters": tool["parameters"],
            },
        }
        for tool in TOOLS
    ]


class OpenAIProvider:
    def __init__(
        self,
        api_key: str,
        options: Options,
        notify: Callable[[str], None] = print,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not api_key.strip():
            raise AppError("Set OPENAI_API_KEY before starting language practice.")
        self.options = options
        self.notify = notify
        self.client = client or AsyncOpenAI(
            api_key=api_key, timeout=options.timeout, max_retries=0
        )

    async def close(self) -> None:
        await self.client.close()

    async def transcribe(self, wav: bytes) -> str:
        if not wav:
            raise AppError("STT requires non-empty WAV audio.")

        async def request() -> str:
            try:
                response = await self.client.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe",
                    file=("utterance.wav", wav, "audio/wav"),
                )
            except (APIConnectionError, APIStatusError) as exc:
                raise provider_error(exc) from exc
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise AppError("Transcription returned no usable text.")
            return text.strip()

        return await retry(request, self.options.max_retries, self.notify)

    async def reply(
        self,
        conversation: Conversation,
        extra: list[ChatCompletionMessageParam] | None = None,
    ) -> AsyncIterator[ReplyEvent]:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": conversation.prompt()}
        ]
        for message in conversation.history:
            if message["role"] == "user":
                messages.append({"role": "user", "content": message["content"]})
            else:
                messages.append({"role": "assistant", "content": message["content"]})
        messages.extend(extra or [])

        async def open_stream():
            try:
                return await self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    stream=True,
                    tools=chat_tools(),
                    max_completion_tokens=350,
                )
            except (APIConnectionError, APIStatusError) as exc:
                raise provider_error(exc) from exc

        stream = await retry(open_stream, self.options.max_retries, self.notify)
        calls: dict[int, dict[str, str]] = {}
        text_length = 0
        async with stream:
            try:
                async for chunk in stream:
                    choices = getattr(chunk, "choices", None)
                    if not isinstance(choices, list):
                        raise AppError("Chat returned an unexpected response shape.")
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    if delta is None:
                        raise AppError("Chat returned an unexpected delta shape.")
                    if delta.content:
                        if not isinstance(delta.content, str):
                            raise AppError("Chat returned malformed text.")
                        text_length += len(delta.content)
                        if text_length > 4000:
                            raise AppError("Chat reply exceeds the safe text limit.")
                        yield ReplyEvent("text", delta.content)
                    for call in delta.tool_calls or []:
                        if not isinstance(call.index, int) or (
                            call.id is not None and not isinstance(call.id, str)
                        ):
                            raise AppError("Chat returned malformed tool identifiers.")
                        if call.index not in calls and len(calls) >= 8:
                            raise AppError("Too many tool calls in one response.")
                        state = calls.setdefault(
                            call.index, {"name": "", "id": "", "arguments": ""}
                        )
                        state["id"] += call.id or ""
                        if call.function:
                            if any(
                                value is not None and not isinstance(value, str)
                                for value in (
                                    call.function.name,
                                    call.function.arguments,
                                )
                            ):
                                raise AppError(
                                    "Chat returned malformed tool arguments."
                                )
                            state["name"] += call.function.name or ""
                            state["arguments"] += call.function.arguments or ""
                            if len(state["arguments"]) > 4000:
                                raise AppError("Tool arguments exceed the safe limit.")
                for state in calls.values():
                    if not state["id"] or not state["name"]:
                        raise AppError("Chat returned malformed tool calls.")
                    yield ReplyEvent(
                        "tool", state["arguments"], state["name"], state["id"]
                    )
                if not text_length and not calls:
                    raise AppError("Chat returned an empty response.")
            except (APIConnectionError, APIStatusError) as exc:
                raise provider_error(exc) from exc

    async def speech(self, text: str, settings: Settings) -> AsyncIterator[bytes]:
        if not text.strip():
            raise AppError("TTS requires non-empty text.")
        emitted = False
        for attempt in range(self.options.max_retries + 1):
            carry = b""
            try:
                async with self.client.audio.speech.with_streaming_response.create(
                    model="gpt-4o-mini-tts",
                    voice=self.options.voice,
                    input=text,
                    response_format="pcm",
                    stream_format="audio",
                    speed=settings.speed,
                    instructions=f"Speak naturally in {settings.practice_language}.",
                ) as response:
                    async for chunk in response.iter_bytes(chunk_size=1024):
                        if not isinstance(chunk, bytes):
                            raise AppError("TTS returned a non-binary audio payload.")
                        carry += chunk
                        size = len(carry) // 2 * 2
                        if size:
                            emitted = True
                            yield carry[:size]
                            carry = carry[size:]
                if carry:
                    raise AppError("TTS returned an incomplete PCM16 sample.")
                if not emitted:
                    raise AppError("TTS returned empty audio.")
                return
            except (APIConnectionError, APIStatusError) as exc:
                error = provider_error(exc)
                if (
                    emitted
                    or not error.retryable
                    or attempt == self.options.max_retries
                ):
                    raise error from exc
                delay = (
                    error.retry_after
                    if error.retry_after is not None
                    else backoff(attempt)
                )
                self.notify(f"{error} Retrying TTS in {delay:.2f}s.")
                await asyncio.sleep(delay)


def get_provider(
    name: str, api_key: str, options: Options, notify: Callable[[str], None] = print
) -> OpenAIProvider:
    if name != "openai":
        raise AppError(f"Unknown provider: {name}.")
    return OpenAIProvider(api_key, options, notify)
