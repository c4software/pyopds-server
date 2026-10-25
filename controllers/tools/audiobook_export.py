"""Make the audiobook of an EPUB: one WAV file per chapter, read by the speech server of the settings
(audiobook.toml, AUDIOBOOK_CONFIG_PATH) with a voice of the server or a cloned voice, and a reading tone.

At a terminal, what the command line leaves out is asked: the book (the catalog's search: title, author
or series), the voice (chosen in the list of the server) and the reading tone.

Before starting, the tool measures the speed of the server on the first request (after one short request
that loads the model) and tells how long the generation and the audiobook will last; it then shows the
time left as it goes. The chapters already written are skipped: run the same command again to resume an
interrupted generation. A chapter is written once complete (<n> - <title>.wav, in the folder of the book).

Command line, from the application directory (in the Docker image: `docker exec -it cops python -m
controllers.tools.audiobook_export …`, the files written to /config/livres-audio/):

    python -m controllers.tools.audiobook_export                            # book, voice and tone asked
    python -m controllers.tools.audiobook_export livre.epub --voice narrateur
    python -m controllers.tools.audiobook_export Auteur/livre.epub --voice ryan --tone "Voix posée de conteur" --chapters 1-3,5
"""
import argparse
import io
import re
import sys
import time
import wave
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

from controllers.audiobook.book_text import Chapter, book_chapters, speech_chunks
from controllers.audiobook.config import get_audiobook_config, settings_folder
from controllers.audiobook.speech_client import SpeechClient, SpeechError
from controllers.catalog.opds import BookMetadata, BookScanner
from controllers.library.index import LibraryIndex
from controllers.tools.prompts import ask, choose, confirm, interactive

# Silence between two requests of a chapter (the paragraphs of one request get the voice's own pauses)
PAUSE_SECONDS = 0.6
# Seconds waited before each new attempt of a failed request (the server may be loading a model)
RETRY_DELAYS = (5, 15, 30)
WARMUP_TEXT = 'Bonjour.'
# Books listed at most by a search of the library
SEARCH_RESULTS_MAX = 20
UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def audio_seconds(params: wave._wave_params, frames: bytes) -> float:
    """Length in seconds of WAV frames."""
    return len(frames) / (params.framerate * params.nchannels * params.sampwidth)


def format_duration(seconds: float) -> str:
    """Duration for a person: « 2 h 05 », « 12 min », « 40 s »."""
    seconds = max(0, round(seconds))
    if seconds >= 3600:
        return f'{seconds // 3600} h {seconds % 3600 // 60:02d}'
    if seconds >= 60:
        return f'{round(seconds / 60)} min'
    return f'{seconds} s'


def file_name(title: str) -> str:
    """File or folder name of a title (characters refused by file systems removed)."""
    return ' '.join(UNSAFE_NAME_RE.sub(' ', title).split())[:80].strip(' .') or 'Livre audio'


def parse_selection(value: str, count: int) -> list[int]:
    """Chapter numbers (1-based) of a selection « 1-3,5 », in order and without repeats.

    Raises:
        ValueError: When the selection is invalid or out of the book.
    """
    numbers: list[int] = []
    for part in value.split(','):
        first, _, last = part.strip().partition('-')
        start, end = int(first), int(last or first)
        if not 1 <= start <= end <= count:
            raise ValueError(f'chapitres de 1 à {count}')
        numbers.extend(n for n in range(start, end + 1) if n not in numbers)
    return numbers


def resolve_book(value: str) -> Path | None:
    """EPUB file named on the command line: a path, else a library EPUB given relative to LIBRARY_DIR."""
    if Path(value).is_file():
        return Path(value)
    path = LibraryIndex.resolve_relative(value)
    return Path(path) if path else None


def pick_book() -> Path | None:
    """Book chosen at the terminal: the catalog's search (title, author or series, in the library database),
    then its number in the list (None: given up)."""
    scanner = BookScanner.get_instance()
    while query := ask("Livre (titre, auteur, série ou chemin ; Entrée pour quitter) :"):
        while query:
            if path := resolve_book(query):
                return path
            found, total = scanner.search_books(query, 1, SEARCH_RESULTS_MAX)
            if not found:
                print('  Aucun livre trouvé.')
                break
            labels = {}
            for book in found:
                label = f"{book['title']} — {book['author']}"
                labels[label if label not in labels else f"{label} ({book['relative_path']})"] = book['path']
            choice = choose('Numéro du livre (ou une autre recherche) :', list(labels),
                            more=f'… et {total - len(found)} autres : précisez la recherche' if total > len(found) else '')
            if choice in labels:
                return Path(labels[choice])
            query = choice
    return None


