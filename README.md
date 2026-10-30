# Language Practice Assistant

**Version: 1.3**

A speech-first Python application for practising languages with a conversational tutor.
Speak naturally, receive short spoken replies, request gentle corrections and steer
the session by voice. Defaults: **English practice**, **B1**, free conversation and
automatic selection of the learner's explanation language.

## Features

- Native speech-to-speech over a persistent OpenAI Realtime connection (default).
- Optional separate-stage transcription, streaming chat and streaming TTS engine.
- Automatic speech/silence detection with startup noise calibration.
- Barge-in: speaking interrupts playback and cancels the previous response.
- Spoken controls for language, level, scenario, speaking rate, corrections and quitting.
- Bounded audio buffers and conversation history, reconnect with recent context,
  friendly failures and optional cascade fallback.
- Optional local **web chat window**: live state, what was heard, tutor replies,
  corrections, per-turn timings, session settings, quick controls and notifications.
- Latency reports and opt-in event/transcript logging.
- Spoken/printed session summary of vocabulary and corrections recorded by the tutor.

## Setup

Python 3.10+ is intended; the installed environment was tested with Python **3.14.3**.
A working microphone, compatible output device, headphones, internet access and your
own OpenAI API key with access to the selected models are needed.
API requests incur usage charges.

Headphones are recommended for voice interruption. For a laptop's built-in speaker
and microphone, use half-duplex mode instead:

```powershell
.\Scripts\python.exe -m practice_assistant --practice-language en --learner-language fi --echo-guard half-duplex
```

Wait until the assistant finishes before speaking. Half-duplex intentionally disables
voice interruption to prevent the assistant's speaker output triggering the microphone.
This setup was reported working by the user on 2026-10-13.

The project has its own virtual environment in `.venv` next to the source.

```powershell
Set-Location 'C:\path\to\practice-assistant'
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:OPENAI_API_KEY = "<your OpenAI API key>"
.\.venv\Scripts\python.exe -m practice_assistant
```

Activation is optional:

```powershell
& '.\venv\Scripts\Activate.ps1'
python -m practice_assistant
```

The repository contains source and tests, not an installed environment. For a fresh
checkout, create the environment first:

```powershell
Set-Location 'C:\path\to\practice-assistant'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:OPENAI_API_KEY = "<your OpenAI API key>"
.\.venv\Scripts\python.exe -m practice_assistant
```

The terminal prints `recording` only after the microphone starts. With automatic
calibration, stay quiet for the first second, then say something such as
"Hello, I would like to practise ordering coffee." Pause after speaking.
Say "end session" for a spoken summary and goodbye; **Ctrl+C** closes the application.

### Choose your practice

```powershell
# Spanish practice, Finnish explanations, beginner role-play
.\Scripts\python.exe -m practice_assistant --practice-language es --learner-language fi --level A2 --scenario "ordering at a cafe"

# Separate stages with sentence-by-sentence streamed speech playback
.\Scripts\python.exe -m practice_assistant --engine cascade

# A shorter endpoint wait; check that hesitation pauses do not split sentences
.\Scripts\python.exe -m practice_assistant --silence-ms 400

# Inspect device IDs without a key or opening streams
.\Scripts\python.exe -m practice_assistant --list-devices
.\Scripts\python.exe -m practice_assistant --input-device 1 --output-device 3
```


## Web chat window

Start the assistant with the chat window:

```powershell
.\Scripts\python.exe -m practice_assistant --ui
```

The terminal prints an address such as `http://127.0.0.1:8770/` and the browser opens
it. Useful flags:

```text
--ui               Open the local web chat window
--ui-port N        Default 8770
--no-browser       Print the address without opening a browser
```

The window is a **controller and transcript view**. Microphone and speaker remain native
on this PC; no audio travels over HTTP. A browser window can only be opened on this
machine.

What it shows:

- A large state pill: *Listening…*, *Thinking…*, *Speaking…*, *Interrupted*, *Ended*.
- Setting chips for practice language, level, scenario and corrections. Clicking one
  applies exactly the same change as the spoken command.
- Your words and the tutor's replies as chat bubbles. Each reply carries how long it
  took until the first audible words, and an *"you interrupted here"* marker. An
  interrupted reply shows only the part that was actually played.
