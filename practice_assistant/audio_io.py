from __future__ import annotations

import asyncio
import io
import threading
import time
import wave
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from .resilience import AppError

OUTPUT_RATE = 24000


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    if not pcm or len(pcm) % 2:
        raise AppError("Recording is empty or contains incomplete PCM16.")
    file = io.BytesIO()
    with wave.open(file, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return file.getvalue()


@dataclass
class Segment:
    key: str
    pcm: bytes
    offset: int = 0


class Playback:
    def __init__(
        self,
        rate: int = OUTPUT_RATE,
        max_seconds: float = 5,
        on_first: Callable[[str, float], None] | None = None,
        on_silent: Callable[[float], None] | None = None,
    ) -> None:
        self.rate = rate
        self.limit = int(rate * 2 * max_seconds)
        self.lock = threading.Lock()
        self.queue: deque[Segment] = deque()
        self.pending = 0
        self.played: dict[str, int] = defaultdict(int)
        self.generated: dict[str, int] = defaultdict(int)
        self.first: dict[str, float] = {}
        self.last_at = 0.0
        self.on_first = on_first
        self.on_silent = on_silent
        self.awaiting_silence = False

    def reset(self) -> None:
        with self.lock:
            self.queue.clear()
            self.pending = 0
            self.played.clear()
            self.generated.clear()
            self.first.clear()
            self.last_at = 0

    def enqueue(self, key: str, pcm: bytes) -> bool:
        if not pcm or len(pcm) % 2:
            raise AppError("Output audio is empty or has an incomplete PCM16 sample.")
        with self.lock:
            if len(pcm) > self.limit:
                raise AppError(
                    "Provider audio chunk exceeds the playback buffer limit."
                )
            if self.pending + len(pcm) > self.limit:
                return False
            self.queue.append(Segment(key, pcm))
            self.pending += len(pcm)
            self.generated[key] += len(pcm) // 2
            return True

    async def put(
        self,
        key: str,
        pcm: bytes,
        timeout: float,
        is_current: Callable[[], bool] | None = None,
    ) -> bool:
        deadline = time.perf_counter() + timeout
        while True:
            if is_current and not is_current():
                return False
            if self.enqueue(key, pcm):
                return True
            if time.perf_counter() >= deadline:
                raise AppError("Playback buffer stalled; check the output device.")
            await asyncio.sleep(0.01)

    def interrupt(self) -> dict[str, int]:
        with self.lock:
            self.queue.clear()
            self.pending = 0
            self.awaiting_silence = True
            return dict(self.played)

    def callback(self, outdata, frames: int, _timing, _status) -> None:
        now = time.perf_counter()
        result = bytearray(frames * 2)
        cursor = 0
        notifications: list[str] = []
        silent = False
        with self.lock:
            while self.queue and cursor < len(result):
                segment = self.queue[0]
                count = min(len(result) - cursor, len(segment.pcm) - segment.offset)
                result[cursor : cursor + count] = segment.pcm[
                    segment.offset : segment.offset + count
                ]
                if segment.key not in self.first:
                    self.first[segment.key] = now
                    notifications.append(segment.key)
                self.played[segment.key] += count // 2
                self.pending -= count
                segment.offset += count
                cursor += count
                self.last_at = now + cursor / (2 * self.rate)
                if segment.offset == len(segment.pcm):
                    self.queue.popleft()
            if self.awaiting_silence and cursor == 0:
                self.awaiting_silence = False
                silent = True
        outdata[:] = bytes(result)
        for key in notifications:
            if self.on_first:
                self.on_first(key, now)
        if silent and self.on_silent:
            self.on_silent(now)

    def idle(self) -> bool:
        with self.lock:
            return self.pending == 0 and time.perf_counter() >= self.last_at

    async def drain(self, timeout: float) -> None:
        deadline = time.perf_counter() + timeout
        while not self.idle():
            if time.perf_counter() >= deadline:
                raise AppError("Audio playback timed out.")
            await asyncio.sleep(0.02)

    def played_ms(self, key: str) -> int:
        with self.lock:
            return self.played.get(key, 0) * 1000 // self.rate

    def complete(self, key: str) -> bool:
        with self.lock:
            return (
                self.generated.get(key, 0) > 0
                and self.played.get(key, 0) == self.generated[key]
            )


class AudioIO:
    def __init__(
        self,
        rate: int,
        playback: Playback,
        input_device=None,
        output_device=None,
        backend=None,
    ) -> None:
        self.rate = rate
        self.playback = playback
        self.input_device = input_device
        self.output_device = output_device
        self.backend = backend
        self.incoming: asyncio.Queue[tuple[bytes, float]] = asyncio.Queue(maxsize=100)
        self.error: asyncio.Future[None] | None = None
        self.streams: list = []

    def start(self) -> None:
        if self.backend is None:
            import sounddevice

            self.backend = sounddevice
        sd = self.backend
        loop = asyncio.get_running_loop()
        self.error = loop.create_future()

        def fail(message: str) -> None:
            if self.error is not None and not self.error.done():
                self.error.set_exception(AppError(message))

        def publish(pcm: bytes, at: float) -> None:
            if self.incoming.full():
                fail("Microphone buffer overflow; processing/network cannot keep up.")
            else:
                self.incoming.put_nowait((pcm, at))

        def capture(indata, _frames, _timing, status) -> None:
            if status:
                loop.call_soon_threadsafe(fail, f"Microphone error: {status}")
            else:
                loop.call_soon_threadsafe(publish, bytes(indata), time.perf_counter())

        def output(outdata, frames, timing, status) -> None:
            if status:
                loop.call_soon_threadsafe(fail, f"Playback error: {status}")
            self.playback.callback(outdata, frames, timing, status)

        try:
            sd.check_input_settings(
                device=self.input_device,
                channels=1,
                dtype="int16",
                samplerate=self.rate,
            )
            sd.check_output_settings(
                device=self.output_device,
                channels=1,
                dtype="int16",
                samplerate=OUTPUT_RATE,
            )
            for factory, kwargs in (
                (
                    sd.RawOutputStream,
                    dict(
                        device=self.output_device,
                        samplerate=OUTPUT_RATE,
                        channels=1,
                        dtype="int16",
                        blocksize=480,
                        callback=output,
                    ),
                ),
                (
                    sd.RawInputStream,
                    dict(
                        device=self.input_device,
                        samplerate=self.rate,
                        channels=1,
                        dtype="int16",
                        blocksize=self.rate // 50,
                        callback=capture,
                    ),
                ),
            ):
                stream = factory(**kwargs)
                self.streams.append(stream)
                stream.start()
        except (sd.PortAudioError, ValueError) as exc:
            self.close()
            raise AppError(
                f"Audio device startup failed ({self.input_device!r}, "
                f"{self.output_device!r}; mono {self.rate}/{OUTPUT_RATE} Hz): {exc}. "
                "Use --list-devices and select compatible devices."
            ) from exc

    async def read(self) -> tuple[bytes, float]:
        if self.error is None:
            raise AppError("Microphone is not started.")
        task = asyncio.create_task(self.incoming.get())
        try:
            done, _ = await asyncio.wait(
                (task, self.error), return_when=asyncio.FIRST_COMPLETED
            )
            if self.error in done:
                await self.error
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def close(self) -> None:
        errors: list[str] = []
        for stream in reversed(self.streams):
            try:
                stream.stop()
            except (self.backend.PortAudioError, ValueError) as exc:
                errors.append(str(exc))
            finally:
                try:
                    stream.close()
                except (self.backend.PortAudioError, ValueError) as exc:
                    errors.append(str(exc))
        self.streams.clear()
        if self.error is not None:
            if self.error.done() and not self.error.cancelled():
                self.error.exception()
            elif not self.error.done():
                self.error.cancel()
        if errors:
            raise AppError("Audio shutdown failed: " + "; ".join(errors))
