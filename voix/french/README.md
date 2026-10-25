# French reference voices

Reference recordings of the audiobook's cloned voices (`[clones.<name>]` of [`audiobook.toml`](../../audiobook.toml)), read by a voice cloning model (Qwen3-TTS « Base »). Neither is the voice of a real person: each was made from the written description below by a voice design model (Qwen3-TTS 1.7B « VoiceDesign », `python -m controllers.tools.voice_design`), WAV 24 kHz mono.

In the Docker image they are in `/app/voix/french/`:

```toml
clone_model = "silentchuck/qwen3-tts-12hz-1.7b-base"
voice = "narrateur"

[clones.narrateur]
audio = "/app/voix/french/narrateur.wav"
text = "Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé."

[clones.narratrice]
audio = "/app/voix/french/narratrice.wav"
text = "Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé."
```

| Voice | Length | Description given to the voice design model |
|---|---|---|
| `narrateur.wav` | 14.7 s | Voix masculine française d'une cinquantaine d'années, grave, chaude et veloutée, légèrement feutrée. Narrateur professionnel de livre audio : diction parfaite, accent français standard, débit posé, intonation naturelle et expressive sans excès. |
| `narratrice.wav` | 12.6 s | Voix féminine française d'une quarantaine d'années, chaude, douce et légèrement grave, timbre velouté et rassurant. Narratrice professionnelle de livre audio : diction parfaite, accent français standard, débit posé, intonation naturelle et expressive sans excès. |

Both read the same text, their transcript (`text`, word for word): « Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé. »

To make another one (normalized, with an example to listen to): `python -m controllers.tools.voice_design <name> "<description>"`, see [Making a voice](../../docs/audiobook.md#making-a-voice).

Only add here voices that anyone may use: designed from a description, or recorded by someone who agreed to share their voice.
