// Helpers shared by the static pages (reading.js, clippings.js, loaded with defer before them) and the
// catalog scripts (loaded before static/catalog/): dates (absolute and relative), library paths in URLs, JSON calls of the web UI
// endpoints and the lucide icons refresh of the Alpine components.
(function () {
    'use strict';

    const dateFormat = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });

    /** "12 oct. 2026" from unix seconds ('' when missing). */
    function formatDate(seconds) {
        return seconds ? dateFormat.format(new Date(seconds * 1000)) : '';
    }

    const relativeFormat = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' });
    const RELATIVE_UNITS = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];

    /** "il y a 3 jours" from unix seconds ('' when missing). */
    function relativeTime(seconds) {
        if (!seconds) return '';
        const diff = seconds - Date.now() / 1000;
        for (const [unit, size] of RELATIVE_UNITS) {
            if (Math.abs(diff) >= size) return relativeFormat.format(Math.round(diff / size), unit);
        }
        return "à l'instant";
    }

    /** Caption of a clipping (static/partials/clipping.html, catalog book sheet): book when given, chapter, page, date. */
    function clippingCaption(clip, book) {
        const page = clip.page ? 'p. ' + clip.page : '';
        return [book, clip.chapter, page, formatDate(clip.created_at)].filter(Boolean).join(' · ');
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
     * JSON answer of a web UI or sync API endpoint (session cookie); `body` is sent as JSON, `options.keepalive`
     * lets the request leave while the page closes. No content (204) gives null. A failure, or an expired
     * session (401, or the sign-in page answering instead: 200), throws an Error whose `status` is the HTTP
     * status, `data` the JSON error body and message the server's one when given.
     */
    async function fetchJson(url, method, body, options) {
        const response = await fetch(url, {
            method: method || 'GET',
            headers: body ? { 'Accept': 'application/json', 'Content-Type': 'application/json' } : { 'Accept': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
            cache: 'no-store',
            credentials: 'same-origin',
            keepalive: Boolean(options && options.keepalive),
        });
        if (response.status === 204) return null;
        const isJson = (response.headers.get('Content-Type') || '').indexOf('application/json') === 0;
        if (!response.ok || !isJson) {
            const data = isJson ? await response.json().catch(function () { return null; }) : null;
            const error = new Error((data && data.message) || 'Erreur ' + response.status);
            error.status = response.status;
            error.data = data;
            throw error;
        }
        return response.json();
    }

    /** Whether a fetchJson failure means the session expired (the page should sign in again). */
    function signedOut(error) {
        return Boolean(error) && (error.status === 401 || error.status === 200);
    }

    /**
     * Enabled parts of the server (menu attributes, controllers/core/features.py): {minimal} (MINIMAL_MODE: catalog
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
        formatDate: formatDate, relativeTime: relativeTime, clippingCaption: clippingCaption, encodePath: encodePath, libraryLinks: libraryLinks, fetchJson: fetchJson, signedOut: signedOut,
        icons: icons, features: features,
    };
})();
