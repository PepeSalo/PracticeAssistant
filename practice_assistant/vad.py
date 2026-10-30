from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AudioEvent:
    kind: str
    at: float
    pcm: bytes = b""
    speech_end: float | None = None
    started: float | None = None


class EndpointDetector:
    def __init__(
        self,
        rate: int,
        silence_ms: int = 700,
        threshold: float | None = None,
        min_seconds: float = 0.2,
        max_seconds: float = 30,
        calibration_seconds: float = 1,
    ) -> None:
        self.rate = rate
        self.silence = silence_ms / 1000
        self.threshold = threshold
        self.minimum = min_seconds
        self.maximum = max_seconds
        self.calibration_seconds = 0 if threshold is not None else calibration_seconds
        self.calibrated_seconds = 0.0
        self.noise: list[float] = []
        self.active = False
        self.streak = 0
        self.pre: deque[bytes] = deque(maxlen=8)
        self.audio = bytearray()
        self.started = 0.0
        self.last_voiced = 0.0
        self.voiced_seconds = 0.0

    def reset(self) -> None:
        self.active = False
        self.streak = 0
        self.pre.clear()
        self.audio.clear()
        self.voiced_seconds = 0

    def feed(self, pcm: bytes, at: float) -> list[AudioEvent]:
        if len(pcm) % 2:
            raise ValueError("Incomplete microphone PCM16 sample.")
        if not pcm:
            return []
        duration = len(pcm) / (2 * self.rate)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        rms = float(np.sqrt(np.mean(samples**2)))
        if self.calibrated_seconds < self.calibration_seconds:
            self.noise.append(rms)
            self.calibrated_seconds += duration
            if self.calibrated_seconds >= self.calibration_seconds:
                self.threshold = max(150, float(np.median(self.noise)) * 3)
            return []
        threshold = self.threshold if self.threshold is not None else 150
        voiced = rms >= threshold * (0.75 if self.active else 1)
        if not self.active:
            self.pre.append(pcm)
            self.streak = self.streak + 1 if voiced else 0
            if self.streak < 3:
                return []
            self.active = True
            self.started = at - self.streak * duration
            self.last_voiced = at
            self.voiced_seconds = self.streak * duration
            self.audio = bytearray(b"".join(self.pre))
            self.pre.clear()
            return [
                AudioEvent("start", at, started=self.started),
                AudioEvent("chunk", at, bytes(self.audio)),
            ]
        self.audio.extend(pcm)
        if voiced:
            self.last_voiced = at
            self.voiced_seconds += duration
        events = [AudioEvent("chunk", at, pcm)]
        if at - self.last_voiced >= self.silence or at - self.started >= self.maximum:
            kind = "end" if self.voiced_seconds >= self.minimum else "discard"
            events.append(
                AudioEvent(kind, at, bytes(self.audio), self.last_voiced, self.started)
            )
            self.reset()
        return events
