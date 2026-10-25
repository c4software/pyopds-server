"""Make a cloned voice of the audiobook: its reference recording, normalized, and an example read with it.

The reference comes either from a written description, read by the voice design model of the settings
(`design_model` of audiobook.toml, Qwen3-TTS "VoiceDesign": no real person's voice), or from a recording
of your own voice (`--recording`, with its word-for-word transcript `--text`; only a voice you may use,
yours or one whose owner agreed). It is normalized (controllers.audiobook.recording: mono 24 kHz,
silences cut, level set), written next to the settings (voix/<name>.wav, or --output), then the cloning
model (`clone_model`, Qwen3-TTS "Base") reads an example with it (voix/<name>-exemple.wav), played here
for validation when a player is installed (aplay, paplay, afplay, ffplay; --no-play to skip). The
`[clones.<name>]` table to add to the settings is printed. The design model invents the voice again on
each call: run the tool until the example pleases.

Command line, from the application directory (in the Docker image: `docker exec cops python -m
controllers.tools.voice_design …`, the files written to /config/voix/, nothing played):

    python -m controllers.tools.voice_design narrateur "Voix masculine française d'une cinquantaine
        d'années, grave, chaude et veloutée. Narrateur professionnel de livre audio : diction parfaite,
        débit posé, intonation naturelle."
    python -m controllers.tools.voice_design moi --recording ma-voix.wav --text "Il était une fois, …"
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from dataclasses import replace
from pathlib import Path

from controllers.audiobook.config import ClonedVoice, get_audiobook_config
from controllers.audiobook.recording import ADVISED_SECONDS, normalize
from controllers.audiobook.speech_client import SpeechClient, SpeechError

# Read by the designed voice (and to read for a recording of your own voice): varied sounds and
# punctuation, about fifteen seconds of speech
REFERENCE_TEXT = ("Il était une fois, au bord d'une forêt immense, un vieux meunier qui vivait seul avec son chat. "
                  "Chaque matin, il ouvrait les volets de son moulin et regardait le soleil se lever sur les champs de blé.")
# Read by the cloned voice for validation: narration, a question and a line of dialogue
EXAMPLE_TEXT = ("Le meunier soupira longuement. Il savait que le chat avait raison : depuis des semaines, le ciel "
                "restait d'un bleu implacable.\n« Que veux-tu que j'y fasse ? » murmura-t-il.")
# Command lines of the audio players, the first one installed plays the files
PLAYERS = (['aplay', '-q'], ['paplay'], ['afplay'], ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet'])


def default_output(name: str) -> Path:
    """voix/<name>.wav next to the settings file of AUDIOBOOK_CONFIG_PATH (the working directory without one)."""
    settings = os.environ.get('AUDIOBOOK_CONFIG_PATH')
    folder = Path(settings).resolve().parent if settings else Path.cwd()
    return folder / 'voix' / f'{name}.wav'


def example_path(output: Path) -> Path:
    """voix/<name>-exemple.wav, next to the reference recording."""
    return output.with_name(f'{output.stem}-exemple{output.suffix}')


def clone_table(name: str, audio: Path, text: str) -> str:
    """`[clones.<name>]` table of the settings for a recording (its path relative to the settings folder when inside)."""
    settings = os.environ.get('AUDIOBOOK_CONFIG_PATH')
    path = audio.resolve()
    if settings and path.is_relative_to(Path(settings).resolve().parent):
        path = path.relative_to(Path(settings).resolve().parent)
    return f"[clones.{name}]\naudio = {json.dumps(str(path))}\ntext = {json.dumps(text, ensure_ascii=False)}\n"


def player() -> list[str] | None:
    """Command line of the first audio player installed, None without one."""
    return next((command for command in PLAYERS if shutil.which(command[0])), None)


def play(paths: list[Path]) -> None:
    """Play the files one after the other with the first player installed (nothing without one)."""
    command = player()
    if not command:
        print('Aucun lecteur audio (aplay, paplay, afplay, ffplay) : écoutez les fichiers ci-dessus.')
        return
    for path in paths:
        print(f'Lecture de {path.name}…')
        subprocess.run([*command, str(path)], check=False)


def reference_audio(args: argparse.Namespace, config, text: str) -> bytes:
    """Raw reference recording: the file given (--recording), else the description read by the design model.

    Raises:
        SpeechError: When the design model fails.
        OSError: When the recording cannot be read.
    """
    if args.recording:
        return args.recording.read_bytes()
    # The description goes as the reading tone of the design model; a WAV file comes back
    designer = SpeechClient(replace(config, model=args.model or config.design_model, response_format='wav', clones={}))
    return designer.synthesize(text, '', args.description)[0]


def main(argv: list[str] | None = None) -> int:
    """Command line entry point (see the module docstring)."""
    parser = argparse.ArgumentParser(prog='python -m controllers.tools.voice_design',
                                     description="Crée une voix clonée du livre audio : enregistrement de référence normalisé et exemple à écouter.")
    parser.add_argument('name', help='nom de la voix (proposée aux lecteurs), ex. narrateur')
    parser.add_argument('description', nargs='?', help='description de la voix : genre, âge, timbre, accent, style de lecture')
    parser.add_argument('--recording', type=Path, help='enregistrement WAV de votre voix, à la place de la description')
    parser.add_argument('--text', help="transcription mot pour mot de l'enregistrement (par défaut, le texte lu par la voix conçue)")
    parser.add_argument('--example', default=EXAMPLE_TEXT, help="texte de l'exemple lu par la voix clonée")
    parser.add_argument('--output', type=Path, help='fichier WAV écrit (par défaut voix/<nom>.wav à côté des réglages)')
    parser.add_argument('--model', help='modèle de conception de voix (par défaut design_model des réglages)')
    parser.add_argument('--no-play', action='store_true', help="ne pas écouter la référence et l'exemple")
    args = parser.parse_args(argv)

    if bool(args.description) == bool(args.recording):
        parser.error('une description ou --recording (avec --text), pas les deux')
    if args.recording and not args.text:
        parser.error("--recording demande --text, la transcription mot pour mot de l'enregistrement")
    config = get_audiobook_config()
    if not config.url or not (args.recording or args.model or config.design_model):
        print('Réglages incomplets : url et design_model (ou --model) sont requis (audiobook.toml, AUDIOBOOK_CONFIG_PATH).', file=sys.stderr)
        return 2
    text = ' '.join((args.text or REFERENCE_TEXT).split())
    try:
        reference = normalize(reference_audio(args, config, text))
    except SpeechError as e:
        print(e.message, file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Enregistrement illisible : {e}", file=sys.stderr)
        return 1
    except (wave.Error, EOFError, ValueError) as e:
        print(f"L'enregistrement n'est pas un fichier WAV PCM utilisable : {e}", file=sys.stderr)
        return 1
    output = args.output or default_output(args.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(reference.wav)
    print(f'Référence « {args.name} » : {output} ({reference.seconds:.1f} s, gain {reference.gain_db:+.1f} dB)')
    if not ADVISED_SECONDS[0] <= reference.seconds <= ADVISED_SECONDS[1]:
        print(f'  Attention : une référence de {ADVISED_SECONDS[0]:.0f} à {ADVISED_SECONDS[1]:.0f} s clone mieux.')

    listen = [output]
    if config.clone_model:
        cloner = SpeechClient(replace(config, response_format='wav', clones={args.name: ClonedVoice(output, text)}))
        try:
            example = cloner.synthesize(args.example.strip(), args.name, '')[0]
        except SpeechError as e:
            print(f"Exemple non généré : {e.message}", file=sys.stderr)
        else:
            example_path(output).write_bytes(example)
            print(f'Exemple lu par la voix clonée : {example_path(output)}')
            listen.append(example_path(output))
    else:
        print("Pas d'exemple : clone_model n'est pas réglé.")
    if not args.no_play:
        play(listen)
    print('\nÀ ajouter aux réglages, puis redémarrer le serveur :\n')
    print(clone_table(args.name, output, text))
    return 0


if __name__ == '__main__':
    sys.exit(main())
