// Web reader: reads the book aloud (audiobook, « Écouter »). foliate-js's TTS module (static/foliate/tts.js,
// vendored) splits each chapter into blocks and sentences (SSML marks); the sentences are grouped into
// requests of at most `maxChars` characters, each synthesized by the server (POST /webui/audiobook/speech)
// just before it is played: one request played, the next AHEAD ones prepared, never the whole book.
// The sentence read is underlined and the pages follow it, so the reading position (sync.js) and the
// statistics (stats.js) move with the listening. Two TTS instances walk the same chapter: one prepares
// the requests ahead, the other follows the playback for the underlining (foliate's TTS only marks the
// sentences of its current block).
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const FOLIATE = '/static/foliate/';
    // Requests prepared after the one being played
    const AHEAD = 2;
    // The first request is shorter: the sound starts sooner
    const FIRST_CHARS = 200;
    // Overlay of the sentence read (foliate overlayer key) and its underline width (px)
    const MARK_KEY = 'pyopds-narration';
    const MARK_WIDTH = 2;
    const LETTER_RE = /[\p{L}\p{N}]/u;

    let modules = null;

    /** foliate's TTS class and text walker, imported once. */
    function foliateTTS() {
        modules = modules || Promise.all([import(FOLIATE + 'tts.js'), import(FOLIATE + 'text-walker.js')])
            .then(function ([tts, walker]) { return { TTS: tts.TTS, textWalker: walker.textWalker }; });
        return modules;
    }

    /** Sentences [{mark, text}] of a block of foliate's TTS (SSML: a <mark name> before each sentence). */
    function sentences(ssml) {
        const doc = new DOMParser().parseFromString(ssml, 'application/xml');
        if (doc.getElementsByTagName('parsererror').length) return [];
        const list = [];
        let current = null;
        const walk = function (node) {
            for (let child = node.firstChild; child; child = child.nextSibling) {
                if (child.nodeType === Node.TEXT_NODE || child.nodeType === Node.CDATA_SECTION_NODE) {
                    if (!current) list.push(current = { mark: null, text: '' });
                    current.text += child.data;
                } else if (child.nodeType === Node.ELEMENT_NODE && child.localName === 'mark') {
                    list.push(current = { mark: child.getAttribute('name'), text: '' });
                } else if (child.nodeType === Node.ELEMENT_NODE && child.localName === 'break') {
                    if (current) current.text += ' ';
                } else if (child.nodeType === Node.ELEMENT_NODE) {
                    walk(child);
                }
            }
        };
        walk(doc.documentElement);
        return list
            .map(function (s) { return { mark: s.mark, text: s.text.replace(/­/g, '').replace(/\s+/g, ' ').trim() }; })
            .filter(function (s) { return LETTER_RE.test(s.text); });
    }

    /** Pieces of at most `max` characters of a text, cut between words. */
    function pieces(text, max) {
        const parts = [];
        let rest = text;
        while (rest.length > max) {
            const cut = rest.lastIndexOf(' ', max);
            const end = cut > max / 2 ? cut : max;
            parts.push(rest.slice(0, end).trim());
            rest = rest.slice(end).trim();
        }
        if (rest) parts.push(rest);
        return parts;
    }

    /**
     * Requests of a block: its sentences grouped up to `max` characters (a longer one cut between words),
     * each {text, marks: [{mark, at}]}, `at` being where the sentence starts (fraction of the text, which
     * places it in the audio).
     */
    function requests(list, max) {
        const out = [];
        let request = null;
        list.forEach(function (sentence) {
            pieces(sentence.text, max).forEach(function (piece, i) {
                if (!request || request.text.length + 1 + piece.length > max) out.push(request = { text: '', marks: [] });
                if (i === 0 && sentence.mark !== null) request.marks.push({ mark: sentence.mark, start: request.text ? request.text.length + 1 : 0 });
                request.text = request.text ? request.text + ' ' + piece : piece;
            });
        });
        out.forEach(function (r) {
            r.marks.forEach(function (m) { m.at = m.start / r.text.length; });
        });
        return out;
    }

    /** First block of a TTS: the one of the starting point in its chapter, else the chapter's first. */
    function begin(tts, doc, start) {
        if (start) {
            try {
                const range = start.anchor(doc);
                if (range) return tts.from(range);
            } catch (e) { /* point not found in this document: the chapter's start */ }
        }
        return tts.start();
    }

    /** Silent WAV: played on the button tap, it allows the later plays on phones (autoplay rules). */
    function silence() {
        const buffer = new ArrayBuffer(44 + 800);
        const view = new DataView(buffer);
        const ascii = function (offset, text) { for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i)); };
        ascii(0, 'RIFF'); view.setUint32(4, 36 + 800, true); ascii(8, 'WAVE'); ascii(12, 'fmt ');
        view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
        view.setUint32(24, 8000, true); view.setUint32(28, 16000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
        ascii(36, 'data'); view.setUint32(40, 800, true);
        return URL.createObjectURL(new Blob([buffer], { type: 'audio/wav' }));
    }

    /**
     * Narration of an opened book (book.js). `options`: {maxChars, speech(text) → Promise<Blob> (the audio,
     * an Error with the message to show on failure), location() → {cfi} of the place shown,
     * onState(state: 'idle' | 'loading' | 'playing' | 'paused'), onError(message)}.
     * Returns {start(), pause(), resume(), next(), replay(), stop(), restart(), setRate(rate)}.
     */
    function create(reader, options) {
        const view = reader.view;
        const audio = new Audio();
        audio.preservesPitch = true;
        const unlock = silence();
        let rate = 1;
        // Run of the narration: bumped by stop / restart, every pending step of an older run gives up
        let run = 0;
        let state = 'idle';
        // Paused by the reader: the next audio waits for the play button
        let paused = false;
        let start = null;
        let iterator = null;
        let ended = false;
        let filling = null;
        let queue = [];
        let chain = Promise.resolve();
        let follower = null;
        let lastRange = null;
        let stopPlaying = null;

        function setState(value) {
            state = value;
            options.onState(value);
            if (navigator.mediaSession) navigator.mediaSession.playbackState = value === 'playing' ? 'playing' : value === 'paused' ? 'paused' : 'none';
        }

        /** Requests from the starting point to the end of the book, chapter by chapter (linear ones). */
        async function* sequence() {
            const { TTS, textWalker } = await foliateTTS();
            const sections = reader.book.sections;
            let first = true;
            for (let index = start.index; index < sections.length; index++) {
                if (index !== start.index && sections[index].linear === 'no') continue;
                const doc = await PyReader.book.sectionDocument(reader, index);
                if (!doc || !doc.body) continue;
                const tts = new TTS(doc, textWalker, function () {}, 'sentence');
                let ssml = begin(tts, doc, index === start.index ? start : null);
                for (let block = 0; ssml; block++, ssml = tts.next()) {
                    for (const request of requests(sentences(ssml), first ? Math.min(options.maxChars, FIRST_CHARS) : options.maxChars)) {
                        first = false;
                        yield Object.assign({ index: index, block: block }, request);
                    }
                }
            }
        }

        /** Audio of a request, asked once the previous one is answered (one request at a time per reader);
            never asked when the run is over (stopped, or restarted with another voice). */
        function prepare(request, id) {
            const result = chain.then(function () {
                if (id !== run) throw new Error('Lecture arrêtée');
                return options.speech(request.text);
            });
            chain = result.catch(function () {});
            result.catch(function () {});
            return result;
        }

        /** Keep AHEAD requests prepared after the one being played. */
        function fill(id) {
            if (filling) return filling;
            filling = (async function () {
                while (id === run && !ended && queue.length < AHEAD + 1) {
                    const step = await iterator.next();
                    if (id !== run) break;
                    if (step.done) ended = true;
                    else queue.push({ request: step.value, audio: prepare(step.value, id) });
                }
            })().finally(function () { filling = null; });
            return filling;
        }

        /** Content shown of a chapter (rendered document and overlayer), null when it is not on screen. */
        function shown(index) {
            return view.renderer.getContents().find(function (content) { return content.index === index && content.doc; }) || null;
        }

        /** Underline the sentence read and turn the page when it is not on the one shown. */
        function highlight(range) {
            lastRange = range;
            const content = view.renderer.getContents().find(function (c) { return c.doc === range.startContainer.ownerDocument; });
            if (!content) return;
            if (content.overlayer) {
                const color = getComputedStyle(document.documentElement).getPropertyValue('--reader-accent').trim() || '#0f766e';
                content.overlayer.remove(MARK_KEY);
                content.overlayer.add(MARK_KEY, range, PyReader.Overlayer.underline, { color: color, width: MARK_WIDTH });
            }
            const visible = view.lastLocation && view.lastLocation.range;
            const onPage = visible && visible.startContainer.ownerDocument === content.doc
                && visible.comparePoint(range.startContainer, range.startOffset) === 0;
            if (!onPage) view.renderer.scrollToAnchor(range);
        }

        function clearHighlight() {
            view.renderer.getContents().forEach(function (content) { if (content.overlayer) content.overlayer.remove(MARK_KEY); });
        }

        /** Show the chapter of a request and put the follower TTS on its block. */
        async function follow(request) {
            const { TTS, textWalker } = await foliateTTS();
            let content = shown(request.index);
            if (!follower || follower.index !== request.index || !content || content.doc !== follower.doc) {
                if (!content) {
                    await view.goTo(request.index);
                    content = shown(request.index);
                }
                if (!content) return false;
                const tts = new TTS(content.doc, textWalker, highlight, 'sentence');
                begin(tts, content.doc, request.index === start.index ? start : null);
                follower = { index: request.index, doc: content.doc, tts: tts, block: 0 };
            }
            while (follower.block < request.block) {
                follower.tts.next();
                follower.block++;
            }
            return true;
        }

        /** Play the audio of a request, underlining its sentences as they come; resolves at its end. */
        function play(request, blob, followed) {
            return new Promise(function (resolve, reject) {
                const url = URL.createObjectURL(blob);
                let mark = -1;
                const markAt = function (i) {
                    if (i === mark || !followed) return;
                    mark = i;
                    follower.tts.setMark(request.marks[i].mark);
                };
                const onTime = function () {
                    if (!audio.duration || !Number.isFinite(audio.duration)) return;
                    const fraction = audio.currentTime / audio.duration;
                    let i = Math.max(mark, 0);
                    while (i + 1 < request.marks.length && request.marks[i + 1].at <= fraction) i++;
                    if (request.marks.length) markAt(i);
                };
                const cleanup = function () {
                    audio.removeEventListener('timeupdate', onTime);
                    audio.removeEventListener('ended', onEnded);
                    audio.removeEventListener('error', onFailed);
                    stopPlaying = null;
                    URL.revokeObjectURL(url);
                };
                const onEnded = function () { cleanup(); resolve(); };
                const onFailed = function () { cleanup(); reject(new Error('Audio illisible par le navigateur')); };
                stopPlaying = function () { cleanup(); resolve(); };
                audio.addEventListener('timeupdate', onTime);
                audio.addEventListener('ended', onEnded);
                audio.addEventListener('error', onFailed);
                if (request.marks.length) markAt(0);
                audio.src = url;
                audio.playbackRate = rate;
                if (paused) {
                    setState('paused');
                    return;
                }
                setState('playing');
                audio.play().catch(function () {
                    // Refused (no user gesture on this device): waits for the play button
                    paused = true;
                    setState('paused');
                });
            });
        }

        async function loop(id) {
            for (;;) {
                while (!queue.length && !ended) {
                    try {
                        await fill(id);
                    } catch (e) {
                        if (id === run) fail('Texte du livre illisible');
                        return;
                    }
                    if (id !== run) return;
                }
                const item = queue.shift();
                if (!item) {
                    finish();
                    return;
                }
                fill(id).catch(function () {});
                if (!paused) setState('loading');
                let blob;
                try {
                    blob = await item.audio;
                } catch (e) {
                    if (id === run) fail(e.message);
                    return;
                }
                if (id !== run) return;
                const followed = await follow(item.request).catch(function () { return false; });
                if (id !== run) return;
                try {
                    await play(item.request, blob, followed);
                } catch (e) {
                    if (id === run) fail(e.message);
                    return;
                }
                if (id !== run) return;
            }
        }

        function reset() {
            run++;
            if (stopPlaying) stopPlaying();
            audio.pause();
            paused = false;
            queue = [];
            ended = false;
            iterator = null;
            follower = null;
            chain = Promise.resolve();
        }

        function finish() {
            reset();
            clearHighlight();
            setState('idle');
        }

        function fail(message) {
            finish();
            options.onError(message);
        }

        /** Read from a CFI (the place shown when absent). Call it from a tap: phones allow the sound then. */
        function startAt(cfi) {
            reset();
            audio.src = unlock;
            audio.play().catch(function () {});
            const target = cfi || (options.location() || {}).cfi;
            try {
                start = target ? view.resolveCFI(target) : null;
            } catch (e) {
                start = null;
            }
            if (!start || typeof start.index !== 'number') {
                const current = shown((view.lastLocation && view.lastLocation.section && view.lastLocation.section.current) || 0);
                start = { index: current ? current.index : 0, anchor: function () { return null; } };
            }
            iterator = sequence();
            setState('loading');
            loop(run);
        }

        if (navigator.mediaSession) {
            const info = reader.info;
            navigator.mediaSession.metadata = new MediaMetadata({ title: info.title, artist: info.author });
            const actions = {
                play: function () { resume(); },
                pause: function () { pause(); },
                stop: function () { finish(); },
                nexttrack: function () { next(); },
                previoustrack: function () { replay(); },
            };
            Object.keys(actions).forEach(function (name) {
                try { navigator.mediaSession.setActionHandler(name, actions[name]); } catch (e) { /* action unsupported */ }
            });
        }

        function pause() {
            if (state === 'idle') return;
            paused = true;
            audio.pause();
            setState('paused');
        }

        function resume() {
            if (state === 'idle') {
                startAt(null);
                return;
            }
            paused = false;
            // An audio is loaded: played now; else the one being prepared plays when it comes
            if (!stopPlaying) {
                setState('loading');
                return;
            }
            setState('playing');
            audio.play().catch(function () {
                paused = true;
                setState('paused');
            });
        }

        /** Skip to the next request (already prepared). */
        function next() {
            if (state !== 'idle' && stopPlaying) stopPlaying();
        }

        /** Hear the current request again from its start. */
        function replay() {
            if (state !== 'idle' && stopPlaying) audio.currentTime = 0;
        }

        return {
            start: function () { startAt(null); },
            pause: pause,
            resume: resume,
            next: next,
            replay: replay,
            stop: finish,
            /** Start again at the sentence read (new voice or tone: the prepared requests are dropped). */
            restart: function () {
                if (state === 'idle') return;
                const current = lastRange && view.renderer.getContents().find(function (c) { return c.doc === lastRange.startContainer.ownerDocument; });
                startAt(current ? view.getCFI(current.index, lastRange) : null);
            },
            setRate: function (value) {
                rate = value;
                audio.playbackRate = value;
            },
        };
    }

    PyReader.narration = { create: create, sentences: sentences, requests: requests };
})();
