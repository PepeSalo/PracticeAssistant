Assignment 11 — Real-Time Voice-Controlled AI Application
Create a Python application controlled primarily by speech.

Examples:

interactive language-practice assistant

voice-controlled image application

conversational game or character

spoken AI tutor

voice-controlled information assistant

accessibility application

A simple STT → AI → TTS pipeline is acceptable.

For a more advanced implementation, use a native real-time speech-to-speech API such as OpenAI Live, Gemini Live or Grok Voice.

Evaluate:

response latency

transcription mistakes

background noise

interruptions

microphone/speaker echo

network failure

whether the system correctly detects when the user has finished speaking

Advanced challenge: allow the user to interrupt the AI while it is speaking.

---

# Implementation plan (added; the original assignment text above remains authoritative)

Chosen application: **interactive language-practice assistant** — the learner talks, the
assistant answers aloud in the practice language, gently corrects mistakes, and the
learner can steer the whole session by voice. Everything below must satisfy the original
assignment text above and `instructions.md`; `agents.md` defines working rules.

## 1. Scope and decisions

| Topic | Decision |
|---|---|
| Interaction | Fully hands-free after start: speech in, speech out. Keyboard is only for start/quit (Ctrl+C). |
| Engines | Two engines behind one interface: **live** (native speech-to-speech, default, advanced) and **cascade** (STT → LLM → TTS, simple/inspectable baseline and the only way to get numeric per-stage latencies). |
| Provider | OpenAI first (`OPENAI_API_KEY`). Keep provider code isolated so another provider can be added later; do not implement others now. |
| Defaults | Practice language `en`, learner/explanation language `auto` (explain in the language the learner uses), level `B1`, scenario `free-conversation`. All overridable by CLI flags **and** spoken commands. |
| Privacy | Audio is never saved. Transcripts/timing logs are written only when `--log-dir` is given. Reports never contain keys, audio, or (unless `--log-transcripts`) text. |
| Out of scope | Native desktop GUI, browser-based microphone/speaker capture, accounts, databases, voice cloning, wake-word, acoustic echo cancellation implemented from scratch. A local browser **chat window** is now in scope — see **Part II** below. |

> Note: the paths below (`E:\StudioCode\venv_11`, `venv_10`) describe the original
> development machine recorded while the work was done. The project now lives in its
> own folder with a standard `.venv`; see `readme.md` for the current commands.

## 2. Environment (already prepared)

