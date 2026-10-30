from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class TurnTiming:
    turn: int
    engine: str
    speech_end: float
    detected: float
    clock: Callable[[], float] = time.perf_counter
    marks: dict[str, float] = field(default_factory=dict)
    interrupted: bool = False
    command: bool = False

    def mark(self, name: str, at: float | None = None) -> None:
        self.marks.setdefault(name, self.clock() if at is None else at)

    def difference(self, start: str, end: str) -> float | None:
        a = self.speech_end if start == "speech_end" else self.marks.get(start)
        b = self.marks.get(end)
        return None if a is None or b is None else max(0, (b - a) * 1000)

    def values(self) -> dict[str, float | None]:
        return {
            "STT delay": self.difference("speech_end", "transcript"),
            "Translation/LLM delay": (
                self.difference("transcript", "token")
                if self.engine == "cascade"
                else None
            ),
            "TTS time-to-first-audio": (
                self.difference("token", "audio") if self.engine == "cascade" else None
            ),
            "Total end-of-speech-to-playback delay": self.difference(
                "speech_end", "playback"
            ),
            "Total response completion time": self.difference("speech_end", "finished"),
            "Silence detection": max(0, (self.detected - self.speech_end) * 1000),
            "Audio-to-playback": self.difference("audio", "playback"),
        }

    @property
    def successful(self) -> bool:
        return (
            "playback" in self.marks
            and "finished" in self.marks
            and not self.interrupted
            and not self.command
        )
