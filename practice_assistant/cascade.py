from __future__ import annotations

import asyncio
import json
import re
import time

from openai.types.chat import ChatCompletionMessageParam

from .audio_io import wav_bytes
from .commands import apply_command, parse_command, tool_command
from .events import settings_payload
from .providers import OpenAIProvider, ReplyEvent
from .resilience import AppError
from .session import Session
from .vad import AudioEvent


class Sentences:
    def __init__(self) -> None:
        self.pending = ""

    def feed(self, text: str, *, final: bool = False) -> list[str]:
        self.pending += text
        sentences = []
        while self.pending:
            match = re.search(r"[.!?](?:\s|$)", self.pending)
            length = (
                match.end()
                if match
                else (self.pending.rfind(" ", 0, 220) if len(self.pending) > 220 else 0)
            )
            if not length:
                if final:
                    sentences.append(self.pending.strip())
                    self.pending = ""
                break
            sentences.append(self.pending[:length].strip())
            self.pending = self.pending[length:].lstrip()
        return [s for s in sentences if s]


class CascadeEngine:
    def __init__(self, session: Session, provider: OpenAIProvider) -> None:
        self.session = session
        self.provider = provider
        self.task: asyncio.Task[None] | None = None
        self.spoken: list[tuple[str, str]] = []
        self.response_text = ""
        self.goodbye_requested = False
        self.completed_sentences: set[str] = set()

    async def run(self) -> None:
        await self.session.listen(self, 16000)

    async def audio_event(self, event: AudioEvent) -> None:
        if event.kind == "end":
            await self.interrupt(event.at, count=False)
            self.task = asyncio.create_task(self.turn(event))

    async def check(self) -> None:
        if self.task is not None and self.task.done():
            task, self.task = self.task, None
            await task

    def heard_answer(self) -> str:
        return " ".join(
            text
            for key, text in self.spoken
            if key in self.completed_sentences and self.session.playback.complete(key)
        )

    async def interrupt(self, at: float, *, count: bool = True) -> None:
        s = self.session
        if self.task is None:
            return
        if self.task.done():
            await self.check()
            return
        s.output("Interrupted — listening.")
        if count:
            s.interrupted_at = at
        if s.timing:
            s.timing.interrupted = True
        s.playback.interrupt()
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
        heard = self.heard_answer()
        if heard:
            s.conversation.add("assistant", heard)
        s.emit(
            "reply",
            turn=s.turn_number,
            text=heard,
            engine="cascade",
            interrupted=True,
        )
        s.reports.event("interrupted", at=at, text=heard)
        s.reports.save()
        s.generating = False

    async def speak_text(self, text: str, key: str) -> None:
        s = self.session
        key = f"{s.turn_number}:{key}"
        self.spoken.append((key, text))
        s.register_playback(key)
        async for pcm in self.provider.speech(text, s.conversation.settings):
            if s.timing:
                s.timing.mark("audio")
            await s.playback.put(key, pcm, s.options.timeout)
        self.completed_sentences.add(key)

    async def tool(self, event: ReplyEvent) -> str:
        s = self.session
        if event.name == "record_learning":
            result = s.conversation.record_learning(event.text)
            s.emit(
                "learning",
                vocabulary=list(s.conversation.vocabulary),
                mistakes=list(s.conversation.mistakes),
            )
            return result
        if event.name != "control":
            raise ValueError("Unknown model tool.")
        command = tool_command(event.text)
        confirmation = apply_command(command, s.conversation.settings)
        s.output(confirmation)
        s.emit("confirmation", text=confirmation, action=command.action)
        s.emit("session", **settings_payload(s.conversation.settings))
        if s.timing:
            s.timing.command = True
        if command.action == "end":
            self.goodbye_requested = True
            return s.conversation.summary()
        if command.action == "repeat":
            return f"Repeat exactly: {s.conversation.last_answer}"
        if command.action == "stop":
            return "Briefly confirm you are listening; do not ask a new question."
        return confirmation

    async def generate(self) -> None:
        s = self.session
        extra: list[ChatCompletionMessageParam] = []
        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(maxsize=8)

        async def producer() -> None:
            index = 0
            for _ in range(3):
                splitter = Sentences()
                calls: list[ReplyEvent] = []
                response = ""
                async for event in self.provider.reply(s.conversation, extra):
                    if event.kind == "text":
                        if s.timing:
                            s.timing.mark("token")
                        response += event.text
                        self.response_text += event.text
                        for sentence in splitter.feed(event.text):
                            index += 1
                            await queue.put((f"sentence-{index}", sentence))
                    else:
                        calls.append(event)
                for sentence in splitter.feed("", final=True):
                    index += 1
                    await queue.put((f"sentence-{index}", sentence))
                if not calls:
                    await queue.put(None)
                    return
                extra.append(
                    {
                        "role": "assistant",
                        "content": response or None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "id": c.call_id,
                                "function": {"name": c.name, "arguments": c.text},
                            }
                            for c in calls
                        ],
                    }
                )
                for call in calls:
                    try:
                        result = await self.tool(call)
                    except (ValueError, json.JSONDecodeError) as exc:
                        s.output(f"Invalid model control/learning output: {exc}")
                        result = f"Invalid tool arguments: {exc}. Correct them."
                    extra.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": result,
                        }
                    )
                if self.goodbye_requested:
                    # End is handled locally, so it cannot rely on another LLM call.
                    await queue.put(("goodbye", s.conversation.summary()))
                    await queue.put(None)
                    return
            raise AppError("Model exceeded the three-step tool limit.")

        async def consumer() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    return
                await self.speak_text(item[1], item[0])

        tasks = [asyncio.create_task(producer()), asyncio.create_task(consumer())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def turn(self, event: AudioEvent) -> None:
        s = self.session
        timing = s.new_turn(event, "cascade")
        self.spoken.clear()
        self.response_text = ""
        self.goodbye_requested = False
        self.completed_sentences.clear()
        s.generating = True
        try:
            s.output("Transcribing...")
            transcript = await self.provider.transcribe(wav_bytes(event.pcm, 16000))
            timing.mark("transcript")
            s.output(f"Heard: {transcript}")
            s.reports.event("transcript", turn=timing.turn, text=transcript)
            s.emit(
                "transcript",
                turn=timing.turn,
                text=transcript,
                engine="cascade",
            )
            if s.echo_suspected(transcript):
                timing.interrupted = True
                return
            await self.respond(transcript, timing)
        except AppError as exc:
            self.fail_turn(timing, exc)
        finally:
            s.generating = False

    def fail_turn(self, timing, exc: AppError) -> None:
        s = self.session
        timing.interrupted = True
        s.playback.interrupt()
        heard = self.heard_answer()
        if heard:
            s.conversation.add("assistant", heard)
        s.output(f"This turn failed: {exc} Please try again.")
        s.emit("error", message=str(exc), retryable=exc.retryable)
        s.reports.event("turn_failed", turn=timing.turn)
        s.reports.save()

    async def respond(self, transcript: str, timing) -> None:
        """Shared by spoken and typed turns: command handling, reply, playback."""
        s = self.session
        try:
            command = parse_command(transcript)
            if command:
                timing.command = True
                confirmation = apply_command(command, s.conversation.settings)
                s.output(confirmation)
                s.emit("confirmation", text=confirmation, action=command.action)
                s.emit("session", **settings_payload(s.conversation.settings))
                if command.action == "repeat":
                    text = (
                        s.conversation.last_answer
                        or "There is no answer to repeat yet."
                    )
                elif command.action == "end":
                    text = s.conversation.summary()
                    self.goodbye_requested = True
                else:
                    text = confirmation
                await self.speak_text(text, "confirmation")
            else:
                s.conversation.add("user", transcript)
                s.output("Thinking...")
                await self.generate()
            await s.playback.drain(s.options.timeout)
            heard = self.heard_answer()
            if heard and not command:
                s.conversation.add("assistant", heard)
            if heard:
                s.emit(
                    "reply",
                    turn=timing.turn,
                    text=heard,
                    engine="cascade",
                    interrupted=bool(timing.interrupted),
                )
            if self.goodbye_requested:
                s.output(s.conversation.summary())
            s.complete_turn()
            if self.goodbye_requested:
                s.ending = True
        except AppError as exc:
            self.fail_turn(timing, exc)

    async def stop(self) -> None:
        if self.task and not self.task.done():
            await self.interrupt(time.perf_counter(), count=False)
        else:
            await self.check()

    def can_accept_text(self) -> bool:
        s = self.session
        return not s.generating and (self.task is None or self.task.done())

    def submit_text(self, text: str) -> None:
        """Queue a typed turn; called on the loop thread by the web controller."""
        if not self.can_accept_text():
            raise RuntimeError("Wait for the current answer to finish first.")
        self.task = asyncio.create_task(self.text_turn(text))

    async def text_turn(self, text: str) -> None:
        s = self.session
        s.bind_callbacks()
        timing = s.new_text_turn("cascade")
        self.spoken.clear()
        self.response_text = ""
        self.goodbye_requested = False
        self.completed_sentences.clear()
        s.generating = True
        timing.mark("transcript")
        try:
            s.emit("transcript", turn=timing.turn, text=text, engine="cascade")
            await self.respond(text, timing)
        finally:
            s.generating = False

    def echo_text(self) -> str:
        return self.response_text or self.session.conversation.last_answer
