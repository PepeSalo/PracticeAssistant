AI Audio: Text-to-Speech, Speech-to-Text/Speech, AI Music
AI audio includes four main areas:

Text-to-Speech (TTS): text → spoken audio

Speech-to-Text (STT / ASR): spoken audio → text

Speech-to-Speech: spoken audio → AI reasoning → spoken response

AI music generation: prompt/lyrics/audio → music

Modern models usually represent audio as spectrograms, learned audio tokens or continuous latent representations. STT converts acoustic representations into text. TTS converts text or phonetic information into acoustic tokens/latents and then reconstructs a waveform with a neural codec or vocoder. Native speech-to-speech models can process and generate audio directly instead of using a separate STT → LLM → TTS pipeline. Music models commonly combine transformer/LLM planning with diffusion, flow-matching or neural audio-codec generation.

Text-to-Speech
Text-to-Speech converts text into spoken audio. Modern TTS can control voice, pronunciation, pace, emotion and speaking style.

Recommended: ElevenLabs
For this course, ElevenLabs is the default recommendation for high-quality TTS.

Current useful choices include:

Eleven v3: highest expressiveness and quality; 70+ languages.

Eleven Flash v2.5: designed for low-latency applications; ElevenLabs reports approximately 75 ms model latency.

Eleven v3 Conversational: expressive real-time speech.

Finnish is supported by the multilingual models.

ElevenLabs supports both ordinary HTTP generation and WebSocket streaming. For an AI assistant, start playing audio as chunks arrive instead of waiting for the complete audio file.

Other closed-source alternatives
OpenAI: TTS, STT and native speech-to-speech are available. GPT-Realtime-2.1 supports audio input/output and tool use, while GPT-4o Transcribe is available for transcription.

Google Gemini: Gemini provides dedicated TTS and the Gemini Live API. Current Gemini TTS supports streaming audio, while Live provides bidirectional real-time audio over WebSockets.

Grok / xAI: xAI now provides separate TTS, STT and real-time speech-to-speech APIs. Its real-time API uses bidirectional WebSockets.

Open-source TTS
Chatterbox Multilingual V3 is a strong local alternative. It is MIT licensed, supports 23 languages including Finnish, and supports voice cloning.

Qwen3-TTS is an Apache-2.0 family with 0.6B and 1.7B models, streaming generation, voice cloning and natural-language voice design. It currently supports ten major languages, but not Finnish.

Pocket TTS from Kyutai is interesting for standalone applications because its 100M-parameter model can run on CPU, supports streaming and has a Python API. Kyutai reports roughly 200 ms to the first audio chunk on its reference setup.

Speech-to-Text
Speech-to-Text, or Automatic Speech Recognition (ASR), converts spoken audio into text.

Modern STT systems must handle much more than clean dictation:

background noise

accents and dialects

several speakers

overlapping speech

names and technical vocabulary

code-switching between languages

punctuation and formatting

timestamps and speaker identification

Closed-source choices
ElevenLabs Scribe v2 supports transcription in 90+ languages, timestamps and speaker diarization. Scribe v2 Realtime streams partial and committed transcripts over WebSockets; ElevenLabs reports approximately 150 ms model latency.

OpenAI's newer transcription models improve on the original Whisper models. GPT-4o Transcribe is intended for high-accuracy transcription, while OpenAI also provides dedicated real-time transcription models.

Gemini can transcribe and analyze recorded audio, but Google recommends its Live API or dedicated Cloud Speech-to-Text products for real-time use.

Open-source STT
Qwen3-ASR is one of the most interesting 2026 open models. The 0.6B and 1.7B Apache-2.0 models support both offline and streaming recognition, timestamps and multilingual transcription. Finnish is included.

NVIDIA Parakeet-TDT-0.6B-v3 is a 600M-parameter multilingual ASR model covering 25 European languages, including Finnish. It supports punctuation, capitalization and timestamps and is released under CC BY 4.0.

Whisper remains useful because of its mature ecosystem and local implementations, but it should now be taught as an established baseline rather than the newest state-of-the-art option. OpenAI itself reports improved recognition from its newer transcription models compared with original Whisper models.

Speech-to-Speech
There are two basic architectures.

Cascaded
microphone → STT → LLM/application → TTS → speaker

Advantages: easy to inspect, debug, translate, moderate and log.

Disadvantages: every stage adds latency.

Native real-time audio
microphone → multimodal speech model → speaker

Modern APIs from OpenAI, Gemini and Grok can keep a persistent real-time audio session instead of making independent STT, LLM and TTS requests.

Native audio usually gives better conversational flow, but application logic becomes harder: transcripts, interruptions, tool calls, audio playback and conversation state must all be synchronized.

