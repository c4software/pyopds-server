// Web reader: clippings of the book, shared with the CrossPoint readers, the CrossPoint Sync app and
// the /clippings page (GET / PUT /api/v1/clippings/{document}). A clipping made here is a CrossPoint one:
// spine index, visible offsets of the selection in its chapter (start_offset / end_offset), paragraph,
// chapter title, text, optional note. Clippings with offsets are drawn where they are; the others
// (KOReader highlights exported as Nextcloud Notes) are found by their text in each chapter shown.
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const PAGE_LIMIT = 100;
    const TEXT_MAX_BYTES = 4096;
    const CHAPTER_MAX = 64;
    const SEARCH_CHARS = 300;
    // Highlight colors (CrossPoint `color`: free text): stored name -> drawn color
    const COLORS = { yellow: '#facc15', green: '#4ade80', blue: '#60a5fa', pink: '#f472b6' };
    const DEFAULT_COLOR = 'yellow';

    function randomId() {
        const bytes = new Uint8Array(12);
        crypto.getRandomValues(bytes);
        return 'web-' + Array.from(bytes, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    }

    /** Longest prefix of `text` within `max` UTF-8 bytes. */
    function truncateBytes(text, max) {
        const encoder = new TextEncoder();
        if (encoder.encode(text).length <= max) return text;
        let end = Math.min(text.length, max);
        while (end > 0 && encoder.encode(text.slice(0, end)).length > max) end--;
        return text.slice(0, end);
    }

    function escapeRegExp(text) {
        return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    /** Range of a passage found in a chapter (whitespace and quotes may differ), null when absent. */
    function findText(doc, text) {
        const positions = PyReader.positions;
        const words = text.slice(0, SEARCH_CHARS).trim().split(/\s+/).filter(Boolean);
        if (!words.length) return null;
        const nodes = positions.visibleTextNodes(doc);
        const full = nodes.map(function (node) { return node.data; }).join('');
        const pattern = words.map(function (word) {
            return escapeRegExp(word).replace(/['’‘]/g, "['’‘]").replace(/["“”«»]/g, '["“”«»]');
        }).join('\\s+');
        const match = new RegExp(pattern).exec(full);
        if (!match) return null;
        // Offsets in UTF-16 units of the joined text -> code points of the chapter
        const start = positions.codePoints(full.slice(0, match.index));
        const length = text.length > SEARCH_CHARS ? positions.codePoints(text.trim()) : positions.codePoints(match[0]);
        return positions.offsetsRange(doc, start, start + length);
    }

    /**
     * Clippings of a book: `reader` is the opened book (book.js), `documentId` the sync document.
     * onChange() is called whenever the list changes.
     */
    function create(reader, documentId, onChange) {
        const view = reader.view;
        const clips = new Map();
        // Drawn annotations: CFI -> clipping id, and where each clipping was found {index, cfi}
        const byCfi = new Map();
        const placed = new Map();

        function sorted() {
            return Array.from(clips.values()).sort(function (a, b) {
                return (a.spine - b.spine) || ((a.start_offset ?? a.para ?? a.start_page ?? 0) - (b.start_offset ?? b.para ?? b.start_page ?? 0));
            });
        }

        /** Range of a clipping in a chapter document, null when it is not there. */
        function rangeIn(doc, index, clip) {
            const offsets = clip.start_offset !== null && clip.start_offset !== undefined;
            if (offsets) {
                if (reader.sectionOf(clip.spine) !== index) return null;
                return PyReader.positions.offsetsRange(doc, clip.start_offset, clip.end_offset);
            }
            return findText(doc, clip.text);
        }

        function draw(index, doc, clip) {
            const range = rangeIn(doc, index, clip);
            if (!range) return;
            const cfi = view.getCFI(index, range);
            const previous = placed.get(clip.id);
            if (previous) {
                byCfi.delete(previous.cfi);
                view.deleteAnnotation({ value: previous.cfi });
            }
            byCfi.set(cfi, clip.id);
            placed.set(clip.id, { index: index, cfi: cfi });
            view.addAnnotation({ value: cfi });
        }

        /** Draw the clippings of a chapter once it is shown (foliate "create-overlay"). */
        function drawSection(index) {
            const content = view.renderer.getContents().find(function (c) { return c.index === index; });
            if (!content || !content.doc) return;
            clips.forEach(function (clip) { draw(index, content.doc, clip); });
        }

        function redraw() {
            view.renderer.getContents().forEach(function (content) { drawSection(content.index); });
        }

        view.addEventListener('create-overlay', function (event) { drawSection(event.detail.index); });
        view.addEventListener('draw-annotation', function (event) {
            const clip = clips.get(byCfi.get(event.detail.annotation.value));
            const color = COLORS[clip && clip.color] || COLORS[DEFAULT_COLOR];
            event.detail.draw(window.PyReader.Overlayer.highlight, { color: color });
        });

        async function load() {
            if (!documentId) return;
            let cursor = 0;
            for (;;) {
                const data = await window.PageKit.fetchJson('/api/v1/clippings/' + encodeURIComponent(documentId) + '?cursor=' + cursor + '&limit=' + PAGE_LIMIT);
                data.items.forEach(function (item) {
                    if (item.deleted) clips.delete(item.id);
                    else clips.set(item.id, item);
                });
                if (!data.more || data.cursor <= cursor) break;
                cursor = data.cursor;
            }
            redraw();
            onChange();
        }

        async function upload(items) {
            await window.PageKit.fetchJson('/api/v1/clippings/' + encodeURIComponent(documentId), 'PUT', { items: items });
        }

        /** Clipping of a selection (Range in the chapter of section `index`), saved; returns it. */
        async function add(index, range, location, color) {
            const doc = range.startContainer.ownerDocument;
            const positions = PyReader.positions;
            const offsets = positions.rangeOffsets(doc, range);
            const text = truncateBytes(range.toString().trim(), TEXT_MAX_BYTES);
            if (!text || offsets.end <= offsets.start) return null;
            const tocItem = view.getProgressOf(index, range).tocItem;
            const page = location.page || { page: 1, pages: 1 };
            const para = positions.paragraphIndex(doc, range.startContainer, range.startOffset);
            const clip = {
                id: randomId(),
                spine: reader.spineOf(index),
                start_page: Math.max(0, page.page - 1),
                end_page: Math.max(0, page.page - 1),
                pages: Math.max(1, page.pages),
                para: para > 0 ? para : null,
                chapter: ((tocItem && tocItem.label) || '').trim().slice(0, CHAPTER_MAX),
                text: text,
                note: null,
                color: color || DEFAULT_COLOR,
                created_at: Math.floor(Date.now() / 1000),
                start_offset: offsets.start,
                end_offset: offsets.end,
            };
            await upload([clip]);
            clips.set(clip.id, clip);
            draw(index, doc, clip);
            onChange();
            return clip;
        }

        /** Save the note and the color of a clipping (the whole clipping is sent again). */
        async function update(id, changes) {
            const clip = Object.assign({}, clips.get(id), changes);
            const item = {
                id: clip.id, spine: clip.spine, start_page: clip.start_page, end_page: clip.end_page, pages: clip.pages,
                start_word: clip.start_word || 0, end_word: clip.end_word || 0, words: clip.words || 0,
                para: clip.para ?? null, chapter: clip.chapter || '', text: clip.text, note: clip.note || null,
                color: clip.color || null, created_at: clip.created_at || 0, layout_signature: clip.layout_signature || 0,
            };
            if (clip.start_offset !== null && clip.start_offset !== undefined) {
                item.start_offset = clip.start_offset;
                item.end_offset = clip.end_offset;
            }
            await upload([item]);
            clips.set(id, clip);
            const where = placed.get(id);
            if (where) {
                // Same CFI: the new color is drawn by draw-annotation
                view.addAnnotation({ value: where.cfi });
            }
            onChange();
            return clip;
        }

        async function remove(id) {
            await upload([{ id: id, deleted: 1 }]);
            const where = placed.get(id);
            if (where) {
                view.deleteAnnotation({ value: where.cfi });
                byCfi.delete(where.cfi);
                placed.delete(id);
            }
            clips.delete(id);
            onChange();
        }

        /** Go to a clipping: drawn ones by their CFI, else searched in their chapter or in the whole book. */
        async function show(id) {
            const clip = clips.get(id);
            if (!clip) return false;
            const where = placed.get(id);
            if (where) {
                await view.goTo(where.cfi);
                return true;
            }
            const first = clip.start_offset !== null && clip.start_offset !== undefined ? reader.sectionOf(clip.spine) : null;
            const indexes = first !== null ? [first] : reader.book.sections.map(function (_, i) { return i; });
            for (const index of indexes) {
                const doc = await PyReader.book.sectionDocument(reader, index);
                const range = doc && rangeIn(doc, index, clip);
                if (range) {
                    await view.goTo(view.getCFI(index, range));
                    return true;
                }
            }
            return false;
        }

        return {
            load: load, add: add, update: update, remove: remove, show: show, sorted: sorted,
            /** Clipping drawn at a CFI (tap on a highlight), null otherwise. */
            atCfi: function (cfi) { return clips.get(byCfi.get(cfi)) || null; },
            get: function (id) { return clips.get(id) || null; },
        };
    }

    PyReader.highlights = { create: create, COLORS: COLORS };
})();
