// Catalog web UI: "Marquer comme lu" button of the book sheet (button#book-dialog-read), for signed-in
// readers only (SyncAccount.sessionUser). On each opened sheet, reads the book's status from
// GET /webui/book-status?path=… (path sent by book-sheet.js) and toggles it with PUT /webui/book-status
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
    // Book shown by the sheet (PyOPDS.bookSheet.follow)
    let shownPath = null;

    /** Status of a book (GET), or the new one after a change (PUT with a body). */
    function call(method, path, body) {
        const url = method === 'GET' ? '/webui/book-status?path=' + encodeURIComponent(path) : '/webui/book-status';
        return window.PageKit.fetchJson(url, method, body);
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

    /** Show the button of the opened book once its status is known (none when signed out). */
    async function show(path) {
        button.hidden = true;
        dateBlock.hidden = true;
        if (!path) return;
        try {
            const state = await call('GET', path);
            if (path !== shownPath()) return;
            render(state);
            button.disabled = false;
            button.hidden = false;
        } catch (e) {
            // Unknown book or expired session: no button
        }
    }

    async function toggle() {
        const path = shownPath();
        if (!path) return;
        const read = button.getAttribute('aria-pressed') !== 'true';
        button.disabled = true;
        try {
            const state = await call('PUT', path, { path: path, read: read });
            if (path === shownPath()) render(state);
        } catch (e) {
            if (path === shownPath()) button.title = 'Échec de la mise à jour, réessayer';
        } finally {
            button.disabled = false;
        }
    }

    /** Save the day the book was read (an empty field clears it). */
    async function saveDate() {
        const path = shownPath();
        if (!path || (dateInput.value && !dateInput.checkValidity())) return;
        dateInput.disabled = true;
        try {
            const state = await call('PUT', path, { path: path, read: true, finished_date: dateInput.value || null });
            if (path === shownPath()) render(state);
        } catch (e) {
            if (path === shownPath()) dateInput.title = 'Échec de la mise à jour, réessayer';
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
        if (!button || !label || !dateBlock || !dateInput) return;
        shownPath = PyOPDS.bookSheet.follow(show);
        if (!shownPath) return;
        button.addEventListener('click', toggle);
        dateInput.addEventListener('change', saveDate);
    }

    PyOPDS.readStatus = { init: init };
})();