- Quick buttons: *Stop speaking*, *Repeat*, *Slower*, *Faster*, *Corrections*, *End session*.
- **This session**: vocabulary and corrections recorded by the tutor, with an honest note
  that these are model-generated and incomplete.
- **Recent notifications**: timestamped session changes, warnings and errors, mirrored by
  short pop-ups, so a missed message is still recoverable.
- **Diagnostics**: engine, models, silence interval, RMS threshold, echo guard, devices,
  log and report paths.
- **What you can say**: the spoken-command table, so you do not need this file.

Typing is also possible in cascade mode; typed messages are answered aloud like spoken
ones. In live mode, speak - the text box explains this.

Keyboard: `Enter` sends typed text, `Esc` stops the assistant speaking.

Live-only timings that the API cannot expose stay labelled *unavailable*; they are not
invented.

### Safety of the local window

The server binds to `127.0.0.1` only, requires a per-run random token (delivered inside
the page, so a foreign site cannot read it), rejects a mismatched `Host` and a
cross-origin `Origin`, serves only an allowlisted set of static files, and sends a
restrictive Content-Security-Policy. Errors return a plain message, never a traceback,
and the window never contains the API key.

## Spoken controls

| Say | Result |
|---|---|
| "stop", "wait", "hold on" | Interrupt and keep listening |
| "repeat that", "say it again" | Repeat the last completed answer |
| "speak slower", "speak faster" | Adjust speaking rate |
| "make it easier", "level B2" | Change CEFR level |
| "let's practise Spanish" | Change practice language |
| "role-play ordering at a cafe", "job interview" | Change scenario |
| "correct my mistakes", "don't correct me" | Enable/disable corrections |
| "how do you say ...?", "what does ... mean?" | Ask for a mini-lesson |
| "end session", "goodbye", "quit" | Summary, goodbye and clean exit |

Direct, whole-phrase parsing supports English and Finnish (e.g. `toista`,
`puhu hitaammin`, `harjoitellaan suomi`, `lopeta harjoitus`).
Model function tools interpret additional languages and requests such as changing the
explanation language. Actual multilingual recognition/control accuracy needs real tests.
Quoted commands or phrases such as "the bus stop" are conversation, not local controls.
Commands never execute from partial transcripts or cancelled tool responses.

Local cascade confirmations and the deterministic summary use English. Live can render
confirmations in the learner's language. Corrections and learning notes are model-generated,
not an authoritative language assessment or reliable pronunciation score.

## Engines and implementation

### Live (default)

OpenAI SDK **3.29.0** was inspected and its supported request schema validated offline:
`AsyncOpenAI().realtime.connect`, session updates, completed input transcripts,
response creation/cancellation, function tools and conversation-item audio truncation.
Default model: `gpt-realtime-2.1`; input/output: mono PCM16 at 24 kHz.

The distinct newer `client.live` API used by the earlier interpreter does not expose
final input transcript events and has immutable startup instructions. This application
uses **Realtime**, which supports the controls and interruption/context lifecycle needed
here. Both are native audio APIs; their event names are not interchangeable.

Audio uploads while you speak. Local endpointing commits each utterance, and the app
waits for the final **auxiliary transcription** before requesting a native audio response.
That wait improves deterministic command handling but adds latency compared with an
automatically responding native session. Server VAD/semantic VAD is intentionally disabled
to avoid competing endpoint detectors. It is not exposed as an untested toggle.

### Cascade

Mono 16 kHz microphone audio becomes an in-memory WAV for `gpt-4o-mini-transcribe`.
`gpt-4o-mini` produces a streaming reply; completed sentences (or bounded long-text
chunks) feed `gpt-4o-mini-tts`. Returned 24 kHz PCM plays while later audio downloads
and later reply sentences are generated. The app does not wait for the full audio file.

Both engines use async SDK stream contexts, not blocking Requests worker threads.
Interrupted cascade history retains only fully generated, fully submitted sentences.
Live truncates server audio at the submitted sample count; uncertain partial text is
omitted from the local reconnect history rather than guessed.


## Installing on another Windows PC from a USB stick

The target PC needs internet access (for the AI service). It does **not** need a GitHub
account, a pre-installed Python, or administrator rights.

