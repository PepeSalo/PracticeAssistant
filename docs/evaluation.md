# Voice application evaluation

Real microphone/API results are **pending**. The automated tests use synthetic audio,
fake devices and fake provider streams; their durations are not application latency
measurements. Never fill the tables below with simulated timings.

## Version 1.0 user smoke-test observations (2026-10-13)

The user reported satisfactory operation after using the laptop speaker/microphone
in half-duplex mode. This is qualitative confirmation only; the structured result
tables below still require measurements.

Earlier speaker tests with the default headphones/full-duplex setting repeatedly
triggered barge-in and short/noisy discards. An incomplete reply incorrectly caused
a reconnect; this handling has been fixed, with response reasons now available in
optional diagnostic logs. An empty final transcript also caused reconnect; that
recovery behavior remains a documented limitation rather than evidence of network loss.

For speaker smoke tests:

```powershell
.\Scripts\python.exe -m practice_assistant --practice-language en --learner-language fi --echo-guard half-duplex --log-dir session-logs\speakers
```

Wait for every answer to finish; half-duplex disables microphone input during
generation/playback. Use headphones to evaluate barge-in separately.

## Preparation

Run from the application directory with the existing environment's interpreter.
Set your own `OPENAI_API_KEY` in PowerShell. Use headphones first, a quiet room and a
consistent microphone/output device. Record date, device names, network, language,
level, model, silence setting, noise conditions and echo guard for each experiment.
Stay quiet for the first second if using automatic noise calibration.

Default configuration: English practice, B1, automatic explanation-language selection,
700 ms silence, automatic RMS threshold, headphones. Speech shorter than approximately
200 ms of voiced audio is discarded; the maximum utterance duration is 30 seconds.

## 1. Response latency

```powershell
.\Scripts\python.exe -m practice_assistant --engine cascade --benchmark-runs 5 --report cascade-report.txt
.\Scripts\python.exe -m practice_assistant --engine live --benchmark-runs 5 --report live-report.txt
.\Scripts\python.exe -m practice_assistant --engine live --silence-ms 400 --benchmark-runs 5 --report live-400-report.txt
```

Use five comparable conversational utterances (not control commands). Wait until each
answer finishes before the next. Interrupted, command and failed turns do not count.
The benchmark stops **after** the fifth answer finishes. Reports are saved incrementally;
an incomplete report explicitly says to collect at least five successful turns.
The first turn is a cold proxy; subsequent turns share a client/session and are warm
proxies, not guarantees about model caches. A reconnect weakens that interpretation.

| Metric (median unless specified) | Cascade | Live 700 ms | Live 400 ms |
|---|---|---|---|
| End of speech to final transcript | Pending | Pending (auxiliary STT) | Pending (auxiliary STT) |
| Final transcript to first LLM token | Pending | Unavailable | Unavailable |
| First LLM token to first TTS PCM sample | Pending | Unavailable | Unavailable |
| End of speech to first playback callback | Pending | Pending | Pending |
| End of speech to playback completion proxy | Pending | Pending | Pending |
| Fastest / median / slowest first playback | Pending | Pending | Pending |
| Cold / warm proxy | Pending | Pending | Pending |
| Largest median component and reduction suggestion | Pending | Pending | Pending |

Definitions: speech end is the last voiced microphone block, not acoustic ground truth.
STT includes the local endpoint wait and network/service time. Cascade LLM first-token
time differs from full-answer generation. Its TTS interval begins at the first LLM token
and includes waiting for a sentence/chunk and the first HTTP audio delivery (1024-byte
reader chunks). Playback is output callback submission, **not** hardware audibility.
Completion waits for the duration of the final submitted samples, not measured device
loopback. Native STT is not measured: the live transcript is an auxiliary service.

Stage medians may not sum to the median total, and streaming stages overlap.
Live bottleneck suggestions cover only observable local components, not inferred
provider-internal stages. For true audibility, use a separately consented hardware
loopback experiment; this application does not record a loopback.

## 2. Transcription mistakes

For optional transcript logging:

```powershell
.\Scripts\python.exe -m practice_assistant --log-dir session-logs\stt --log-transcripts
.\Scripts\python.exe -m practice_assistant.evaluation --intended "I would like a coffee" --heard "I would like coffee"
```

Logs append JSON lines; use a new subdirectory per experiment. Normal logs omit text.
Adding `--log-transcripts` explicitly stores recognized input and completed replies.
Do not include sensitive or third-party conversations. Record the intended text yourself;
the application cannot know what you intended to say.

Read these phrases naturally, then repeat with an accent/hesitation:

1. I would like a small coffee, please.
2. My appointment is at thirteen thirty.
3. Could you spell the name Lehtinen?
4. Yesterday I go to the shop. (Intentional learner error.)
5. I have lived here for three years.
6. Could I have fifteen tickets, not fifty?
7. The bus stop is near the library. (Must not execute stop.)
8. She said goodbye to her friend. (Must not end the session.)
9. Haluaisin kahvin, please.
10. How do you say työhaastattelu in English?

