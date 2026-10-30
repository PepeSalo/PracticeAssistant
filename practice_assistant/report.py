from __future__ import annotations

import json
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import Options, Settings
from .metrics import TurnTiming
from .resilience import AppError

UNAVAILABLE = "unavailable (not independently exposed by native speech-to-speech)"


def format_report(
    turns: list[TurnTiming],
    settings: Settings,
    interruptions: list[float],
    options: Options | None = None,
) -> str:
    valid = [t for t in turns if t.successful]
    lines = [
        f"Language practice: {settings.practice_language}, level {settings.level}",
        f"Successful real turns: {len(valid)} (minimum five for benchmark)",
        "Speech end: last voiced microphone block (local proxy).",
        "Playback: first output callback submitting PCM, not audible loopback.",
        "Completion: submitted sample duration elapsed; device tail is unmeasured.",
        "Cascade TTS interval includes sentence assembly and network/client buffering.",
        "Realtime STT is auxiliary transcription, not the native model's internal STT.",
        "Command, interrupted and failed turns are excluded from latency summaries.",
    ]
    if options:
        lines.extend(
            [
                f"Models: live={options.live_model}; cascade="
                "gpt-4o-mini-transcribe/gpt-4o-mini/gpt-4o-mini-tts",
                f"Voice: {options.voice}; silence: {options.silence_ms} ms; "
                f"RMS threshold: {options.vad_threshold or 'auto'}; "
                f"echo guard: {options.echo_guard}",
                f"Live output budget: {options.live_max_output_tokens} tokens "
                "(includes audio tokens)",
            ]
        )
    if len(valid) < 5:
        lines.append("INCOMPLETE: collect at least five successful turns.")
    for t in valid:
        lines.append(f"\nRun {t.turn} ({t.engine}):")
        for label, value in t.values().items():
            lines.append(
                f"{label}: {value:.1f} ms"
                if value is not None
                else f"{label}: {UNAVAILABLE}"
            )
    if valid:
        lines.append("\nSummary (stage medians):")
        medians: dict[str, float] = {}
        for label in valid[0].values():
            values = [t.values()[label] for t in valid]
            measured = [v for v in values if v is not None]
            if len(measured) == len(valid):
                medians[label] = statistics.median(measured)
                lines.append(f"{label}: {medians[label]:.1f} ms")
            else:
                lines.append(f"{label}: {UNAVAILABLE} (or mixed engines)")
        total_label = "Total end-of-speech-to-playback delay"
        totals = [t.values()[total_label] for t in valid]
        numbers = [n for n in totals if n is not None]
        for label, value in (
            ("Fastest run", min(numbers)),
            ("Median run", statistics.median(numbers)),
            ("Slowest run", max(numbers)),
        ):
            lines.append(f"{label}: {value:.1f} ms")
        lines.append(f"First run (cold proxy): {numbers[0]:.1f} ms")
        if len(numbers) > 1:
            lines.append(
                f"Subsequent median (warm proxy): "
                f"{statistics.median(numbers[1:]):.1f} ms"
            )
        components = {
            k: v
            for k, v in medians.items()
            if k
            in (
                "STT delay",
                "Translation/LLM delay",
                "TTS time-to-first-audio",
                "Audio-to-playback",
            )
        }
        if valid[0].engine == "live":
            components = {
                "Silence detection": medians["Silence detection"],
                "Audio-to-playback": medians.get("Audio-to-playback", 0),
            }
            lines.append("Live bottleneck analysis covers only local components.")
        largest = max(components, key=components.get)
        lines.append(
            f"Largest measured median component: {largest} "
            f"({components[largest]:.1f} ms)."
        )
        suggestions = {
            "STT delay": "Stream STT during speech; compare faster STT models.",
            "Translation/LLM delay": "Keep context short; compare faster chat models.",
            "TTS time-to-first-audio": "Try shorter sentence chunks and faster TTS.",
            "Audio-to-playback": "Check device buffers and output latency.",
            "Silence detection": "Try 400 ms silence; check pauses do not split turns.",
        }
        lines.append(suggestions[largest])
    if interruptions:
        lines.append(
            f"Barge-in callback stop delay median: "
            f"{statistics.median(interruptions):.1f} ms"
        )
    return "\n".join(lines) + "\n"


class Reports:
    def __init__(
        self,
        settings: Settings,
        path: Path | None,
        log_dir: Path | None,
        log_transcripts: bool = False,
        options: Options | None = None,
    ) -> None:
        self.settings = settings
        self.path = path
        self.log_dir = log_dir
        self.log_transcripts = log_transcripts
        self.options = options
        self.session_id = uuid4().hex
        self.turns: list[TurnTiming] = []
        self.interruptions: list[float] = []
        if log_dir:
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise AppError("Cannot create the requested log directory.") from exc
        self.save()

    def save(self) -> None:
        if self.path:
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as file:
                    temporary = Path(file.name)
                    file.write(self.text())
                temporary.replace(self.path)
            except OSError as exc:
                raise AppError(f"Cannot save report: {self.path}.") from exc
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()

    def text(self) -> str:
        return format_report(
            self.turns, self.settings, self.interruptions, self.options
        )

    def event(self, kind: str, **data: object) -> None:
        if not self.log_dir:
            return
        if not self.log_transcripts:
            data = {
                k: v for k, v in data.items() if k not in ("text", "heard", "intended")
            }
        try:
            with (self.log_dir / "events.jsonl").open("a", encoding="utf-8") as file:
                file.write(
                    json.dumps(
                        {
                            **data,
                            "event": kind,
                            "session_id": self.session_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError as exc:
            raise AppError("Cannot write session log.") from exc