@dataclass
class Progress:
    """Generation speed measured so far and the time left."""

    total_chars: int
    chars: int = 0
    seconds: float = 0.0
    audio_seconds: float = 0.0

    def add(self, chars: int, seconds: float, audio_seconds: float) -> None:
        """Count a request done: its characters, its generation time and its audio length."""
        self.chars += chars
        self.seconds += seconds
        self.audio_seconds += audio_seconds

    @property
    def remaining(self) -> float:
        """Seconds of generation left, at the speed measured so far."""
        return self.seconds / self.chars * (self.total_chars - self.chars) if self.chars else 0.0

    @property
    def audio_total(self) -> float:
        """Audio length of the whole selection, at the ratio measured so far."""
        return self.audio_seconds / self.chars * self.total_chars if self.chars else 0.0


class Narrator:
    """Requests of the export to the speech server: one voice and tone, WAV answers, failed requests tried again."""

    def __init__(self, client: SpeechClient, voice: str, tone: str, quiet: bool = False):
        self.client, self.voice, self.tone, self.quiet = client, voice, tone, quiet

    def read(self, text: str) -> tuple[wave._wave_params, bytes]:
        """(WAV parameters, frames) of `text` read aloud.

        Raises:
            SpeechError: When the server still fails after the retries, or answers no WAV file.
        """
        for delay in (*RETRY_DELAYS, None):
            try:
                audio, _ = self.client.synthesize(text, self.voice, self.tone)
                break
            except SpeechError as e:
                if delay is None:
                    raise
                if not self.quiet:
                    print(f'\n  {e.message} : nouvel essai dans {delay} s', file=sys.stderr)
                time.sleep(delay)
        try:
            with wave.open(io.BytesIO(audio)) as answer:
                return answer.getparams(), answer.readframes(answer.getnframes())
        except (wave.Error, EOFError) as e:
            raise SpeechError(502, f"La synthèse vocale n'a pas répondu un fichier WAV : {e}")


class ChapterWriter:
    """WAV file of a chapter, written as the requests come (`.part` until complete)."""

    def __init__(self, path: Path):
        self.path = path
        self.part = path.with_name(path.name + '.part')
        self._wav: wave.Wave_write | None = None
        self._params: wave._wave_params | None = None

    def add(self, params: wave._wave_params, frames: bytes) -> float:
        """Append the audio of a request (a pause before it, except the first); returns its length in seconds.

        Raises:
            SpeechError: When the format changes between two requests.
        """
        if self._wav is None:
            self._wav = wave.open(str(self.part), 'wb')
            self._wav.setnchannels(params.nchannels)
            self._wav.setsampwidth(params.sampwidth)
            self._wav.setframerate(params.framerate)
            self._params = params
        else:
            if params[:3] != self._params[:3]:
                raise SpeechError(502, 'Format audio différent entre deux requêtes')
            self._wav.writeframes(bytes(int(params.framerate * PAUSE_SECONDS) * params.nchannels * params.sampwidth))
        self._wav.writeframes(frames)
        return audio_seconds(params, frames)

    def finish(self) -> None:
        """Close the file under its final name."""
        if self._wav is not None:
            self._wav.close()
            self.part.replace(self.path)

    def discard(self) -> None:
        """Remove the incomplete file."""
        if self._wav is not None:
            self._wav.close()
        self.part.unlink(missing_ok=True)


def chapter_path(folder: Path, number: int, count: int, chapter: Chapter) -> Path:
    """<folder>/<number> - <title>.wav, the numbers padded to the same width."""
    return folder / f'{number:0{max(2, len(str(count)))}d} - {file_name(chapter.title)}.wav'


