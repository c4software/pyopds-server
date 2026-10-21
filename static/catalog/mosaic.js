// Catalog web UI: animated cover mosaic in the background of the navigation tiles
// (.tile-mosaic[data-covers], filled by the feed for browsers), one cell changing every few
// seconds. Chosen per browser with the header button (localStorage 'tile-mosaic', off by default)
// and applied without reloading. Covers come through PyOPDS.coverCache (static/catalog/cover-cache.js).
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    const STORAGE_KEY = 'tile-mosaic';
    const CELLS = 4;
    let session = null; // Running mosaic: its observer, timers and cover requests
    let enabled = (function () {
        try {
            return localStorage.getItem(STORAGE_KEY) === 'on';
        } catch (e) {
            return false;
        }
    })();

    function saveEnabled(value) {
        enabled = value;
        try {
            localStorage.setItem(STORAGE_KEY, value ? 'on' : 'off');
        } catch (e) {
            // Storage unavailable: the choice only lasts for this page
        }
    }

    function updateToggle(value) {
        const button = document.getElementById('mosaic-toggle');
        if (!button) return;
        button.setAttribute('aria-pressed', value ? 'true' : 'false');
        button.title = value ? 'Désactiver la mosaïque de couvertures' : 'Activer la mosaïque de couvertures';
        document.getElementById('mosaic-toggle-on-icon').classList.toggle('hidden', !value);
        document.getElementById('mosaic-toggle-off-icon').classList.toggle('hidden', value);
    }

    /** Stop the mosaic: no more timers nor cover requests, tiles back to their plain look. */
    function stop() {
        document.documentElement.classList.remove('mosaic-enabled');
        if (!session) return;
        const current = session;
        session = null;
        current.active = false;
        current.observer.disconnect();
        current.timers.forEach(clearTimeout);
        current.timers.clear();
        current.images.forEach(function (img) {
            img.onload = img.onerror = null;
            img.src = '';
        });
        current.images.clear();
        document.querySelectorAll('.tile-mosaic').forEach(function (mosaic) {
            mosaic.replaceChildren();
            mosaic.classList.remove('single');
            mosaic.parentElement.classList.remove('has-mosaic');
        });
        current.thumbs.forEach(function (url) { URL.revokeObjectURL(url); });
        current.thumbs.clear();
    }

    /** Fill the navigation tiles with a mosaic of covers (when enabled and not already running). */
    function start() {
        if (session || !enabled) return;
        document.documentElement.classList.add('mosaic-enabled');
        const coverCache = PyOPDS.coverCache;
        const current = { observer: null, timers: new Set(), images: new Set(), thumbs: new Map(), active: true };
        session = current;
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        function schedule(fn, delay) {
            if (!current.active) return;
            const id = setTimeout(function () {
                current.timers.delete(id);
                fn();
            }, delay);
            current.timers.add(id);
        }

        function useThumbnail(src, blob) {
            if (current.active && blob && !current.thumbs.has(src)) current.thumbs.set(src, URL.createObjectURL(blob));
        }

        // Resolve with the cover URL once displayable (from the browser cache when known),
        // or null when the EPUB has no cover (or the mosaic was stopped)
        async function loadCover(src) {
            if (!current.active) return null;
            const cached = await coverCache.get(src);
            if (cached) {
                useThumbnail(src, cached.blob);
                return current.active && !cached.missing ? src : null;
            }
            const img = await new Promise(function (resolve) {
                const image = new Image();
                current.images.add(image);
                image.onload = function () { current.images.delete(image); resolve(image); };
                image.onerror = function () { current.images.delete(image); resolve(null); };
                image.src = src;
            });
            if (!current.active) return null;
            if (!img) {
                coverCache.put(src, { missing: true });
                return null;
            }
            const blob = await coverCache.thumbnail(img);
            if (blob) {
                coverCache.put(src, { blob: blob });
                useThumbnail(src, blob);
            }
            return current.active ? src : null;
        }

        // Next cover of the pool that exists
        async function nextCover(state) {
            while (current.active && state.pending.length) {
                const src = await loadCover(state.pending.shift());
                if (src) {
                    state.covers.push(src);
                    return src;
                }
            }
            return null;
        }

        // Fade a cover in above the previous one, then drop the previous one
        function showCover(cell, src, instant) {
            const img = document.createElement('img');
            img.className = instant ? 'tile-mosaic-cover instant' : 'tile-mosaic-cover';
            img.alt = '';
            img.src = current.thumbs.get(src) || src;
            cell.appendChild(img);
            void img.offsetWidth; // Start the transition from opacity 0
            img.classList.add('loaded');
            img.addEventListener('transitionend', function () {
                while (cell.firstChild !== img) cell.firstChild.remove();
            }, { once: true });
            return img;
        }

        async function startMosaic(mosaic, state) {
            const first = (await Promise.all(state.pending.splice(0, CELLS).map(loadCover))).filter(Boolean);
            state.covers.push(...first);
            while (first.length < CELLS) {
                const src = await nextCover(state);
                if (!src) break;
                first.push(src);
            }
            if (!current.active || !first.length) return;

            // Too few covers for a grid: a single cover filling the tile
            const shown = first.length < CELLS ? first.slice(0, 1) : first;
            mosaic.classList.toggle('single', shown.length === 1);
            const images = [];
            const cells = shown.map(function (src) {
                const cell = document.createElement('div');
                cell.className = 'tile-mosaic-cell';
                mosaic.appendChild(cell);
                images.push(showCover(cell, src, true));
                return cell;
            });
            // Fade the tile in once its covers are decoded, not cell by cell as they paint
            await Promise.all(images.map(function (img) { return img.decode().catch(function () {}); }));
            if (!current.active) return;
            mosaic.parentElement.classList.add('has-mosaic');
            coverCache.remember(state.tile, shown);
            if (reduceMotion) return;

            let lastCell = -1;
            async function rotate() {
                if (!current.active) return;
                if (!document.hidden && state.visible) {
                    // Discover the rest of the pool first, then reuse the known covers
                    let src = await nextCover(state);
                    if (!current.active) return;
                    if (!src) {
                        const hidden = state.covers.filter(function (cover) { return !shown.includes(cover); });
                        src = hidden.length ? hidden[Math.floor(Math.random() * hidden.length)] : null;
                    }
                    if (!src) return; // Every cover is already on screen
                    let index = Math.floor(Math.random() * cells.length);
                    if (index === lastCell && cells.length > 1) index = (index + 1) % cells.length;
                    lastCell = index;
                    shown[index] = src;
                    showCover(cells[index], src);
                    coverCache.remember(state.tile, shown);
                }
                schedule(rotate, 5000 + Math.random() * 4000);
            }
            schedule(rotate, 3000 + Math.random() * 5000);
        }

        // Load covers only once a tile is near the viewport, and animate only visible tiles
        const states = new Map();
        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                let state = states.get(entry.target);
                if (!state) {
                    if (!entry.isIntersecting) return;
                    // Covers shown on the last visit first (instant, from the cache), then the feed's sample
                    const link = entry.target.closest('a');
                    const tile = link ? link.getAttribute('href') : '';
                    const pending = coverCache.seen(tile).concat(entry.target.getAttribute('data-covers').split(' ').filter(Boolean));
                    state = { tile: tile, pending: pending.filter(function (src, i) { return pending.indexOf(src) === i; }), covers: [], visible: true };
                    states.set(entry.target, state);
                    startMosaic(entry.target, state);
                }
                state.visible = entry.isIntersecting;
            });
        }, { rootMargin: '50px' });
        current.observer = observer;

        document.querySelectorAll('.tile-mosaic[data-covers]').forEach(function (mosaic) {
            observer.observe(mosaic);
        });
    }

    /** Header button switching the mosaic on and off, applied without reloading. */
    function initToggle() {
        updateToggle(enabled);
        const button = document.getElementById('mosaic-toggle');
        if (!button) return;
        button.addEventListener('click', function () {
            saveEnabled(!enabled);
            updateToggle(enabled);
            if (enabled) {
                start();
            } else {
                stop();
            }
        });
    }

    PyOPDS.mosaic = { initToggle: initToggle, start: start, stop: stop };
})();
