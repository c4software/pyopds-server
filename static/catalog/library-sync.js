// Catalog web UI: « Recharger la bibliothèque » button of the header (button#library-sync), shown to signed-in
// readers (to everyone when the server has no accounts: minimal mode with a public catalog). A click asks
// POST /webui/library/sync to read the new and modified EPUBs (controllers/library/store.py), shows what changed,
// then reloads the page when the library changed.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    const STATUS_MS = 4000;

    let button = null;
    let icon = null;
    let status = null;

    function show(text) {
        status.textContent = text;
        status.hidden = false;
        setTimeout(function () { status.hidden = true; }, STATUS_MS);
    }

    /** "+3 −1 ~2" of a sync answer, "À jour" when nothing changed. */
    function summary(result) {
        const parts = [];
        if (result.added) parts.push('+' + result.added);
        if (result.removed) parts.push('−' + result.removed);
        if (result.updated) parts.push('~' + result.updated);
        return parts.length ? parts.join(' ') : 'À jour';
    }

    async function sync() {
        button.disabled = true;
        // lucide replaced the <i> placeholder by an <svg> keeping its id
        icon = document.getElementById('library-sync-icon') || icon;
        icon.classList.add('animate-spin');
        try {
            const result = await window.PageKit.fetchJson('/webui/library/sync', 'POST', {});
            button.title = 'Recharger la bibliothèque (' + result.total + ' livres, ' + summary(result) + ')';
            show(summary(result));
            if (result.added || result.removed || result.updated) window.location.reload();
        } catch (e) {
            show(window.PageKit.signedOut(e) ? 'Connexion requise' : 'Échec');
        } finally {
            icon.classList.remove('animate-spin');
            button.disabled = false;
        }
    }

    function init() {
        button = document.getElementById('library-sync');
        icon = document.getElementById('library-sync-icon');
        status = document.getElementById('library-sync-status');
        if (!button || !icon || !status) return;
        if (!window.SyncAccount.sessionUser() && window.PageKit.features().accounts) return;
        button.classList.remove('hidden');
        button.classList.add('flex');
        button.addEventListener('click', sync);
    }

    PyOPDS.librarySync = { init: init };
})();