def main(argv: list[str] | None = None) -> int:
    """Command line entry point (see the module docstring)."""
    parser = argparse.ArgumentParser(prog='python -m controllers.tools.audiobook_export',
                                     description="Génère le livre audio d'un EPUB, un fichier WAV par chapitre.")
    parser.add_argument('book', nargs='?', help="fichier EPUB (chemin, ou relatif au dossier de la bibliothèque ; demandé sinon)")
    parser.add_argument('--voice', help='voix (du modèle ou clonée ; demandée sinon, par défaut voice des réglages)')
    parser.add_argument('--tone', help="ton de lecture (demandé sinon, par défaut instructions des réglages ; sans effet sur une voix clonée)")
    parser.add_argument('--chapters', help='chapitres à générer, ex. 1-3,5 (par défaut tous)')
    parser.add_argument('--output', type=Path, help='dossier écrit (par défaut livres-audio/<titre> à côté des réglages)')
    parser.add_argument('--yes', '-y', action='store_true', help="lancer sans demander confirmation après l'estimation")
    args = parser.parse_args(argv)

    config = get_audiobook_config()
    if not config.enabled:
        print('Livre audio non configuré : url et model sont requis (audiobook.toml, AUDIOBOOK_CONFIG_PATH).', file=sys.stderr)
        return 2
    if args.book:
        book = resolve_book(args.book)
        if not book:
            print(f'Livre introuvable : {args.book}', file=sys.stderr)
            return 2
    elif interactive():
        book = pick_book()
        if not book:
            return 0
    else:
        parser.error('le livre est requis sans terminal')
    try:
        chapters = book_chapters(str(book))
    except (OSError, zipfile.BadZipFile) as e:
        print(f'EPUB illisible : {e}', file=sys.stderr)
        return 1
    if not chapters:
        print("Aucun texte dans ce livre.", file=sys.stderr)
        return 1
    try:
        numbers = parse_selection(args.chapters, len(chapters)) if args.chapters else list(range(1, len(chapters) + 1))
    except ValueError as e:
        parser.error(f'--chapters : {e}')

    # The answers are assembled in WAV files: always asked as WAV
    client = SpeechClient(replace(config, response_format='wav'))
    voice = args.voice
    if voice is None and interactive():
        print('Voix :')
        voice = choose('Numéro ou nom de la voix (Entrée : celle par défaut) :', client.voices(), config.voice)
    voice = voice or config.voice
    tone = args.tone
    if voice in config.clones:
        if tone:
            print('Voix clonée : elle lit comme son enregistrement, le ton est ignoré.')
    elif tone is None and interactive():
        default = config.instructions[:60] + '…' if len(config.instructions) > 60 else config.instructions or 'aucun'
        tone = ask(f'Ton de lecture (Entrée : {default}) :')
    tone = tone or config.instructions
    title = BookMetadata.extract_epub_metadata(str(book))[0] or book.stem
    folder = args.output or settings_folder() / 'livres-audio' / file_name(title)
    todo = [(n, chapters[n - 1], chapter_path(folder, n, len(chapters), chapters[n - 1])) for n in numbers]
    if done := [n for n, _, path in todo if path.exists()]:
        print(f'{len(done)} chapitre(s) déjà générés dans {folder}, ignorés.')
        todo = [item for item in todo if not item[2].exists()]
    if not todo:
        print('Rien à générer.')
        return 0

    narrator = Narrator(client, voice, tone)
    jobs = [(n, chapter, path, speech_chunks(chapter.text, config.max_chars)) for n, chapter, path in todo]
    progress = Progress(sum(len(chunk) for *_, chunks in jobs for chunk in chunks))
    print(f'« {title} » : {len(jobs)} chapitre(s), {progress.total_chars} caractères, voix {voice or "par défaut"}')
    folder.mkdir(parents=True, exist_ok=True)
    writer: ChapterWriter | None = None
    try:
        print('Chargement du modèle et mesure de la vitesse…')
        narrator.read(WARMUP_TEXT)
        started = time.monotonic()
        first = narrator.read(jobs[0][3][0])
        progress.add(len(jobs[0][3][0]), time.monotonic() - started, audio_seconds(*first))
        print(f'Estimation : génération ~{format_duration(progress.remaining + progress.seconds)}, '
              f'livre audio ~{format_duration(progress.audio_total)}')
        if not args.yes and not confirm('Lancer la génération ?'):
            return 0
        book_started = time.monotonic() - progress.seconds
        for index, (number, chapter, path, chunks) in enumerate(jobs, 1):
            writer = ChapterWriter(path)
            chapter_audio = 0.0
            for position, chunk in enumerate(chunks):
                _status(f'[{index}/{len(jobs)}] {chapter.title[:40]} : {position * 100 // len(chunks)} %, '
                        f'livre {progress.chars * 100 // progress.total_chars} %, restant ~{format_duration(progress.remaining)}')
                if first is not None:
                    params, frames = first
                    first = None
                    chapter_audio += writer.add(params, frames)
                    continue
                started = time.monotonic()
                params, frames = narrator.read(chunk)
                seconds = writer.add(params, frames)
                progress.add(len(chunk), time.monotonic() - started, seconds)
                chapter_audio += seconds
            writer.finish()
            writer = None
            _status(f'[{index}/{len(jobs)}] {path.name} : {format_duration(chapter_audio)} d\'audio, '
                    f'restant ~{format_duration(progress.remaining)}', end='\n')
    except SpeechError as e:
        print(f'\n{e.message}', file=sys.stderr)
        if writer:
            writer.discard()
        print('Relancer la même commande reprend au chapitre interrompu.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if writer:
            writer.discard()
        print('\nInterrompu : relancer la même commande reprend au chapitre interrompu.', file=sys.stderr)
        return 130
    print(f'\nTerminé en {format_duration(time.monotonic() - book_started)} : {folder}')
    return 0


def _status(line: str, end: str = '') -> None:
    """Progress line: rewritten in place on a terminal, one line per chapter otherwise."""
    if sys.stdout.isatty():
        print(f'\r\033[K{line}', end=end, flush=True)
    elif end:
        print(line)


if __name__ == '__main__':
    sys.exit(main())
