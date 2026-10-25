"""Audiobook of the web reader: settings, voice and tone per book, synthesis relay."""
import base64
import contextlib
import http.server
import importlib
import io
import json
import math
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
import wave
from pathlib import Path
from unittest import mock
from support import ThreadedTCPServer, basic_header, http_request, create_chapter_epub  # noqa: F401  (first: test database, project on sys.path)


def silent_wav(seconds: float = 0.5) -> bytes:
    """WAV recording of `seconds` of silence (16 kHz, mono)."""
    return tone_wav(seconds, amplitude=0)


def tone_wav(seconds: float = 1.0, amplitude: int = 3000, rate: int = 16000, channels: int = 1, silence: float = 0.0) -> bytes:
    """WAV recording of a 220 Hz tone of `seconds`, between `silence` seconds of silence."""
    frames = []
    for i in range(int(rate * (seconds + 2 * silence))):
        t = i / rate
        value = round(amplitude * math.sin(2 * math.pi * 220 * t)) if silence <= t < silence + seconds else 0
        frames.extend([value] * channels)
    out = io.BytesIO()
    with wave.open(out, 'wb') as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(b''.join(v.to_bytes(2, 'little', signed=True) for v in frames))
    return out.getvalue()


class FakeSpeechServer(http.server.BaseHTTPRequestHandler):
    """OpenAI-compatible text-to-speech server of the audiobook tests: records the requests, answers WAV bytes
    (a real WAV file for the voice design model, or for every model with `wav_answers`), an error when the text
    says "erreur", and a voice listing."""

    requests: list = []
    wav_answers = False

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeSpeechServer.requests.append({'path': self.path, 'body': body, 'auth': self.headers.get('Authorization')})
        if 'erreur' in body['input']:
            self._answer(500, b'{"error": {"message": "GPU indisponible"}}', 'application/json')
        elif body['model'] == 'bigchuck/tts-design' or FakeSpeechServer.wav_answers:
            self._answer(200, tone_wav(), 'audio/wav')
        else:
            self._answer(200, b'RIFF-audio:' + body['input'].encode('utf-8'), 'audio/wav')

    def do_GET(self):
        FakeSpeechServer.requests.append({'path': self.path, 'auth': self.headers.get('Authorization')})
        self._answer(200, b'{"data": [{"id": "vivian"}, {"id": "ryan"}]}', 'application/json')

    def _answer(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestAudiobookConfig(unittest.TestCase):
    """audiobook.toml: settings of the speech server, completed by AUDIOBOOK_CONFIG_PATH."""

    def setUp(self):
        self.config = importlib.import_module('controllers.audiobook.config')

    def test_default_file_keeps_the_audiobook_off(self):
        config = self.config.load_audiobook_config(self.config.DEFAULT_AUDIOBOOK_CONFIG_PATH)
        self.assertFalse(config.enabled)
        self.assertEqual(config.response_format, 'wav')

    def test_completing_file_reads_the_environment_and_wins(self):
        base = self.config.AudiobookConfig(voice='vivian', extra={'language': 'French'})
        with mock.patch.dict(os.environ, {'TEST_TTS_KEY': 'secret'}):
            config = self.config.AudiobookConfig.from_dict({
                'url': 'http://proxy:8000/', 'model': 'bigchuck/tts', 'api_key': '${TEST_TTS_KEY}', 'max_chars': 300,
                'extra': {'speed': 1.1},
            }, base)
        self.assertTrue(config.enabled)
        self.assertEqual((config.url, config.api_key, config.voice, config.max_chars), ('http://proxy:8000', 'secret', 'vivian', 300))
        self.assertEqual(config.extra, {'language': 'French', 'speed': 1.1})

    def test_invalid_values_are_refused(self):
        for data in ({'url': 'proxy:8000'}, {'max_chars': 10}, {'response_format': 'pcm'}, {'voices': 'vivian'},
                     {'timeout': True}, {'unknown': 1}, {'clones': {'narrateur': {'audio': 'n.wav', 'text': 'Bonjour.'}}},
                     {'clone_model': 'base', 'clones': {'narrateur': {'audio': 'n.wav'}}},
                     {'clone_model': 'base', 'clones': {'narrateur': {'audio': 'n.wav', 'text': 'Bonjour.', 'voice': 'x'}}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.config.AudiobookConfig.from_dict(data)

    def test_cloned_voices_are_read_next_to_the_settings(self):
        base = self.config.AudiobookConfig.from_dict({'clone_model': 'base', 'clones': {'a': {'audio': '/voix/a.wav', 'text': 'Un.'}}})
        config = self.config.AudiobookConfig.from_dict(
            {'clones': {'b': {'audio': 'voix/b.wav', 'text': ' Deux  mots. '}}}, base, Path('/config'))
        self.assertEqual(config.clones['a'].audio, Path('/voix/a.wav'))
        self.assertEqual((config.clones['b'].audio, config.clones['b'].text), (Path('/config/voix/b.wav'), 'Deux mots.'))


class TestAudiobook(unittest.TestCase):
    """Audiobook of the web reader: voice and tone per book, synthesis relayed to the speech server."""

    password = 'ecoute'

    @classmethod
    def setUpClass(cls):
        cls._orig_env = {key: os.environ.get(key) for key in ('LIBRARY_DIR', 'OPDS_AUTH', 'OPDS_SESSION_SECRET', 'AUDIOBOOK_CONFIG_PATH')}
        cls.library_dir = tempfile.TemporaryDirectory()
        cls.db_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        os.environ.pop('OPDS_AUTH', None)
        os.environ['OPDS_SESSION_SECRET'] = 'test-session-secret'
        cls.epub = os.path.join(cls.library_dir.name, 'Contes', 'conte.epub')
        os.makedirs(os.path.dirname(cls.epub))
        create_chapter_epub(cls.epub, 'Le Conte', 'Un Auteur')
        # Fake speech server, named by the completing settings file
        cls.speech = http.server.ThreadingHTTPServer(('localhost', 0), FakeSpeechServer)
        cls.speech_thread = threading.Thread(target=cls.speech.serve_forever, daemon=True)
        cls.speech_thread.start()
        config_path = os.path.join(cls.db_dir.name, 'audiobook.toml')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(f'url = "http://localhost:{cls.speech.server_address[1]}"\nmodel = "bigchuck/tts"\napi_key = "jeton"\n'
                    'voice = "vivian"\ninstructions = "Ton posé"\nmax_chars = 100\nclone_model = "bigchuck/tts-base"\n'
                    'design_model = "bigchuck/tts-design"\n[extra]\nlanguage = "French"\n'
                    '[clones.narrateur]\naudio = "voix/narrateur.wav"\ntext = "Il était une fois."\n')
        os.makedirs(os.path.join(cls.db_dir.name, 'voix'))
        with open(os.path.join(cls.db_dir.name, 'voix', 'narrateur.wav'), 'wb') as f:
            f.write(silent_wav())
        os.environ['AUDIOBOOK_CONFIG_PATH'] = config_path
        for module in ('controllers.catalog.opds', 'controllers.sync.storage', 'controllers.sync.koreader',
                       'controllers.library.index', 'controllers.sync.crosspoint', 'controllers.accounts.opds_auth',
                       'controllers.audiobook.config', 'controllers.audiobook.speech_client', 'controllers.audiobook.controller'):
            importlib.reload(importlib.import_module(module))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        cls.koreader_sync = importlib.import_module('controllers.sync.koreader')
        cls.audiobook = importlib.import_module('controllers.audiobook.controller')
        sync_storage = importlib.import_module('controllers.sync.storage')
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = sync_storage.SyncStorage(
            os.path.join(cls.db_dir.name, 'sync.db'))
        cls.httpd = ThreadedTCPServer(('localhost', 0), cls.server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)
        with contextlib.redirect_stdout(io.StringIO()):
            status, _, _ = http_request(cls.port, 'POST', '/users/create', {'username': 'auditeur', 'password': cls.password})
            assert status == 201, status

    @classmethod
    def tearDownClass(cls):
        for server, thread in ((cls.httpd, cls.thread), (cls.speech, cls.speech_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = None
        cls.library_dir.cleanup()
        cls.db_dir.cleanup()
        for key, value in cls._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module('controllers.audiobook.config'))
        importlib.reload(importlib.import_module('controllers.audiobook.speech_client'))
        importlib.reload(importlib.import_module('controllers.catalog.opds'))
        importlib.reload(importlib.import_module('server'))

    def setUp(self):
        FakeSpeechServer.requests = []
        FakeSpeechServer.wav_answers = False
        # The tools ask nothing, even when the tests run at a terminal
        for module in ('controllers.tools.prompts', 'controllers.tools.audiobook_export'):
            patcher = mock.patch.object(importlib.import_module(module), 'interactive', return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _request(self, method, path, body=None, headers=None):
        return http_request(self.port, method, path, body, headers)

    def _auth(self):
        return basic_header('auditeur', self.password)

    def test_voice_and_tone_are_kept_per_book(self):
        url = '/webui/audiobook?path=' + urllib.parse.quote('Contes/conte.epub')
        # An account is always required (every synthesis costs the speech server a generation)
        self.assertEqual(self._request('GET', url)[0], 401)
        status, _, body = self._request('GET', url, headers=self._auth())
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {'voice': '', 'instructions': '', 'max_chars': 100,
                                            'defaults': {'voice': 'vivian', 'instructions': 'Ton posé'}})
        settings = {'path': 'Contes/conte.epub', 'voice': ' ryan ', 'instructions': 'Voix grave, ton de conteur'}
        status, _, body = self._request('PUT', '/webui/audiobook', settings, self._auth())
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(self._request('GET', url, headers=self._auth())[2])['voice'], 'ryan')
        self.assertEqual(json.loads(self._request('GET', url, headers=self._auth())[2])['instructions'], 'Voix grave, ton de conteur')
        # A voice no longer offered (removed from the settings or the server): the default voice, the tone kept
        self._request('PUT', '/webui/audiobook', dict(settings, voice='supprimee'), self._auth())
        self.assertEqual(json.loads(self._request('GET', url, headers=self._auth())[2])['voice'], '')
        self._request('PUT', '/webui/audiobook', dict(settings, voice='narrateur'), self._auth())
        self.assertEqual(json.loads(self._request('GET', url, headers=self._auth())[2])['voice'], 'narrateur')
        # Invalid fields, books outside the library
        too_long = dict(settings, instructions='x' * 1001)
        self.assertEqual(self._request('PUT', '/webui/audiobook', too_long, self._auth())[0], 400)
        self.assertEqual(self._request('PUT', '/webui/audiobook', dict(settings, path='../sync.db'), self._auth())[0], 404)
        self.assertEqual(self._request('GET', '/webui/audiobook?path=absent.epub', headers=self._auth())[0], 404)

    def test_speech_is_relayed_to_the_speech_server(self):
        status, headers, body = self._request('POST', '/webui/audiobook/speech',
                                              {'text': 'Il était  une fois.', 'voice': '', 'instructions': 'Chuchoté'}, self._auth())
        self.assertEqual(status, 200, body)
        self.assertEqual((headers['Content-Type'], body), ('audio/wav', 'RIFF-audio:Il était une fois.'.encode('utf-8')))
        sent = FakeSpeechServer.requests[-1]
        self.assertEqual((sent['path'], sent['auth']), ('/v1/audio/speech', 'Bearer jeton'))
        self.assertEqual(sent['body'], {'language': 'French', 'model': 'bigchuck/tts', 'input': 'Il était une fois.',
                                        'response_format': 'wav', 'voice': 'vivian', 'instructions': 'Chuchoté'})
        # Several paragraphs: spaces collapsed, the line breaks kept (pauses of the voice)
        status, _, _ = self._request('POST', '/webui/audiobook/speech', {'text': 'Chapitre un\n\n  Il était  une fois.\n'}, self._auth())
        self.assertEqual(status, 200)
        self.assertEqual(FakeSpeechServer.requests[-1]['body']['input'], 'Chapitre un\nIl était une fois.')
        # The server's error, a text too long or empty, no account
        status, _, body = self._request('POST', '/webui/audiobook/speech', {'text': 'Une erreur.'}, self._auth())
        self.assertEqual(status, 502)
        self.assertIn('GPU indisponible', json.loads(body)['message'])
        self.assertEqual(self._request('POST', '/webui/audiobook/speech', {'text': 'x' * 101}, self._auth())[0], 400)
        self.assertEqual(self._request('POST', '/webui/audiobook/speech', {'text': '  '}, self._auth())[0], 400)
        self.assertEqual(self._request('POST', '/webui/audiobook/speech', {'text': 'Bonjour.'})[0], 401)

    def test_voices_of_the_speech_server(self):
        status, _, body = self._request('GET', '/webui/audiobook/voices', headers=self._auth())
        self.assertEqual(status, 200, body)
        # The cloned voices first, then those of the model
        self.assertEqual(json.loads(body), {'voices': ['narrateur', 'vivian', 'ryan']})
        self.assertEqual(FakeSpeechServer.requests[-1]['path'], '/v1/audio/voices?model=bigchuck%2Ftts')

    def test_cloned_voice_goes_to_the_cloning_model_with_its_recording(self):
        status, headers, _ = self._request('POST', '/webui/audiobook/speech',
                                           {'text': 'Le meunier soupira.', 'voice': 'narrateur', 'instructions': 'Chuchoté'}, self._auth())
        self.assertEqual((status, headers['Content-Type']), (200, 'audio/wav'))
        self.assertEqual(FakeSpeechServer.requests[-1]['body'], {
            'language': 'French', 'model': 'bigchuck/tts-base', 'input': 'Le meunier soupira.', 'response_format': 'wav',
            'reference_audio': base64.b64encode(silent_wav()).decode('ascii'), 'reference_text': 'Il était une fois.'})

    def test_voice_design_tool_writes_a_cloned_voice(self):
        voice_design = importlib.import_module('controllers.tools.voice_design')
        output = Path(self.db_dir.name, 'voix', 'narratrice.wav')
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = voice_design.main(['narratrice', 'Voix féminine chaude et posée', '--text', 'Il était  une fois.',
                                      '--example', 'Bonjour.', '--no-play'])
        self.assertEqual(code, 0)
        design, example = FakeSpeechServer.requests[-2:]
        self.assertEqual(design['body'], {
            'language': 'French', 'model': 'bigchuck/tts-design', 'input': 'Il était une fois.', 'response_format': 'wav',
            'instructions': 'Voix féminine chaude et posée'})
        # The reference is normalized (24 kHz mono), then the cloning model reads the example with it
        with wave.open(str(output)) as reference:
            self.assertEqual((reference.getnchannels(), reference.getframerate()), (1, 24000))
        self.assertEqual(example['body'], {
            'language': 'French', 'model': 'bigchuck/tts-base', 'input': 'Bonjour.', 'response_format': 'wav',
            'reference_audio': base64.b64encode(output.read_bytes()).decode('ascii'), 'reference_text': 'Il était une fois.'})
        self.assertEqual(Path(self.db_dir.name, 'voix', 'narratrice-exemple.wav').read_bytes(), b'RIFF-audio:Bonjour.')
        # The table to add to the settings, its path relative to the settings folder
        self.assertIn('[clones.narratrice]\naudio = "voix/narratrice.wav"\ntext = "Il était une fois."', printed.getvalue())
        # The server's error is reported
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(voice_design.main(['x', 'Voix', '--text', 'Une erreur.', '--output', str(output)]), 1)
        self.assertIn('GPU indisponible', errors.getvalue())

    def test_voice_design_tool_takes_a_recording(self):
        voice_design = importlib.import_module('controllers.tools.voice_design')
        recording = Path(self.db_dir.name, 'ma-voix.wav')
        recording.write_bytes(tone_wav(4.0, amplitude=800, rate=44100, channels=2, silence=1.0))
        output = Path(self.db_dir.name, 'voix', 'moi.wav')
        with contextlib.redirect_stdout(io.StringIO()):
            code = voice_design.main(['moi', '--recording', str(recording), '--text', 'Bonjour à tous.', '--no-play'])
        self.assertEqual(code, 0)
        # No design request: the recording itself, normalized, then the example
        self.assertEqual(FakeSpeechServer.requests[-1]['body']['reference_text'], 'Bonjour à tous.')
        self.assertNotIn('bigchuck/tts-design', [r['body']['model'] for r in FakeSpeechServer.requests if 'body' in r])
        with wave.open(str(output)) as reference:
            self.assertEqual((reference.getnchannels(), reference.getframerate()), (1, 24000))
            # Silences cut: 4 s of tone and 150 ms kept around it
            self.assertAlmostEqual(reference.getnframes() / 24000, 4.3, delta=0.05)
        # A recording needs its transcript; a description and a recording are not both taken
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            voice_design.main(['moi', '--recording', str(recording)])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            voice_design.main(['moi', 'Voix', '--recording', str(recording), '--text', 'x'])

    def test_audiobook_export_writes_one_wav_per_chapter(self):
        export = importlib.import_module('controllers.tools.audiobook_export')
        FakeSpeechServer.wav_answers = True
        folder = Path(self.db_dir.name, 'livres-audio', 'Le Conte')
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = export.main([self.epub, '--voice', 'ryan', '--tone', 'Voix de conteur', '--yes'])
        self.assertEqual(code, 0)
        self.assertIn('Estimation : génération ~', printed.getvalue())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['01 - Chapitre 1.wav', '02 - Chapitre 2.wav'])
        # A short request loads the model, then each chapter is read in requests of max_chars (100) at most
        bodies = [r['body'] for r in FakeSpeechServer.requests]
        self.assertEqual(bodies[0]['input'], 'Bonjour.')
        self.assertEqual({(b['model'], b['voice'], b['instructions'], b['response_format']) for b in bodies},
                         {('bigchuck/tts', 'ryan', 'Voix de conteur', 'wav')})
        self.assertEqual(bodies[1]['input'], 'Chapitre 1, paragraphe 1.\nChapitre 1, paragraphe 2.\nChapitre 1, paragraphe 3.')
        self.assertEqual(len(bodies), 1 + 2 * 2)
        with wave.open(str(folder / '01 - Chapitre 1.wav')) as chapter:
            # Two answers of 1 s and the pause between them
            self.assertAlmostEqual(chapter.getnframes() / chapter.getframerate(), 2 + export.PAUSE_SECONDS, delta=0.01)
        # Run again: the chapters written are skipped
        FakeSpeechServer.requests = []
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(export.main([self.epub, '--yes']), 0)
        self.assertIn('Rien à générer', printed.getvalue())
        self.assertEqual(FakeSpeechServer.requests, [])

    def test_audiobook_export_asks_the_book_the_voice_and_the_tone(self):
        export = importlib.import_module('controllers.tools.audiobook_export')
        prompts = importlib.import_module('controllers.tools.prompts')
        FakeSpeechServer.wav_answers = True
        output = Path(self.db_dir.name, 'interactif')
        # A search with no result, the book found by the catalog's search, the voice by its number
        # (cloned voices first, then the server's), a tone, the go after the estimate
        answers = iter(['absent', 'le conte', '1', '3', 'Voix de conteur', ''])
        with mock.patch.object(prompts, 'interactive', return_value=True), mock.patch.object(export, 'interactive', return_value=True), \
                mock.patch('builtins.input', lambda _: next(answers)), contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(export.main(['--output', str(output), '--chapters', '2']), 0)
        self.assertIn('Aucun livre trouvé', printed.getvalue())
        self.assertIn('1. narrateur', printed.getvalue())
        self.assertIn('vivian  (par défaut)', printed.getvalue())
        self.assertEqual([p.name for p in output.iterdir()], ['02 - Chapitre 2.wav'])
        body = FakeSpeechServer.requests[-1]['body']
        self.assertEqual((body['voice'], body['instructions']), ('ryan', 'Voix de conteur'))

    def test_audiobook_export_reports_errors(self):
        export = importlib.import_module('controllers.tools.audiobook_export')
        # The server's error stops the generation after the retries, nothing incomplete left
        output = Path(self.db_dir.name, 'erreur')
        with mock.patch.object(export, 'RETRY_DELAYS', ()), mock.patch.object(export, 'WARMUP_TEXT', 'Une erreur.'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(export.main(['Contes/conte.epub', '--output', str(output), '--yes']), 1)
        self.assertIn('GPU indisponible', errors.getvalue())
        self.assertEqual(list(output.iterdir()), [])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            export.main([self.epub, '--chapters', '3'])
        # Without a terminal, the book is required
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            export.main([])

    def test_unconfigured_audiobook_answers_404(self):
        config = importlib.import_module('controllers.audiobook.config')
        with mock.patch.object(self.audiobook, 'get_audiobook_config', return_value=config.AudiobookConfig()):
            self.assertEqual(self._request('GET', '/webui/audiobook?path=Contes/conte.epub', headers=self._auth())[0], 404)
            self.assertEqual(self._request('POST', '/webui/audiobook/speech', {'text': 'Bonjour.'}, self._auth())[0], 404)
        self.assertEqual(FakeSpeechServer.requests, [])

    def test_reader_page_loads_the_audiobook(self):
        page = self._request('GET', '/read?path=Contes/conte.epub')[2].decode('utf-8')
        self.assertIn('/static/reader/audiobook.js?v=', page)
        self.assertIn('class="reader-player"', page)
        self.assertEqual(self._request('GET', '/static/vendor/foliate/tts.js')[0], 200)


class TestBookText(unittest.TestCase):
    """Chapters of an EPUB and requests of the speech server."""

    def setUp(self):
        self.book_text = importlib.import_module('controllers.audiobook.book_text')

    def test_paragraphs_are_grouped_and_long_ones_cut_at_sentences(self):
        chunks = self.book_text.speech_chunks('Un.\nDeux  trois.\n\n' + 'Phrase longue. ' * 10, 40)
        self.assertEqual(chunks[0], 'Un.\nDeux trois.')
        self.assertTrue(all(len(c) <= 40 for c in chunks))
        self.assertEqual(' '.join(chunks[1:]), ' '.join(['Phrase longue.'] * 10))
        # A sentence longer than a request is cut between words
        self.assertEqual(self.book_text.speech_chunks('mot ' * 30, 20), ['mot mot mot mot mot'] * 6)

    def test_chapters_are_titled_by_the_table_of_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'livre.epub')
            create_chapter_epub(path, 'Livre', 'Auteur', chapters=3, toc={'ch1.xhtml': 'Le départ', 'ch2.xhtml#s1': 'Le  retour'},
                                bodies={3: '<h2>Épilogue <em>final</em></h2><p>Fin.</p>'})
            chapters = self.book_text.book_chapters(path)
        # The table of contents is not read; a document missing from it is titled by its first heading
        self.assertEqual([c.title for c in chapters], ['Le départ', 'Le retour', 'Épilogue final'])
        self.assertEqual(chapters[2].text, 'Épilogue final\nFin.')
        self.assertTrue(chapters[0].text.startswith('Chapitre 1, paragraphe 1.\nChapitre 1, paragraphe 2.'))


class TestRecordingNormalization(unittest.TestCase):
    """Reference recordings of the cloned voices: mono 24 kHz, silences cut, level set."""

    def setUp(self):
        self.recording = importlib.import_module('controllers.audiobook.recording')

    def test_quiet_stereo_recording_is_normalized(self):
        normalized = self.recording.normalize(tone_wav(2.0, amplitude=500, rate=44100, channels=2, silence=0.5))
        samples, rate = self.recording.read_samples(normalized.wav)
        self.assertEqual(rate, 24000)
        self.assertAlmostEqual(normalized.seconds, 2.3, delta=0.05)
        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        self.assertAlmostEqual(20 * math.log10(rms), -20.0, delta=0.5)
        self.assertGreater(normalized.gain_db, 0)

    def test_loud_recording_is_limited_by_its_peak(self):
        samples, _ = self.recording.read_samples(self.recording.normalize(tone_wav(1.0, amplitude=32000)).wav)
        self.assertLessEqual(max(abs(s) for s in samples), 10 ** (-1 / 20) + 0.001)

    def test_silent_or_invalid_recordings_are_refused(self):
        with self.assertRaises(ValueError):
            self.recording.normalize(silent_wav())
        with self.assertRaises((wave.Error, EOFError)):
            self.recording.normalize(b'RIFF-not-a-wav')
