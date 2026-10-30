from __future__ import annotations

import asyncio
import time
from difflib import SequenceMatcher

from .audio_io import AudioIO, Playback
from .commands import normalize
from .config import Options
from .conversation import Conversation
from .metrics import TurnTiming
from .report import Reports
from .resilience import AppError
from .vad import AudioEvent, EndpointDetector


class Session:
    """One event loop owns conversational state; callbacks only post notifications."""

    def __init__(
        self,
        conversation: Conversation,
        options: Options,
        reports: Reports,
        output=print,
        playback: Playback | None = None,
        audio: AudioIO | None = None,
        bus=None,
    ) -> None:
        self.conversation = conversation
        self.options = options
        self.reports = reports
        self.output = output
        self.bus = bus
        self.playback = playback or Playback()
        self.audio = audio
        self.timing: TurnTiming | None = None
        self.turn_number = len(reports.turns)
        self.ending = False
        self.generating = False
        self.interrupted_at: float | None = None
        self.playback_started: float | None = None
        self.speech_started: float | None = None
        self.echo_delay: float | None = None
        self.echo_answer = ""
        self.playback_timings: dict[str, TurnTiming] = {}
        self.callback_error: AppError | None = None
        self.effective_threshold: float | None = options.vad_threshold

    def emit(self, kind: str, **data) -> None:
        """Publish a structured event for the web UI; a no-op without a bus."""
        if self.bus is not None:
            self.bus.emit(kind, **data)

    def bind_callbacks(self) -> None:
        loop = asyncio.get_running_loop()
        self.playback.on_first = lambda key, at: loop.call_soon_threadsafe(
            self.first_playback, key, at
        )
        self.playback.on_silent = lambda at: loop.call_soon_threadsafe(
            self.silence_observed, at
        )

    def first_playback(self, key: str, at: float) -> None:
        timing = self.playback_timings.get(key)
        if timing is None or timing.interrupted:
            return
        first = "playback" not in timing.marks
        timing.mark("playback", at)
        self.playback_started = (
            at if self.playback_started is None else (self.playback_started)
        )
        if first:
            self.output("Speaking...")

    def register_playback(self, key: str) -> None:
        if self.timing:
            self.playback_timings[key] = self.timing

    def silence_observed(self, at: float) -> None:
        if self.interrupted_at is not None:
            self.reports.interruptions.append(max(0, (at - self.interrupted_at) * 1000))
            self.interrupted_at = None
            try:
                self.reports.save()
            except AppError as exc:
                self.callback_error = exc

    def new_turn(self, event: AudioEvent, engine: str) -> TurnTiming:
        if event.speech_end is None:
            raise AppError("Endpoint event is missing the speech-end timestamp.")
        self.turn_number += 1
        self.playback.reset()
        self.playback_started = None
        self.playback_timings.clear()
        self.timing = TurnTiming(self.turn_number, engine, event.speech_end, event.at)
        self.reports.turns.append(self.timing)
        self.emit("state", state="thinking", detail="Thinking…")
        self.reports.event(
            "speech_end",
            turn=self.turn_number,
            at=event.speech_end,
            detected=event.at,
            threshold=self.effective_threshold,
        )
        return self.timing

    def new_text_turn(self, engine: str) -> TurnTiming:
        """A typed turn has no detected speech end, so both timestamps are 'now'."""
        self.turn_number += 1
        self.playback.reset()
        self.playback_started = None
        self.playback_timings.clear()
        now = time.perf_counter()
        self.timing = TurnTiming(self.turn_number, engine, now, now)
        self.reports.turns.append(self.timing)
        self.emit("state", state="thinking", detail="Thinking…")
        return self.timing

    def echo_suspected(self, transcript: str) -> bool:
        if self.options.echo_guard == "off":
            return False
        if self.echo_delay is None:
            return False
        delay = self.echo_delay
        heard = normalize(transcript)
        answer = normalize(self.echo_answer)
        suspected = (
            0 <= delay <= 0.3
            and len(heard) >= 8
            and (heard in answer or SequenceMatcher(None, heard, answer).ratio() > 0.85)
        )
        if suspected:
            self.output("Echo suspected — use headphones; this turn was discarded.")
            self.reports.event("echo_suspected", turn=self.turn_number)
        return suspected

    def complete_turn(self) -> None:
        if self.timing is None:
            return
        self.timing.mark("finished")
        self.reports.save()
        values = self.timing.values()
        self.emit(
            "timing",
            turn=self.timing.turn,
            engine=self.timing.engine,
            stages={name: values[name] for name in values},
            total_ms=values["Total end-of-speech-to-playback delay"],
        )
        if self.timing.successful:
            self.output(
                f"Measured turn {self.timing.turn}: "
                f"{self.timing.values()['Total end-of-speech-to-playback delay']:.1f}"
                " ms to first playback."
            )
        limit = self.options.benchmark_runs
        if limit and sum(t.successful for t in self.reports.turns) >= limit:
            self.ending = True

    async def listen(self, engine, rate: int) -> None:
        self.bind_callbacks()
        self.audio = self.audio or AudioIO(
            rate, self.playback, self.options.input_device, self.options.output_device
        )
        detector = EndpointDetector(
            rate, self.options.silence_ms, self.options.vad_threshold
        )
        self.effective_threshold = self.options.vad_threshold
        self.audio.start()
        self.emit(
            "state",
            state="listening",
            detail="Listening… speak whenever you like",
        )
        self.output(
            "recording — Listening..."
            + (
                " Stay quiet for one second to calibrate background noise."
                if self.options.vad_threshold is None
                else ""
            )
        )
        try:
            while not self.ending:
                if self.callback_error is not None:
                    raise self.callback_error
                try:
                    pcm, at = await asyncio.wait_for(self.audio.read(), 0.1)
                except TimeoutError:
                    await engine.check()
                    continue
                if self.options.echo_guard == "half-duplex" and (
                    self.generating or not self.playback.idle()
                ):
                    detector.reset()
                    await engine.check()
                    continue
                events = detector.feed(pcm, at)
                if self.effective_threshold is None and detector.threshold is not None:
                    self.effective_threshold = detector.threshold
                    self.output(
                        f"Calibration complete: RMS threshold "
                        f"{self.effective_threshold:.1f}. Listening..."
                    )
                    self.reports.event("calibrated", threshold=self.effective_threshold)
                    self.emit(
                        "diagnostic",
                        calibrated_rms=round(self.effective_threshold, 1),
                    )
                for event in events:
                    if event.kind == "start":
                        self.speech_started = event.at
                        self.echo_delay = (
                            event.at - self.playback_started
                            if self.playback_started is not None
                            else None
                        )
                        self.echo_answer = engine.echo_text()
                        self.output("Speech detected.")
                        self.reports.event(
                            "speech_started",
                            at=event.at,
                            threshold=self.effective_threshold,
                        )
                        await engine.interrupt(event.at)
                    elif event.kind == "discard":
                        self.output("Very short/noisy utterance discarded.")
                        if self.interrupted_at is not None or (
                            self.timing is not None and self.timing.interrupted
                        ):
                            self.output(
                                "If you did not speak, check speaker echo or try "
                                "--vad-threshold 500 with headphones."
                            )
                        self.reports.event(
                            "utterance_discarded",
                            at=event.at,
                            threshold=self.effective_threshold,
                        )
                    await engine.audio_event(event)
                await engine.check()
        finally:
            try:
                await engine.stop()
            finally:
                self.audio.close()
