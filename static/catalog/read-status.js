// Catalog web UI: "Marquer comme lu" button of the book sheet (button#book-dialog-read), for signed-in
// readers only (cookie pyopds_user). On each opened sheet, reads the book's status from
// GET /webui/book-status?path=… (path of the /download/ link) and toggles it with PUT /webui/book-status
// (manual "finished" status of the sync account, shown by the reading page and the recommendations).
// Once read, an optional date field (input#book-dialog-read-date) sets the day it was read, used by the
// reading statistics instead of the day it was marked.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    let button = null;
    let label = null;
    let dateBlock = null;
    let dateInput = null;
    // Book shown by the sheet: answers for an earlier book are ignored
    let current = null;

    function signedIn() {
        return /(?:^|;\s*)pyopds_user=/.test(document.cookie);
    }

    /** EPUB path relative to the library, from the tile's download link (/download/<path>). */
    function bookPath(link) {
        const href = link.getAttribute('href') || '';
        const prefix = '/download/';
        if (href.indexOf(prefix) !== 0) return null;
        try {
            return decodeURIComponent(href.slice(prefix.length).split('?')[0]);
        } catch (e) {
            return null;
        }
    }

    /** JSON answer of the endpoint; an expired session (redirected to /login) counts as a failure. */
    async function call(method, path, body) {
        const response = await fetch(
            method === 'GET' ? '/webui/book-status?path=' + encodeURIComponent(path) : '/webui/book-status',
            {
                method: method,
                headers: body ? { 'Accept': 'application/json', 'Content-Type': 'application/json' } : { 'Accept': 'application/json' },
                body: body ? JSON.stringify(body) : undefined,
                cache: 'no-store',
                credentials: 'same-origin',
            }
        );
        const type = response.headers.get('Content-Type') || '';
        if (!response.ok || type.indexOf('application/json') !== 0) {
            throw new Error('Erreur ' + response.status);
        }
        return response.json();
    }

    function today() {
        const now = new Date();
        return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    }

    function render(state) {
        const read = state.read;
        button.setAttribute('aria-pressed', read ? 'true' : 'false');
        label.textContent = read ? 'Lu' : 'Marquer comme lu';
        button.title = read ? 'Marquer comme non lu' : 'Marquer ce livre comme lu';
        dateBlock.hidden = !read;
        dateInput.value = state.finished_date || '';
        dateInput.max = today();
    }

    /** Show the button of the opened book once its status is known. */
    async function show(link) {
        button.hidden = true;
        dateBlock.hidden = true;
        current = signedIn() ? bookPath(link) : null;
        if (!current) return;
        const path = current;
        try {
            const state = await call('GET', path);
            if (path !== current) return;
            render(state);
            button.disabled = false;
            button.hidden = false;
        } catch (e) {
            // Unknown book or expired session: no button
        }
    }

    async function toggle() {
        const path = current;
        if (!path) return;
        const read = button.getAttribute('aria-pressed') !== 'true';
        button.disabled = true;
        try {
            const state = await call('PUT', path, { path: path, read: read });
            if (path === current) render(state);
        } catch (e) {
            if (path === current) button.title = 'Échec de la mise à jour, réessayer';
        } finally {
            button.disabled = false;
        }
    }

    /** Save the day the book was read (an empty field clears it). */
    async function saveDate() {
        const path = current;
        if (!path || (dateInput.value && !dateInput.checkValidity())) return;
        dateInput.disabled = true;
        try {
            const state = await call('PUT', path, { path: path, read: true, finished_date: dateInput.value || null });
            if (path === current) render(state);
        } catch (e) {
            if (path === current) dateInput.title = 'Échec de la mise à jour, réessayer';
        } finally {
            dateInput.disabled = false;
        }
    }

    /** Wire the button and follow the sheets opened by book-sheet.js. */
    function init() {
        button = document.getElementById('book-dialog-read');
        label = document.getElementById('book-dialog-read-label');
        dateBlock = document.getElementById('book-dialog-read-date-block');
        dateInput = document.getElementById('book-dialog-read-date');
        const dialog = document.getElementById('book-dialog');
        if (!button || !label || !dateBlock || !dateInput || !dialog) return;
        button.addEventListener('click', toggle);
        dateInput.addEventListener('change', saveDate);
        dialog.addEventListener('book-sheet:open', function (event) {
            show(event.detail.link);
        });
    }

    PyOPDS.readStatus = { init: init };
})();
