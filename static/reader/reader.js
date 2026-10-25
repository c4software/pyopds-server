// Web reader page (/read?path=<EPUB>): the `reader` Alpine component (templates in reader.html) around a
// foliate-js view. Opens the book where the reader stopped on any device (sync.js), turns the pages
// (taps on the sides, swipes, keys, wheel, arrows), shows the table of contents, the clippings
// (highlights.js) and the display settings (settings.js), and counts the reading time (stats.js).
// The foliate objects live outside Alpine's reactive data: its proxies would break their private fields.
(function () {
    'use strict';

    const PyReader = window.PyReader;

    const PUSH_DELAY_MS = 3000;
    const OPEN_SETTLE_MS = 400;
    // The end of the book counts as finished when reached by turning pages from this point
    const NEAR_END = 0.9;
    const NOTICE_MS = 8000;
    const WHEEL_GAP_MS = 350;
    // Side areas of a tap that turn the page (fraction of the width); the middle shows the bars
    const TAP_SIDE = 0.3;
    // Top and bottom bands (fraction of the height): a tap there always shows / hides the bars, even on a link
    const TAP_BARS = 0.15;
    // A touch counts as a tap below this move (px) and duration (ms); the click that may follow is then ignored
    const TAP_MOVE = 12;
    const TAP_MS = 600;
    const CLICK_AFTER_TAP_MS = 800;

    const percentFormat = new Intl.NumberFormat('fr-FR', { style: 'percent', maximumFractionDigits: 0 });
    const relativeFormat = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' });

    function relativeTime(seconds) {
        const diff = seconds - Date.now() / 1000;
        const units = [['day', 86400], ['hour', 3600], ['minute', 60]];
        for (const [unit, size] of units) {
            if (Math.abs(diff) >= size) return relativeFormat.format(Math.round(diff / size), unit);
        }
        return "à l'instant";
    }

    /**
     * Taps on a touch screen, seen from touchstart / touchend: foliate's paginator cancels every touchmove
     * (page swipes), so a real finger, which always moves a little, never gets a click.
     */
    function onTouchTap(target, handler) {
        let start = null;
        target.addEventListener('touchstart', function (event) {
            const touch = event.touches.length === 1 ? event.touches[0] : null;
            start = touch ? { x: touch.clientX, y: touch.clientY, at: Date.now() } : null;
        }, { passive: true });
        target.addEventListener('touchend', function (event) {
            const touch = event.changedTouches[0];
            const from = start;
            start = null;
            if (!from || !touch || Date.now() - from.at > TAP_MS) return;
            if (Math.hypot(touch.clientX - from.x, touch.clientY - from.y) > TAP_MOVE) return;
            handler(event, touch);
        }, { passive: true });
    }

    // Phones and tablets: the bars hide when a page is turned by a tap
    const touchScreen = window.matchMedia('(pointer: coarse)').matches;

    function editable(target) {
        return target && target.closest && target.closest('input, textarea, select, [contenteditable="true"]');
    }

    document.addEventListener('alpine:init', function () {
        window.Alpine.data('reader', function () {
            // Non-reactive state (foliate view, book, trackers)
            let reader = null;
            let highlights = null;
            let tracker = null;
            let awake = null;
            let fullscreenAsked = false;
            let current = null;
            let selection = null;
            let pushTimer = null;
            let noticeTimer = null;
            let lastPushed = '';
            let lastPushAt = 0;
            let jumping = false;
            let lastFraction = null;
            let lastScroll = null;
            let lastWheel = 0;
            let lastTouchTap = 0;
            let syntheticClick = false;
            // Until the opening position is laid out, foliate may report transient places (even the end of the book)
            let opened = false;

            return {
                path: new URLSearchParams(location.search).get('path') || '',
                signedIn: Boolean(window.SyncAccount.sessionUser()),
                title: '',
                author: '',
                loading: true,
                error: '',
                downloadUrl: '',
                catalogUrl: '/opds',
                // Bars shown: always at first on a computer, on a tap in the middle of the page on a touch screen
                chrome: true,
                panel: null,
                toc: [],
                tocCurrent: '',
                progress: { value: 0, percent: '', chapter: '', page: '' },
                settings: PyReader.settings.load(),
                clips: [],
                selecting: false,
                editor: null,
                notice: null,
                noticeTarget: null,
                // Phones and tablets only: on a computer the book keeps a reading width (reader.css)
                fullscreenAvailable: Boolean(document.fullscreenEnabled && document.documentElement.requestFullscreen) && touchScreen,
                fullscreen: false,

                async init() {
                    window.PageKit.icons(this);
                    if (!this.path) {
                        this.fail('Aucun livre indiqué.');
                        return;
                    }
                    const links = window.PageKit.libraryLinks(this.path, '');
                    this.downloadUrl = links.download;
                    this.catalogUrl = links.catalog;
                    try {
                        reader = await PyReader.book.open(this.path, this.$refs.book);
                    } catch (e) {
                        this.fail(e && e.status === 404 ? 'Ce livre n’est plus dans la bibliothèque.' : 'Impossible d’ouvrir ce livre.');
                        return;
                    }
                    const info = reader.info;
                    this.title = info.title;
                    this.author = info.author;
                    this.catalogUrl = window.PageKit.libraryLinks(this.path, info.title).catalog;
                    document.title = info.title;
                    this.toc = PyReader.book.tableOfContents(reader.book);
                    PyReader.settings.apply(reader.view, this.settings);
                    this.wire();
                    tracker = PyReader.stats.track(this.path, this.signedIn);
                    awake = PyReader.wake.keep();
                    if (this.signedIn && info.document) {
                        highlights = PyReader.highlights.create(reader, info.document, () => this.refreshClips());
                        highlights.load().catch(() => this.showNotice('Surlignages indisponibles pour le moment.'));
                    }
                    await this.openAtLastPosition();
                    this.loading = false;
                    // Phones and tablets read full screen: the bars come back with a tap
                    if (touchScreen) this.chrome = false;
                    window.PageKit.icons(this);
                },

                fail(message) {
                    this.error = message;
                    this.loading = false;
                    window.PageKit.icons(this);
                },

                /** Listeners of the view, of the chapters and of the page. */
                wire() {
                    const view = reader.view;
                    view.addEventListener('relocate', (event) => this.relocated(event.detail));
                    view.addEventListener('load', (event) => this.chapterLoaded(event.detail.doc, event.detail.index));
                    view.addEventListener('show-annotation', (event) => {
                        const clip = highlights && highlights.atCfi(event.detail.value);
                        if (clip) this.edit(clip.id);
                    });
                    document.addEventListener('keydown', (event) => this.key(event));
                    this.$refs.book.addEventListener('wheel', (event) => this.wheel(event), { passive: true });
                    this.$refs.book.addEventListener('click', (event) => this.tapOutside(event));
                    onTouchTap(this.$refs.book, (event, touch) => {
                        // Inside a chapter: handled by its own listeners (chapterLoaded)
                        if (event.target === this.$refs.book || this.$refs.book.contains(event.target)) {
                            lastTouchTap = Date.now();
                            this.tapAt(touch.clientX, touch.clientY);
                        }
                    });
                    let resizeTimer = null;
                    window.addEventListener('resize', () => {
                        clearTimeout(resizeTimer);
                        resizeTimer = setTimeout(() => PyReader.settings.apply(reader.view, this.settings), 200);
                    });
                    // Theme of the site changed by the menu: the "auto" book theme follows it
                    new MutationObserver(() => PyReader.settings.apply(reader.view, this.settings))
                        .observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
                    document.addEventListener('visibilitychange', () => {
                        if (document.visibilityState === 'hidden') this.pushNow(true);
                        else this.checkOtherDevices();
                    });
                    window.addEventListener('pagehide', () => this.pushNow(true));
                    document.addEventListener('fullscreenchange', () => { this.fullscreen = Boolean(document.fullscreenElement); });
                },

                /** Each chapter shown: keys, taps, wheel and text selection inside it. */
                chapterLoaded(doc, index) {
                    doc.addEventListener('keydown', (event) => this.key(event));
                    doc.addEventListener('wheel', (event) => this.wheel(event), { passive: true });
                    // The click following a touch tap is ignored (foliate's link and highlight handlers included)
                    doc.addEventListener('click', (event) => {
                        if (!syntheticClick && Date.now() - lastTouchTap < CLICK_AFTER_TAP_MS) {
                            event.stopImmediatePropagation();
                            event.preventDefault();
                        }
                    }, true);
                    doc.addEventListener('click', (event) => this.tap(event, doc));
                    onTouchTap(doc, (event, touch) => this.touchTap(event, touch, doc));
                    let timer = null;
                    doc.addEventListener('selectionchange', () => {
                        clearTimeout(timer);
                        timer = setTimeout(() => this.selectionChanged(doc, index), 250);
                    });
                },

                // ------------------------------------------------------------ position

                async openAtLastPosition() {
                    const sync = PyReader.sync;
                    const local = sync.localPosition(this.path);
                    const devices = this.signedIn ? await sync.remotePositions(reader.info.document) : [];
                    const newest = devices[0];
                    let target = local ? local.cfi : null;
                    if (newest && (!local || newest.timestamp * 1000 > local.at + 5000)) {
                        const found = await sync.remoteTarget(reader, newest).catch(() => null);
                        target = found ? (found.cfi || found.index) : { fraction: newest.percentage };
                        if (newest.device_id !== sync.thisDevice().id) {
                            this.showNotice('Reprise à ' + percentFormat.format(newest.percentage) + ', position de ' + (newest.device || 'un autre appareil')
                                + ' (' + relativeTime(newest.timestamp) + ').', local ? { label: 'Revenir', cfi: local.cfi } : null);
                        }
                        lastPushAt = newest.timestamp;
                    }
                    jumping = true;
                    await reader.view.init({ lastLocation: target, showTextStart: false });
                    await new Promise(function (resolve) { setTimeout(resolve, OPEN_SETTLE_MS); });
                    opened = true;
                    // The opening place is kept, not sent: it is already the newest one
                    if (reader.view.lastLocation) {
                        this.relocated(reader.view.lastLocation);
                        const sync = PyReader.sync.positionOf(reader, current);
                        lastPushed = sync.progress + sync.percentage;
                        clearTimeout(pushTimer);
                    }
                },

                /** Page info of the place shown: {page, pages} in the chapter (paginated), or of the scroll. */
                pageInfo() {
                    const renderer = reader.view.renderer;
                    if (renderer.scrolled) {
                        const size = renderer.size || 1;
                        return { page: Math.floor(renderer.start / size) + 1, pages: Math.max(1, Math.ceil(renderer.viewSize / size)) };
                    }
                    const pages = Math.max(1, (renderer.pages || 3) - 2);
                    return { page: Math.min(pages, Math.max(1, renderer.page || 1)), pages: pages };
                },

                relocated(detail) {
                    const index = detail.section ? detail.section.current : 0;
                    const page = this.pageInfo();
                    current = { index: index, range: detail.range, fraction: detail.fraction, cfi: detail.cfi, page: page };
                    const chapter = detail.tocItem && detail.tocItem.label ? detail.tocItem.label.trim() : '';
                    this.progress = {
                        value: Math.round((detail.fraction || 0) * 1000),
                        percent: percentFormat.format(detail.fraction || 0),
                        chapter: chapter,
                        page: 'p. ' + page.page + ' / ' + page.pages,
                    };
                    this.tocCurrent = detail.tocItem ? detail.tocItem.href : '';
                    this.runningHeads(chapter, page, detail.fraction || 0);
                    if (!opened) return;
                    if (awake) awake.activity();
                    PyReader.sync.keepLocal(this.path, detail.cfi, detail.fraction || 0);

                    // Statistics: pages turned forward by the reader (jumps are not reading)
                    if (tracker) {
                        const renderer = reader.view.renderer;
                        if (!jumping && lastFraction !== null && detail.fraction > lastFraction) {
                            if (renderer.scrolled) {
                                const moved = Math.max(0, renderer.start - (lastScroll ?? renderer.start)) / (renderer.size || 1);
                                if (moved >= 1) tracker.pageTurned(moved);
                            } else {
                                tracker.pageTurned(1);
                            }
                        }
                        if (!jumping && lastFraction !== null && lastFraction >= NEAR_END) tracker.reached(detail.fraction || 0);
                        lastScroll = renderer.scrolled ? renderer.start : null;
                    }
                    lastFraction = detail.fraction;
                    jumping = false;
                    this.schedulePush();
                },

                /** Running heads of the paginator: book title and chapter above the text, page and percentage under it
                    (one column: chapter above, both under). */
                runningHeads(chapter, page, fraction) {
                    const renderer = reader.view.renderer;
                    const heads = renderer.heads || [];
                    const feet = renderer.feet || [];
                    const pageText = page.page + ' / ' + page.pages;
                    const percent = percentFormat.format(fraction);
                    heads.forEach((head, i) => { head.textContent = i === heads.length - 1 ? chapter : this.title; });
                    feet.forEach(function (foot, i) {
                        if (feet.length === 1) foot.textContent = pageText + ' · ' + percent;
                        else foot.textContent = i === 0 ? pageText : i === feet.length - 1 ? percent : '';
                    });
                },

                schedulePush() {
                    clearTimeout(pushTimer);
                    pushTimer = setTimeout(() => this.pushNow(false), PUSH_DELAY_MS);
                },

                async pushNow(keepalive) {
                    clearTimeout(pushTimer);
                    if (tracker) tracker.flush(keepalive);
                    if (!opened || !current || !reader || !reader.info.document) return;
                    const sync = PyReader.sync.positionOf(reader, current);
                    if (sync.progress + sync.percentage === lastPushed) return;
                    lastPushed = sync.progress + sync.percentage;
                    const timestamp = await PyReader.sync.push(reader, sync, keepalive);
                    if (timestamp) lastPushAt = timestamp;
                    else lastPushed = '';
                },

                /** Back on the page: offer the newer position of another device, if any. */
                async checkOtherDevices() {
                    if (!reader || !this.signedIn) return;
                    const devices = await PyReader.sync.remotePositions(reader.info.document);
                    const newest = devices[0];
                    if (!newest || newest.device_id === PyReader.sync.thisDevice().id || newest.timestamp <= lastPushAt) return;
                    if (current && Math.abs(newest.percentage - (current.fraction || 0)) < 0.002) return;
                    lastPushAt = newest.timestamp;
                    const found = await PyReader.sync.remoteTarget(reader, newest).catch(() => null);
                    this.showNotice((newest.device || 'Un autre appareil') + ' est plus loin : ' + percentFormat.format(newest.percentage)
                        + ' (' + relativeTime(newest.timestamp) + ').', { label: 'Y aller', cfi: found ? (found.cfi || found.index) : { fraction: newest.percentage } });
                },

                // ------------------------------------------------------------ navigation

                async go(target) {
                    jumping = true;
                    try {
                        if (target && typeof target.fraction === 'number') await reader.view.goToFraction(target.fraction);
                        else await reader.view.goTo(target);
                    } finally {
                        jumping = false;
                    }
                },

                /** The reader is reading: counted by the statistics, the screen stays on. */
                active() {
                    if (tracker) tracker.activity();
                    if (awake) awake.activity();
                },

                next() { if (reader) reader.view.goRight(); },
                prev() { if (reader) reader.view.goLeft(); },
                forward() { if (reader) reader.view.next(); },
                backward() { if (reader) reader.view.prev(); },

                key(event) {
                    if (!reader || editable(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
                    this.active();
                    const actions = {
                        ArrowLeft: () => this.prev(), ArrowRight: () => this.next(),
                        PageDown: () => this.forward(), PageUp: () => this.backward(),
                        ' ': () => (event.shiftKey ? this.backward() : this.forward()),
                        Escape: () => this.closeAll(),
                    };
                    if (reader.view.renderer.scrolled && event.key !== 'Escape') return;
                    const action = actions[event.key];
                    if (action) {
                        event.preventDefault();
                        action();
                    }
                },

                wheel(event) {
                    if (!reader || reader.view.renderer.scrolled || this.panel || Math.abs(event.deltaY) < 4) return;
                    const now = Date.now();
                    if (now - lastWheel < WHEEL_GAP_MS) return;
                    lastWheel = now;
                    if (event.deltaY > 0) this.forward();
                    else this.backward();
                },

                /** Tap in a chapter (links, highlights and selections excepted). */
                tap(event, doc) {
                    const selected = doc.getSelection();
                    if (event.defaultPrevented || (selected && !selected.isCollapsed) || (event.target.closest && event.target.closest('a[href]'))) return;
                    const frame = doc.defaultView.frameElement.getBoundingClientRect();
                    this.tapAt(frame.left + event.clientX, frame.top + event.clientY);
                },

                /** Touch tap in a chapter: a link is followed, a highlight opened, else like a tap (tapAt). */
                touchTap(event, touch, doc) {
                    const selected = doc.getSelection();
                    if (selected && !selected.isCollapsed) return;
                    lastTouchTap = Date.now();
                    const frame = doc.defaultView.frameElement.getBoundingClientRect();
                    const x = frame.left + touch.clientX;
                    const y = frame.top + touch.clientY;
                    if (this.inBarsBand(y)) {
                        this.tapAt(x, y);
                        return;
                    }
                    const link = event.target.closest ? event.target.closest('a[href]') : null;
                    if (link) {
                        syntheticClick = true;
                        try { link.click(); } finally { syntheticClick = false; }
                        return;
                    }
                    const content = reader.view.renderer.getContents().find(function (c) { return c.doc === doc; });
                    const hit = content && content.overlayer ? content.overlayer.hitTest({ x: touch.clientX, y: touch.clientY }) : [];
                    const clip = hit[0] && highlights ? highlights.atCfi(hit[0]) : null;
                    if (clip) {
                        this.edit(clip.id);
                        return;
                    }
                    this.tapAt(x, y);
                },

                /** Tap in the margins of the book, outside the chapter's frame. */
                tapOutside(event) {
                    if (Date.now() - lastTouchTap < CLICK_AFTER_TAP_MS) return;
                    if (event.target === this.$refs.book || this.$refs.book.contains(event.target)) this.tapAt(event.clientX, event.clientY);
                },

                /** Whether `y` (page coordinates) is in the top or bottom band of the book, which toggles the bars. */
                inBarsBand(y) {
                    const box = this.$refs.book.getBoundingClientRect();
                    const height = (y - box.top) / (box.height || 1);
                    return height < TAP_BARS || height > 1 - TAP_BARS;
                },

                /**
                 * Tap at `x`, `y` (page coordinates): the top and bottom bands and the middle show / hide the bars,
                 * the sides turn the page (and hide the bars).
                 */
                tapAt(x, y) {
                    if (!reader) return;
                    this.active();
                    const box = this.$refs.book.getBoundingClientRect();
                    const side = this.inBarsBand(y) ? 0.5 : (x - box.left) / (box.width || 1);
                    // A phone reads full screen: entered on the first tap (a user gesture is required)
                    if (this.fullscreenAvailable && !fullscreenAsked) {
                        fullscreenAsked = true;
                        this.enterFullscreen();
                    }
                    // After the highlight hit test of foliate (same click), which opens the clipping instead
                    setTimeout(() => {
                        if (this.editor) return;
                        if (this.panel) {
                            this.panel = null;
                        } else if (!reader.view.renderer.scrolled && side < TAP_SIDE) {
                            if (touchScreen) this.chrome = false;
                            this.prev();
                        } else if (!reader.view.renderer.scrolled && side > 1 - TAP_SIDE) {
                            if (touchScreen) this.chrome = false;
                            this.next();
                        } else {
                            this.chrome = !this.chrome;
                        }
                    }, 0);
                },

                seek(value) {
                    this.go({ fraction: Number(value) / 1000 });
                },

                goToToc(href) {
                    this.go(href);
                    this.panel = null;
                },

                togglePanel(name) {
                    this.panel = this.panel === name ? null : name;
                    window.PageKit.icons(this);
                },

                /** Tap on the dimmed background: closes the editor or the panel (not the click that follows the tap which opened them). */
                closeFromScrim() {
                    if (Date.now() - lastTouchTap < CLICK_AFTER_TAP_MS) return;
                    if (this.editor) this.editor = null;
                    else this.panel = null;
                },

                closeAll() {
                    if (this.editor) this.editor = null;
                    else if (this.panel) this.panel = null;
                    else this.clearSelection();
                },

                /** Full screen (Android, computers; iOS has no Fullscreen API for pages: "Add to Home Screen"). */
                toggleFullscreen() {
                    if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
                    else this.enterFullscreen();
                },

                enterFullscreen() {
                    if (this.fullscreenAvailable && !document.fullscreenElement) {
                        document.documentElement.requestFullscreen({ navigationUI: 'hide' }).catch(function () {});
                    }
                },

                async back() {
                    if (document.fullscreenElement) await document.exitFullscreen().catch(function () {});
                    // Back to the page the book was opened from (catalog, reading page…), else the catalog
                    if (document.referrer && new URL(document.referrer).origin === location.origin && history.length > 1) history.back();
                    else location.href = this.catalogUrl;
                },

                // ------------------------------------------------------------ notices

                showNotice(text, action) {
                    clearTimeout(noticeTimer);
                    this.notice = { text: text, action: action ? action.label : '' };
                    this.noticeTarget = action ? action.cfi : null;
                    noticeTimer = setTimeout(() => { this.notice = null; }, NOTICE_MS);
                },

                noticeAction() {
                    const target = this.noticeTarget;
                    this.notice = null;
                    if (target !== null && target !== undefined) this.go(target);
                },

                // ------------------------------------------------------------ settings

                changeSetting(name, value) {
                    const settings = Object.assign({}, this.settings);
                    if (name === 'fontSize' || name === 'lineHeight') {
                        const [min, max] = PyReader.settings.LIMITS[name];
                        const step = name === 'fontSize' ? 10 : 0.1;
                        settings[name] = Math.round(Math.min(max, Math.max(min, settings[name] + value * step)) * 10) / 10;
                    } else {
                        settings[name] = value;
                    }
                    this.settings = settings;
                    PyReader.settings.save(settings);
                    if (reader) PyReader.settings.apply(reader.view, settings);
                },

                // ------------------------------------------------------------ clippings

                refreshClips() {
                    this.clips = highlights.sorted().map(function (clip) {
                        return {
                            id: clip.id,
                            text: clip.text,
                            note: clip.note || '',
                            caption: [clip.chapter, window.PageKit.formatDate(clip.created_at)].filter(Boolean).join(' · '),
                            color: PyReader.highlights.COLORS[clip.color] || PyReader.highlights.COLORS.yellow,
                        };
                    });
                    window.PageKit.icons(this);
                },

                selectionChanged(doc, index) {
                    const selected = doc.getSelection();
                    if (selected && !selected.isCollapsed && selected.rangeCount && selected.toString().trim()) {
                        selection = { doc: doc, index: index, range: selected.getRangeAt(0).cloneRange() };
                        this.selecting = Boolean(highlights);
                    } else if (selection && selection.doc === doc) {
                        selection = null;
                        this.selecting = false;
                    }
                },

                clearSelection() {
                    if (selection) selection.doc.getSelection().removeAllRanges();
                    selection = null;
                    this.selecting = false;
                },

                /** Clipping of the selected text; `withNote` opens it to write a note. */
                async highlight(color, withNote) {
                    if (!selection || !highlights || !current) return;
                    const chosen = selection;
                    this.clearSelection();
                    try {
                        const clip = await highlights.add(chosen.index, chosen.range, current, color);
                        if (clip && withNote) this.edit(clip.id);
                    } catch (e) {
                        this.showNotice('Le surlignage n’a pas pu être enregistré : ' + e.message);
                    }
                },

                edit(id) {
                    const clip = highlights.get(id);
                    if (!clip) return;
                    this.editor = { id: clip.id, text: clip.text, note: clip.note || '', color: clip.color || 'yellow', chapter: clip.chapter || '', saving: false };
                    window.PageKit.icons(this);
                    this.$nextTick(() => this.$refs.note && this.$refs.note.focus({ preventScroll: true }));
                },

                async saveEditor() {
                    const editor = this.editor;
                    editor.saving = true;
                    try {
                        await highlights.update(editor.id, { note: editor.note.trim() || null, color: editor.color });
                        this.editor = null;
                    } catch (e) {
                        editor.saving = false;
                        this.showNotice('Modification non enregistrée : ' + e.message);
                    }
                },

                async deleteClip() {
                    const editor = this.editor;
                    if (!window.confirm('Supprimer ce surlignage ?')) return;
                    try {
                        await highlights.remove(editor.id);
                        this.editor = null;
                    } catch (e) {
                        this.showNotice('Suppression impossible : ' + e.message);
                    }
                },

                async showClip(id) {
                    jumping = true;
                    const found = await highlights.show(id);
                    jumping = false;
                    if (!found) this.showNotice('Passage introuvable dans cette édition du livre.');
                    else if (window.innerWidth < 1024) this.panel = null;
                },

                get colorNames() {
                    return Object.keys(PyReader.highlights.COLORS);
                },

                colorOf(name) {
                    return PyReader.highlights.COLORS[name];
                },
            };
        });
    });
})();
