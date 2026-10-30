from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Voice = Literal[
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
]

LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)


def language(value: str, *, allow_auto: bool = False) -> str:
    value = value.strip().lower()
    if allow_auto and value == "auto":
        return value
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2,4})?", value):
        raise ValueError("Use a language code such as en, fi, es or de.")
    return value


@dataclass
class Settings:
    practice_language: str = "en"
    learner_language: str = "auto"
    level: str = "B1"
    scenario: str = "free-conversation"
    correction: bool = True
    speed: float = 1.0

    def validate(self) -> None:
        self.practice_language = language(self.practice_language)
        self.learner_language = language(self.learner_language, allow_auto=True)
        self.level = self.level.upper()
        if self.level not in LEVELS:
            raise ValueError("Level must be A1, A2, B1, B2, C1 or C2.")
        if not self.scenario.strip() or len(self.scenario) > 300:
            raise ValueError("Scenario must contain 1 to 300 characters.")
        if not 0.5 <= self.speed <= 1.5:
            raise ValueError("Speaking rate must be between 0.5 and 1.5.")


@dataclass(frozen=True)
class Options:
    engine: str = "live"
    voice: Voice = "alloy"
    silence_ms: int = 700
    vad_threshold: float | None = None
    echo_guard: str = "headphones"
    timeout: float = 30.0
    max_retries: int = 3
    fallback_cascade: bool = False
    benchmark_runs: int | None = None
    report: Path | None = None
    log_dir: Path | None = None
    log_transcripts: bool = False
    input_device: int | str | None = None
    output_device: int | str | None = None
    live_model: str = "gpt-realtime-2.1"
    live_max_output_tokens: int = 2048

    def validate(self) -> None:
        if not 512 <= self.live_max_output_tokens <= 4096:
            raise ValueError("Live output token limit must be 512 to 4096.")
        if self.engine not in ("live", "cascade"):
            raise ValueError("Unknown engine.")
        if self.voice not in VOICES:
            raise ValueError("Unknown voice.")
        if not 200 <= self.silence_ms <= 3000:
            raise ValueError("Silence interval must be 200 to 3000 ms.")
        if self.vad_threshold is not None and not 0 < self.vad_threshold < 32768:
            raise ValueError("VAD threshold must be positive and below 32768.")
        if self.echo_guard not in ("headphones", "half-duplex", "off"):
            raise ValueError("Unknown echo guard.")
        if not 1 <= self.timeout <= 300 or not 0 <= self.max_retries <= 6:
            raise ValueError("Timeout must be 1–300 seconds; retries must be 0–6.")
        if self.benchmark_runs is not None and self.benchmark_runs < 5:
            raise ValueError("Benchmark requires at least five successful turns.")
        if self.log_transcripts and self.log_dir is None:
            raise ValueError("--log-transcripts requires --log-dir (privacy opt-in).")
