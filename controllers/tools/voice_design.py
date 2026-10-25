"""Create a voice of the audiobook from a written description, without any recording of a real person.

The voice design model of the settings (`design_model` of audiobook.toml, Qwen3-TTS "VoiceDesign") reads
a reference text with a voice made from the description; the recording becomes a cloned voice
(`[clones.<name>]`), read by the cloning model (`clone_model`, Qwen3-TTS "Base") for every passage of
the books, so that the voice stays the same from one request to the next. The design model invents the
voice again on each call: run the tool until a recording pleases, then keep it.

Command line, from the application directory (in the Docker image: `docker exec cops python -m
controllers.tools.voice_design …`):

    python -m controllers.tools.voice_design narrateur "Voix masculine française d'une cinquantaine
        d'années, grave, chaude et veloutée. Narrateur professionnel de livre audio : diction parfaite,
        débit posé, intonation naturelle."

The recording is written next to the settings file (voix/<name>.wav, or --output), and the
`[clones.<name>]` table to add to the settings is printed.
"""
import argparse
import io
import json
import os
import sys
import wave
from dataclasses import replace
from pathlib import Path

from controllers.audiobook.config import get_audiobook_config
from controllers.audiobook.speech_client import SpeechClient, SpeechError

# Read by the designed voice: varied sounds and punctuation, about fifteen seconds of speech
REFERENCE_TEXT = ("Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. "
                  "Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé.")


def default_output(name: str) -> Path:
    """voix/<name>.wav next to the settings file of AUDIOBOOK_CONFIG_PATH (the working directory without one)."""
    settings = os.environ.get('AUDIOBOOK_CONFIG_PATH')
    folder = Path(settings).resolve().parent if settings else Path.cwd()
    return folder / 'voix' / f'{name}.wav'


def clone_table(name: str, audio: Path, text: str) -> str:
    """`[clones.<name>]` table of the settings for a recording (its path relative to the settings folder when inside)."""
    settings = os.environ.get('AUDIOBOOK_CONFIG_PATH')
    path = audio.resolve()
    if settings and path.is_relative_to(Path(settings).resolve().parent):
        path = path.relative_to(Path(settings).resolve().parent)
    return f"[clones.{name}]\naudio = {json.dumps(str(path))}\ntext = {json.dumps(text, ensure_ascii=False)}\n"


def wav_seconds(audio: bytes) -> float:
    """Length of a WAV recording.

    Raises:
        wave.Error, EOFError: When the audio is not a WAV file.
    """
    with wave.open(io.BytesIO(audio)) as recording:
        return recording.getnframes() / recording.getframerate()


def main(argv: list[str] | None = None) -> int:
    """Command line entry point (see the module docstring)."""
    parser = argparse.ArgumentParser(prog='python -m controllers.tools.voice_design',
                                     description='Crée une voix du livre audio à partir de sa description.')
    parser.add_argument('name', help='nom de la voix (proposée aux lecteurs), ex. narrateur')
    parser.add_argument('description', help='description de la voix : genre, âge, timbre, accent, style de lecture')
    parser.add_argument('--text', default=REFERENCE_TEXT, help="texte lu par la voix (sa transcription accompagne l'enregistrement)")
    parser.add_argument('--output', type=Path, help='fichier WAV écrit (par défaut voix/<nom>.wav à côté des réglages)')
    parser.add_argument('--model', help='modèle de conception de voix (par défaut design_model des réglages)')
    args = parser.parse_args(argv)

    config = get_audiobook_config()
    model = args.model or config.design_model
    if not config.url or not model:
        print('Réglages incomplets : url et design_model (ou --model) sont requis (audiobook.toml, AUDIOBOOK_CONFIG_PATH).', file=sys.stderr)
        return 2
    text = ' '.join(args.text.split())
    # The reference recording of a cloned voice is a WAV file; the description goes as the reading tone
    client = SpeechClient(replace(config, model=model, response_format='wav', clones={}))
    try:
        audio, _ = client.synthesize(text, '', args.description)
        seconds = wav_seconds(audio)
    except SpeechError as e:
        print(e.message, file=sys.stderr)
        return 1
    except (wave.Error, EOFError):
        print("La réponse du serveur n'est pas un fichier WAV.", file=sys.stderr)
        return 1
    output = args.output or default_output(args.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(audio)
    print(f'Voix « {args.name} » : {output} ({seconds:.1f} s). À ajouter aux réglages, puis redémarrer le serveur :\n')
    print(clone_table(args.name, output, text))
    return 0


if __name__ == '__main__':
    sys.exit(main())
