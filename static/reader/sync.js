// Web reader: reading position. Sent to the sync account like a reader does (PUT /api/v1/progress:
// KOReader XPointer + percentage, CrossPoint rich position {pctQ, spine, page, pages, para, xpath}),
// so that KOReader and CrossPoint readers continue where the browser stopped, and the other way round
// (GET /api/v1/progress/{document}: every device's position). Each browser is its own device
// (random id kept in localStorage); its last position is also kept locally, for the readers without
// an account and when the server cannot be reached.
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const DEVICE_KEY = 'pyopds-reader-device';
    const LOCAL_PREFIX = 'pyopds-reader-position:';
    const XPATH_MAX_BYTES = 120;
    const UINT16_MAX = 65535;

    function randomId() {
        // crypto.getRandomValues also exists over plain HTTP (randomUUID needs HTTPS)
        const bytes = new Uint8Array(8);
        crypto.getRandomValues(bytes);
        return Array.from(bytes, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    }

    let device = null;

    /** This browser as a sync device: {id, name} (name from the platform, e.g. "Navigateur (Android)"). */
    function thisDevice() {
        if (device) return device;
        let id = null;
        try { id = localStorage.getItem(DEVICE_KEY); } catch (e) { /* storage unavailable */ }
        if (!id) {
            id = 'web-' + randomId();
            try { localStorage.setItem(DEVICE_KEY, id); } catch (e) { /* storage unavailable */ }
        }
        const agent = navigator.userAgent;
        const platform = /iPad/.test(agent) || (/Macintosh/.test(agent) && navigator.maxTouchPoints > 1) ? 'iPad'
            : /iPhone/.test(agent) ? 'iPhone' : /Android/.test(agent) ? 'Android' : /Windows/.test(agent) ? 'Windows'
            : /Mac OS X/.test(agent) ? 'Mac' : /Linux/.test(agent) ? 'Linux' : '';
        device = { id: id, name: 'Navigateur' + (platform ? ' (' + platform + ')' : '') };
        return device;
    }

    function byteLength(text) {
        return new TextEncoder().encode(text).length;
    }

    /**
     * Sync position of the place shown: {progress (XPointer), percentage, position (CrossPoint)}.
     * `location`: foliate relocate detail; `reader`: the opened book (book.js).
     */
    function positionOf(reader, location) {
        const positions = PyReader.positions;
        const index = location.index;
        const range = location.range;
        const spine = reader.spineOf(index);
        const doc = range ? range.startContainer.ownerDocument : null;
        const xpointer = doc ? positions.toXPointer(doc, spine, range.startContainer, range.startOffset) : '/body/DocFragment[' + (spine + 1) + ']';
        const percentage = Math.min(1, Math.max(0, location.fraction || 0));
        const page = location.page || { page: 1, pages: 1 };
        const position = {
            pctQ: Math.round(percentage * 1000000),
            spine: Math.min(spine, UINT16_MAX),
            page: Math.min(Math.max(0, page.page - 1), UINT16_MAX),
            pages: Math.min(Math.max(1, page.pages), UINT16_MAX),
        };
        const para = doc ? positions.paragraphIndex(doc, range.startContainer, range.startOffset) : 0;
        if (para > 0) position.para = Math.min(para, UINT16_MAX);
        if (byteLength(xpointer) <= XPATH_MAX_BYTES) position.xpath = xpointer;
        return { progress: xpointer, percentage: percentage, position: position };
    }

    /** Last position kept by this browser for a book: {cfi, percentage, at (ms)} or null. */
    function localPosition(path) {
        try { return JSON.parse(localStorage.getItem(LOCAL_PREFIX + path) || 'null'); } catch (e) { return null; }
    }

    function keepLocal(path, cfi, percentage) {
        try { localStorage.setItem(LOCAL_PREFIX + path, JSON.stringify({ cfi: cfi, percentage: percentage, at: Date.now() })); } catch (e) { /* storage unavailable */ }
    }

    /** Every device's position of the book, newest first ([] without an account or on failure). */
    async function remotePositions(documentId) {
        if (!documentId) return [];
        try {
            const data = await window.PageKit.fetchJson('/api/v1/progress/' + encodeURIComponent(documentId));
            return data.devices || [];
        } catch (e) {
            return [];
        }
    }

    /**
     * Send the position to the sync account; `keepalive` lets it leave while the page closes.
     * Returns the server timestamp (seconds), null on failure.
     */
    async function push(reader, sync, keepalive) {
        const info = reader.info;
        if (!info.document) return null;
        const me = thisDevice();
        const body = {
            document: info.document,
            progress: sync.progress,
            percentage: sync.percentage,
            device: me.name,
            device_id: me.id,
            position: sync.position,
            metadata: { filename: info.filename, title: info.title, authors: info.author },
        };
        try {
            const response = await fetch('/api/v1/progress', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify(body),
                credentials: 'same-origin',
                keepalive: Boolean(keepalive),
            });
            if (!response.ok) return null;
            return (await response.json()).timestamp || null;
        } catch (e) {
            return null;
        }
    }

    /**
     * Where to open a remote position: {index, anchor} for foliate's renderer (XPointer of KOReader /
     * CrossPoint readers, else CrossPoint spine + paragraph), null when only the percentage is usable.
     */
    async function remoteTarget(reader, row) {
        const positions = PyReader.positions;
        const xpointer = [row.position && row.position.xpath, row.progress].find(function (value) {
            return positions.xpointerSpine(value) !== null;
        });
        const spine = xpointer ? positions.xpointerSpine(xpointer) : row.position ? row.position.spine : null;
        const index = spine === null || spine === undefined ? null : reader.sectionOf(spine);
        if (index === null) return null;
        const doc = await PyReader.book.sectionDocument(reader, index);
        if (!doc) return null;
        let range = xpointer ? positions.xpointerRange(doc, xpointer) : null;
        if (!range && row.position && row.position.para) {
            const paragraphs = doc.querySelectorAll('p, li');
            const element = paragraphs[row.position.para - 1];
            if (element) {
                range = doc.createRange();
                range.selectNodeContents(element);
                range.collapse(true);
            }
        }
        if (!range) return { index: index, anchor: 0 };
        // The CFI of the place works in the rendered document as well as in a parsed copy
        return { cfi: reader.view.getCFI(index, range) };
    }

    PyReader.sync = {
        thisDevice: thisDevice, positionOf: positionOf, localPosition: localPosition, keepLocal: keepLocal,
        remotePositions: remotePositions, push: push, remoteTarget: remoteTarget,
    };
})();
