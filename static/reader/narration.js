// Web reader: reads the book aloud (audiobook, « Écouter »). foliate-js's TTS module (static/vendor/foliate/tts.js,
// vendored) splits each chapter into blocks and sentences (SSML marks); the sentences of several paragraphs
// are grouped into one request (the voice keeps its tone across them), each synthesized by the server
// (POST /webui/audiobook/speech) just before it is played: one request played, the next AHEAD ones prepared,
// never the whole book. The requests grow, so the sound starts soon: a short first one, then up to `maxChars`.
// The sentence read is underlined and the pages follow it, so the reading position (sync.js) and the
// statistics (stats.js) move with the listening. Two TTS instances walk the same chapter: one prepares
// the requests ahead, the other follows the playback for the underlining (foliate's TTS only marks the
// sentences of its current block).
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const FOLIATE = '/static/vendor/foliate/';
    // Requests prepared after the one being played
    const AHEAD = 2;
    // Size limits of the first requests (characters), then `maxChars`: the first sound comes after a short one,
    // the next ones are generated while the previous ones play
    const GROWTH = [200, 500];
    // Overlay of the sentence read (foliate overlayer key) and its underline width (px)
    const MARK_KEY = 'pyopds-narration';
    const MARK_WIDTH = 2;
    const LETTER_RE = /[\p{L}\p{N}]/u;
    // Word cut at the end of a line (fixed-layout books converted from a PDF: "cer", "-", "veau", one line per
    // element, each line a sentence for the segmenter): the start of the word, its hyphen, the rest of the word
    const WORD_START_RE = /\p{L}$/u;
    const CUT_WORD_RE = /\p{L}-$/u;
    const WORD_END_RE = /^\p{Ll}/u;

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
        return joinCutWords(list.map(function (s) { return { mark: s.mark, text: s.text.replace(/­/g, '').replace(/\s+/g, ' ').trim() }; }))
            .filter(function (s) { return LETTER_RE.test(s.text); });
    }

    /** Sentences with the words cut at the end of a line read whole: "cer" + "-" + "veau va" → "cerveau va". */
    function joinCutWords(list) {
        const joined = [];
        list.forEach(function (sentence) {
            const last = joined[joined.length - 1];
            if (last && sentence.text === '-' && WORD_START_RE.test(last.text)) last.text += '-';
            else if (last && CUT_WORD_RE.test(last.text) && WORD_END_RE.test(sentence.text)) last.text = last.text.slice(0, -1) + sentence.text;
            else joined.push(Object.assign({}, sentence));
        });
        return joined;
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
     * Requests built across the blocks of a chapter: the sentences are joined (a line break between two blocks,
     * a pause for the voice) until the next one would pass the size limit of the request (`limit()`, read as
     * the request fills: the requests grow); a sentence longer than the limit is cut between words. A request is
     * {text, marks: [{block, mark, at}]}, `at` being where the sentence starts (fraction of the text, which
     * places it in the audio). Returns {add(block, sentences) → completed requests, close() → the last one}.
     */
    function requestBuilder(limit) {
        let request = null;

        function close() {
            const done = request;
            request = null;
            if (done) done.marks.forEach(function (m) { m.at = m.start / done.text.length; });
            return done;
        }

        function add(block, list) {
            const out = [];
            list.forEach(function (sentence, s) {
                pieces(sentence.text, limit()).forEach(function (piece, i) {
                    const separator = s === 0 && i === 0 ? '\n' : ' ';
                    if (request && request.text.length + separator.length + piece.length > limit()) out.push(close());
                    const join = request ? separator : '';
                    request = request || { text: '', marks: [] };
                    if (i === 0 && sentence.mark !== null) request.marks.push({ block: block, mark: sentence.mark, start: request.text.length + join.length });
                    request.text += join + piece;
                });
            });
            return out;
        }

        return { add: add, close: close };
    }

    /** Range of a sentence of the current block of a TTS (its highlight callback is not called). */
    function markRange(tts, mark) {
        const highlight = tts.highlight;
        let range = null;
        tts.highlight = function (r) { range = r; };
        try {
            tts.setMark(mark);
        } finally {
            tts.highlight = highlight;
        }
        return range;
    }

    /**
     * First block of a TTS: from the first sentence starting on the page of the starting point, else the
     * chapter's first. foliate reads the whole block when none of its sentences starts after the point (the
     * page opens on the last sentence of a paragraph begun on the previous one): the next block then.
     */
    function begin(tts, doc, start) {
        if (start) {
            try {
                const range = start.anchor(doc);
                if (range) {
                    const ssml = tts.from(range);
                    const first = ssml ? sentences(ssml)[0] : null;
                    const at = first && first.mark !== null ? markRange(tts, first.mark) : null;
                    return at && at.compareBoundaryPoints(Range.START_TO_START, range) < 0 ? tts.next() : ssml;
                }
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
        // Sentences of the audio being played: {index() → sentence read, seek(i) → play from sentence i}
        let playing = null;

        function setState(value) {
            state = value;
            options.onState(value);
            if (navigator.mediaSession) navigator.mediaSession.playbackState = value === 'playing' ? 'playing' : value === 'paused' ? 'paused' : 'none';
        }

        /** Requests from the starting point to the end of the book, chapter by chapter (linear ones). */
        async function* sequence() {
            const { TTS, textWalker } = await foliateTTS();
            const sections = reader.book.sections;
            let count = 0;
            const limit = function () { return Math.min(options.maxChars, GROWTH[count] || Infinity); };
            for (let index = start.index; index < sections.length; index++) {
                if (index !== start.index && sections[index].linear === 'no') continue;
                const doc = await PyReader.book.sectionDocument(reader, index);
                if (!doc || !doc.body) continue;
                const tts = new TTS(doc, textWalker, function () {}, 'sentence');
                const builder = requestBuilder(limit);
                let ssml = begin(tts, doc, index === start.index ? start : null);
                for (let block = 0; ssml; block++, ssml = tts.next()) {
                    for (const request of builder.add(block, sentences(ssml))) {
                        count++;
                        yield Object.assign({ index: index }, request);
                    }
                }
                // A chapter ends its request: the next one starts on the next chapter's page
                const last = builder.close();
                if (last) {
                    count++;
                    yield Object.assign({ index: index }, last);
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

        /** Show the chapter of a request and the follower TTS on it (moved block by block as the sentences are read). */
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
                    const sentence = request.marks[i];
                    while (follower.block < sentence.block) {
                        follower.tts.next();
                        follower.block++;
                    }
                    while (follower.block > sentence.block) {
                        follower.tts.prev();
                        follower.block--;
                    }
                    follower.tts.setMark(sentence.mark);
                };
                const known = function () { return audio.duration && Number.isFinite(audio.duration); };
                const onTime = function () {
                    if (!known() || !request.marks.length) return;
                    const fraction = audio.currentTime / audio.duration;
                    let i = 0;
                    while (i + 1 < request.marks.length && request.marks[i + 1].at <= fraction) i++;
                    markAt(i);
                };
                playing = {
                    index: function () { return mark; },
                    count: request.marks.length,
                    /** Play from sentence i (false when the audio length is not known yet). */
                    seek: function (i) {
                        if (!known() || !request.marks[i]) return false;
                        audio.currentTime = request.marks[i].at * audio.duration;
                        onTime();
                        return true;
                    },
                };
                const cleanup = function () {
                    audio.removeEventListener('timeupdate', onTime);
                    audio.removeEventListener('ended', onEnded);
                    audio.removeEventListener('error', onFailed);
                    stopPlaying = null;
                    playing = null;
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

        /** Go on after a pause (nothing when stopped: the reading starts only from the player). */
        function resume() {
            if (state === 'idle') return;
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

        /** Skip to the next sentence. */
        function next() {
            if (state === 'idle' || !stopPlaying) return;
            // The next sentence of this audio, else the next request (already prepared)
            const i = playing ? playing.index() + 1 : 0;
            if (!playing || i >= playing.count || !playing.seek(i)) stopPlaying();
        }

        /** Hear the sentence read again. */
        function replay() {
            if (state === 'idle' || !stopPlaying) return;
            if (!playing || !playing.seek(Math.max(playing.index(), 0))) audio.currentTime = 0;
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

    PyReader.narration = { create: create, sentences: sentences, joinCutWords: joinCutWords, requestBuilder: requestBuilder };
})();
