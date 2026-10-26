// Catalog web UI: book sheet (dialog#book-dialog), filled from the data-* attributes of the
// clicked book tile (a.book-link: title, author, cover, series, year, rating, genres, summary),
// with the web reader button (/read?path=…) of the library EPUBs.
// A plain click opens it; modified clicks (ctrl, cmd, shift, alt, middle) keep the download link.
// The opened sheet is shareable (#book=<id>), and such a link opens it on load.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    const CHIP_CLASS = 'px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-200 dark:hover:bg-slate-600';
    let bookDialog = null;

    function bookField(link, name) {
        return (link.getAttribute('data-' + name) || '').trim();
    }

    function formatRating(value) {
        return (Math.round(value * 10) / 10).toString().replace('.', ',');
    }

    function chip(text, href) {
        const el = document.createElement(href ? 'a' : 'span');
        if (href) el.href = href;
        el.className = CHIP_CLASS;
        el.textContent = text;
        return el;
    }

    function fillBookDialog(link) {
        const title = bookField(link, 'title');
        const author = bookField(link, 'author');
        document.getElementById('book-dialog-title').textContent = title;
        document.getElementById('book-dialog-cover-title').textContent = title;

        // Author navigation: all the books of the author (none for an unknown author)
        const authorEl = document.getElementById('book-dialog-author');
        authorEl.hidden = !author;
        document.getElementById('book-dialog-author-name').textContent = author;
        if (author && author !== 'Unknown') {
            authorEl.href = '/opds/by-author/' + encodeURIComponent(author) + '?page=1';
            authorEl.title = 'Voir les livres de ' + author;
        } else {
            authorEl.removeAttribute('href');
            authorEl.removeAttribute('title');
        }

        // Large cover, title block when the EPUB has none
        const cover = document.getElementById('book-dialog-cover');
        const coverSrc = bookField(link, 'cover');
        cover.hidden = true;
        cover.onload = function () { cover.hidden = false; };
        cover.onerror = function () { cover.hidden = true; };
        cover.alt = title;
        if (coverSrc) {
            cover.src = coverSrc;
        } else {
            cover.removeAttribute('src');
        }

        const series = bookField(link, 'series');
        const seriesIndex = bookField(link, 'series-index');
        const seriesRow = document.getElementById('book-dialog-series-row');
        const seriesEl = document.getElementById('book-dialog-series');
        seriesRow.hidden = !series;
        seriesEl.textContent = seriesIndex ? 'Tome ' + seriesIndex + ' · ' + series : series;
        seriesEl.href = bookField(link, 'series-href') || ('/opds/series?name=' + encodeURIComponent(series));

        const facts = document.getElementById('book-dialog-facts');
        facts.replaceChildren();
        const year = bookField(link, 'year');
        if (year) {
            const yearEl = document.createElement('span');
            yearEl.textContent = year;
            facts.appendChild(yearEl);
        }
        const rating = parseFloat(bookField(link, 'rating'));
        if (rating > 0) {
            // Grey stars with the amber ones on top, cut at the rating
            const label = 'Note : ' + formatRating(rating) + ' sur 5';
            const stars = document.createElement('span');
            stars.className = 'book-stars';
            stars.setAttribute('role', 'img');
            stars.setAttribute('aria-label', label);
            stars.title = label;
            stars.textContent = '★★★★★';
            const on = document.createElement('span');
            on.className = 'book-stars-on';
            on.setAttribute('aria-hidden', 'true');
            on.style.width = Math.min(100, rating * 20) + '%';
            on.textContent = '★★★★★';
            stars.appendChild(on);
            facts.appendChild(stars);
        }

        const genres = document.getElementById('book-dialog-genres');
        genres.replaceChildren();
        bookField(link, 'genres').split('\n').filter(Boolean).forEach(function (genre) {
            // Tag chip: opens the books of that genre
            const tag = chip(genre, '/opds/by-genre/' + encodeURIComponent(genre) + '?page=1');
            tag.classList.add('inline-flex', 'items-center', 'gap-1');
            tag.title = 'Voir les livres du genre « ' + genre + ' »';
            const icon = document.createElement('i');
            icon.setAttribute('data-lucide', 'tag');
            icon.className = 'w-3 h-3';
            tag.prepend(icon);
            genres.appendChild(tag);
        });
        if (window.lucide) {
            lucide.createIcons();
        }

        document.getElementById('book-dialog-download').href = link.getAttribute('href');
        // Web reader of a library EPUB
        const path = bookPath(link);
        const read = document.getElementById('book-dialog-open');
        read.hidden = !path || window.PageKit.features().minimal;
        if (path) read.href = window.PageKit.libraryLinks(path, title).read;

        const summary = bookField(link, 'summary');
        const block = document.getElementById('book-dialog-description-block');
        const description = document.getElementById('book-dialog-description');
        const more = document.getElementById('book-dialog-more');
        block.hidden = !summary;
        description.textContent = summary;
        description.classList.add('clamped');
        more.hidden = true;
        more.textContent = 'Lire la suite';
    }

    /** EPUB path relative to the library, from the tile's download link (/download/<path>); null otherwise. */
    function bookPath(link) {
        const href = link.getAttribute('href') || '';
        const prefix = '/download/';
        if (href.indexOf(prefix) !== 0) return null;
        try {
            return decodeURIComponent(href.slice(prefix.length).split('?')[0]);
        } catch (e) {
            return null;
        }
    }

    /** Open the sheet of a book tile; false when the browser has no modal dialogs. */
    function open(link, fromHash) {
        if (!bookDialog || typeof bookDialog.showModal !== 'function') {
            return false;
        }
        fillBookDialog(link);
        // Other features of the sheet follow the opened book (follow)
        bookDialog.dispatchEvent(new CustomEvent('book-sheet:open', { detail: { link: link, path: bookPath(link) } }));
        bookDialog.showModal();
        document.documentElement.style.overflow = 'hidden';
        bookDialog.scrollTop = 0;
        // "Lire la suite" only when the description is actually cut
        const description = document.getElementById('book-dialog-description');
        document.getElementById('book-dialog-more').hidden = description.scrollHeight - description.clientHeight <= 2;
        // Shareable link to the opened details
        const id = link.getAttribute('data-id');
        if (id && !fromHash) {
            history.replaceState(null, '', '#book=' + encodeURIComponent(id));
        }
        return true;
    }

    /**
     * Follow the books opened by the sheet for the signed-in reader (read-status.js, book-clippings.js):
     * onOpen(path) on each opened sheet, path null when signed out. Returns the getter of the book shown
     * now (answers for an earlier book are ignored), null without the sheet.
     */
    function follow(onOpen) {
        const dialog = document.getElementById('book-dialog');
        if (!dialog) return null;
        let current = null;
        dialog.addEventListener('book-sheet:open', function (event) {
            current = window.SyncAccount.sessionUser() ? event.detail.path : null;
            onOpen(current);
        });
        return function () { return current; };
    }

    /** Intercept plain clicks on the books, wire the dialog and open the sheet of a #book= link. */
    function init() {
        bookDialog = document.getElementById('book-dialog');
        if (!bookDialog) return;

        document.addEventListener('click', function (event) {
            const link = event.target.closest ? event.target.closest('a.book-link') : null;
            if (!link || event.defaultPrevented || event.button !== 0
                || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                return;
            }
            if (open(link, false)) {
                event.preventDefault();
            }
        });

        document.getElementById('book-dialog-close').addEventListener('click', function () {
            bookDialog.close();
        });

        // Click on the backdrop (outside the content) closes; Escape closes natively
        bookDialog.addEventListener('click', function (event) {
            if (event.target === bookDialog) {
                bookDialog.close();
            }
        });

        bookDialog.addEventListener('close', function () {
            document.documentElement.style.overflow = '';
            if (location.hash.indexOf('#book=') === 0) {
                history.replaceState(null, '', location.pathname + location.search);
            }
        });

        document.getElementById('book-dialog-more').addEventListener('click', function () {
            const description = document.getElementById('book-dialog-description');
            const clamped = description.classList.toggle('clamped');
            this.textContent = clamped ? 'Lire la suite' : 'Réduire';
        });

        // Opened from a shared link (#book=urn:book:…)
        if (location.hash.indexOf('#book=') === 0) {
            const wanted = decodeURIComponent(location.hash.slice('#book='.length));
            const target = Array.prototype.find.call(document.querySelectorAll('a.book-link'), function (link) {
                return link.getAttribute('data-id') === wanted;
            });
            if (target) {
                open(target, true);
            }
        }
    }

    PyOPDS.bookSheet = { init: init, open: open, follow: follow };
})();
