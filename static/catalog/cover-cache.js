// Catalog web UI: browser cache of the cover mosaic (static/catalog/mosaic.js). Small JPEG
// thumbnails in IndexedDB (the full EPUB covers are heavy for a mosaic cell), books without a
// cover, and the covers last shown per tile (localStorage) to show them again at once.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    const DB_NAME = 'pyopds-mosaic';
    const STORE = 'thumbs';
    const MAX_ENTRIES = 800;
    const TTL = 7 * 86400 * 1000;
    const THUMB_WIDTH = 200;
    const SEEN_KEY = 'tile-mosaic-seen';
    let dbPromise = null;

    function openDb() {
        if (!dbPromise) {
            dbPromise = new Promise(function (resolve) {
                try {
                    const request = indexedDB.open(DB_NAME, 1);
                    request.onupgradeneeded = function () {
                        request.result.createObjectStore(STORE).createIndex('at', 'at');
                    };
                    request.onsuccess = function () { resolve(request.result); };
                    request.onerror = function () { resolve(null); };
                } catch (e) {
                    resolve(null);
                }
            });
        }
        return dbPromise;
    }

    function run(mode, fn) {
        return openDb().then(function (db) {
            if (!db) return null;
            return new Promise(function (resolve) {
                try {
                    const tx = db.transaction(STORE, mode);
                    const request = fn(tx.objectStore(STORE));
                    tx.oncomplete = function () { resolve(request ? request.result : null); };
                    tx.onerror = tx.onabort = function () { resolve(null); };
                } catch (e) {
                    resolve(null);
                }
            });
        });
    }

    // Drop the oldest thumbnails above the size limit
    function prune() {
        run('readwrite', function (store) {
            const count = store.count();
            count.onsuccess = function () {
                let excess = count.result - MAX_ENTRIES;
                if (excess <= 0) return;
                store.index('at').openCursor().onsuccess = function (event) {
                    const cursor = event.target.result;
                    if (!cursor || excess-- <= 0) return;
                    cursor.delete();
                    cursor.continue();
                };
            };
            return null;
        });
    }

    /** Cached entry of a cover URL: {blob} or {missing: true}, null when unknown or expired. */
    async function get(url) {
        const entry = await run('readonly', function (store) { return store.get(url); });
        return entry && Date.now() - entry.at < TTL ? entry : null;
    }

    /** Store the entry of a cover URL (pruning the oldest ones). */
    function put(url, entry) {
        entry.at = Date.now();
        run('readwrite', function (store) { return store.put(entry, url); }).then(prune);
    }

    /** Thumbnail of a loaded cover, as a JPEG blob (null if the browser cannot make one). */
    function thumbnail(img) {
        return new Promise(function (resolve) {
            try {
                const scale = Math.min(1, THUMB_WIDTH / img.naturalWidth);
                const canvas = document.createElement('canvas');
                canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
                canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
                canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
                canvas.toBlob(resolve, 'image/jpeg', 0.8);
            } catch (e) {
                resolve(null);
            }
        });
    }

    function loadSeen() {
        try {
            return JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') || {};
        } catch (e) {
            return {};
        }
    }

    /** Covers last shown by a tile, to show them again right away on the next visit. */
    function seen(tile) {
        return loadSeen()[tile] || [];
    }

    /** Remember the covers shown by a tile (the 200 most recent tiles are kept). */
    function remember(tile, covers) {
        const all = loadSeen();
        delete all[tile];
        all[tile] = covers.slice(0, 8);
        const tiles = Object.keys(all);
        tiles.slice(0, Math.max(0, tiles.length - 200)).forEach(function (key) { delete all[key]; });
        try {
            localStorage.setItem(SEEN_KEY, JSON.stringify(all));
        } catch (e) {
            // Storage full or unavailable: covers are only remembered for this page
        }
    }

    PyOPDS.coverCache = { get: get, put: put, thumbnail: thumbnail, seen: seen, remember: remember };
})();
