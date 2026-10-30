from __future__ import annotations

import argparse
import asyncio
import os
import time
import webbrowser
from pathlib import Path

from .cascade import CascadeEngine
from .commands import Command, apply_command
from .config import LEVELS, VOICES, Options, Settings
from .conversation import Conversation
from .events import EventBus, settings_payload
from .live import LiveEngine
from .providers import get_provider
from .report import Reports
from .resilience import AppError
from .session import Session
from .webui import WebUI

CONTROL_ACTIONS = frozenset(
    {
        "stop",
        "repeat",
        "speed",
        "level",
        "language",
        "learner_language",
        "scenario",
        "correction",
        "end",
    }
)


def device(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def threshold(value: str) -> float | None:
    if value.casefold() == "auto":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Threshold must be numeric or auto.") from exc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Speech-first interactive language practice."
    )
    p.add_argument("--engine", choices=("live", "cascade"), default="live")
    p.add_argument("--practice-language", default="en")
    p.add_argument("--learner-language", default="auto")
    p.add_argument("--level", choices=LEVELS, default="B1")
    p.add_argument("--scenario", default="free-conversation")
    p.add_argument("--voice", choices=VOICES, default="alloy")
    p.add_argument("--silence-ms", type=int, default=700)
    p.add_argument(
        "--vad-threshold",
        type=threshold,
        default=None,
        help="PCM16 RMS threshold or auto (one second ambient calibration).",
    )
    p.add_argument(
        "--echo-guard",
        choices=("headphones", "half-duplex", "off"),
        default="headphones",
    )
    p.add_argument("--no-correction", action="store_true")
    p.add_argument("--log-dir", type=Path)
    p.add_argument(
        "--log-transcripts",
        action="store_true",
        help="Opt in to transcript logging; requires --log-dir.",
    )
    p.add_argument("--benchmark-runs", type=int)
    p.add_argument("--report", type=Path)
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--fallback-cascade", action="store_true")
    p.add_argument("--list-devices", action="store_true")
    p.add_argument("--input-device", type=device)
    p.add_argument("--output-device", type=device)
    p.add_argument("--ui", action="store_true", help="Open the local web chat window.")
    p.add_argument("--ui-port", type=int, default=8770)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--live-model", default="gpt-realtime-2.1")
    p.add_argument(
        "--live-max-output-tokens",
        type=int,
        default=2048,
        help="Live response budget including audio tokens (default: 2048).",
    )
    return p


