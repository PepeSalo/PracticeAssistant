"""Watchdog that keeps a stuck session from freezing silently.

Two layers protect the user:

1. **Hard timeouts** (in ``Session.listen``) bound every call that can reach the
   network or the audio device, so a stall becomes an error instead of a freeze.
   This is the correctness layer: nothing can block forever.
2. **This watchdog** is the comfort layer. It runs as its own asyncio task, so it
   keeps working even when the main loop is blocked inside an ``await``. It warns
   the user early and, past the hard limit, asks the engine to recover.

The watchdog never guesses timings the application cannot observe: it measures
only the time since the last real sign of progress (a transcript, audio, a state
change), which is exactly what the user perceives as "nothing is happening".
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

DEFAULT_HARD_SECONDS = 25.0
DEFAULT_SOFT_SECONDS = 8.0
CHECK_INTERVAL = 0.5


class Watchdog:
    """Watches one session for lack of progress while a turn is in flight."""

    def __init__(
        self,
        session: Any,
        *,
        hard_seconds: float = DEFAULT_HARD_SECONDS,
        soft_seconds: float = DEFAULT_SOFT_SECONDS,
        interval: float = CHECK_INTERVAL,
        clock=time.perf_counter,
    ) -> None:
        self.session = session
        self.hard_seconds = hard_seconds
        self.soft_seconds = min(soft_seconds, hard_seconds)
        self.interval = interval
        self.clock = clock
        self.last_progress = clock()
        self.warned = False
        self.recovering = False
        self.stopped = False
        self.task: asyncio.Task[None] | None = None

    # ---- activity reporting ----------------------------------------------
    def progress(self, at: float | None = None) -> None:
        """Called whenever something meaningful happens; resets both timers."""
        self.last_progress = self.clock() if at is None else at
        self.warned = False

    def idle_seconds(self) -> float:
        return max(0.0, self.clock() - self.last_progress)

    # ---- lifecycle -------------------------------------------------------
    def start(self, engine: Any) -> None:
        self.task = asyncio.create_task(self.run(engine), name="watchdog")

    async def stop(self) -> None:
        self.stopped = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None

    # ---- main loop -------------------------------------------------------
    async def run(self, engine: Any) -> None:
        try:
            while not self.stopped:
                await asyncio.sleep(self.interval)
                await self.tick(engine)
        except asyncio.CancelledError:  # normal shutdown
            raise

    async def tick(self, engine: Any) -> None:
        session = self.session
        if session.ending:
            self.progress()
            return

        # Idle listening is not a stall: only judge work in flight.
        if not _busy(session):
            self.progress()
            return

        idle = self.idle_seconds()
        if idle < self.soft_seconds:
            return

        if not self.warned:
            self.warned = True
            self._notify(
                "Still waiting for the assistant… "
                f"{idle:.0f} seconds without an answer. "
                "Check the network connection.",
                level="warn",
            )
            session.emit(
                "state",
                state="thinking",
                detail=f"Still waiting… {idle:.0f} s",
            )

        if idle >= self.hard_seconds and not self.recovering:
            await self.recover(engine, idle)

    async def recover(self, engine: Any, idle: float) -> None:
        """Stop the stuck turn and tell the user, instead of hanging forever."""
        self.recovering = True
        session = self.session
        self._notify(
            f"The assistant stopped responding ({idle:.0f} s). "
            "The current turn was cancelled and the session is still running. "
            "Please repeat what you said.",
            level="error",
        )
        session.emit(
            "error",
            message=(
                f"No answer within {idle:.0f} seconds; the turn was cancelled. "
                "Check the network connection and try again."
            ),
            retryable=True,
        )
        try:
            recover = getattr(engine, "recover", None)
            if recover is not None:
                await recover()
        except Exception as exc:  # a broken recovery must not kill the watchdog
            session.emit(
                "error",
                message=f"Recovery failed ({type(exc).__name__}); restart the app.",
                retryable=False,
            )
        finally:
            self.recovering = False
            self.progress()

    # ---- helpers ---------------------------------------------------------
    def _notify(self, message: str, level: str = "info") -> None:
        self.session.output(message)
        self.session.emit("notification", text=message, level=level)


def _busy(session: Any) -> bool:
    """A turn is in flight if the engine is generating or audio is playing."""
    if getattr(session, "generating", False):
        return True
    playback = getattr(session, "playback", None)
    if playback is not None and not playback.idle():
        return True
    return False