Build the folder on the development PC while it still has internet:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_usb_bundle.ps1
```

That creates `dist\PracticeAssistant-<version>-usb\` (about 0.2 MB). Copy the whole
folder to the USB stick, then to the target PC.

On the target PC:

1. Open the copied folder and double-click **`Install.cmd`**.
2. If Windows shows "Windows protected your PC", choose *More info* -> *Run anyway*.
3. Follow the prompts. The installer checks the PC, finds or downloads Python, creates a
   private environment in `%LOCALAPPDATA%\PracticeAssistant`, installs the packages,
   copies the application, creates Desktop and Start Menu shortcuts, optionally stores
   the API key, and verifies the result.
4. Start **Practice Assistant** from the Desktop.

### Automatic updates from GitHub (optional)

If the PC can read the project repository, the assistant can check for a newer version
each time it starts. This is opt-in and never blocks starting the program.

- The installer asks once for a repository URL; press Enter to skip and nothing ever
  contacts GitHub.
- Settings live in `%LOCALAPPDATA%\PracticeAssistant\update.ini` (`RepoUrl`, `AutoUpdate`).
  Delete the file to stop checking.
- Updates need Git (git-scm.com). The installer offers to install it with `winget`; without
  Git the check is skipped and the installed version keeps working.
- `Update Practice Assistant.cmd` updates on demand. `PA_NO_UPDATE=1` skips the startup
  check once.

The updater is deliberately conservative:

- `git pull --ff-only` only; diverged history is reported, never merged, and nothing is
  force-overwritten.
- Uncommitted local changes cause the update to be skipped.
- Offline, unreachable, or unauthenticated private repositories produce a warning, and the
  program starts normally.
- A version change re-runs `pip install -r requirements.txt`.
- **No credentials are stored by the installer**, and the token is never written to
  `update.ini` or `.git/config`. Options:
  - *Simplest*: the installer (or `Update Practice Assistant.cmd`) asks once and hands the
    token to **Git Credential Manager**, which keeps it encrypted by Windows.
  - *Advanced*: set `PA_GITHUB_TOKEN` in the environment; it is passed to Git as an HTTP
    header in memory only.
  Create a fine-grained token scoped to that one repository with **Contents: read-only**.
  No write access is requested or needed, and never use an account password.

Verified with a real local Git repository as a stand-in for GitHub: first clone, a version
bump propagating (1.1 to 1.2 including a new upstream file), local edits being preserved,
and an unreachable repository warning while the installed copy stayed intact. Not verified
against the real GitHub repository, nor on the target PC.

Re-running `Install.cmd` upgrades in place. If a step fails, the installer stops with a
readable reason and writes details to `%TEMP%\practice-assistant-install.log` (no key is
ever written there). `INSTALL.txt` in the bundle covers the recovery steps, including
headphone/echo advice for laptops.

Verified: a real install into a temporary directory on this development PC (Windows 10,
Python 3.14) passed preflight, package installation, file copy, launcher creation and the
`--help` verification, and the installed copy served the chat window with its assets.
Not verified: a clean Windows 10/11 virtual machine, the actual target PC over USB, and
Windows 11 ARM64 (expected to work through x64 emulation).

## Options

Use `.\Scripts\python.exe -m practice_assistant --help` for complete help.

```text
--engine {live,cascade}          Default: live
--practice-language CODE        Default: en
--learner-language CODE         Default: auto; explanation language
--level {A1,A2,B1,B2,C1,C2}      Default: B1
--scenario TEXT                 Default: free-conversation
--voice NAME                    Default: alloy
--silence-ms MS                 Default: 700; accepted range 200–3000
--vad-threshold RMS|auto        Default: auto; one-second calibration
--echo-guard {headphones,half-duplex,off}
--no-correction                 Disable automatic corrections
--log-dir PATH                  Opt in to append-only JSON event logs
--log-transcripts               Also log text; requires --log-dir
--benchmark-runs N              At least five completed conversational turns
--report PATH                   Incrementally replace the timing report
--timeout SECONDS               Default: 30
--max-retries N                 Default: 3 retries after the initial attempt
--fallback-cascade              Opt in after retryable live exhaustion
--list-devices                  Print device IDs/names without starting audio
--input-device ID|NAME
--output-device ID|NAME
--live-model NAME               Default: gpt-realtime-2.1
--live-max-output-tokens N       Default: 2048; audio + text budget, 512–4096
--ui                            Open the local web chat window
--ui-port N                     Default: 8770
--no-browser                    Print the address without opening a browser
```

Practice and explanation languages may be the same; this is a tutor, not a translator
requiring different source/target languages.

## Latency and evaluation

```powershell
.\Scripts\python.exe -m practice_assistant --engine cascade --benchmark-runs 5 --report cascade-report.txt
.\Scripts\python.exe -m practice_assistant --benchmark-runs 5 --report live-report.txt
```

Speak five conversational utterances, waiting for each answer to finish. Controls,
interrupted and failed turns are excluded. The benchmark ends **after** final playback,
not at the first sample. An existing report is replaced atomically after each completed
turn so previously saved results survive a failed write.

Cascade reports measure:

- Speech end to final transcription.
- Final transcript to first LLM text token (not the full reply).
- First LLM token to first complete TTS PCM sample, including sentence assembly,
  network and client buffering (1024-byte HTTP reader chunks).
- Speech end to first output callback containing response audio.
- Speech end to the duration of the last submitted audio sample elapsing.
- Silence detection, audio-to-playback, fastest/median/slowest first playback,
  cold/warm proxies, largest median component and a reduction suggestion.

Live reports expose auxiliary transcription and client audio boundaries.
Independent native LLM/TTS stage delays are explicitly **unavailable**.
Live bottleneck advice covers only local observable components, not internal inference.

All timings use `time.perf_counter()`. Speech end is the last voiced microphone block,
not a physical speech-end measurement. Playback callback submission is **not audible
loopback**; device buffering and the audible tail are unmeasured. Stage medians do not
necessarily sum to the median total; streaming stages overlap.

See [evaluation.md](docs/evaluation.md) for the seven-part protocol: latency,
transcription errors, noise, interruption, echo, network failure and endpointing.
Its real-results tables are deliberately **pending**, not filled with simulated numbers.

## Reliability and limitations

- Use headphones. No acoustic echo cancellation is implemented.
- `half-duplex` suppresses microphone input during generation/playback and disables
  barge-in. `off` disables the optional transcript-similarity echo guard.
- The echo heuristic examines near-identical text within 300 ms of playback onset;
  it can miss delayed echo or mistake a quick legitimate repetition for echo.
- VAD is amplitude-based, with three-block speech confirmation, hysteresis, about
  200 ms minimum voiced duration, 30 s maximum utterance and a configurable silence
  wait. Calibration does not track later changes in background noise.
- Already-submitted device audio cannot be recalled. Reported interruption delay
  starts at local speech confirmation and ends at a silent output callback.
- Devices that reject mono 16/24 kHz fail with an actionable error; there is no implicit
  resampling. Queues are bounded; overflow/stalled playback is an explicit error.
- Live reconnect is application-owned, uses bounded exponential backoff/jitter and
  reseeds recent conversation. Exhaustion exits by default. Optional cascade fallback
  applies only to retryable failures, not authentication/configuration errors.
- A disconnected/interrupted turn is not automatically replayed. Repeat it after
  recovery. Cascaded stage failures are printed and isolated to that turn.
- Known limitation: an empty final live transcript currently triggers reconnect rather
  than just discarding the turn. This is not proof of network loss. Speaker echo/noise
  can contribute; use headphones or speaker half-duplex mode.
- A live reply with status `incomplete` is not a disconnected WebSocket. The application
  reports its reason (output-token limit, content filter or unspecified), finishes any
  received audio and keeps listening on the same connection. Incomplete replies do not
  count as successful benchmark turns; their unfinished tools never execute.
  The live output budget includes audio tokens, so it is higher than the cascade text
  budget. `--live-max-output-tokens` changes that ceiling, not the desired reply length.
- Retryable HTTP failures include connection/timeouts, 429 and 5xx. `Retry-After` is
  honored with a bounded wait. Partially emitted audio/text is never blindly replayed.
- An online TTS apology cannot be guaranteed when the network/TTS service is unavailable;
  terminal error notifications remain available.
- Conversation context is bounded. Older context is retained as short excerpts, not
  a verified semantic summary. Learning notes can be incomplete or mistaken.

## Privacy

Audio is processed in memory and sent to OpenAI; recordings are not saved locally.
Reports contain numeric timings and configuration, not transcripts or keys.
Logs are disabled unless requested. `--log-dir` alone omits text;
`--log-transcripts` explicitly enables input/completed-response text.
Use a different log directory for each experiment; event logs append to `events.jsonl`.

### Troubleshooting interrupted conversations

No file log exists for a run started without `--log-dir`. Enable a diagnostic log
without storing spoken text:

```powershell
.\Scripts\python.exe -m practice_assistant --practice-language en --learner-language sv --log-dir session-logs\diagnostic
Get-Content .\session-logs\diagnostic\events.jsonl -Tail 30
```

Each event includes a UTC timestamp and session ID. Response-end events record status,
reason, error code and token budget; connection failures record the reason and whether
the app will reconnect. Speech-start/discard events help distinguish local barge-in
from provider or transport failures. Share relevant diagnostic lines, not API keys.

If `Speech detected` / `Interrupted` is followed by a short/noisy discard when you did
not speak, noise or speaker leakage may be triggering the local detector. Use headphones
and try `--vad-threshold 500`; adjust for your microphone and verify quiet speech still
registers. For speakers use `--echo-guard half-duplex` (intentionally disables barge-in).
The default automatic threshold is unchanged; a low calibrated value can be sensitive
to brief sounds.

Never hard-code, share or commit credentials, environment folders or recordings.
This local application uses the user's environment key; it embeds no permanent key.
For a distributed/browser client, a backend with short-lived client credentials would
be required. Consider provider data-retention policies before speaking sensitive content.

## Development and verification

```powershell
$env:PYTHONUTF8 = "1"
.\Scripts\python.exe -m pip install -r requirements-dev.txt
.\Scripts\python.exe -m pytest -q
.\Scripts\python.exe -m ruff check practice_assistant tests
$files = @(Get-ChildItem practice_assistant, tests -Recurse -Filter *.py | ForEach-Object FullName)
.\Scripts\python.exe -m black --check --workers 1 @files
.\Scripts\python.exe -m pip check
```

Two environment notes for this virtual-environment-root layout, both found while
building version 1.1:

- `black --check .` and a bare `black practice_assistant tests` appear to hang here
  because Black's multiprocessing pool does not come back on this Windows setup. Passing
  `--workers 1` (as above) finishes in a second.
- Black's Unicode banner fails to print in a legacy console code page, which can make a
  passing run look like a failure. `$env:PYTHONUTF8 = "1"` avoids that.

Verified on Python 3.14.3: **211 offline tests passed**, Ruff/Black passed, source
compiled and dependency consistency passed. Tests fake SDK requests, streams,
connections and devices, with an external-network guard. No actual API calls,
microphone use or real latency measurements were made during implementation.
Real device compatibility and speech behavior must be checked using the evaluation
protocol. Pylance excludes this virtual-environment-root layout from its user-file
syntax checker; executable tests and standard-library compilation supplied validation.

The user reported that laptop speaker/microphone use worked after switching to
half-duplex mode on 2026-10-13. Earlier full-duplex speaker tests showed false barge-in,
discarded short/noisy turns and an empty-transcript reconnect. This is qualitative
feedback, not a completed noise/echo evaluation or five-run latency benchmark.

Developed with an AI assistant using Copilot SDK in VS Code, model **GPT-6.1 Sol**.
The initial implementation plan was prepared with **Claude Sonnet 5.5**.
These are development attributions, not runtime model dependencies.

## Version history

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-10-13 | Initial versioned release: both speech engines, voice controls, interruption, response recovery, diagnostics, offline tests and evaluation protocol. |
| 1.1 | 2026-10-13 | Local web chat window with notifications, per-turn timings, settings chips, quick controls and typed turns; USB installer (`Install.cmd`), `INSTALL.txt`, bundle build script and installer tests. |
| 1.2 | 2026-10-13 | Optional automatic version updates from a GitHub repository (`install/update.ps1`, `Update Practice Assistant.cmd`, update check at startup), configurable repository URL, and documentation of the OpenRouter limitation. |
| 1.3 | 2026-10-13 | Read-only GitHub token support for private repositories: optional one-time sign-in through Git Credential Manager, `PA_GITHUB_TOKEN` for automated setups, with the token never written to `update.ini`, `.git/config` or logs. OpenAI confirmed as the only provider. |

Start numbering at 1.0 and increase by exactly 0.1 for every subsequent completed
code, configuration or documentation change set. Update this version, add a history
entry and keep the project version in `pyproject.toml` synchronized. Do not increase
the version for individual edits within the same change set.