class WebController:
    """Adapts browser requests to the running loop without touching engine state."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.session: Session | None = None
        self.engine: object | None = None

    def attach(self, session: Session, engine: object) -> None:
        self.session = session
        self.engine = engine

    def command(self, action: str, value: str) -> None:
        if action not in CONTROL_ACTIONS:
            raise ValueError("Unknown control action.")
        self._post(
            lambda session, engine: self._apply(session, engine, Command(action, value))
        )

    def message(self, text: str) -> None:
        self._post(lambda session, engine: self._submit(engine, text))

    def stop(self) -> None:
        self._post(self._interrupt)

    # ---- runs on the asyncio loop ----------------------------------------
    def _post(self, action) -> None:
        def run() -> None:
            session, engine = self.session, self.engine
            if session is None or engine is None:
                return
            try:
                action(session, engine)
            except (AppError, ValueError, RuntimeError) as exc:
                session.emit("error", message=str(exc), retryable=False)

        self.loop.call_soon_threadsafe(run)

    def _apply(self, session: Session, engine: object, command: Command) -> None:
        confirmation = apply_command(command, session.conversation.settings)
        session.output(confirmation)
        session.emit("confirmation", text=confirmation, action=command.action)
        session.emit("session", **settings_payload(session.conversation.settings))
        if command.action == "end":
            session.ending = True
        elif command.action == "stop":
            self._interrupt(session, engine)
        elif command.action == "repeat":
            last = session.conversation.last_answer
            if last and not session.generating:
                self._submit(engine, last)

    def _submit(self, engine: object, text: str) -> None:
        submit = getattr(engine, "submit_text", None)
        if submit is None:
            raise RuntimeError(
                "Typed messages need cascade mode; speak, or restart with "
                "--engine cascade."
            )
        submit(text)

    def _interrupt(self, _session: Session, engine: object) -> None:
        interrupt = getattr(engine, "interrupt", None)
        if interrupt is None:
            return
        asyncio.ensure_future(interrupt(time.perf_counter(), count=False))


async def run(
    settings: Settings,
    options: Options,
    key: str,
    bus: EventBus | None = None,
    web: WebController | None = None,
) -> None:
    reports = Reports(
        settings, options.report, options.log_dir, options.log_transcripts, options
    )
    conversation = Conversation(settings)
    session = Session(conversation, options, reports, bus=bus)
    provider = get_provider("openai", key, options)
    print(
        "Audio is sent to OpenAI; API usage charges apply. Use headphones. "
        "Press Ctrl+C to stop."
    )
    if bus is not None:
        bus.describe(settings, options, options.engine)
        bus.emit("session", **settings_payload(settings))
    engine: LiveEngine | CascadeEngine
    try:
        if options.engine == "live":
            engine = LiveEngine(session, provider)
            if web is not None:
                web.attach(session, engine)
            try:
                await engine.run()
            except AppError as exc:
                if not options.fallback_cascade or not exc.retryable:
                    raise
                print(f"Live retries exhausted: {exc} Switching to cascade.")
                session.ending = False
                await CascadeEngine(session, provider).run()
        else:
            engine = CascadeEngine(session, provider)
            if web is not None:
                web.attach(session, engine)
            await engine.run()
    finally:
        await provider.close()
        reports.save()
        if reports.turns:
            print(reports.text())
        if not session.ending and conversation.history:
            print(conversation.summary())


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        settings = Settings(
            args.practice_language,
            args.learner_language,
            args.level,
            args.scenario,
            not args.no_correction,
        )
        settings.validate()
        options = Options(
            engine=args.engine,
            voice=args.voice,
            silence_ms=args.silence_ms,
            vad_threshold=args.vad_threshold,
            echo_guard=args.echo_guard,
            timeout=args.timeout,
            max_retries=args.max_retries,
            fallback_cascade=args.fallback_cascade,
            benchmark_runs=args.benchmark_runs,
            report=args.report,
            log_dir=args.log_dir,
            log_transcripts=args.log_transcripts,
            input_device=args.input_device,
            output_device=args.output_device,
            live_model=args.live_model,
            live_max_output_tokens=args.live_max_output_tokens,
        )
        options.validate()
        if not 1024 <= args.ui_port <= 65535:
            raise ValueError("UI port must be 1024 to 65535.")
        if args.list_devices:
            import sounddevice

            try:
                print(sounddevice.query_devices())
            except sounddevice.PortAudioError as exc:
                raise AppError("Cannot enumerate audio devices.") from exc
            return 0
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key.strip():
            raise AppError(
                "Set OPENAI_API_KEY in PowerShell before starting: "
                '$env:OPENAI_API_KEY = "<your key>"'
            )

        bus: EventBus | None = None
        webui: WebUI | None = None
        if args.ui:
            bus = EventBus()
            loop = asyncio.new_event_loop()
            controller = WebController(loop)
            webui = WebUI(bus, controller, port=args.ui_port)
            try:
                url = webui.start()
            except RuntimeError as exc:
                raise AppError(str(exc)) from exc
            print(f"Chat window: {url}")
            bus.emit(
                "state",
                state="starting",
                detail="Starting the assistant…",
            )
            if not args.no_browser:
                webbrowser.open(url)
            try:
                loop.run_until_complete(run(settings, options, key, bus, controller))
            finally:
                bus.emit("state", state="ended", detail="Session ended")
                webui.stop()
                loop.close()
            return 0

        asyncio.run(run(settings, options, key))
    except KeyboardInterrupt:
        print("Stopped. Audio streams and network connections closed.")
        return 0
    except (AppError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0
