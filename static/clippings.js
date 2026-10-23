// Clippings page: the `clippings` Alpine component (templates in clippings.html) loads the signed-in
// reader's clippings grouped by book (GET /webui/clippings, catalog session) and filters them in the
// browser (search, book). It never builds HTML; the Markdown export is served by /webui/clippings/export.
(function () {
    'use strict';

    const EXPORT_URL = '/webui/clippings/export';

    function clipView(clip, index) {
        const page = clip.page ? 'p. ' + clip.page : '';
        return {
            key: clip.id || String(index),
            text: clip.text,
            note: clip.note || '',
            caption: [clip.chapter, page, window.PageKit.formatDate(clip.created_at)].filter(Boolean).join(' · '),
            haystack: [clip.text, clip.note, clip.chapter].join('\n').toLowerCase(),
        };
    }

    function groupView(group) {
        const book = group.book;
        const links = book.path ? window.PageKit.libraryLinks(book.path, book.title) : null;
        return {
            key: book.document,
            title: book.title,
            author: book.author || '',
            links: links,
            broken: false,
            exportUrl: book.path ? EXPORT_URL + '?path=' + encodeURIComponent(book.path) : '',
            bookHaystack: [book.title, book.author].join('\n').toLowerCase(),
            clips: group.clippings.map(clipView),
        };
    }

    document.addEventListener('alpine:init', function () {
        window.Alpine.data('clippings', function () {
            return {
                account: { user: window.SyncAccount.sessionUser() },
                ready: false,
                loading: false,
                error: '',
                groups: [],
                query: '',
                book: '',

                init() {
                    if (!this.account.user) {
                        window.SyncAccount.signIn('/clippings');
                        return;
                    }
                    this.ready = true;
                    window.PageKit.icons(this);
                    this.load();
                    // New matches show new placeholders (cover links, icons)
                    this.$watch('query', () => window.PageKit.icons(this));
                    this.$watch('book', () => window.PageKit.icons(this));
                },

                logout() {
                    window.SyncAccount.logout();
                },

                coverError(key) {
                    const group = this.groups.find((g) => g.key === key);
                    if (group) group.broken = true;
                    window.PageKit.icons(this);
                },

                async load() {
                    this.loading = true;
                    this.error = '';
                    try {
                        this.groups = (await window.PageKit.fetchJson('/webui/clippings')).groups.map(groupView);
                    } catch (e) {
                        // Expired session: the sign-in page answers instead of the data
                        if (e.status === 200 || e.status === 401) {
                            window.SyncAccount.signIn('/clippings');
                            return;
                        }
                        this.error = 'Impossible de charger les surlignages : ' + e.message;
                    } finally {
                        this.loading = false;
                        window.PageKit.icons(this);
                    }
                },

                /** Groups of the selected book matching the search, with only their matching clippings. */
                get shown() {
                    const terms = this.query.toLowerCase().split(/\s+/).filter(Boolean);
                    const matches = function (text) { return terms.every(function (term) { return text.indexOf(term) !== -1; }); };
                    return this.groups
                        .filter((group) => !this.book || group.key === this.book)
                        .map(function (group) {
                            const clips = group.clips.filter(function (clip) { return matches(group.bookHaystack + '\n' + clip.haystack); });
                            return Object.assign({}, group, { clips: clips });
                        })
                        .filter(function (group) { return group.clips.length; });
                },

                get total() {
                    return this.groups.reduce(function (n, group) { return n + group.clips.length; }, 0);
                },

                get summary() {
                    const shown = this.shown.reduce(function (n, group) { return n + group.clips.length; }, 0);
                    const label = (count) => count + ' surlignage' + (count > 1 ? 's' : '');
                    return shown === this.total ? label(this.total) : label(shown) + ' sur ' + this.total;
                },

                /** Markdown export of the selected library book, else of every book. */
                get exportUrl() {
                    const group = this.book && this.groups.find((g) => g.key === this.book);
                    return (group && group.exportUrl) || EXPORT_URL;
                },
            };
        });
    });
})();