Latency: the main problem in real-world voice applications
A model being "fast" does not mean the application feels fast.

A voice application's delay can contain:

microphone buffering → turn detection → network → STT → AI reasoning → tools → TTS → network → playback buffer

The delay produced by silence detection can be especially important. The program cannot answer until it decides that the user has actually finished speaking.

Measure at least:

End of speech → final transcript

Final transcript → first AI response token

AI response → first TTS audio chunk

End of speech → first audible response

Total response completion time

Use time.perf_counter() in Python and report several runs rather than one measurement. Test both cold and warm requests.

Vendor latency numbers such as ElevenLabs' ~75 ms Flash TTS or ~150 ms realtime transcription are only one part of the complete user-visible delay.

Common causes of delay and failure
Turn detection / VAD: Too short a silence threshold interrupts the user. Too long a threshold makes the assistant feel slow.

Barge-in: A real conversation requires the user to interrupt the AI. Playback must stop immediately and the new microphone input must become the active turn.

Echo: If the microphone hears the AI's own speaker output, the system may transcribe and answer itself. Headphones, acoustic echo cancellation or careful audio routing may be necessary.

Partial transcripts: Streaming STT results can change. Do not trigger irreversible actions from unstable partial text.

Audio formats: APIs may require 16 kHz, 24 kHz or other sample rates; mono/stereo, PCM16, WAV, MP3 and Opus are not interchangeable. Incorrect resampling is a common source of distorted or rejected audio.

Network jitter: WebSocket packets can arrive unevenly. Playback normally needs a small buffer, but larger buffers increase latency.

Backpressure: Audio may be produced faster than your program sends or plays it. Unbounded queues eventually consume memory.

Tool calls: Database queries, web searches or other APIs can dominate the latency even when the speech model itself is fast.

Errors and rate limits: Production applications need timeouts, retries with exponential backoff, reconnect logic and graceful fallbacks.

Secrets: Never place permanent API keys inside distributed desktop/client applications. Where supported, use short-lived or ephemeral client tokens and keep permanent credentials on a backend. Gemini, ElevenLabs and xAI document this pattern for client-side real-time applications.

Privacy and consent: Voice recordings can contain biometric and personal information. Voice cloning should only be done with permission.

Audio in Python
Useful packages and tools:

sounddevice     Microphone recording and speaker playback
soundfile       Read/write WAV, FLAC and other audio
numpy           PCM sample buffers
wave            Simple WAV handling from the Python standard library
ffmpeg          Conversion, resampling and codecs
asyncio         Concurrent recording, networking and playback
websockets      Bidirectional real-time API connections
httpx           REST APIs and HTTP streaming
python-dotenv   Development-time API key loading
For basic assignments, sounddevice + soundfile + numpy is sufficient.

For real-time applications, avoid the pattern:

record entire file → upload → wait → download entire result → play

Prefer:

microphone chunks → WebSocket → partial results → streamed output chunks → playback

Streaming lowers perceived latency because processing, networking and playback can overlap.

AI Music Generation
Modern AI music generation can create complete songs containing arrangement, instruments, vocals and lyrics rather than merely predicting notes or MIDI sequences.

At a high level, current systems encode music into learned audio representations. Transformer/LLM components can plan lyrics and long-term musical structure, while diffusion, flow-matching or codec decoders generate the detailed audio.

Recommended commercial API: Suno v5.5
Suno v5.5 was released in March 2026 and is the main commercial music generator recommended for this course. It improves vocals, arrangements and personalization and supports features including Voices and Custom Models.

Importantly, Suno now has an official REST API for generating songs, covers and mashups. There is no longer a need to teach unofficial reverse-engineered Suno APIs as the normal solution.

MiniMax Music 3.0
MiniMax Music 3.0 is another strong 2026 music-generation API. It supports full-song generation, lyrics and instrumental generation through MiniMax's official API. It was released in July 2026.

However, MiniMax Music 3.0 is proprietary, not open source.

Open-source music generation
ACE-Step 1.5 is the main open-source recommendation. It is MIT licensed, runs locally, supports long-form songs, multilingual lyrics, reference audio, editing and LoRA personalization.

Other 2026 research options include HeartMuLa, an Apache-2.0 family of music models, and Muse, an MIT-licensed reproducible long-form song-generation project with public model/data/training pipelines.

Music generation has different latency requirements from conversation: waiting several seconds for a complete song may be acceptable. The more important engineering problems are job queues, progress/status polling, long-running API requests, storage of large audio files and handling failed generations.

Copyright, training-data provenance, voice imitation and commercial-use terms must also be considered separately from technical model quality.