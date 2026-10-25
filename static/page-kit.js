// Helpers shared by the static pages (reading.js, clippings.js, loaded with defer before them) and the
// catalog scripts (loaded before static/catalog/): dates, library paths in URLs, JSON calls of the web UI
// endpoints and the lucide icons refresh of the Alpine components.
(function () {
    'use strict';

    const dateFormat = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });

    /** "12 oct. 2026" from unix seconds ('' when missing). */
    function formatDate(seconds) {
        return seconds ? dateFormat.format(new Date(seconds * 1000)) : '';
    }

    /** A library path (relative to LIBRARY_DIR) for an URL, its '/' kept. */
    function encodePath(path) {
        return path.split('/').map(encodeURIComponent).join('/');
    }

    /** Cover, catalog (its folder, else a title search), download and web reader URLs of a library EPUB (path relative to LIBRARY_DIR). */
    function libraryLinks(path, title) {
        const folder = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
        return {
            cover: '/cover/' + encodePath(path),
            catalog: folder ? '/opds/folder/' + encodePath(folder) : '/opds/search?q=' + encodeURIComponent(title || ''),
            download: '/download/' + encodePath(path),
            read: '/read?path=' + encodeURIComponent(path),
        };
    }

    /**
     * JSON answer of a web UI or sync API endpoint (session cookie); `body` is sent as JSON. A failure, or an
     * expired session (401, or the sign-in page answering instead: 200), throws an Error whose `status` is
     * the HTTP status and message the server's one when given.
     */
    async function fetchJson(url, method, body) {
        const response = await fetch(url, {
            method: method || 'GET',
            headers: body ? { 'Accept': 'application/json', 'Content-Type': 'application/json' } : { 'Accept': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
            cache: 'no-store',
            credentials: 'same-origin',
        });
        const isJson = (response.headers.get('Content-Type') || '').indexOf('application/json') === 0;
        if (!response.ok || !isJson) {
            const data = isJson ? await response.json().catch(function () { return null; }) : null;
            const error = new Error((data && data.message) || 'Erreur ' + response.status);
            error.status = response.status;
            throw error;
        }
        return response.json();
    }

    /** Whether a fetchJson failure means the session expired (the page should sign in again). */
    function signedOut(error) {
        return Boolean(error) && (error.status === 401 || error.status === 200);
    }

    /**
     * Enabled parts of the server (menu attributes, controllers/features.py): {minimal} (MINIMAL_MODE: catalog
     * only, no web reader nor reader's data) and {accounts} (signing in offered).
     */
    function features() {
        const nav = document.getElementById('site-nav');
        const data = nav ? nav.dataset : {};
        return { minimal: data.mode === 'minimal', accounts: data.accounts !== 'off' };
    }

    // lucide replaces the <i data-lucide> placeholders by SVGs: run after each render of `component` that may
    // add some (placeholders are never Alpine roots nor bound elements, so the replacement never breaks a binding)
    function icons(component) {
        component.$nextTick(function () { window.lucide.createIcons(); });
    }

    window.PageKit = {
        formatDate: formatDate, encodePath: encodePath, libraryLinks: libraryLinks, fetchJson: fetchJson, signedOut: signedOut,
        icons: icons, features: features,
    };
})();
