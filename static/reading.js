// Reading statistics page: the `reading` Alpine component (templates in reading.html) loads the sync API
// data of the account signed in on /login (web UI session, static/account.js) and turns it into view models.
// It never builds HTML. Endpoints used: /api/v1/progress, /api/v1/progress/{document},
// /api/v1/stats/summary, /api/v1/stats/activity, /api/v1/clippings, PUT /api/v1/documents/{document}/info.
(function () {
    'use strict';

    const DAY_MS = 86400000;
    const MAX_DEVICE_LOOKUPS = 30;
    const CLIPPINGS_SHOWN = 12;
    // Literal class names (custom classes of reading.css)
    const HEAT_CLASSES = ['heat-cell heat-0', 'heat-cell heat-1', 'heat-cell heat-2', 'heat-cell heat-3', 'heat-cell heat-4'];
    const BOOK_SECTIONS = [
        { id: 'reading', icon: 'book-open', title: 'En cours', grid: 'grid gap-3 md:grid-cols-2' },
        { id: 'finished', icon: 'check-circle-2', title: 'Terminés', grid: 'grid gap-3 sm:grid-cols-2 lg:grid-cols-3' },
        { id: 'dnf', icon: 'circle-slash', title: 'Abandonnés', grid: 'grid gap-3 sm:grid-cols-2 lg:grid-cols-3' },
    ];

    // ----------------------------------------------------------------- helpers

    const { formatDate } = window.PageKit;

    function dayKey(date) {
        return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0');
    }

    function parseDay(key) {
        const [y, m, d] = key.split('-').map(Number);
        return new Date(y, m - 1, d);
    }

    function startOfToday() {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        return today;
    }

    const dayFormat = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' });
    const monthFormat = new Intl.DateTimeFormat('fr-FR', { month: 'short' });
    const relativeFormat = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' });

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

    function count(list) {
        return list.length ? '(' + list.length + ')' : '';
    }

    function isReading(item) {
        return item.status === 'reading' || item.status === 'paused';
    }

    // --------------------------------------------------------------------- API

    // Sync API calls with the web UI session (same sign-in as the catalog)
    const api = window.PageKit.fetchJson;

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
            const path = download ? decodeURIComponent(download.slice('/download/'.length)) : null;
            const author = text(entry.getElementsByTagNameNS(ATOM, 'author')[0], 'name');
            return { title: text(entry, 'title'), detail: [author, path].filter(Boolean).join(' · '), path: path };
        }).filter(function (book) { return book.path; });
    }

    // ------------------------------------------------------------ computations

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
        const cursor = startOfToday();
        if (!days[dayKey(cursor)] || !days[dayKey(cursor)].read) cursor.setDate(cursor.getDate() - 1);
        let current = 0;
        while (days[dayKey(cursor)] && days[dayKey(cursor)].read) {
            current++;
            cursor.setDate(cursor.getDate() - 1);
        }
        return { current: current, longest: longest, total: keys.length };
    }

    function kpis(items, reading, finished, summary, activity, days) {
        const seconds = summary ? summary.seconds : 0;
        if (!items.length && !seconds) return [];
        const streak = streaks(days);
        const pages = Math.max(summary ? summary.pages : 0, activity.pages_total || 0);
        return [
            { icon: 'clock', label: 'Temps de lecture', value: seconds ? formatDuration(seconds) : '—' },
            { icon: 'file-text', label: 'Pages lues', value: pages ? pages.toLocaleString('fr-FR') : '—' },
            { icon: 'book-open', label: 'En cours', value: String(reading.length) },
            { icon: 'check-circle-2', label: 'Terminés', value: String(finished.length) },
            { icon: 'calendar-check', label: 'Jours de lecture', value: String(streak.total) },
            { icon: 'flame', label: 'Série (record)', value: Math.max(streak.current, summary ? summary.current_streak : 0) + ' j (' + Math.max(streak.longest, summary ? summary.streak : 0) + ')' },
        ];
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
    function heatmap(days) {
        const today = startOfToday();
        const start = new Date(today);
        start.setDate(start.getDate() - 52 * 7 - ((today.getDay() + 6) % 7));
        const score = function (day) { return day ? day.pages + day.minutes : 0; };
        const max = Math.max(1, ...Object.values(days).map(score));
        const cells = [], months = [];
        let lastMonth = -1;
        for (let date = new Date(start), index = 0; date <= today || date.getDay() !== 1; date.setDate(date.getDate() + 1), index++) {
            const key = dayKey(date);
            const day = days[key];
            if (index % 7 === 0) {
                const label = date.getMonth() !== lastMonth && date.getDate() <= 7 ? monthFormat.format(date) : '';
                if (label) lastMonth = date.getMonth();
                months.push({ key: key, label: label });
            }
            if (date > today) {
                cells.push({ key: key, cls: 'heat-cell future', tip: '' });
                continue;
            }
            const s = score(day);
            const level = s > 0 ? Math.min(4, Math.ceil((s / max) * 4)) : (day && day.read ? 1 : 0);
            cells.push({ key: key, cls: HEAT_CLASSES[level], tip: dayTip(key, day) });
        }
        return { months: months, cells: cells };
    }

    function dailyBars(days) {
        const today = startOfToday();
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
        return {
            title: (useMinutes ? 'Minutes de lecture' : 'Pages lues') + ' — 30 derniers jours',
            bars: series.map(function (s) { return { key: s.key, height: metric(s) / max * 100, tip: dayTip(s.key, s.day) }; }),
            start: dayFormat.format(parseDay(series[0].key)),
            max: 'max ' + Math.round(max) + (useMinutes ? ' min' : ' p.'),
        };
    }

    function hbars(labels, values) {
        const max = Math.max(1, ...values);
        return labels.map(function (label, i) { return { label: label, width: values[i] / max * 100, value: String(values[i]) }; });
    }

    function devices(items, summary, devicesByDocument) {
        const list = {};
        const touch = function (id, name, at, document) {
            if (!id && !name) return;
            const device = list[id || name] || (list[id || name] = { key: id || name, name: name || id, last: 0, books: new Set(), stats: false });
            if (name) device.name = name;
            device.last = Math.max(device.last, at || 0);
            if (document) device.books.add(document);
            return device;
        };
        items.forEach(function (item) { touch(item.device_id, item.device, item.timestamp, item.document); });
        Object.entries(devicesByDocument).forEach(function ([document, rows]) {
            rows.forEach(function (d) { touch(d.device_id, d.device, d.timestamp, document); });
        });
        ((summary && summary.devices) || []).forEach(function (d) {
            const device = touch(d.device_id, d.device, d.updated_at);
            if (device) device.stats = true;
        });
        return Object.values(list).sort(function (a, b) { return b.last - a.last; }).map(function (d) {
            const books = d.books.size ? d.books.size + (d.books.size > 1 ? ' livres' : ' livre') : '';
            return { key: d.key, name: d.name, stats: d.stats, detail: 'Dernière synchro ' + relativeTime(d.last) + (books ? ' · ' + books : '') };
        });
    }

    // Common part of the book cards: cover, title and the catalog / association links
    function bookView(item) {
        const title = item.title || item.filename || item.document;
        const links = item.library_path ? window.PageKit.libraryLinks(item.library_path, title) : null;
        const view = {
            key: item.document,
            title: title,
            author: item.author || '',
            cover: item.cover_url || (links ? links.cover : ''),
            broken: false,
            linkable: !item.library_path,
            document: item.document,
        };
        if (links) {
            view.catalog = links.catalog;
            view.download = links.download;
            view.read = links.read;
            view.unlink = Boolean(item.library_linked);
        }
        return view;
    }

    function readingCard(item, deviceRows, stats) {
        const view = bookView(item);
        const pages = item.page && item.pages ? ' · p. ' + item.page + '/' + item.pages : '';
        view.reading = true;
        view.progress = (item.percentage || 0) * 100;
        view.percent = percent(item.percentage);
        view.meta = (item.device || item.device_id || 'Appareil inconnu') + ' · ' + relativeTime(item.timestamp) + pages;
        view.paused = item.status === 'paused';
        view.started = stats && stats.started_at ? 'Commencé le ' + formatDate(stats.started_at) : '';
        // Per-device rows when the book is read on several devices
        view.devices = (deviceRows && deviceRows.length > 1 ? deviceRows : []).map(function (d, i) {
            return { key: (d.device_id || d.device || '') + i, label: (d.device || d.device_id) + ' · ' + relativeTime(d.timestamp), percent: percent(d.percentage), progress: (d.percentage || 0) * 100 };
        });
        return view;
    }

    function compactCard(item, stats) {
        const view = bookView(item);
        if (item.status === 'finished') {
            const at = (stats && stats.finished_at) || item.status_at || item.timestamp;
            view.detail = 'Terminé le ' + formatDate(at) + (stats && stats.days_to_finish ? ' · ' + stats.days_to_finish + ' j' : '');
        } else {
            view.detail = 'Arrêté à ' + percent(item.percentage) + ' · ' + formatDate(item.status_at || item.timestamp);
        }
        return view;
    }

    function clippingViews(clippings, items) {
        const titles = {};
        items.forEach(function (item) {
            [item.document].concat(item.aliases || []).forEach(function (doc) { titles[doc] = item.title || item.filename || item.document; });
        });
        return clippings.slice().sort(function (a, b) { return (b.created_at || 0) - (a.created_at || 0); }).slice(0, CLIPPINGS_SHOWN).map(function (c, i) {
            return {
                key: c.document + ':' + (c.id || i),
                text: c.text,
                note: c.note || '',
                caption: [titles[c.document] || c.document, c.chapter, formatDate(c.created_at)].filter(Boolean).join(' · '),
            };
        });
    }

    // --------------------------------------------------------------- component

    document.addEventListener('alpine:init', function () {
        window.Alpine.data('reading', function () {
            return {
                ...window.SyncAccount.pageSession('/reading'),
                loading: false,
                error: '',
                empty: false,
                kpis: [],
                showActivity: false,
                heatmap: { months: [], cells: [] },
                daily: { title: '30 derniers jours', bars: [], start: '', max: '' },
                tod: [],
                dow: [],
                devices: [],
                sections: [],
                clippings: [],
                clippingsCount: '',
                tooltip: { text: '', show: false, left: 0, top: 0 },
                link: { document: null, label: '', query: '', status: '', results: [] },

                init() {
                    if (!this.startSession()) return;
                    this.icons();
                    this.load();
                },

                icons() {
                    window.PageKit.icons(this);
                },

                async load() {
                    this.loading = true;
                    this.error = '';
                    const call = api;
                    const tz = new Date().getTimezoneOffset();
                    const results = await Promise.allSettled([
                        call('/api/v1/progress?limit=500'),
                        call('/api/v1/stats/summary'),
                        call('/api/v1/stats/activity?tz=' + tz),
                        call('/api/v1/clippings'),
                    ]);
                    this.loading = false;
                    if (results.some(function (r) { return r.status === 'rejected' && window.PageKit.signedOut(r.reason); })) {
                        this.signIn();
                        return;
                    }
                    if (results[0].status === 'rejected') {
                        this.error = 'Impossible de charger la progression : ' + results[0].reason.message;
                        return;
                    }
                    const value = function (i, fallback) { return results[i].status === 'fulfilled' && results[i].value ? results[i].value : fallback; };
                    const items = value(0, {}).items || [];
                    const summary = value(1, null);
                    const activity = value(2, { books: [], days: [], reading_days: [], pages_total: 0 });
                    const clippings = value(3, {}).items || [];

                    // Every device's position for the books in progress (one request per book, capped)
                    const reading = items.filter(isReading);
                    const lookups = await Promise.allSettled(reading.slice(0, MAX_DEVICE_LOOKUPS).map(function (item) {
                        return call('/api/v1/progress/' + encodeURIComponent(item.document));
                    }));
                    const devicesByDocument = {};
                    lookups.forEach(function (result, i) {
                        if (result.status === 'fulfilled') devicesByDocument[reading[i].document] = result.value.devices || [];
                    });
                    this.render(items, summary, activity, clippings, devicesByDocument);
                },

                render(items, summary, activity, clippings, devicesByDocument) {
                    const reading = items.filter(isReading);
                    const finished = items.filter(function (i) { return i.status === 'finished'; });
                    const dnf = items.filter(function (i) { return i.status === 'dnf'; });
                    const activityBooks = {};
                    (activity.books || []).forEach(function (b) { activityBooks[b.document] = b; });
                    finished.sort(function (a, b) {
                        return ((activityBooks[b.document] || {}).finished_at || b.status_at || b.timestamp) - ((activityBooks[a.document] || {}).finished_at || a.status_at || a.timestamp);
                    });

                    const days = buildDays(activity, summary);
                    this.kpis = kpis(items, reading, finished, summary, activity, days);
                    this.heatmap = heatmap(days);
                    this.daily = dailyBars(days);
                    // Reading sessions by time of day / weekday (sent by CrossPoint readers only). Buckets assumed in
                    // hour order (0-6 h, 6-12 h, 12-18 h, 18-24 h) and weekdays from Sunday (tm_wday)
                    const tod = (summary && summary.tod) || [];
                    const dow = (summary && summary.dow) || [];
                    this.tod = tod.some(Boolean) ? hbars(['Nuit', 'Matin', 'Après-midi', 'Soir'], tod) : [];
                    this.dow = dow.some(Boolean) ? hbars(['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'], [1, 2, 3, 4, 5, 6, 0].map(function (i) { return dow[i] || 0; })) : [];
                    this.devices = devices(items, summary, devicesByDocument);
                    this.showActivity = items.length > 0 || Boolean(summary && summary.seconds);

                    const lists = {
                        reading: reading.map(function (item) { return readingCard(item, devicesByDocument[item.document], activityBooks[item.document]); }),
                        finished: finished.map(function (item) { return compactCard(item, activityBooks[item.document]); }),
                        dnf: dnf.map(function (item) { return compactCard(item, activityBooks[item.document]); }),
                    };
                    this.sections = BOOK_SECTIONS.map(function (section) {
                        return Object.assign({}, section, { books: lists[section.id], count: count(lists[section.id]) });
                    });
                    this.clippings = clippingViews(clippings, items);
                    this.clippingsCount = count(clippings);
                    this.empty = items.length === 0;
                    this.icons();
                    // Most recent weeks first in view when the calendar scrolls (narrow screens)
                    const calendar = this.$refs.heatmap;
                    this.$nextTick(function () { requestAnimationFrame(function () { calendar.scrollLeft = calendar.scrollWidth; }); });
                },

                coverError(book) {
                    book.broken = true;
                    this.icons();
                },

                async showTip(event, text) {
                    const el = event.currentTarget;
                    this.tooltip.text = text;
                    this.tooltip.show = true;
                    await this.$nextTick();
                    const tip = this.$refs.tooltip;
                    const rect = el.getBoundingClientRect();
                    this.tooltip.left = Math.min(Math.max(8, rect.left + rect.width / 2 - tip.offsetWidth / 2), window.innerWidth - tip.offsetWidth - 8);
                    this.tooltip.top = Math.max(8, rect.top - tip.offsetHeight - 6);
                },

                hideTip() {
                    this.tooltip.show = false;
                },

                // ------------------------------------------- manual library association

                openLink(book) {
                    this.link = { document: book.document, label: book.title, query: this.link.query, status: '', results: [] };
                    this.$refs.linkDialog.showModal();
                    this.$refs.linkQuery.focus();
                },

                async search() {
                    const query = this.link.query.trim();
                    if (!query) return;
                    this.link.status = 'Recherche…';
                    this.link.results = [];
                    try {
                        const books = await searchLibrary(query);
                        this.link.status = books.length ? books.length + ' résultat(s)' : 'Aucun livre trouvé.';
                        this.link.results = books;
                    } catch (error) {
                        this.link.status = 'Recherche impossible : ' + error.message;
                    }
                },

                async associate(path) {
                    if (!this.link.document) return;
                    this.link.status = 'Association…';
                    try {
                        await this.setLibraryPath(this.link.document, path);
                        this.$refs.linkDialog.close();
                    } catch (error) {
                        this.link.status = 'Association impossible : ' + error.message;
                    }
                },

                unlink(book) {
                    this.setLibraryPath(book.document, null).catch(function (error) { console.error(error); });
                },

                async setLibraryPath(document, path) {
                    await api('/api/v1/documents/' + encodeURIComponent(document) + '/info', 'PUT', { library_path: path });
                    this.load();
                },
            };
        });
    });
})();
