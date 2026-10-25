# Audiobook

« Écouter » in the web reader reads the book aloud with a text-to-speech server: a few paragraphs at a time, just before they are played, the sentence read underlined and the pages following it. The book is never generated whole and no audio is kept on the server. Each book can have its own voice and reading tone, and the voices can be cloned from a short recording, designed from a written description.

Optional and off by default: it needs an OpenAI-compatible text-to-speech server (`POST /v1/audio/speech`), for instance an LLM proxy in front of a local model server running [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice). It is part of the full server (not in `MINIMAL_MODE`) and offered to signed-in readers only: every request costs the speech server a generation.

- [Listening](#listening)
- [How it works](#how-it-works)
- [Settings](#settings)
- [Voices](#voices)
- [Making a voice](#making-a-voice)
- [With curl](#with-curl)
- [Endpoints](#endpoints)
- [Troubleshooting](#troubleshooting)
- [Code](#code)

## Listening

- **Start**: « Écouter » (headphones) in the bottom bar of the web reader reads from the page shown. The button only appears for a signed-in reader when a speech server is configured.
- **Player**: a floating bar while reading (also on the phone's lock screen, Media Session): pause / resume, hear the sentence again, next sentence, « Voix et ton », stop.
- **Following**: the sentence read is underlined and the pages turn with it, chapter after chapter. The reading position and the statistics move with the listening, like a reading: continue on KoReader or a CrossPoint reader where the audio stopped.
- **Voix et ton** (per book, kept by the server for each reader): the **voice** (the cloned voices of the settings first, then those of the model, or typed) and the **reading tone**, a free prompt (« voix grave et posée, ton de conteur, lecture lente »). Empty: the defaults of the settings. A change applies from the sentence read. The **speed** (0.8× to 1.5×) is kept in the browser.
- Phones may refuse to start the sound without a tap: the player then waits on pause, a tap on play starts it.

## How it works

1. foliate-js's TTS module (`static/vendor/foliate/tts.js`, vendored unchanged) splits each chapter into blocks (paragraphs, titles) and sentences, starting from the place shown.
2. The sentences of several paragraphs are joined into one request, so that the voice keeps the same tone across them; line breaks between paragraphs are kept, as pauses. The requests grow: about **200 characters** for the first one, so that the sound starts within seconds, then **500**, then up to **`max_chars`** (1,000 by default). A chapter always ends its request.
3. Each request goes to `POST /webui/audiobook/speech` with the book's voice and tone; the server relays it to the speech server and answers the audio. One request is played while the **next two** are prepared, one at a time per reader, and at most `concurrency` requests reach the speech server for all the readers.
4. While a request plays, the sentences are underlined in turn (their place in the audio is estimated from their place in the text); « next sentence » and « hear the sentence again » move inside the audio.

With Qwen3-TTS 1.7B on a local GPU, a request is generated 2 to 3 times faster than it plays (a sentence of 6 seconds in about 3 seconds, a request of 1,000 characters, about a minute of speech, in 20 to 25 seconds): the requests follow each other without waiting.

Only the text being read is sent, with the voice and the tone; every request is made by the server, which holds the API key (the browser never sees it).

## Settings

The settings are read from [`audiobook.toml`](../audiobook.toml) (all keys documented, audiobook off), completed by the file named by **`AUDIOBOOK_CONFIG_PATH`** (same keys, its values win; `config/audiobook.toml` with the Docker compose file). String values may read the environment: `"${TTS_API_KEY}"`. Restart the server after editing it. The audiobook is off while `url` or `model` is empty.

| Key | Default | Meaning |
|---|---|---|
| `url` | | Base URL of the speech server (`/v1/audio/speech` and `/v1/audio/voices` are appended) |
| `model` | | Model of the requests, for the voices that are not cloned (an llm-proxy names it with its backend: `silentchuck/…`) |
| `api_key` | | Bearer token sent to the server (empty: no `Authorization` header) |
| `voice` | | Default voice: a voice of `model` or a cloned voice (some models require one) |
| `instructions` | | Default reading tone, sent as `instructions_field` (not used by cloned voices) |
| `instructions_field` | `instructions` | Request field carrying the tone (empty: never sent) |
| `response_format` | `wav` | Audio format asked for: `wav`, `mp3`, `opus`, `aac` or `flac` (what the server produces and the browsers play) |
| `voices` | `[]` | Voices offered to the readers (empty: asked to the server, `GET /v1/audio/voices?model=…`) |
| `max_chars` | `1000` | Characters of a request at most (50 to 4,000): longer requests keep the tone longer, but take longer to generate |
| `timeout` | `120` | Seconds allowed for one request |
| `concurrency` | `2` | Requests sent to the speech server at the same time, for all the readers |
| `clone_model` | | Voice cloning model, reading the cloned voices (Qwen3-TTS « Base ») |
| `design_model` | | Voice design model, used by the [voice tool](#making-a-voice) only (Qwen3-TTS « VoiceDesign ») |
| `[extra]` | | Fields added to every request: model options, e.g. `language = "French"` |
| `[clones.<name>]` | | A cloned voice: `audio` (WAV recording, path relative to the settings file's folder) and `text` (its word-for-word transcript) |

### Example: Qwen3-TTS behind an llm-proxy

```toml
url = "http://llm-proxy:8000"
api_key = "${TTS_API_KEY}"
# Ready-made voices and a reading tone (CustomVoice)
model = "silentchuck/qwen3-tts-12hz-1.7b-customvoice"
instructions = "Narrateur professionnel de livre audio : voix masculine grave, chaleureuse et posée. Diction claire et soignée, rythme calme et régulier, intonation naturelle et nuancée qui suit le sens du texte. Courtes pauses aux virgules, pauses plus marquées entre les phrases et les paragraphes. Dialogues interprétés avec sobriété, sans emphase théâtrale."
# Cloned voices (Base) and the voice tool (VoiceDesign)
clone_model = "silentchuck/qwen3-tts-12hz-1.7b-base"
design_model = "silentchuck/qwen3-tts-12hz-1.7b-voice-design"
voice = "narrateur"
response_format = "wav"
max_chars = 1000
concurrency = 2

[extra]
language = "French"   # otherwise the model guesses the language of each request

[clones.narrateur]
audio = "/app/voix/french/narrateur.wav"
text = "Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé."

[clones.narratrice]
audio = "/app/voix/french/narratrice.wav"
text = "Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé."
```

The Qwen3-TTS 1.7B models, as an llm-proxy in front of a gufo server names them:

| Model | Use | Requires |
|---|---|---|
| `qwen3-tts-12hz-1.7b-customvoice` | `model`: 9 ready-made voices, reading tone | a `voice` |
| `qwen3-tts-12hz-1.7b-base` | `clone_model`: reads with the voice of a recording | `reference_audio` (base64 WAV) and `reference_text`, sent by the server for a cloned voice |
| `qwen3-tts-12hz-1.7b-voice-design` | `design_model`: invents a voice from a description | non-empty `instructions` (the description) |
| `qwen3-asr-1.7b` | speech recognition (audio to text): **not** a speech model | |

## Voices

### Ready-made voices

The CustomVoice model has 9 voices; none is a native French speaker, but all speak French (Qwen3-TTS reads 10 languages). Read back by the speech recognition model, the 5 male voices were all understood word for word in French; they differ in timbre and pace, the choice is made by ear.

| Voice | Description (model card) | Native language |
|---|---|---|
| `uncle_fu` | seasoned man, low and mellow | Chinese |
| `ryan` | man, dynamic, strong rhythm | English |
| `aiden` | man, sunny American, clear midrange (the fastest) | English |
| `dylan` | young man, clear and natural | Chinese (Beijing) |
| `eric` | man, lively, slightly husky (the slowest) | Chinese (Sichuan) |
| `vivian` | young woman, bright | Chinese |
| `serena` | young woman, warm and gentle | Chinese |
| `ono_anna` | woman, playful and light | Japanese |
| `sohee` | woman, warm and emotional | Korean |

The reading tone (`instructions`, and « Voix et ton » per book) steers them: written in French or in English, it changes the delivery, not the intelligibility.

### Cloned voices

A cloning model reads with the voice of a short WAV recording: the same voice from one request to the next, with the accent of the recording, so a French reference gives a native French voice. Each `[clones.<name>]` voice is offered to the readers before those of `model`, and can be the default `voice`. Its recording is sent with every request (`reference_audio`, base64, and `reference_text`, the transcript); the reading tone is not used, the voice reads as recorded.

Two French voices come with the project, in [`voix/french/`](../voix/french/README.md) (`/app/voix/french/` in the Docker image), both designed from a description: **narrateur** (a man in his fifties, low, warm and velvety) and **narratrice** (a woman in her forties, warm, soft and slightly low).

Model servers that load one model at a time (llama-swap) switch models when the books use both a cloned voice and a ready-made one: the first request after a switch takes a few more seconds.

**Clone only voices you may use**: a voice designed from a description, your own, or one whose owner agreed. A narrator's or a public figure's voice is protected like their image, and published recordings (audiobook samples…) by their publisher's rights.

## Making a voice

`python -m controllers.tools.voice_design` makes a cloned voice: a reference recording, normalized, and an example read with it, to listen to before keeping the voice.

```bash
# From a written description, read by the voice design model (design_model): no real person's voice
python -m controllers.tools.voice_design narrateur "Voix masculine française d'une cinquantaine d'années, grave, chaude et veloutée, légèrement feutrée. Narrateur professionnel de livre audio : diction parfaite, accent français standard, débit posé, intonation naturelle et expressive sans excès."
# From your own voice: read the reference text below (about fifteen seconds, in a quiet room), as a WAV file
python -m controllers.tools.voice_design moi --recording ma-voix.wav --text "Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé."
# In the Docker image: files written to /config/voix/, next to AUDIOBOOK_CONFIG_PATH (nothing played)
docker exec cops python -m controllers.tools.voice_design narrateur "…"
```

1. **Reference**: a WAV file, designed from the description (about fifteen seconds of a fixed French text, `--text` to read another one; `--model` for another design model) or read from `--recording` (PCM WAV, any rate and channels, with its word-for-word transcript `--text`).
2. **Normalization** (standard library only): mono 24 kHz, DC offset removed, silences cut at both ends (150 ms kept), level set to -20 dBFS RMS with peaks at -1 dBFS at most, 10 ms fades. The cloning model copies the level, the room and the pauses of its reference: a clean, even recording gives a clean voice. A reference of 3 to 20 seconds clones best (a warning is printed otherwise). Written to `voix/<name>.wav` next to the settings file, or `--output`.
3. **Example**: the cloning model (`clone_model`) reads a short passage with the new voice (`--example` for another text), written to `voix/<name>-exemple.wav`.
4. **Listening**: the reference, then the example, are played with the first player installed (`aplay`, `paplay`, `afplay`, `ffplay`; `--no-play` to skip; in Docker, copy the files to listen to them).
5. The `[clones.<name>]` table to add to the settings is printed; then restart the server.

The design model invents the voice again on each call: run the tool again until the example pleases. Describe the gender, age, timbre, accent and reading style; the two voices of `voix/french/` came from the descriptions of [their README](../voix/french/README.md).

## With curl

The same steps by hand, straight to the speech server (here an llm-proxy in front of Qwen3-TTS; the model names carry its backend prefix). The JSON bodies are built with `jq`: a reference recording in base64 weighs about a megabyte, too much for a command line.

```bash
export TTS_URL=http://llm-proxy:8000 TTS_API_KEY=…
TEXT="Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé."
```

**Voices of a model:**

```bash
curl -sS "$TTS_URL/v1/audio/voices?model=silentchuck/qwen3-tts-12hz-1.7b-customvoice" \
  -H "Authorization: Bearer $TTS_API_KEY" | jq -c '[.data[].id]'
```

**A ready-made voice** (CustomVoice: `voice` required, `instructions` optional):

```bash
curl -sS -o essai.wav "$TTS_URL/v1/audio/speech" -H "Authorization: Bearer $TTS_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "silentchuck/qwen3-tts-12hz-1.7b-customvoice", "voice": "ryan", "language": "French",
       "instructions": "Voix posée de conteur", "input": "Bonjour, ceci est un essai.", "response_format": "wav"}'
aplay essai.wav
```

**1. Design a voice** (VoiceDesign: the description goes in `instructions`, the reference text in `input`; a new voice on each call):

```bash
jq -n --arg text "$TEXT" --arg description "Voix masculine française d'une cinquantaine d'années, grave, chaude et veloutée. Narrateur professionnel de livre audio : diction parfaite, débit posé, intonation naturelle." \
  '{model: "silentchuck/qwen3-tts-12hz-1.7b-voice-design", input: $text, instructions: $description,
    language: "French", response_format: "wav"}' > design.json
curl -sS -o design.wav "$TTS_URL/v1/audio/speech" -H "Authorization: Bearer $TTS_API_KEY" \
  -H "Content-Type: application/json" -d @design.json
aplay design.wav
```

Or record your own voice reading `$TEXT` (WAV), and check its transcript with the speech recognition model: the cloning model needs it word for word.

```bash
curl -sS "$TTS_URL/v1/audio/transcriptions" -H "Authorization: Bearer $TTS_API_KEY" \
  -F model=silentchuck/qwen3-asr-1.7b -F language=fr -F file=@ma-voix.wav | jq -r .text
```

**2. Normalize the reference** with ffmpeg, the same targets as the voice tool (which does it in Python, without ffmpeg; its silence threshold is relative to the loudest part and its level an RMS, so results are close, not identical): mono 24 kHz, silences cut at both ends (the `areverse` pair trims the end; `stop_periods` would cut at the first pause of the sentence), level at -20 LUFS with peaks at -1 dB.

```bash
ffmpeg -i design.wav -ac 1 -ar 24000 -c:a pcm_s16le -af "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse,loudnorm=I=-20:TP=-1:LRA=11" voix.wav
```

**3. Read with the cloned voice** (Base: the recording in `reference_audio`, base64 WAV, and its transcript in `reference_text`, both required; no `voice` nor `instructions`):

```bash
base64 -w0 voix.wav > voix.b64
jq -n --rawfile audio voix.b64 --arg reference "$TEXT" --arg text "Le meunier soupira longuement. Il savait que le chat avait raison." \
  '{model: "silentchuck/qwen3-tts-12hz-1.7b-base", input: $text, reference_audio: $audio, reference_text: $reference,
    language: "French", response_format: "wav"}' > clone.json
curl -sS -o exemple.wav "$TTS_URL/v1/audio/speech" -H "Authorization: Bearer $TTS_API_KEY" \
  -H "Content-Type: application/json" -d @clone.json
aplay exemple.wav
```

When the example pleases, declare the voice in the settings (`[clones.<name>]` with `audio = "voix.wav"` and `text` = the transcript) and restart the server: pyopds sends the same request for every passage read with it.

A model server that loads one model at a time answers « backend … hors ligne » (or takes longer) while it switches models: try again a few seconds later.

## Endpoints

All in the full server only, for signed-in readers (`auth='user'`), and `404` while no speech server is configured.

| Endpoint | Answer |
|---|---|
| `GET /webui/audiobook?path=<EPUB>` | `{voice, instructions, defaults: {voice, instructions}, max_chars}`: the book's voice and tone for the reader (`''`: the defaults) |
| `PUT /webui/audiobook` `{path, voice, instructions}` | Keeps them (in the reader's preferences of the sync database, per book document); answers like `GET` |
| `GET /webui/audiobook/voices` | `{voices}`: the cloned voices, then those of the settings or of the model on the server |
| `POST /webui/audiobook/speech` `{text, voice, instructions}` | The audio of `text` (`max_chars` at most; empty voice and tone: the defaults). `502` when the speech server fails or is unreachable, `503` when it is busy, `504` when too slow, `500` when a cloned voice's recording cannot be read |

## Troubleshooting

The player shows « Lecture audio interrompue : … » with the speech server's message.

| Message | Cause |
|---|---|
| « … audio service is not configured » | `model` names a model that is not a speech model, e.g. the speech recognition one (`qwen3-asr`) |
| « CustomVoice requests require a 'voice' speaker » | No voice: set `voice` (or one per book) |
| « Base ICL voice cloning requires … » | A cloned voice without its recording or transcript: check `[clones.<name>]` |
| « backend … hors ligne » / slow first sound | The model server is loading the model (or switching models) |
| « Voix clonée … illisible » | The `audio` file of a cloned voice is missing or larger than 8 MB |
| No « Écouter » button | Not signed in, `url` or `model` empty, an invalid settings file (logged at the first request), or `MINIMAL_MODE` |
| French read with a foreign accent | `[extra] language = "French"`, or a cloned French voice |
| The voices list is empty | The server did not answer (model loading): asked again on the next opening of « Voix et ton »; a voice can be typed |

## Code

| Part | Files |
|---|---|
| Settings | `controllers/audiobook/config.py` (`audiobook.toml`, `AUDIOBOOK_CONFIG_PATH`, cloned voices) |
| Speech server client | `controllers/audiobook/speech_client.py` (`/v1/audio/speech`, `/v1/audio/voices`, recordings of the cloned voices, concurrency limit) |
| Endpoints | `controllers/audiobook/controller.py` (routes in `routes.py`, full server only) |
| Reference recordings | `controllers/audiobook/recording.py` (normalization), `controllers/tools/voice_design.py` (tool), `voix/french/` |
| Web reader | `static/reader/narration.js` (sentences, requests, playback, underlining), `static/reader/audiobook.js` (Alpine store `audiobook`), `static/partials/reader-audio.html` (player, « Voix et ton ») |
| Tests | `tests/audiobook/test_audiobook.py` (a fake speech server) |