- `E:\StudioCode\venv_11` is **both the workspace and the virtual-environment root**
  (created with Python 3.14.3: `Scripts\`, `Lib\`, `Include\`, `pyvenv.cfg`).
  Do **not** create `.venv`, rename it, or nest another environment. Packages go to
  `Lib\site-packages` via `E:\StudioCode\venv_11\Scripts\python.exe -m pip ...`.
- Dependencies are now installed. Maintain `requirements.txt` (runtime) and
  `requirements-dev.txt` (`-r requirements.txt` + pytest, pytest-asyncio or plain
  `asyncio.run` in tests, ruff, black), then install with the environment's own
  interpreter. Runtime: `numpy`, `sounddevice`, `openai[realtime]>=3.29.0,<4`
  (the **`[realtime]` extra is required** — without it live mode fails with
  "You need to install `openai[realtime]`"). `soundfile` only if actually used.
- Always invoke `E:\StudioCode\venv_11\Scripts\python.exe` explicitly; the shell is
  PowerShell on Windows, does not support `&&`/`||`, and each command starts in a fresh
  process.
- `.gitignore` already excludes the environment folders, `.env`, caches, `*.wav`,
  `session-logs/`. Never commit `Scripts`, `Lib`, `Include`, `pyvenv.cfg`, or keys.
- Set the key per session: `$env:OPENAI_API_KEY = "<key>"`. Never hard-code it.

## 3. Reference material to reuse (read-only)

A working live interpreter exists in `E:\StudioCode\venv_10\voice_interpreter\`. Do not
import from or modify it; read it and re-implement the proven patterns cleanly here:

- `realtime.py`: `AsyncOpenAI().live.connect(...)`, `connection.session.start(session=...)`
  (waits for `session.started` before opening audio), `connection.session.input_audio.append`
  with base64 PCM16 at 24 kHz mono, events `session.output_audio.delta`,
  `session.input_transcript.delta`, `session.output_transcript.delta`, `error`,
  `session.closed`; the thread-safe `PlaybackBuffer` (bounded queue, `interrupt()` clears
  queued audio); RMS `VoiceActivityDetector`; bounded input queue with failure on overflow;
  clean shutdown.
- `providers.py`: Requests-based STT (`gpt-4o-mini-transcribe`), chat (`gpt-4o-mini`), and
  streamed PCM TTS (`gpt-4o-mini-tts`, `response_format="pcm"`, `stream_format="audio"`,
  24 kHz PCM16, first complete sample callback), validation of empty/odd-length audio, response closing on failure.
- `live_metrics.py`, `batch_report.py`: incremental report writing and honest labelling of
  unmeasurable values.
- Lessons already learned: the native live API does **not** expose separate STT/LLM/TTS
  timings — label them "unavailable", never invent them; the "speech end" in live mode
  is a local proxy; a 700 ms silence interval dominated live latency (400 ms halved the
  local component but may split sentences); Python's `venv` overwrites `.gitignore`.
- **Verify the installed SDK before relying on any API name** (`openai` ≥ 3.26 in
  `Lib\site-packages`, e.g. inspect `openai.types` live/realtime modules and
  `connection.response`/`conversation` helpers). Document in the README what was verified.

## 4. Spoken control (voice-first requirement)

All of these must work by voice in both engines, in the learner's language or the practice
language, tolerant of filler words and punctuation:

| Intent | Example phrases | Effect |
|---|---|---|
| Stop speaking / be quiet | "stop", "wait", "hold on" | Cut playback now (also automatic barge-in), keep listening. |
| Repeat | "repeat that", "say it again" | Replay/regenerate last answer. |
| Slow down / speed up | "speak slower" | Change speaking-style instruction (and TTS speed if supported). |
| Change level | "make it easier", "level B2" | Update system instructions. |
| Change language | "let's practise Spanish" | Update practice language. |
| Change scenario | "role-play ordering at a café", "job interview" | Update scenario prompt. |
| Translate / explain | "how do you say … ?", "what does … mean?" | Normal answer with mini-lesson. |
| Feedback mode | "correct my mistakes" / "don't correct me" | Toggle correction style. |
| End session | "goodbye", "end session", "quit" | Spoken goodbye, session summary, clean exit. |

Implementation rules:
- `commands.py` is a **pure, offline-testable** parser: normalized transcript → `Command | None`
  (casefold, strip punctuation/fillers, whole-phrase/word-boundary matching, so
  "stop" inside "bus stop" or a story must not trigger). Ambiguous text is treated as
  conversation, not as a command.
- Cascade engine: match locally on the final transcript before calling the LLM.
- Live engine: the model answers every utterance automatically, so prefer **model tool
  calling** (functions such as `set_practice_language`, `set_level`, `set_scenario`,
  `set_speaking_rate`, `end_session`) if the installed SDK supports tools for live sessions
  (verify; document the result). If not, apply the local parser to final input transcripts,
  cancelling/overriding the in-flight response and updating session instructions.
  Never trigger a destructive/irreversible action (like ending the session) from a
  **partial** transcript; use final transcripts or tool results only.
- Every command gets brief audible/visible confirmation (e.g. "Switching to Spanish.").
- The only non-voice controls are the start command and Ctrl+C (graceful stop).

## 5. Tutor behaviour (prompting)

System prompt built by `conversation.py` from settings (practice language, level,
scenario, learner language, correction mode, speaking rate):
- Speak mainly in the practice language at the requested CEFR level; short turns
  (1–3 sentences) so latency stays low and the learner talks most of the time.
- Always end with a short question or prompt to keep the dialogue going.
- Correction mode: recast naturally, name at most one or two important mistakes per turn,
  then continue; never lecture. Explain in the learner language only when asked or when
  the learner is clearly stuck.
- Treat transcription errors gently: if the transcript looks like a mis-hearing, ask a
  clarifying question instead of "correcting" it as a learner mistake.
- Speech-friendly output only: no markdown, lists, emojis, or URLs.
- Safe and polite; stays on language practice; refuses unrelated or unsafe requests briefly.
- Keep a bounded history (last N turns + rolling summary) so cost and latency do not grow
  without limit. Record per-session vocabulary and recurring mistakes for a spoken and
  printed end-of-session summary.

## 6. Audio, turn detection and barge-in

Pipeline (both engines): microphone callback → bounded queue → engine → bounded playback
queue → output callback. No unbounded buffers; overflow produces a clear, actionable error.

- **Formats:** mono PCM16. Live: 24 kHz. Cascade: 16 kHz capture for STT, 24 kHz TTS
  playback. Any resampling is explicit, tested (length/rate/clipping), and uses one
  helper; never silently assume a device rate. Handle devices that refuse the requested
  rate (query, resample, or fail with a message naming the device and rate).
- **Endpointing (`vad.py`):** RMS/energy detector with a hysteresis state machine:
  speech-start after N consecutive voiced blocks (avoids clicks), speech-end after
  `--silence-ms` of silence (default 700), a minimum utterance length, and a maximum
  utterance length. Adaptive noise floor: calibrate during the first ~1 s of ambient
  noise and set the threshold as a multiple of it (flags override: `--vad-threshold`,
  `--silence-ms`). For the live engine, also evaluate/allow the server's own turn
  detection if the SDK exposes it; document which is used and why. Record every
  detected turn so false cuts (utterance split mid-sentence) and late cuts can be
  counted in the evaluation.
- **Barge-in (advanced challenge, required):** when speech starts during assistant audio,
  (1) clear the playback queue immediately, (2) cancel the in-flight model response
  (live: response cancel/truncate event if available; cascade: stop consuming/close the
  LLM and TTS streams), (3) make the new microphone input the active turn, and
  (4) store only what was actually played (estimate by played samples) so the model's
  context matches what the learner heard. Audio already submitted to the device cannot be
  withdrawn; report the measured stop delay (speech start → output callback silent).
- **Echo:** `--echo-guard {headphones,half-duplex,off}`. `headphones` (default) assumes no
  acoustic leakage and permits barge-in. `half-duplex` ignores/mutes the microphone while
  the assistant speaks (no barge-in; documented trade-off) for speakers. Add a
  self-trigger detector: if the VAD fires within ~100–300 ms of assistant playback starting
  and the input transcript resembles the assistant's own last words, drop that turn and
  warn "echo suspected — use headphones". Document that real acoustic echo cancellation is
  not implemented.
- **Status output:** print `recording`/`Listening...` only after the microphone stream has
  actually started, then state changes (`speech detected`, `thinking`, `speaking`,
  `interrupted`).

## 7. Reliability and errors

`resilience.py` provides tested helpers (injectable clock/sleep/random):
- Timeouts on every request (connect + read); no unbounded waits.
- Retries with exponential backoff **and jitter**, bounded attempts, only for retryable
  failures (timeouts, connection errors, 429, 5xx honouring `Retry-After`); 4xx like 401
  fail immediately with a clear message.
- Live: detect closed/failed WebSocket, tell the learner ("Connection lost, reconnecting…"),
  reconnect with backoff, **resume the conversation** by seeding the new session with the
  summary + recent turns, then continue; after the retry budget is exhausted, exit cleanly
  or fall back to cascade mode (configurable `--fallback-cascade`) — choose and document one default.
- Cascade: a failed stage affects only that turn (spoken/printed apology, conversation
  continues). Network failure while offline must not crash the loop.
- Microphone/speaker missing, device disconnect, stream status flags, malformed or empty
  provider responses, malformed audio payloads: all become friendly, specific errors.
- Missing API key: actionable message before opening any audio device; no stack trace.
- Ctrl+C closes streams, tasks and connections without orphaned threads.

## 8. Latency measurement (required by instructions.md)

Use `time.perf_counter()`. Report **≥ 5 successful runs**, cold (first) vs warm (later),
and print/save: fastest, median, slowest, plus the largest median component with a
concrete suggestion.

Cascade engine, per turn:
1. end of speech → final transcript (STT)
2. final transcript → first LLM token (use a streaming chat request)
3. LLM first token → first TTS audio chunk (stream sentences/chunks to TTS as soon as a
   sentence completes; do not wait for the entire LLM answer)
4. end of speech → first audible response (first output-callback with response audio)
5. total response completion time (end of speech → playback finished)
Also record: silence-detection delay (configured silence + VAD block size), barge-in stop
delay, and turn count.

Live engine: measure what is genuinely observable (local speech-end → first returned
audio → first output callback; final input-transcript time **only if** the event stream
exposes it; total response completion from the end-of-response event). Label every
metric that the API cannot expose as `unavailable (reason)`. Never present local proxies
as server timings; state the definitions in the report and README.

`metrics.py` holds injected-clock timing objects (offline-testable, thread-safe because
audio callbacks run on another thread). `report.py` formats text (and optionally JSON)
and is written **incrementally** after each measured turn so partial results survive a
crash. `--benchmark-runs N` (min 5) and `--report PATH` flags mirror the conventions of the
reference project.

## 9. Evaluation deliverable (the assignment's "Evaluate" list)

Create `docs/evaluation.md` with a protocol and result tables for each item. The agent
cannot use a real microphone/API: implement the tools and **offline simulations**, then
leave clearly marked "to be filled from real runs" tables for the user. **Never fabricate
measurements or claim live tests were performed by you.**

| Evaluate | Tool/method | Notes to record |
|---|---|---|
| Response latency | §8 reports, ≥5 runs, cold/warm, both engines, silence 400/700 ms | Median, spread, bottleneck |
| Transcription mistakes | A scripted list of ~10 phrases (accents, numbers, names, language-learner errors, code-switching Finnish/English) read aloud; log "intended vs heard" with `--log-transcripts`; compute word error rate with a small tested helper | Which errors affected corrections |
| Background noise | Quiet / fan / music / cafe-noise conditions; VAD false starts, missed starts, threshold used | Noise-floor calibration effect |
| Interruptions | Barge-in at start/middle/end of AI speech; measure stop delay and whether context matches what was heard | Audible tail |
| Microphone/speaker echo | Headphones vs speakers, each echo-guard mode; count self-triggers | Recommended setup |
| Network failure | Wi-Fi off mid-turn, wrong key, timeout/429 simulation; reconnect result | Recovery time, user messaging |
| End-of-speech detection | Short answers, hesitations, long sentences; count premature cuts and late responses per silence setting | Best default trade-off |

Add a small deterministic test-signal generator (silence, tone/"speech" bursts, noise,
echo-delayed copy) used by the VAD/echo/barge-in unit tests.

## 10. Proposed layout

```text
E:\StudioCode\venv_11\
  plan.md  instructions.md  agents.md  readme.md (written last)
  requirements.txt  requirements-dev.txt  pyproject.toml  .gitignore
  practice_assistant\
    __init__.py  __main__.py  cli.py  config.py
    commands.py      conversation.py   session_log.py
    audio_io.py      vad.py            resilience.py
    providers.py     cascade.py        live.py
    metrics.py       report.py
  tests\   (one test module per source module)
  docs\evaluation.md
```
`python -m practice_assistant` runs it. Use type hints, small modules, injected clocks and
fakes for all I/O. Keep `pyproject.toml` consistent with the reference project (Black
88 cols; Ruff `E,F,I,UP,B`; pytest `testpaths = ["tests"]`).

CLI (all with sane defaults, argparse help text and validation):
`--engine {live,cascade}`, `--practice-language`, `--learner-language`, `--level`,
`--scenario`, `--voice`, `--silence-ms`, `--vad-threshold` (or `auto`), `--echo-guard`,
`--no-correction`, `--log-dir`, `--log-transcripts`, `--benchmark-runs`, `--report`,
`--timeout`, `--max-retries`, `--fallback-cascade`, `--list-devices`, `--input-device`,
`--output-device`.

## 11. Milestones (do in order; each ends green on tests/ruff/black)

> Milestones 1–7 and the acceptance criteria in §13 are **complete** (version 1.0).
> The web UI and the offline installation package continue in **Part II (§15–§26)**.

1. **Foundation:** requirements, pyproject, install into venv_11, config/CLI skeleton,
   missing-key error, `resilience.py`, `metrics.py`.
2. **Voice logic (pure):** `commands.py`, `conversation.py`, `vad.py`, resampling helpers, test-signal generator.
3. **Cascade engine:** providers (STT, streaming chat, streamed TTS), sentence-chunked TTS
   pipeline, playback, barge-in, per-stage metrics and reports.
4. **Live engine:** verify SDK, session config, mic/playback, tool calls or local command
   path, barge-in/cancel, reconnect with context resume, metrics.
5. **Evaluation tooling:** WER helper, report formats, `docs/evaluation.md`, echo/noise
   handling and their tests.
6. **Hardening:** error-path review, shutdown/leak review, CLI help, optional fallback.
7. **Final validation, then `readme.md`** (written only after all tests pass).

## 12. Testing (per `agents.md`/`instructions.md`)

Run from `E:\StudioCode\venv_11` with `.\Scripts\python.exe -m pytest -q`,
`-m ruff check .`, `-m black --check .`. **No real network or audio device in any test**:
mock `requests.post`/streams, fake the SDK connection and `sounddevice`, monkeypatch
`OPENAI_API_KEY`. Minimum coverage:
- Config/CLI validation (languages, levels, same-language rules, benchmark minimum,
  unwritable report path, unknown engine).
- Providers: missing key, success, non-200, 401/429/5xx, timeouts, malformed JSON/shape,
  empty or odd-length PCM, stream closed on failure, factory with unknown name.
- Retry/backoff: first-try success, retry on retryable errors, no retry on 4xx, `Retry-After`,
  jitter bounds, gives up after the maximum attempts.
- `commands.py`: every intent in both languages, false-positive guards ("bus stop"),
  partial vs final transcripts.
- VAD: speech start/end, hysteresis, min/max length, noise floor, false-start suppression,
  noise-only and echo-delayed signals.
- Playback buffer: bounded queue, interrupt clears queue, underrun fills silence, played-sample accounting.
- Barge-in end to end with fakes (both engines): response cancelled, queue cleared,
  history records only the heard part, new turn processed.
- Live engine with a fake connection: start handshake, audio deltas, transcripts, error
  event, closed event, malformed base64, reconnect with context resume, retry exhaustion.
- Cascade turn: each stage's failure is isolated; conversation continues.
- Metrics/report: injected clock values, median of odd/even samples, unavailable labels,
  incremental saving, no secrets in report text.
- Conversation: prompt contains settings, history bounding/summary, session summary.
- Audio edge cases: resampling, device rate mismatch, microphone status flags.

## 13. Acceptance criteria

- `.\Scripts\python.exe -m practice_assistant --help` works; running without a key prints
  an actionable message and exits non-zero with no traceback.
- After the start command, the session can be driven entirely by voice, including every
  spoken command in §4 and a spoken end-of-session summary.
- Both engines run; live is the default; user can interrupt the AI mid-sentence and the
  assistant stops, then answers the new utterance.
- Network/API failures never crash the session silently; behaviour is documented.
- Latency report follows §8 with ≥5 runs, cold/warm, fastest/median/slowest and a
  bottleneck statement; unmeasurable values are labelled, not invented.
- `docs/evaluation.md` covers all seven evaluation items, with real results left for the
  user (or clearly marked pending).
- `pytest -q`, `ruff check .`, `black --check .` all pass with the environment's
  interpreter; no secrets, recordings, or environment folders in the repository.
- `readme.md` (created last, no assignment-specific wording — written for a normal
  application): features, install/run, voice commands, flags, privacy, limitations
  (echo, VAD, latency), how to evaluate, and an honest **model attribution** for the
  agent that wrote the code/docs (only a model name that is actually known).

## 14. Working rules for the implementing agent

- Preserve the original assignment text at the top of this file; only append or refine.
- Do not call the real API, use the microphone, or ask for the user's key; the user runs
  live tests. State clearly in the final report what was and was not verified.
- Do not publish/commit/push unless the user asks.
- Report decisions that need the user (e.g. fallback default, preferred voice) via a
  question; otherwise use the defaults above.

## Implementation decisions verified against the installed SDK

- The implementation uses OpenAI SDK **3.29.0**, with runtime requirement
  `openai[realtime]>=3.29.0,<4`. Live mode uses
  `AsyncOpenAI().realtime.connect(model="gpt-realtime-2.1")`, not the older interpreter's
  `client.live` surface. Inspection of the installed `types/live` shows no final input
  transcript event and immutable startup instructions. The Realtime surface provides
  final auxiliary transcripts, cancellation, item truncation, session updates, response
  IDs and function tools, which are necessary for this application's controls.
- Server endpointing is disabled. Audio streams during speech; the local RMS detector
  commits each utterance. The application waits for the final auxiliary transcript before
  requesting a response so local commands never act on partial text. This deliberately
  adds transcription latency compared with an automatically answering native session.
- Both engines use the async OpenAI SDK rather than a Requests worker thread.
  Async stream context managers make cancellation and connection cleanup reliable.
  Tests fake SDK requests/streams and devices; they never call providers or hardware.
  Generic dictionary-specific tests in agents.md (e.g. `definitions`) are inapplicable;
  strict control and learning-note schemas are tested instead.
- Live reconnect is application-owned (SDK reconnect retries disabled), bounded by
  `--max-retries`. Exhaustion exits by default; `--fallback-cascade` opts into fallback
  for retryable errors only. The interrupted utterance must be repeated after recovery.
- The direct command parser supports English and Finnish. Model function tools cover
  additional languages and arbitrary explicit scenario/language requests. Their real
  recognition accuracy requires user testing; no offline test proves multilingual speech
  recognition. Local cascade confirmations and the deterministic session summary use
  English; live confirmations may be rendered in the learner's language.
- Cascade stores only fully generated and fully submitted sentences when interrupted.
  Live truncates server audio by submitted samples and omits partial items from local
  resume text. No guessed partial transcript is treated as something the learner heard.
- Unsupported device rates fail with a named device/rate message; no implicit resampling.
  Logs/reports share report.py rather than a separate session_log.py. Evaluation's WER
  helper is available with `python -m practice_assistant.evaluation`.
- Real speech, noise, interruption, echo and network evaluation remains pending. Use
  `docs/evaluation.md`; do not substitute offline test timings for real measurements.

---

# Part II — Web UI chat window and USB installation package

Added after the 1.0 implementation. Everything above stays authoritative: this part adds
one user-facing surface (a browser chat window) and one delivery mechanism (an offline
Windows installer built from this workspace). Physics, honesty and safety rules from
`agents.md`/`instructions.md` still apply — in particular: never claim a real device,
key, network or target-PC test that was not actually performed.

## 15. Goals and non-goals for Part II

Goal 1 — a **local web chat window** that makes a running session understandable at a
glance: what state the assistant is in, what the learner said, what the tutor answered,
what was corrected, how fast each turn was, and how to steer the session — without
reading the README.

Goal 2 — a **one-click installation** for a *separate* Windows 10/11 PC whose only file
transport is a USB memory stick. **Revised after the build:** the target PC *does* have
internet (needed for the AI API), so Python and the packages are downloaded during
installation instead of shipping a ~100 MB offline wheelhouse. What the target PC still
cannot do is reach the developer's GitHub repository, so every application file travels
on the stick. No pre-installed Python, no manual `pip`, no code editing.

Non-goals (explicit, with reasons):

| Not doing | Why |
|---|---|
| Browser microphone/speaker capture (WebAudio + WebSocket PCM) | The engines already own the host PC's audio devices through `sounddevice`. Duplicating capture, resampling, playback queue and barge-in in the browser would double the audio surface and the risk, for no new capability on a single-user PC. The UI is used **on the same PC** as the microphone. |
| Multi-user, remote access, accounts, auth server | It is a personal desktop app. Bind to `127.0.0.1` only. |
| A web framework (FastAPI/Streamlit/Chainlit) or a JS build step | Offline USB install favours a tiny dependency set: standard library `http.server` + Server-Sent Events + bundled vanilla HTML/CSS/JS. Streamlit's rerun model also fights a persistent realtime session. |
| New runtime dependencies | Part II adds **zero** third-party runtime packages. `requirements.txt` is unchanged. |
| Offline wheelhouse | Dropped: the target PC has internet for the API, so `pip install` can fetch packages. Shipping no wheels keeps the bundle at 0.2 MB instead of ~100 MB. |
| Auto-update, cloud installer, credential store | Out of scope; keys stay in an environment variable as ruled. |

## 16. Architecture decisions (Part II)

| Topic | Decision |
|---|---|
| Audio path | **Unchanged.** Microphone/speaker stay native (`sounddevice`) on the host PC. The browser is a controller + transcript/log view. |
| Server | `http.server.ThreadingHTTPServer` on `127.0.0.1`, default port **8770** (`--ui-port`), started in a daemon thread from `cli.py`. |
| Push to browser | **Server-Sent Events** (`GET /api/events`). One-way server→browser is all the UI needs; no WebSocket dependency. Heartbeat comment every 15 s. |
| Browser → server | Plain `POST /api/...` with JSON. Applied on the asyncio loop via `loop.call_soon_threadsafe`. |
| Engine seam | A new, small **structured event layer** (`events.py`) so engines stop speaking only in `print` strings. `Session.output` keeps working (console sink) so existing behaviour/tests are preserved. |
| Text input | Optional **typed turns** through the existing cascade provider path (LLM + TTS), so the UI is usable and testable without a microphone. Voice remains the primary path. |
| Threading | One asyncio loop in the main thread (as today); HTTP server threads only enqueue commands and read from per-client queues. No engine state is touched off-loop. |
| Security | Loopback bind + per-run random token (sent in a header) + `Host`/`Origin` validation + restrictive CSP. No key, no raw transcript, no stack trace leaves the process. |
| Session lifecycle | The UI attaches to a session already started by the CLI. Starting/stopping the process stays with the launcher (no remote process control). |
| Versioning | Web UI = **1.1**; installer/packaging/docs = **1.2**. `pyproject.toml` and the README version history move with each. |

## 17. Event model (`events.py`, new)

A tiny, dependency-free, thread-safe bus. Engines emit structured events instead of
formatted sentences; two sinks consume them: `ConsoleSink` (current terminal text) and
`WebSink` (SSE queues). Strings sent to the terminal today must stay byte-identical where
tests or the README depend on them.

Event kinds (all JSON-serializable, no keys, no raw audio):

| `kind` | Payload | Purpose in the UI |
|---|---|---|
| `state` | `state` (`idle`/`listening`/`thinking`/`speaking`/`interrupted`/`error`), `detail` | Status pill |
| `transcript` | `turn`, `text`, `final` | Learner bubble ("heard") |
| `reply` | `turn`, `text`, `final` | Tutor bubble |
| `confirmation` | `text`, `action` | Small inline system line for a spoken/clicked control |
| `learning` | `vocabulary[]`, `mistakes[]` | "This session" panel |
| `timing` | `turn`, `engine`, stage map (ms or `null`) | Timing badge under the bubble |
| `error` | `message`, `retryable`, `detail` | Friendly error banner |
| `session` | `ended`, `summary`, `practice language`, `level`, `scenario`, `correction`, `speed` | Header chips, end card |
| `diagnostic` | `calibrated threshold`, `device`, `reconnect`, `discard` | Collapsed diagnostics drawer |

Rules:
- Bounded per-subscriber queue (default 500 events); on overflow the **oldest** event is
  dropped and a counter is exposed, so a slow browser can never stall the audio loop.
- The server keeps a **snapshot** (settings, state, turns, learning notes, recent events)
  that `GET /api/state` returns, so a page refresh or a late `EventSource` connection
  reconstructs the window without replaying everything.
- Structured events are emitted **only for what the app already knows**. No invented
  partial text, no server-internal timings (live stays `unavailable`).

## 18. HTTP API contract (`webui.py`, new)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Serves `index.html` (token injected as a meta tag). |
| `GET` | `/assets/<name>` | Allowlisted static files only (explicit name set, no path join from the request). |
| `GET` | `/api/state` | Full snapshot (JSON). |
| `GET` | `/api/events` | SSE stream (requires the token header/query param). |
| `POST` | `/api/command` | `{"action": ..., "value": ...}` → same `apply_command` semantics as voice. |
| `POST` | `/api/message` | `{"text": ...}` → typed turn (cascade path). |
| `POST` | `/api/stop` | Stop speaking / interrupt playback now. |

Guard rails to implement and test:
- Reject any request whose `Host` is not `127.0.0.1:<port>`/`localhost:<port>`.
- Reject `POST` without the correct token; reject an `Origin` that is not the same origin.
- Body limit (e.g. 8 KiB), JSON parse errors → `400` with a plain message.
- A `--no-ui`/absent `--ui` flag keeps today's CLI-only behaviour.
- Errors are returned as `{"error": "friendly text"}` — never a traceback.

## 19. Front-end (bundled, no CDN, no build step)

`practice_assistant/assets/ui/{index.html,app.css,app.js}` served by the above. Design
principle: **informative without being busy** — every element answers one beginner
question ("is it listening?", "what did it hear?", "what did it say?", "how do I change
this?", "was that fast?").

Layout (single column, max ~900 px, 16–18 px type, high contrast, light/dark via
`prefers-color-scheme`, system font stack):

1. **Header** — app name, big coloured state pill with plain words
   (`Listening…`, `Thinking…`, `Speaking…`, `Interrupted`, `Not started`, `Error`), plus
   settings chips: *Practice: English · Level: B1 · Scenario: Free conversation ·
   Engine: Live*. Chips open small dropdowns that send `/api/command`.
2. **Conversation** — Learner bubbles (right, "you said") and Tutor bubbles (left). Each
   bubble carries a small meta line: a turn timing badge (`speech end → first audio
   640 ms`, green/amber/red) and an "you interrupted here" marker. An interrupted reply
   shows only the part that was actually played, matching the engine's own truth.
3. **Quick controls** — one row of large buttons that mirror spoken commands: *Stop
   speaking*, *Repeat*, *Slower*, *Faster*, *Corrections on/off*, *End session*.
4. **This session** (collapsible) — vocabulary practised and recurring mistakes from
   `conversation.vocabulary`/`mistakes`, with an honest footnote that these notes are
   model-generated, can be incomplete, and are not an assessment.
5. **Voice commands help** (collapsible) — the spoken-command table from the README, so
   the user never has to leave the page.
6. **Recent notifications** (collapsible, built) — a timestamped feed of session
   changes, warnings and errors, mirrored by short toast pop-ups. Toasts alone would be
   transient; the feed is what makes a missed warning recoverable.
7. **Diagnostics** (collapsed by default) — engine, models, silence ms, RMS threshold,
   echo guard, input/output device, reconnects/discards, last error, report path.
8. **Text input** — "Type instead of speaking" with Send; disabled while a voice answer
   is in flight (with the reason shown).

Accessibility and simplicity: `aria-live="polite"` on new messages, visible focus
outlines, `Esc` stops speech, `Enter` sends text, no icon-only buttons, no raw metric
dumps on the main surface, no API key or token echoed in the page body.

## 20. Engine/session integration

- `cli.py`: `--ui`, `--ui-port`, `--no-browser`; start the server before `asyncio.run`,
  open the browser with `webbrowser.open` after the server is bound, fail with a friendly
  message if the port is taken.
- `Session.output`: kept, and additionally forwards structured events when a bus is
  configured (constructor default = none, so the 138 existing tests keep passing).
- Engines: replace ad-hoc formatted strings with `emit(...)` calls where a machine-readable
  fact already exists (`transcript`, `Tutor: …`, confirmations, learning, turn timing,
  state transitions). Terminal rendering stays human-identical.
- Web controls reuse `parse_command`/`apply_command`/`tool_command` so voice and clicks
  cannot drift apart.
- Typed turns: `Session` gains a `submit_text(text)` path that a cascade task can run when
  no turn is active; concurrent submission returns a friendly "wait for the current
  answer" instead of interleaving audio.
- No behaviour change when `--ui` is absent.

## 21. Verifying the UI ("when the UI is tested")

Offline, no key, no microphone, no external network — the standard-library test suite can
really exercise the server on `127.0.0.1` (the existing `conftest.py` guard already allows
loopback):

1. Start `ThreadingHTTPServer` on port 0 in a test, drive it with `urllib.request`.
2. Assert: `/` serves HTML containing the token meta tag; `/assets/app.css` returns
   `text/css`; an unknown asset path returns 404 (and cannot read `../` files);
   `/api/state` returns the snapshot shape; `POST /api/command` applies a level change
   and the settings chip payload changes; `POST` without/with a wrong token returns 403;
   a foreign `Origin`/`Host` is rejected; oversized/invalid JSON returns 400.
3. SSE: subscribe in a thread, emit events from a fake engine, assert ordering, that the
   heartbeat keeps the stream alive, that overflow drops the oldest and increments the
   counter, and that disconnecting does not raise in the audio path.
4. Event layer: every kind serializes; no key/API-token/transcript leaks into a snapshot
   when transcript logging is off; dropping a slow subscriber does not block.
5. Typed turn: with a fake provider, a typed message produces `transcript`→`reply`→
   `timing` events and does not touch `sounddevice`.
6. Manual, documented check (user-run, not claimed by the agent): open the page, speak,
   interrupt, click a chip, type a message. Record the outcome in the README honestly.

Gate for moving on to packaging: `pytest -q`, `ruff check .`, `black --check .` green,
plus a hand-run session on this PC with a real key by the user.

## 22. Installation package (USB, Windows 10/11) — as built

Build script `scripts/build_usb_bundle.ps1` produces `dist/PracticeAssistant-<version>-usb/`:

```text
PracticeAssistant-1.1-usb/      (0.2 MB, fits any USB stick)
  INSTALL.txt                   plain-language, printable steps
  Install.cmd                   double-click entry point (ASCII wrapper)
  install/install.ps1           the only real installer
  app/practice_assistant/       source, including assets/ui
  app/requirements.txt          runtime dependencies
  app/pyproject.toml            project metadata
```

Install behaviour (current user, no administrator rights):
1. Preflight before changing anything: 64-bit Windows 10+, `app\` present, ≥ 700 MB free.
   Failure prints a specific cause and a concrete next step.
2. `Unblock-File` for copied files (USB Mark-of-the-Web), otherwise Windows may refuse
   the scripts.
3. Find an existing Python ≥ 3.10 via the `py` launcher or `python`; if none, download the
   official python.org installer and install it per-user with `/quiet InstallAllUsers=0`.
4. Create `%LOCALAPPDATA%\PracticeAssistant\.venv`.
5. `pip install -r app/requirements.txt` (network), then copy `app\` into place.
6. Write `Start Practice Assistant.cmd` (runs `-m practice_assistant --ui`, and explains
   how to set the key if it is missing) and `Set API key.cmd` (`setx`, hidden input).
7. Desktop + Start Menu shortcuts.
8. Verify with `python -m practice_assistant --help` **inside `app\`**; then optionally
   ask for the API key once, or say it can be skipped.

Re-running upgrades in place. A failing step stops with a readable message and points to
`%TEMP%\practice-assistant-install.log`, which never contains the key.

ARM64: Win 11 ARM64 runs this under x64 emulation; **untested on ARM64**.

## 23. Verifying the installer

- `tests/test_usb_bundle.py` (offline, temp dirs): bundle manifest completeness (every
  file the scripts reference exists), wheel closure actually resolves in a throw-away venv
  with `--no-index`, script text contains no absolute author paths or secrets, `install.ps1`
  parses (`[ScriptBlock]::Create` or `pwsh -NoProfile -Command` parse check when a
  PowerShell host is available), and idempotency logic is unit-tested where it is plain
  PowerShell-free Python.
- Real install into a temp directory on **this** PC, with `--no-index`, is the strongest
  check the agent can honestly run.
- A clean Windows 10/11 VM and the actual target PC over USB are **user-run** steps; the
  README must mark them pending until the user reports the result.

## 24. Milestones for Part II — status

Phase A and Phase B shipped together as version 1.1 (one change set).

**Phase A — Web UI (done)**
1. `events.py` + 17 tests; `Session`/engines emit structured events; console output
   unchanged and the original 138 tests untouched and passing.
2. `webui.py` (server, API, snapshot, SSE, guards) + 20 tests over real loopback HTTP.
3. Bundled assets and the layout in §19, including the notifications feed.
4. Typed-turn path (`submit_text`/`respond` refactor) and web controls reusing
   `apply_command`; 5 tests plus 3 CLI tests.
5. `cli.py` flags `--ui`, `--ui-port`, `--no-browser`; browser opening; friendly
   port/key failures.

**Phase C — automatic updates (version 1.2, done)**
10. `install/update.ps1`: `git pull --ff-only`, local-change guard, warning-not-crash.
11. Installer asks once for a repository URL (`update.ini`), offers Git via `winget`.
12. Launcher runs the check before starting (skippable with `PA_NO_UPDATE=1`);
    `Update Practice Assistant.cmd` for on-demand updates.
13. Verified against a real local Git repository: clone, 1.1 -> 1.2 propagation, local
    edits preserved, unreachable remote warning with the install intact.

**Phase B — installer (done)**
6. `scripts/build_usb_bundle.ps1` (+ secret/path scan, cache removal).
7. `install/install.ps1`, `Install.cmd`, `INSTALL.txt`.
8. `tests/test_installer.py` (18 offline checks) plus a **real** install into a temp
   directory on this PC, which caught a genuine bug: the launcher and the verification
   step ran from the install root instead of `app\`.
9. README + `INSTALL.txt` + version history + `pyproject.toml` 1.1; `.gitignore` gets
   `dist/`.

## 25. Acceptance criteria for Part II

- `python -m practice_assistant --ui` serves the chat window on `127.0.0.1`, opens the
  browser, and shows live state, learner transcripts, tutor replies, corrections,
  per-turn timings and session settings without reading the README.
- The window works with voice, with typed text, and in cascade **and** live mode; clicking
  a control and saying the same command have identical effects.
- Interrupted replies display only what was actually played; live-only metrics remain
  labelled `unavailable`.
- No API key, token or stack trace is reachable from the page; the UI is not reachable
  from other machines.
- With `--ui` absent, behaviour and all existing tests are unchanged.
- `dist/PracticeAssistant-<version>-usb/` installs with no internet and no admin rights on
  a Windows 10/11 x64 PC: Python (if missing) → venv → offline wheels → shortcut → start,
  ending in a working chat window after the user sets `OPENAI_API_KEY`.
- `pytest -q`, `ruff check .`, `black --check .` pass; README, `pyproject.toml`, version
  history and `INSTALL.txt` are consistent; no secrets, absolute author paths or
  environment folders are in the bundle.

## 26. Risks and open questions

| Risk | Mitigation |
|---|---|
| Python silent-install switches differ from memory | The installer uses the documented `/quiet InstallAllUsers=0 PrependPath=1` set and falls back to a clear "install Python manually" message if the interpreter is not found afterwards. |
| Target PC has no suitable audio device | UI shows the friendly device error; `--list-devices` documented; installer does not require audio to succeed. |
| Execution policy / SmartScreen blocks USB scripts | `-ExecutionPolicy Bypass` in the wrapper, `Unblock-File`, and a printed manual one-liner. |
| Target PC is ARM64 | Emulated x64 execution; documented as untested. |
| Browser refresh mid-session | `/api/state` snapshot restores the window; server remains the source of truth. |
| SSE blocked by an over-eager extension/firewall | UI falls back to 1 s polling of `/api/state` (documented, no extra dependency). |
| Typed turns racing with voice turns | Text turn refused with a friendly message while a turn is in flight. |
| Scope creep into browser audio | Explicitly out of scope (§15); revisit only if the user asks. |

**Answers as built:**
1. The UI stays behind `--ui`; the installed shortcut and launcher pass it.
2. Installation goes to `%LOCALAPPDATA%\PracticeAssistant` (overridable with `-InstallDir`).
3. The installer offers `setx` once and can be skipped; `Set API key.cmd` repeats it.

**Resolved by the user:** keep **OpenAI as the only provider**. No OpenRouter code is
added. A read-only GitHub token for the one repository is acceptable to ease future
updates, so `install/update.ps1` supports `PA_GITHUB_TOKEN` (memory only) and Git
Credential Manager; no token is ever written to `update.ini`, `.git/config` or a log.

The application is hardwired to the **OpenAI** API (Realtime for live mode; transcription,
chat and TTS for cascade). It reads `OPENAI_API_KEY`. There is no OpenRouter code path.
OpenRouter does not provide speech-to-text, text-to-speech or real-time speech-to-speech
endpoints, so an OpenRouter-only key cannot run this application; it could at most serve
the cascade chat step while OpenAI still provided STT/TTS. A provider choice must be
decided before claiming the install is usable with an OpenRouter key.
