from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from collections import deque

from openai import (
    APIConnectionError,
    APIStatusError,
    WebSocketConnectionClosedError,
    WebSocketQueueFullError,
)
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from openai.types.realtime import RealtimeServerEvent
from openai.types.realtime.realtime_function_tool_param import RealtimeFunctionToolParam
from websockets.exceptions import InvalidStatus, WebSocketException

from .commands import TOOLS, apply_command, parse_command, tool_command
from .events import settings_payload
from .providers import OpenAIProvider, provider_error
from .resilience import AppError, backoff, retry_after
from .session import Session
from .vad import AudioEvent


def live_tools() -> list[RealtimeFunctionToolParam]:
    return [
        {
            "type": "function",
            "name": str(t["name"]),
            "description": str(t["description"]),
            "parameters": t["parameters"],
        }
        for t in TOOLS
    ]


class LiveEngine:
    def __init__(self, session: Session, provider: OpenAIProvider) -> None:
        self.session = session
        self.provider = provider
        self.connection: AsyncRealtimeConnection | None = None
        self.receiver: asyncio.Task[None] | None = None
        self.finisher: asyncio.Task[None] | None = None
        self.response_id: str | None = None
        self.response_done = False
        self.generation = 0
        self.items: dict[str, int] = {}
        self.transcripts: dict[str, str] = {}
        self.completed_items: set[str] = set()
        self.groups: deque[list[str]] = deque()
        self.current_items: list[str] = []
        self.input_id: str | None = None
        self.pending_commits: deque[int] = deque()
        self.pending_at: float | None = None
        self.goodbye_requested = False
        self.tool_steps = 0

    async def run(self) -> None:
        s = self.session
        for attempt in range(s.options.max_retries + 1):
            try:
                async with self.provider.client.realtime.connect(
                    model=s.options.live_model,
                    max_retries=0,
                    max_queue_size=65536,
                    websocket_connection_options={
                        "open_timeout": s.options.timeout,
                        "max_queue": 32,
                    },
                ) as connection:
                    self.connection = connection
                    await self.configure()
                    deadline = time.perf_counter() + s.options.timeout
                    while True:
                        event = await asyncio.wait_for(
                            connection.recv(), max(0.01, deadline - time.perf_counter())
                        )
                        if event.type == "error":
                            raise AppError(
                                f"Realtime startup error: {event.error.code}."
                            )
                        if event.type == "session.updated":
                            break
                    await self.seed_history()
                    self.receiver = asyncio.create_task(self.receive())
                    await s.listen(self, 24000)
                    return
            except (APIConnectionError, APIStatusError) as exc:
                error = provider_error(exc)
            except InvalidStatus as exc:
                status = exc.response.status_code
                error = AppError(
                    f"Realtime WebSocket HTTP {status}; check key/model access "
                    "or service availability.",
                    retryable=status == 429 or status >= 500,
                    retry_after=retry_after(exc.response.headers.get("Retry-After")),
                )
            except WebSocketException as exc:
                error = AppError(
                    f"Realtime transport failure ({type(exc).__name__}).",
                    retryable=True,
                )
            except (WebSocketConnectionClosedError, OSError, TimeoutError) as exc:
                error = AppError(
                    f"Realtime connection lost ({type(exc).__name__}).", retryable=True
                )
            except WebSocketQueueFullError:
                error = AppError("Realtime outgoing queue is full; network stalled.")
            except AppError as exc:
                error = exc
            finally:
                self.connection = None
                self.receiver = None
                self.finisher = None
                s.audio = None
                s.generating = False
                self.pending_at = None
                self.response_id = None
                self.items.clear()
                self.transcripts.clear()
                self.completed_items.clear()
                self.current_items.clear()
                self.groups.clear()
                self.pending_commits.clear()
                self.input_id = None
            if not error.retryable or attempt == s.options.max_retries:
                s.reports.event(
                    "connection_failure",
                    reason=str(error),
                    retryable=error.retryable,
                    attempt=attempt + 1,
                    will_reconnect=False,
                )
                raise error
            delay = (
                error.retry_after if error.retry_after is not None else backoff(attempt)
            )
            s.output(
                f"{error} Connection lost, reconnecting in {delay:.2f}s "
                "with recent conversation context."
            )
            s.reports.event(
                "connection_failure",
                reason=str(error),
                retryable=error.retryable,
                attempt=attempt + 1,
                will_reconnect=True,
            )
            s.reports.event("reconnecting", attempt=attempt + 1, delay_seconds=delay)
            await asyncio.sleep(delay)

    async def configure(self) -> None:
        if self.connection is None:
            raise AppError("Realtime connection is not open.")
        s = self.session
        await self.connection.session.update(
            session={
                "type": "realtime",
                "instructions": s.conversation.prompt(),
                "output_modalities": ["audio"],
                "max_output_tokens": s.options.live_max_output_tokens,
                "tools": live_tools(),
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {"model": "gpt-4o-mini-transcribe"},
                        "turn_detection": None,
                        "noise_reduction": {"type": "near_field"},
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": s.options.voice,
                        "speed": s.conversation.settings.speed,
                    },
                },
            }
        )

    async def seed_history(self) -> None:
        if self.connection is None:
            raise AppError("Realtime connection is not open.")
        for index, message in enumerate(self.session.conversation.history):
            item_id = f"seed_{self.generation}_{index}"
            if message["role"] == "user":
                await self.connection.conversation.item.create(
                    item={
                        "id": item_id,
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": message["content"]}],
                    }
                )
            else:
                await self.connection.conversation.item.create(
                    item={
                        "id": item_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": message["content"]}
                        ],
                    }
                )
            if index % 2 == 0:
                self.groups.append([])
            self.groups[-1].append(item_id)

    async def request_response(
        self,
        instructions: str | None = None,
        *,
        no_tools: bool = False,
        expected_generation: int | None = None,
    ) -> None:
        if self.connection is None:
            raise AppError("Realtime connection is not open.")
        s = self.session
        if expected_generation is not None and expected_generation != self.generation:
            return
        s.generating = True
        self.response_done = False
        self.pending_at = time.perf_counter()
        await self.connection.response.create(
            response={
                "instructions": instructions or s.conversation.prompt(),
                "metadata": {"generation": str(self.generation)},
                **({"tool_choice": "none"} if no_tools else {}),
            }
        )
        s.output("Thinking...")

    async def audio_event(self, event: AudioEvent) -> None:
        if self.connection is None:
            raise AppError("Realtime connection is not open.")
        connection = self.connection
        if event.kind == "start":
            await connection.input_audio_buffer.clear()
        elif event.kind == "chunk":
            await connection.input_audio_buffer.append(
                audio=base64.b64encode(event.pcm).decode("ascii")
            )
        elif event.kind == "end":
            await self.interrupt(event.at, count=False)
            self.session.new_turn(event, "live")
            self.tool_steps = 0
            self.goodbye_requested = False
            self.pending_at = time.perf_counter()
            self.pending_commits.append(self.generation)
            await connection.input_audio_buffer.commit()
        elif event.kind == "discard":
            await connection.input_audio_buffer.clear()

    async def interrupt(self, at: float, *, count: bool = True) -> None:
        s = self.session
        awaiting_transcript = self.pending_at is not None
        self.generation += 1
        self.pending_at = None
        self.input_id = None
        finishing = self.finisher is not None and not self.finisher.done()
        if (
            not s.generating
            and s.playback.idle()
            and not awaiting_transcript
            and not finishing
        ):
            return
        s.output("Interrupted — listening.")
        if count:
            s.interrupted_at = at
        if s.timing:
            s.timing.interrupted = True
        s.playback.interrupt()
        if self.finisher and not self.finisher.done():
            self.finisher.cancel()
            await asyncio.gather(self.finisher, return_exceptions=True)
            self.finisher = None
        if self.connection:
            if self.response_id and not self.response_done:
                await self.connection.response.cancel(response_id=self.response_id)
            for item_id, content_index in self.items.items():
                await self.connection.conversation.item.truncate(
                    item_id=item_id,
                    content_index=content_index,
                    audio_end_ms=s.playback.played_ms(item_id),
                )
        # Server truncation removes unplayed text. Only fully submitted items enter
        # the local resume context; a partial transcript cannot be aligned reliably.
        heard = " ".join(
            text
            for key, text in self.transcripts.items()
            if key in self.completed_items and s.playback.complete(key)
        )
        if heard:
            s.conversation.add("assistant", heard)
        self.items.clear()
        self.transcripts.clear()
        self.completed_items.clear()
        self.response_id = None
        s.generating = False
        if self.current_items:
            self.groups.append(self.current_items)
            self.current_items = []
            await self.prune()
        s.reports.event("interrupted", at=at, text=heard)
        s.reports.save()

    async def receive(self) -> None:
        if self.connection is None:
            raise AppError("Realtime connection is not open.")
        async for event in self.connection:
            await self.handle(event)
        raise AppError("Realtime WebSocket closed.", retryable=True)

    async def handle(self, event: RealtimeServerEvent) -> None:
        connection = self.connection
        if connection is None:
            raise AppError("Realtime connection is not open.")
        s = self.session
        kind = event.type
        if kind == "error":
            code = event.error.code or "unspecified"
            raise AppError(
                f"Realtime server error: {code}.",
                retryable=code in ("server_error", "rate_limit_exceeded"),
            )
        if kind == "input_audio_buffer.committed":
            self.current_items.append(event.item_id)
            if (
                self.pending_commits
                and self.pending_commits.popleft() == self.generation
            ):
                self.input_id = event.item_id
            return
        if kind == "conversation.item.input_audio_transcription.failed":
            raise AppError("Realtime transcription failed; try again.", retryable=True)
        if kind == "conversation.item.input_audio_transcription.completed":
            if event.item_id != self.input_id or not s.timing or s.timing.interrupted:
                return
            self.pending_at = None
            generation = self.generation
            raw_transcript = getattr(event, "transcript", None)
            if not isinstance(raw_transcript, str) or not raw_transcript.strip():
                raise AppError("Realtime returned an empty transcript.", retryable=True)
            transcript = raw_transcript.strip()
            s.timing.mark("transcript")
            s.output(f"Heard: {transcript}")
            s.reports.event("transcript", turn=s.turn_number, text=transcript)
            s.emit(
                "transcript",
                turn=s.turn_number,
                text=transcript,
                engine="live",
            )
            if s.echo_suspected(transcript):
                s.timing.interrupted = True
                await connection.conversation.item.delete(item_id=event.item_id)
                self.current_items.remove(event.item_id)
                return
            command = parse_command(transcript)
            if command:
                s.timing.command = True
                confirmation = apply_command(command, s.conversation.settings)
                s.output(confirmation)
                s.emit("confirmation", text=confirmation, action=command.action)
                s.emit("session", **settings_payload(s.conversation.settings))
                self.goodbye_requested = command.action == "end"
                if command.action == "repeat":
                    confirmation = (
                        s.conversation.last_answer or "No answer to repeat yet."
                    )
                elif self.goodbye_requested:
                    confirmation = s.conversation.summary()
                await self.configure()
                await self.request_response(
                    s.conversation.prompt() + "\nSay only this confirmation in the "
                    f"learner's language: {confirmation}",
                    no_tools=True,
                    expected_generation=generation,
                )
            else:
                s.conversation.add("user", transcript)
                await self.request_response(expected_generation=generation)
            return
        if kind == "response.created":
            if (event.response.metadata or {}).get("generation") != str(
                self.generation
            ):
                await connection.response.cancel(response_id=event.response.id)
                return
            self.response_id = event.response.id
            return
        event_response_id = getattr(event, "response_id", None)
        if event_response_id is not None and event_response_id != self.response_id:
            return
        if kind == "response.output_item.added":
            self.current_items.append(event.item.id)
        elif kind == "response.output_audio.delta":
            if not isinstance(getattr(event, "delta", None), str):
                raise AppError("Realtime returned a non-text base64 payload.")
            try:
                pcm = base64.b64decode(event.delta, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise AppError("Realtime returned malformed base64 audio.") from exc
            if not pcm or len(pcm) % 2:
                raise AppError("Realtime returned empty/incomplete PCM16.")
            self.items[event.item_id] = event.content_index
            s.register_playback(event.item_id)
            if s.timing:
                s.timing.mark("audio")
            self.pending_at = time.perf_counter()
            generation = self.generation
            await s.playback.put(
                event.item_id,
                pcm,
                s.options.timeout,
                lambda: generation == self.generation,
            )
        elif kind == "response.output_audio_transcript.done":
            self.transcripts[event.item_id] = event.transcript[:4000]
        elif kind == "response.output_audio.done":
            self.completed_items.add(event.item_id)
        elif kind == "response.output_audio_transcript.delta":
            self.transcripts[event.item_id] = (
                self.transcripts.get(event.item_id, "") + event.delta
            )[:4000]
        elif kind == "response.done":
            if event.response.id != self.response_id:
                return
            generation = self.generation
            self.response_done = True
            self.pending_at = None
            details = getattr(event.response, "status_details", None)
            reason = getattr(details, "reason", None)
            failure = getattr(details, "error", None)
            failure_code = getattr(failure, "code", None)
            s.reports.event(
                "response_done",
                response_id=self.response_id,
                status=event.response.status,
                reason=reason,
                error_code=failure_code,
                max_output_tokens=s.options.live_max_output_tokens,
                turn=s.turn_number,
            )
            if event.response.status != "completed":
                if s.timing:
                    s.timing.interrupted = True
                if event.response.status == "incomplete":
                    explanation = {
                        "max_output_tokens": (
                            "the output budget (including audio tokens) was reached"
                        ),
                        "content_filter": "the provider's content filter stopped it",
                    }.get(reason, "the provider did not specify a reason")
                    s.output(
                        f"Realtime reply incomplete: {explanation}. "
                        "The connection is still open; please ask again or ask "
                        "for a shorter answer."
                    )
                    for item in event.response.output or []:
                        if item.type == "function_call":
                            await connection.conversation.item.delete(item_id=item.id)
                            if item.id in self.current_items:
                                self.current_items.remove(item.id)
                    self.finisher = asyncio.create_task(self.finish(incomplete=True))
                    return
                if event.response.status != "cancelled":
                    raise AppError(
                        f"Realtime response {event.response.status} "
                        f"({failure_code or reason or 'unspecified'}).",
                        retryable=failure_code
                        in ("server_error", "rate_limit_exceeded"),
                    )
                return
            if not isinstance(event.response.output, list):
                raise AppError("Realtime returned a malformed response output.")
            calls = [
                item for item in event.response.output if item.type == "function_call"
            ]
            if calls:
                self.tool_steps += 1
                if self.tool_steps > 3:
                    raise AppError("Realtime exceeded the three-step tool limit.")
                for call in calls:
                    try:
                        if call.name == "record_learning":
                            result = s.conversation.record_learning(call.arguments)
                            s.emit(
                                "learning",
                                vocabulary=list(s.conversation.vocabulary),
                                mistakes=list(s.conversation.mistakes),
                            )
                        elif call.name == "control":
                            command = tool_command(call.arguments)
                            result = apply_command(command, s.conversation.settings)
                            if s.timing:
                                s.timing.command = True
                            s.output(result)
                            s.emit("confirmation", text=result, action=command.action)
                            s.emit(
                                "session", **settings_payload(s.conversation.settings)
                            )
                            self.goodbye_requested = command.action == "end"
                            if self.goodbye_requested:
                                result = s.conversation.summary()
                            elif command.action == "repeat":
                                result = "Repeat exactly: " + s.conversation.last_answer
                        else:
                            raise ValueError("Unknown tool.")
                    except (ValueError, json.JSONDecodeError) as exc:
                        s.output(f"Invalid model tool output: {exc}")
                        result = f"Invalid arguments: {exc}. Correct them."
                    tool_id = f"tool_{self.generation}_{self.tool_steps}_{call.call_id}"
                    await connection.conversation.item.create(
                        item={
                            "id": tool_id,
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": result,
                        }
                    )
                    self.current_items.append(tool_id)
                    if generation != self.generation:
                        return
                await self.configure()
                await self.request_response(
                    s.conversation.prompt()
                    + (
                        "\nGive only a brief spoken goodbye and session summary: "
                        + s.conversation.summary()
                        if self.goodbye_requested
                        else ""
                    ),
                    no_tools=self.goodbye_requested,
                    expected_generation=generation,
                )
            else:
                self.finisher = asyncio.create_task(self.finish())

    async def finish(self, *, incomplete: bool = False) -> None:
        s = self.session
        generation = self.generation
        await s.playback.drain(s.options.timeout)
        if generation != self.generation:
            return
        text = " ".join(
            text
            for key, text in self.transcripts.items()
            if not incomplete or key in self.completed_items
        )
        if text:
            s.conversation.add("assistant", text)
            s.output(f"Tutor: {text}")
            s.reports.event("reply", turn=s.turn_number, text=text)
            s.emit(
                "reply",
                turn=s.turn_number,
                text=text,
                engine="live",
                interrupted=incomplete,
            )
        self.items.clear()
        self.transcripts.clear()
        self.completed_items.clear()
        self.response_id = None
        self.groups.append(self.current_items)
        self.current_items = []
        await self.prune()
        if generation != self.generation:
            return
        s.generating = False
        s.complete_turn()
        if incomplete:
            s.output("Listening... (incomplete reply excluded from benchmark)")
        if self.goodbye_requested:
            s.output(s.conversation.summary())
            s.ending = True

    async def prune(self) -> None:
        if self.connection:
            while len(self.groups) > 8:
                for item_id in self.groups.popleft():
                    await self.connection.conversation.item.delete(item_id=item_id)

    async def check(self) -> None:
        if self.receiver and self.receiver.done():
            await self.receiver
        if self.finisher and self.finisher.done():
            finisher, self.finisher = self.finisher, None
            await finisher
        if self.pending_at is not None and (
            time.perf_counter() - self.pending_at > self.session.options.timeout
        ):
            raise AppError("Realtime turn/transcription timed out.", retryable=True)

    async def stop(self) -> None:
        try:
            await self.interrupt(time.perf_counter(), count=False)
        finally:
            tasks = [task for task in (self.receiver, self.finisher) if task]
            for task in tasks:
                if not task.done():
                    task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    detail = (
                        str(result)
                        if isinstance(result, AppError)
                        else (type(result).__name__)
                    )
                    self.session.output(f"Realtime shutdown encountered: {detail}")

    def echo_text(self) -> str:
        return (
            " ".join(self.transcripts.values()) or self.session.conversation.last_answer
        )
