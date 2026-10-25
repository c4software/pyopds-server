// Web reader: audiobook controls (« Écouter »), offered to a signed-in reader when the server has a speech
// server (audiobook.toml; GET /webui/audiobook answers 404 otherwise). Alpine store `audiobook` (templates:
// static/partials/reader-audio.html and the button of the bottom bar in reader.html) around the narration
// (narration.js): play / pause, sentence again, next sentence, stop, speed (kept in this browser), and the
// book's voice and reading tone, a free prompt kept by the server for the reader and the book
// (PUT /webui/audiobook). The narration object lives outside the store (reactive proxies).
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const RATE_KEY = 'pyopds-audiobook-rate';
    const RATES = [0.8, 1, 1.2, 1.5];

    let narration = null;

    function loadRate() {
        let stored = 1;
        try { stored = Number(localStorage.getItem(RATE_KEY)) || 1; } catch (e) { /* storage unavailable */ }
        return RATES.includes(stored) ? stored : 1;
    }

    function store() {
        return window.Alpine.store('audiobook');
    }

    function refreshIcons() {
        window.PageKit.icons({ $nextTick: window.Alpine.nextTick });
    }

    /** Audio of a text, with the book's voice and tone (empty: the server's defaults). */
    async function speech(text) {
        const audiobook = store();
        const response = await fetch('/webui/audiobook/speech', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'audio/*, application/json' },
            body: JSON.stringify({ text: text, voice: audiobook.voice, instructions: audiobook.instructions }),
            cache: 'no-store',
            credentials: 'same-origin',
        });
        if (!response.ok) {
            const data = await response.json().catch(function () { return null; });
            throw new Error((data && data.message) || 'Erreur ' + response.status);
        }
        return response.blob();
    }

    document.addEventListener('alpine:init', function () {
        window.Alpine.store('audiobook', {
            available: false,
            state: 'idle',
            panel: false,
            path: '',
            // Book's voice and tone ('' : the defaults of the server), as edited in the panel
            voice: '',
            instructions: '',
            defaults: { voice: '', instructions: '' },
            // As last saved, restored when the panel is cancelled
            saved: { voice: '', instructions: '' },
            voices: [],
            voicesLoaded: false,
            saving: false,
            rate: loadRate(),
            rates: RATES,

            get active() { return this.state !== 'idle'; },
            get running() { return this.state === 'playing' || this.state === 'loading'; },

            /** The bottom bar button: start at the page shown, or pause / resume. */
            toggle() {
                if (!narration) return;
                if (this.state === 'idle') narration.start();
                else if (this.running) narration.pause();
                else narration.resume();
            },
            next() { if (narration) narration.next(); },
            replay() { if (narration) narration.replay(); },
            stop() { if (narration) narration.stop(); },

            setRate(rate) {
                this.rate = rate;
                try { localStorage.setItem(RATE_KEY, String(rate)); } catch (e) { /* storage unavailable */ }
                if (narration) narration.setRate(rate);
            },

            async openPanel() {
                this.panel = true;
                refreshIcons();
                if (this.voicesLoaded) return;
                try {
                    this.voices = (await window.PageKit.fetchJson('/webui/audiobook/voices')).voices || [];
                    // None listed (speech server busy or starting): asked again on the next opening
                    this.voicesLoaded = this.voices.length > 0;
                } catch (e) { /* none listed: the voice is typed */ }
            },

            /** Keep the voice and tone for this book; the narration goes on with them from the sentence read. */
            async save(reset) {
                this.saving = true;
                try {
                    const body = reset ? { path: this.path, voice: '', instructions: '' } : { path: this.path, voice: this.voice, instructions: this.instructions };
                    const saved = await window.PageKit.fetchJson('/webui/audiobook', 'PUT', body);
                    this.voice = saved.voice;
                    this.instructions = saved.instructions;
                    this.saved = { voice: saved.voice, instructions: saved.instructions };
                    this.panel = false;
                    if (narration) narration.restart();
                } catch (e) {
                    PyReader.audiobook.notice('Réglages de lecture non enregistrés : ' + e.message);
                } finally {
                    this.saving = false;
                }
            },

            cancel() {
                this.voice = this.saved.voice;
                this.instructions = this.saved.instructions;
                this.panel = false;
            },
        });
    });

    /**
     * Offer the audiobook for an opened book (book.js), `path` relative to the library; `hooks`:
     * {location() → place shown ({cfi}), notice(text)}. Nothing is shown when the server has no speech server.
     */
    async function attach(reader, path, hooks) {
        let settings;
        try {
            settings = await window.PageKit.fetchJson('/webui/audiobook?path=' + encodeURIComponent(path));
        } catch (e) {
            return;
        }
        const audiobook = store();
        PyReader.audiobook.notice = hooks.notice;
        Object.assign(audiobook, {
            path: path, voice: settings.voice, instructions: settings.instructions, defaults: settings.defaults,
            saved: { voice: settings.voice, instructions: settings.instructions },
        });
        narration = PyReader.narration.create(reader, {
            maxChars: settings.max_chars,
            speech: speech,
            location: hooks.location,
            onState: function (state) {
                audiobook.state = state;
                refreshIcons();
            },
            onError: function (message) { hooks.notice('Lecture audio interrompue : ' + message); },
        });
        narration.setRate(audiobook.rate);
        audiobook.available = true;
        refreshIcons();
    }

    PyReader.audiobook = { attach: attach, notice: function () {} };
})();
