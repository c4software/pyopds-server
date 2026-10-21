// Reading statistics page: signs in with the sync account and renders the sync API data.
// Endpoints used: /users/auth, /api/v1/progress, /api/v1/progress/{document},
// /api/v1/stats/summary, /api/v1/stats/activity, /api/v1/clippings.
(function () {
    'use strict';

    const STORAGE_KEY = 'sync-account';
    const DAY_MS = 86400000;
    const MAX_DEVICE_LOOKUPS = 30;
    const CLIPPINGS_SHOWN = 12;

    const $ = (id) => document.getElementById(id);

    // ------------------------------------------------------------------ storage

    function storageGet(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }

    function storageSet(key, value) {
        try {
            if (value === null) localStorage.removeItem(key);
            else localStorage.setItem(key, value);
        } catch (e) { /* private mode: the session simply is not remembered */ }
    }

    function loadAccount() {
        try {
            const account = JSON.parse(storageGet(STORAGE_KEY) || 'null');
            return account && account.user && account.key ? account : null;
        } catch (e) {
            return null;
        }
    }

    // --------------------------------------------------------------------- MD5
    // KOSync sends the MD5 of the password as x-auth-key: the raw password is never stored.

    function md5(text) {
        const bytes = new TextEncoder().encode(text);
        const length = bytes.length;
        const words = new Uint32Array((((length + 8) >> 6) + 1) * 16);
        for (let i = 0; i < length; i++) words[i >> 2] |= bytes[i] << ((i % 4) * 8);
        words[length >> 2] |= 0x80 << ((length % 4) * 8);
        words[words.length - 2] = length * 8;
        words[words.length - 1] = Math.floor(length / 0x20000000);

        const S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
        const K = new Uint32Array(64);
        for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 0x100000000);

        let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
        for (let chunk = 0; chunk < words.length; chunk += 16) {
            let a = a0, b = b0, c = c0, d = d0;
            for (let i = 0; i < 64; i++) {
                let f, g;
                if (i < 16) { f = (b & c) | (~b & d); g = i; }
                else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
                else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
                else { f = c ^ (b | ~d); g = (7 * i) % 16; }
                const shift = S[(i >> 4) * 4 + (i % 4)];
                const sum = (a + f + K[i] + words[chunk + g]) | 0;
                a = d; d = c; c = b;
                b = (b + ((sum << shift) | (sum >>> (32 - shift)))) | 0;
            }
            a0 = (a0 + a) | 0; b0 = (b0 + b) | 0; c0 = (c0 + c) | 0; d0 = (d0 + d) | 0;
        }
        return [a0, b0, c0, d0].map(function (n) {
            let hex = '';
            for (let i = 0; i < 4; i++) hex += ((n >>> (i * 8)) & 0xff).toString(16).padStart(2, '0');
            return hex;
        }).join('');
    }

    // ----------------------------------------------------------------- helpers

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    function encodePath(path) {
        return path.split('/').map(encodeURIComponent).join('/');
    }

    function dayKey(date) {
        return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0');
    }

    function parseDay(key) {
        const [y, m, d] = key.split('-').map(Number);
        return new Date(y, m - 1, d);
    }

    const dateFormat = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
    const dayFormat = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' });
    const monthFormat = new Intl.DateTimeFormat('fr-FR', { month: 'short' });
    const relativeFormat = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' });

    function formatDate(seconds) {
        return seconds ? dateFormat.format(new Date(seconds * 1000)) : '';
    }

    function relativeTime(seconds) {
        if (!seconds) return '';
        const diff = seconds - Date.now() / 1000;
        const units = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];
        for (const [unit, size] of units) {
            if (Math.abs(diff) >= size) return relativeFormat.format(Math.round(diff / size), unit);
        }
        return "à l'instant";
    }

    function formatDuration(seconds) {
        const minutes = Math.round(seconds / 60);
        if (minutes < 60) return minutes + ' min';
        const hours = Math.floor(minutes / 60);
        return hours + ' h' + (minutes % 60 ? ' ' + String(minutes % 60).padStart(2, '0') : '');
    }

    function percent(value) {
        return Math.round((value || 0) * 1000) / 10 + ' %';
    }

    function show(id, visible) {
        $(id).classList.toggle('hidden', !visible);
        if (id === 'stats-view') $(id).classList.toggle('flex', visible);
    }

    // ----------------------------------------------------------------- tooltip

    const tooltip = $('tooltip');

    function bindTooltips(root) {
        root.querySelectorAll('[data-tip]').forEach(function (el) {
            el.addEventListener('mouseenter', function () {
                tooltip.textContent = el.getAttribute('data-tip');
                tooltip.hidden = false;
                const rect = el.getBoundingClientRect();
                const left = Math.min(Math.max(8, rect.left + rect.width / 2 - tooltip.offsetWidth / 2), window.innerWidth - tooltip.offsetWidth - 8);
                tooltip.style.left = left + 'px';
                tooltip.style.top = Math.max(8, rect.top - tooltip.offsetHeight - 6) + 'px';
            });
            el.addEventListener('mouseleave', function () { tooltip.hidden = true; });
        });
    }

    // --------------------------------------------------------------------- API

    let account = loadAccount();

    class Unauthorized extends Error {}

    async function api(path, credentials, method, body) {
        const auth = credentials || account;
        const headers = { 'x-auth-user': auth.user, 'x-auth-key': auth.key, 'Accept': 'application/json' };
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        const response = await fetch(path, {
            method: method || 'GET',
            headers: headers,
            body: body === undefined ? undefined : JSON.stringify(body),
            cache: 'no-store',
        });
        if (response.status === 401) throw new Unauthorized('Identifiants invalides');
        const data = await response.json().catch(function () { return null; });
        if (!response.ok) throw new Error((data && data.message) || 'Erreur ' + response.status);
        return data;
    }

    // ------------------------------------------------------------------- views

    function showLogin(message) {
        account = null;
        show('login-view', true);
        show('stats-view', false);
        show('logout-button', false);
        show('refresh-button', false);
        $('account-name').textContent = '';
        $('login-error').textContent = message || '';
        show('login-error', Boolean(message));
    }

    function showStats() {
        show('login-view', false);
        show('stats-view', true);
        show('logout-button', true);
        show('refresh-button', true);
        $('account-name').textContent = account.user;
        load();
    }

    $('login-form').addEventListener('submit', async function (event) {
        event.preventDefault();
        const user = $('login-user').value.trim().toLowerCase();
        const credentials = { user: user, key: md5($('login-password').value) };
        try {
            await api('/users/auth', credentials);
        } catch (error) {
            showLogin(error instanceof Unauthorized ? 'Utilisateur ou mot de passe incorrect.' : 'Serveur injoignable : ' + error.message);
            return;
        }
        account = credentials;
        storageSet(STORAGE_KEY, JSON.stringify(account));
        $('login-password').value = '';
        showStats();
    });

    $('logout-button').addEventListener('click', function () {
        storageSet(STORAGE_KEY, null);
        showLogin();
    });

    $('refresh-button').addEventListener('click', function () { load(); });

    // -------------------------------------------------------------------- load

    async function load() {
        show('loading', true);
        show('load-error', false);
        const tz = new Date().getTimezoneOffset();
        const results = await Promise.allSettled([
            api('/api/v1/progress?limit=500'),
            api('/api/v1/stats/summary'),
            api('/api/v1/stats/activity?tz=' + tz),
            api('/api/v1/clippings'),
        ]);
        show('loading', false);
        if (results.some(function (r) { return r.status === 'rejected' && r.reason instanceof Unauthorized; })) {
            storageSet(STORAGE_KEY, null);
            showLogin('Session expirée, reconnectez-vous.');
            return;
        }
        if (results[0].status === 'rejected') {
            $('load-error').textContent = 'Impossible de charger la progression : ' + results[0].reason.message;
            show('load-error', true);
            return;
        }
        const value = function (i, fallback) { return results[i].status === 'fulfilled' && results[i].value ? results[i].value : fallback; };
        const items = value(0, {}).items || [];
        const summary = value(1, null);
        const activity = value(2, { books: [], days: [], reading_days: [], pages_total: 0 });
        const clippings = value(3, {}).items || [];

        const devices = await loadDevices(items);
        render(items, summary, activity, clippings, devices);
    }

    // Every device's position for the books in progress (one request per book, capped)
    async function loadDevices(items) {
        const reading = items.filter(isReading).slice(0, MAX_DEVICE_LOOKUPS);
        const lookups = await Promise.allSettled(reading.map(function (item) {
            return api('/api/v1/progress/' + encodeURIComponent(item.document));
        }));
        const byDocument = {};
        lookups.forEach(function (result, i) {
            if (result.status === 'fulfilled') byDocument[reading[i].document] = result.value.devices || [];
        });
        return byDocument;
    }

    function isReading(item) {
        return item.status === 'reading' || item.status === 'paused';
    }

    // ------------------------------------------------------------------ render

    function render(items, summary, activity, clippings, devicesByDocument) {
        const reading = items.filter(isReading);
        const finished = items.filter(function (i) { return i.status === 'finished'; });
        const dnf = items.filter(function (i) { return i.status === 'dnf'; });
        const activityBooks = {};
        (activity.books || []).forEach(function (b) { activityBooks[b.document] = b; });

        const days = buildDays(activity, summary);
        renderKpis(items, reading, finished, summary, activity, days);
        renderHeatmap(days);
        renderDailyBars(days);
        renderDistributions(summary);
        renderDevices(items, summary, devicesByDocument);
        show('activity-section', items.length > 0 || Boolean(summary && summary.seconds));
        // Most recent weeks first in view when the calendar scrolls (narrow screens)
        requestAnimationFrame(function () { $('heatmap').scrollLeft = $('heatmap').scrollWidth; });

        renderBooks('reading', reading, function (item) { return readingCard(item, devicesByDocument[item.document], activityBooks[item.document]); });
        finished.sort(function (a, b) {
            return ((activityBooks[b.document] || {}).finished_at || b.status_at || b.timestamp) - ((activityBooks[a.document] || {}).finished_at || a.status_at || a.timestamp);
        });
        renderBooks('finished', finished, function (item) { return compactCard(item, activityBooks[item.document]); });
        renderBooks('dnf', dnf, function (item) { return compactCard(item, activityBooks[item.document]); });
        renderClippings(clippings, items);
        show('empty', items.length === 0);
        lucide.createIcons();
    }

    // Per-day data in local days: pages (progress history), minutes (reader stats) and reading flag
    function buildDays(activity, summary) {
        const days = {};
        const entry = function (key) { return days[key] || (days[key] = { pages: 0, minutes: 0, syncs: 0, books: 0, read: false }); };
        (activity.days || []).forEach(function (d) {
            const day = entry(d.day);
            day.pages = d.pages || 0;
            day.syncs = d.syncs || 0;
            day.books = (d.books || []).length;
        });
        (activity.reading_days || []).forEach(function (key) { entry(key).read = true; });
        ((summary && summary.daily) || []).forEach(function (d) {
            const day = entry(d.date);
            day.minutes = Math.round(d.minutes || 0);
            if (day.minutes > 0) day.read = true;
        });
        return days;
    }

    function streaks(days) {
        const keys = Object.keys(days).filter(function (k) { return days[k].read; }).sort();
        let longest = 0, run = 0, previous = null;
        keys.forEach(function (key) {
            const date = parseDay(key);
            run = previous && Math.round((date - previous) / DAY_MS) === 1 ? run + 1 : 1;
            longest = Math.max(longest, run);
            previous = date;
        });
        // Current streak: from today, or from yesterday when today is not read yet
        const cursor = new Date();
        cursor.setHours(0, 0, 0, 0);
        if (!days[dayKey(cursor)] || !days[dayKey(cursor)].read) cursor.setDate(cursor.getDate() - 1);
        let current = 0;
        while (days[dayKey(cursor)] && days[dayKey(cursor)].read) {
            current++;
            cursor.setDate(cursor.getDate() - 1);
        }
        return { current: current, longest: longest, total: keys.length };
    }

    function renderKpis(items, reading, finished, summary, activity, days) {
        const streak = streaks(days);
        const seconds = summary ? summary.seconds : 0;
        const pages = Math.max(summary ? summary.pages : 0, activity.pages_total || 0);
        const kpis = [
            { icon: 'clock', label: 'Temps de lecture', value: seconds ? formatDuration(seconds) : '—' },
            { icon: 'file-text', label: 'Pages lues', value: pages ? pages.toLocaleString('fr-FR') : '—' },
            { icon: 'book-open', label: 'En cours', value: reading.length },
            { icon: 'check-circle-2', label: 'Terminés', value: finished.length },
            { icon: 'calendar-check', label: 'Jours de lecture', value: streak.total },
            { icon: 'flame', label: 'Série (record)', value: Math.max(streak.current, summary ? summary.current_streak : 0) + ' j (' + Math.max(streak.longest, summary ? summary.streak : 0) + ')' },
        ];
        $('kpis').innerHTML = items.length || seconds ? kpis.map(function (k) {
            return '<div class="card p-3"><div class="flex items-center gap-1.5 kpi-label"><i data-lucide="' + k.icon + '" class="w-3.5 h-3.5"></i>' + esc(k.label) + '</div>' +
                '<div class="kpi-value mt-1">' + esc(k.value) + '</div></div>';
        }).join('') : '';
    }

    function dayTip(key, day) {
        const parts = [dayFormat.format(parseDay(key))];
        if (!day || !day.read && !day.pages && !day.minutes) return parts[0] + ' : pas de lecture';
        if (day.minutes) parts.push(day.minutes + ' min');
        if (day.pages) parts.push(day.pages + ' p.');
        if (day.books) parts.push(day.books + (day.books > 1 ? ' livres' : ' livre'));
        if (parts.length === 1) parts.push('lecture');
        return parts[0] + ' : ' + parts.slice(1).join(', ');
    }

    // GitHub-style calendar of the last 53 weeks, intensity relative to the busiest day
    function renderHeatmap(days) {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const start = new Date(today);
        start.setDate(start.getDate() - 52 * 7 - ((today.getDay() + 6) % 7));
        const score = function (day) { return day ? day.pages + day.minutes : 0; };
        const max = Math.max(1, ...Object.values(days).map(score));

        let cells = '', months = '', lastMonth = -1;
        for (let date = new Date(start), index = 0; date <= today || date.getDay() !== 1; date.setDate(date.getDate() + 1), index++) {
            const key = dayKey(date);
            const day = days[key];
            if (index % 7 === 0) {
                const label = date.getMonth() !== lastMonth && date.getDate() <= 7 ? monthFormat.format(date) : '';
                if (label) lastMonth = date.getMonth();
                months += '<span>' + esc(label) + '</span>';
            }
            if (date > today) {
                cells += '<span class="heat-cell future"></span>';
                continue;
            }
            const s = score(day);
            const level = s > 0 ? Math.min(4, Math.ceil((s / max) * 4)) : (day && day.read ? 1 : 0);
            cells += '<span class="heat-cell heat-' + level + '" data-tip="' + esc(dayTip(key, day)) + '"></span>';
        }
        const container = $('heatmap');
        container.innerHTML = '<div class="heatmap-months">' + months + '</div><div class="heatmap">' + cells + '</div>';
        bindTooltips(container);
    }

    function renderDailyBars(days) {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const series = [];
        for (let i = 29; i >= 0; i--) {
            const date = new Date(today);
            date.setDate(date.getDate() - i);
            series.push({ key: dayKey(date), day: days[dayKey(date)] });
        }
        // Reading time when the readers send it (CrossPoint), else pages from the progress history
        const useMinutes = series.some(function (s) { return s.day && s.day.minutes; });
        const metric = function (s) { return s.day ? (useMinutes ? s.day.minutes : s.day.pages) : 0; };
        const max = Math.max(1, ...series.map(metric));
        $('daily-title').textContent = (useMinutes ? 'Minutes de lecture' : 'Pages lues') + ' — 30 derniers jours';
        const container = $('daily-bars');
        container.innerHTML = series.map(function (s) {
            return '<div class="bar-slot" data-tip="' + esc(dayTip(s.key, s.day)) + '"><div class="bar" style="height:' + (metric(s) / max * 100) + '%"></div></div>';
        }).join('');
        const axis = container.nextElementSibling && container.nextElementSibling.classList.contains('bars-axis') ? container.nextElementSibling : null;
        const axisHtml = '<div class="bars-axis"><span>' + esc(dayFormat.format(parseDay(series[0].key))) + '</span><span>max ' + Math.round(max) + (useMinutes ? ' min' : ' p.') + '</span><span>Aujourd\'hui</span></div>';
        if (axis) axis.outerHTML = axisHtml;
        else container.insertAdjacentHTML('afterend', axisHtml);
        bindTooltips(container);
    }

    function hbars(labels, values, unit) {
        const max = Math.max(1, ...values);
        return labels.map(function (label, i) {
            return '<div class="hbar"><span>' + esc(label) + '</span><div class="progress-track"><div class="progress-fill" style="width:' + (values[i] / max * 100) + '%"></div></div><span class="value">' + esc(unit(values[i])) + '</span></div>';
        }).join('');
    }

    // Reading sessions by time of day / weekday (sent by CrossPoint readers only)
    function renderDistributions(summary) {
        const tod = summary && summary.tod || [];
        const dow = summary && summary.dow || [];
        const hasTod = tod.some(Boolean), hasDow = dow.some(Boolean);
        const sessions = function (v) { return String(v); };
        if (hasTod) $('tod-bars').innerHTML = hbars(['Nuit', 'Matin', 'Après-midi', 'Soir'], tod, sessions);
        // Buckets assumed in hour order (0-6 h, 6-12 h, 12-18 h, 18-24 h) and weekdays from Sunday (tm_wday)
        if (hasDow) $('dow-bars').innerHTML = hbars(['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'], [1, 2, 3, 4, 5, 6, 0].map(function (i) { return dow[i] || 0; }), sessions);
        show('tod-card', hasTod);
        show('dow-card', hasDow);
    }

    function renderDevices(items, summary, devicesByDocument) {
        const devices = {};
        const touch = function (id, name, at, document) {
            if (!id && !name) return;
            const device = devices[id || name] || (devices[id || name] = { name: name || id, last: 0, books: new Set(), stats: false });
            if (name) device.name = name;
            device.last = Math.max(device.last, at || 0);
            if (document) device.books.add(document);
            return device;
        };
        items.forEach(function (item) { touch(item.device_id, item.device, item.timestamp, item.document); });
        Object.entries(devicesByDocument).forEach(function ([document, list]) {
            list.forEach(function (d) { touch(d.device_id, d.device, d.timestamp, document); });
        });
        ((summary && summary.devices) || []).forEach(function (d) {
            const device = touch(d.device_id, d.device, d.updated_at);
            if (device) device.stats = true;
        });
        const list = Object.values(devices).sort(function (a, b) { return b.last - a.last; });
        $('devices-list').innerHTML = list.map(function (d) {
            const books = d.books.size ? d.books.size + (d.books.size > 1 ? ' livres' : ' livre') : '';
            return '<li class="flex items-start gap-2"><i data-lucide="tablet" class="w-4 h-4 mt-0.5 text-slate-400"></i><div class="min-w-0">' +
                '<div class="truncate">' + esc(d.name) + (d.stats ? ' <span class="badge">stats</span>' : '') + '</div>' +
                '<div class="text-xs text-slate-500 dark:text-slate-400">Dernière synchro ' + esc(relativeTime(d.last)) + (books ? ' · ' + books : '') + '</div></div></li>';
        }).join('');
        show('devices-card', list.length > 0);
    }

    function renderBooks(kind, list, card) {
        $(kind + '-list').innerHTML = list.map(card).join('');
        $(kind + '-count').textContent = list.length ? '(' + list.length + ')' : '';
        show(kind + '-section', list.length > 0);
        list.length && $(kind + '-list').querySelectorAll('img[data-fallback]').forEach(function (img) {
            img.addEventListener('error', function () { img.replaceWith(coverPlaceholder()); lucide.createIcons(); }, { once: true });
        });
    }

    function coverUrl(item) {
        if (item.cover_url) return item.cover_url;
        return item.library_path ? '/cover/' + encodePath(item.library_path) : '';
    }

    function catalogLinks(item) {
        const link = 'inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200';
        // Books the hashes did not match: associate them by hand with a library EPUB
        if (!item.library_path) {
            return '<div class="flex flex-wrap gap-3 mt-2"><button type="button" class="' + link + '" data-link-document="' + esc(item.document) + '" data-link-label="' + esc(item.title || item.filename || item.document) + '">' +
                '<i data-lucide="link" class="w-3.5 h-3.5"></i>Associer à un livre</button></div>';
        }
        const folder = item.library_path.includes('/') ? item.library_path.slice(0, item.library_path.lastIndexOf('/')) : '';
        const catalog = folder ? '/opds/folder/' + encodePath(folder) : '/opds/search?q=' + encodeURIComponent(item.title || item.filename || '');
        const unlink = item.library_linked
            ? '<button type="button" class="' + link + '" data-unlink-document="' + esc(item.document) + '"><i data-lucide="unlink" class="w-3.5 h-3.5"></i>Dissocier</button>'
            : '';
        return '<div class="flex flex-wrap gap-3 mt-2">' +
            '<a class="' + link + '" href="' + esc(catalog) + '"><i data-lucide="library" class="w-3.5 h-3.5"></i>Voir dans le catalogue</a>' +
            '<a class="' + link + '" href="/download/' + esc(encodePath(item.library_path)) + '"><i data-lucide="download" class="w-3.5 h-3.5"></i>Télécharger</a>' + unlink + '</div>';
    }

    function coverPlaceholder() {
        const placeholder = document.createElement('div');
        placeholder.className = 'book-thumb flex items-center justify-center text-slate-400 dark:text-slate-500';
        placeholder.innerHTML = '<i data-lucide="book" class="w-5 h-5"></i>';
        return placeholder;
    }

    function bookHeader(item) {
        const cover = coverUrl(item);
        if (!cover) return '<div class="book-thumb flex items-center justify-center text-slate-400 dark:text-slate-500"><i data-lucide="book" class="w-5 h-5"></i></div>';
        return '<img class="book-thumb" alt="" loading="lazy" data-fallback="1" src="' + esc(cover) + '"/>';
    }

    function bookTitle(item) {
        return '<div class="font-medium text-sm leading-snug">' + esc(item.title || item.filename || item.document) + '</div>' +
            (item.author ? '<div class="text-xs text-slate-500 dark:text-slate-400 truncate">' + esc(item.author) + '</div>' : '');
    }

    function readingCard(item, devices, stats) {
        const pages = item.page && item.pages ? ' · p. ' + item.page + '/' + item.pages : '';
        const paused = item.status === 'paused' ? ' <span class="badge">en pause</span>' : '';
        // Per-device rows when the book is read on several devices
        const rows = (devices && devices.length > 1 ? devices : []).map(function (d) {
            return '<li class="grid grid-cols-[1fr_auto] gap-x-2 items-center"><span class="truncate">' + esc(d.device || d.device_id) + ' · ' + esc(relativeTime(d.timestamp)) + '</span><span>' + esc(percent(d.percentage)) + '</span>' +
                '<div class="progress-track col-span-2" style="height:3px"><div class="progress-fill" style="width:' + (d.percentage || 0) * 100 + '%"></div></div></li>';
        }).join('');
        const started = stats && stats.started_at ? 'Commencé le ' + formatDate(stats.started_at) : '';
        return '<article class="card p-3 flex gap-3">' + bookHeader(item) + '<div class="min-w-0 flex-1">' + bookTitle(item) +
            '<div class="flex items-center gap-2 mt-2"><div class="progress-track flex-1"><div class="progress-fill" style="width:' + (item.percentage || 0) * 100 + '%"></div></div>' +
            '<span class="text-xs font-medium">' + esc(percent(item.percentage)) + '</span></div>' +
            '<div class="text-xs text-slate-500 dark:text-slate-400 mt-1">' + esc((item.device || item.device_id || 'Appareil inconnu') + ' · ' + relativeTime(item.timestamp) + pages) + paused + '</div>' +
            (started ? '<div class="text-xs text-slate-500 dark:text-slate-400">' + esc(started) + '</div>' : '') +
            (rows ? '<ul class="mt-2 space-y-1 text-xs text-slate-500 dark:text-slate-400">' + rows + '</ul>' : '') +
            catalogLinks(item) + '</div></article>';
    }

    function compactCard(item, stats) {
        let detail = '';
        if (item.status === 'finished') {
            const at = (stats && stats.finished_at) || item.status_at || item.timestamp;
            detail = 'Terminé le ' + formatDate(at) + (stats && stats.days_to_finish ? ' · ' + stats.days_to_finish + ' j' : '');
        } else {
            detail = 'Arrêté à ' + percent(item.percentage) + ' · ' + formatDate(item.status_at || item.timestamp);
        }
        return '<article class="card p-3 flex gap-3">' + bookHeader(item) + '<div class="min-w-0 flex-1">' + bookTitle(item) +
            '<div class="text-xs text-slate-500 dark:text-slate-400 mt-1">' + esc(detail) + '</div>' + catalogLinks(item) + '</div></article>';
    }

    function renderClippings(clippings, items) {
        const titles = {};
        items.forEach(function (item) {
            [item.document].concat(item.aliases || []).forEach(function (doc) { titles[doc] = item.title || item.filename || item.document; });
        });
        const latest = clippings.slice().sort(function (a, b) { return (b.created_at || 0) - (a.created_at || 0); }).slice(0, CLIPPINGS_SHOWN);
        $('clippings-list').innerHTML = latest.map(function (c) {
            return '<figure class="card p-3 text-sm"><blockquote class="clip-text">' + esc(c.text) + '</blockquote>' +
                (c.note ? '<p class="mt-2 text-xs italic text-slate-600 dark:text-slate-300">' + esc(c.note) + '</p>' : '') +
                '<figcaption class="mt-2 text-xs text-slate-500 dark:text-slate-400">' + esc([titles[c.document] || c.document, c.chapter, formatDate(c.created_at)].filter(Boolean).join(' · ')) + '</figcaption></figure>';
        }).join('');
        $('clippings-count').textContent = clippings.length ? '(' + clippings.length + ')' : '';
        show('clippings-section', clippings.length > 0);
    }

    // ------------------------------------------------- manual library association

    const linkDialog = $('link-dialog');
    let linkDocument = null;

    // Catalog search through the OPDS feed: each acquisition link gives the EPUB path in the library
    async function searchLibrary(query) {
        const response = await fetch('/opds/search?q=' + encodeURIComponent(query), { cache: 'no-store' });
        if (!response.ok) throw new Error('Erreur ' + response.status);
        const xml = new DOMParser().parseFromString(await response.text(), 'application/xml');
        const ATOM = 'http://www.w3.org/2005/Atom';
        const text = function (parent, name) { const el = parent && parent.getElementsByTagNameNS(ATOM, name)[0]; return el ? el.textContent.trim() : ''; };
        return Array.from(xml.getElementsByTagNameNS(ATOM, 'entry')).map(function (entry) {
            const download = Array.from(entry.getElementsByTagNameNS(ATOM, 'link'))
                .map(function (l) { return l.getAttribute('href') || ''; })
                .find(function (href) { return href.indexOf('/download/') === 0; });
            return {
                title: text(entry, 'title'),
                author: text(entry.getElementsByTagNameNS(ATOM, 'author')[0], 'name'),
                path: download ? decodeURIComponent(download.slice('/download/'.length)) : null,
            };
        }).filter(function (book) { return book.path; });
    }

    async function setLibraryPath(document, path) {
        await api('/api/v1/documents/' + encodeURIComponent(document) + '/info', null, 'PUT', { library_path: path });
        load();
    }

    $('stats-view').addEventListener('click', function (event) {
        const linkButton = event.target.closest('[data-link-document]');
        if (linkButton) {
            linkDocument = linkButton.dataset.linkDocument;
            $('link-document').textContent = linkButton.dataset.linkLabel;
            $('link-results').innerHTML = '';
            $('link-status').textContent = '';
            linkDialog.showModal();
            $('link-query').focus();
            return;
        }
        const unlinkButton = event.target.closest('[data-unlink-document]');
        if (unlinkButton) {
            setLibraryPath(unlinkButton.dataset.unlinkDocument, null).catch(function (error) { console.error(error); });
        }
    });

    $('link-close').addEventListener('click', function () { linkDialog.close(); });

    $('link-search').addEventListener('submit', async function (event) {
        event.preventDefault();
        const query = $('link-query').value.trim();
        if (!query) return;
        $('link-status').textContent = 'Recherche…';
        $('link-results').innerHTML = '';
        try {
            const books = await searchLibrary(query);
            $('link-status').textContent = books.length ? books.length + ' résultat(s)' : 'Aucun livre trouvé.';
            $('link-results').innerHTML = books.map(function (book) {
                return '<li><button type="button" class="link-result" data-path="' + esc(book.path) + '">' +
                    '<span class="block text-sm font-medium">' + esc(book.title) + '</span>' +
                    '<span class="block text-xs text-slate-500 dark:text-slate-400">' + esc([book.author, book.path].filter(Boolean).join(' · ')) + '</span></button></li>';
            }).join('');
        } catch (error) {
            $('link-status').textContent = 'Recherche impossible : ' + error.message;
        }
    });

    $('link-results').addEventListener('click', async function (event) {
        const result = event.target.closest('[data-path]');
        if (!result || !linkDocument) return;
        $('link-status').textContent = 'Association…';
        try {
            await setLibraryPath(linkDocument, result.dataset.path);
            linkDialog.close();
        } catch (error) {
            $('link-status').textContent = 'Association impossible : ' + error.message;
        }
    });

    // ------------------------------------------------------------------- theme

    const darkIcon = $('theme-toggle-dark-icon');
    const lightIcon = $('theme-toggle-light-icon');
    (document.documentElement.classList.contains('dark') ? lightIcon : darkIcon).classList.remove('hidden');
    $('theme-toggle').addEventListener('click', function () {
        const dark = document.documentElement.classList.toggle('dark');
        darkIcon.classList.toggle('hidden', dark);
        lightIcon.classList.toggle('hidden', !dark);
        storageSet('color-theme', dark ? 'dark' : 'light');
    });

    lucide.createIcons();
    if (account) showStats();
    else showLogin();
})();