WER = (substitutions + insertions + deletions) / reference word count; it may exceed 100%.
The helper casefolds and strips punctuation. It is a textual score, not a pronunciation
score. Transcription that silently repairs learner grammar also counts as an STT mismatch.

| Intended | Heard | WER | Tutor wrongly corrected an STT mistake? | Clarification appropriate? |
|---|---|---|---|---|
| Pending | Pending | Pending | Pending | Pending |

## 3. Background noise

Repeat the same five utterances in quiet, with a fan, with low music and with cafe noise.
Use safe listening levels. Log calibration threshold, false starts, missed speech,
discarded short utterances and turn count. Do not speak during calibration. If automatic
thresholds miss speech, try an explicit `--vad-threshold 500` (adjust for your microphone).

| Condition | RMS threshold | False starts | Missed starts | Discarded utterances | Notes |
|---|---|---|---|---|---|
| Quiet / fan / music / cafe | Pending | Pending | Pending | Pending | Pending |

The detector is energy-based with hysteresis, not a trained noise-robust speech model.
Noise changes after startup require restarting/calibrating again or a manual threshold.

## 4. Interruptions

With headphones, interrupt at the beginning, middle and end of an answer. Repeat for
both engines. Confirm that playback clears, no stale audio resumes, the new utterance
is processed and the next answer does not assume you heard the discarded text.

| Engine / interruption point | Callback stop delay | Audible tail (observed) | New turn answered? | Context correct? |
|---|---|---|---|---|
| Pending | Pending | Pending | Pending | Pending |

The report's stop delay begins at local speech-start confirmation (three voiced blocks),
not the first physical sound. It ends at the first silent output callback.
Already-submitted device audio cannot be recalled. Cascade history keeps only completely
submitted, fully generated sentences; partial sentences are omitted conservatively.
Live cancels the response and truncates server audio at submitted sample count; partial
items are omitted from the local reconnect transcript rather than guessing alignment.

## 5. Microphone/speaker echo

Compare headphones with speakers and `--echo-guard headphones`, `half-duplex` and `off`.
Headphones allow barge-in. Half-duplex suppresses input while generating/playing and
therefore intentionally disables barge-in. Off disables the transcript-similarity guard.
The heuristic flags a near-identical transcript within 300 ms of response playback onset;
it can miss delayed echo or suppress a legitimate near-immediate repetition.

| Setup / guard | Self-trigger count | False echo warnings | Barge-in works? | Notes |
|---|---|---|---|---|
| Pending | Pending | Pending | Pending | Pending |

No acoustic echo cancellation is implemented. Use headphones for normal conversation.

## 6. Network failures

Temporarily disconnect networking mid-turn, then restore it. Also test a deliberately
invalid key in a separate shell (never paste it into a report). Record what happened;
do not repeatedly issue costly calls just to provoke rate limits.
429/timeouts/5xx are simulated offline by the automated tests.

| Failure | User message | Recovery time | Context resumed? | Duplicate speech? |
|---|---|---|---|---|
| Network disconnected / invalid key | Pending | Pending | Pending | Pending |

Default: live reconnects up to the configured retry budget, reseeds bounded recent
conversation, then exits with an error if exhausted. `--fallback-cascade` permits switching
to the separate-stage engine after retryable exhaustion. Authentication/configuration
errors do not trigger fallback. The interrupted turn is not replayed; repeat it after
recovery. Cascade stage failures are printed and isolated to that turn.
Do not expect an online TTS apology when the network/TTS service itself is unavailable.

Enable `--log-dir session-logs\diagnostic` to retain timestamped response-status and
connection-failure metadata without transcripts. `incomplete` with reason
`max_output_tokens` or `content_filter` is a reply-level stop, not proof of network loss;
the application remains connected and excludes the turn from benchmark success counts.
Compare the logged live output budget (default 2048 audio/text tokens) with the reason.

## 7. End-of-speech detection

Compare 400/700/1000 ms silence with short answers, slow sentences, internal hesitations
and long utterances. Log intended utterance count separately and compare detected
endpoints in the event log. Count premature cuts and delayed endpoints.

| Silence setting | Intended turns | Detected turns | Premature cuts | Late endpoints | Best trade-off |
|---|---|---|---|---|---|
| 400 / 700 / 1000 ms | Pending | Pending | Pending | Pending | Pending |

Local endpointing is used in both engines. Realtime server VAD/semantic VAD is supported
by the SDK but deliberately disabled here: two competing endpoint detectors could
generate duplicate/early responses. A server-VAD experiment would require a separate,
tested turn-pairing path, not merely toggling a configuration field.

## Offline verification coverage

The tests cover schema validation, bounded histories, English/Finnish command parsing,
multilingual model-tool controls, synthetic VAD/noise, queue overflow, cancellation,
late-event rejection, sample accounting, metrics, incremental reports, privacy opt-in,
provider stream closure, retryable/permanent errors, reconnect context and shutdown.
Synthetic tones and delayed copies validate detector mechanics, not human recognition
accuracy or real acoustic echo behavior. Fill all pending tables from your own real tests.
