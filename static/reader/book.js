// Web reader: opens a library EPUB in a foliate-js view (static/vendor/foliate/, ES modules loaded with import()).
// The EPUB is never downloaded whole: GET /webui/reader/book lists its entries, GET /webui/reader/file
// serves the ones foliate-js asks for (chapters, styles, images…), cached by the browser per EPUB version.
// Also maps foliate's sections to the spine indexes used by KOReader and CrossPoint readers.
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const FOLIATE = '/static/vendor/foliate/';

    /** Zip loader of foliate-js reading the entries from the server. */
    function serverLoader(info) {
        const base = '/webui/reader/file?path=' + encodeURIComponent(info.path) + '&v=' + info.version + '&entry=';
        const fetchEntry = async function (name) {
            if (!Object.prototype.hasOwnProperty.call(info.entries, name)) return null;
            const response = await fetch(base + encodeURIComponent(name), { credentials: 'same-origin' });
            if (!response.ok) throw new Error('Erreur ' + response.status + ' : ' + name);
            return response;
        };
        return {
            loadText: async function (name) {
                const response = await fetchEntry(name);
                return response ? response.text() : null;
            },
            loadBlob: async function (name, type) {
                const response = await fetchEntry(name);
                if (!response) return null;
                const blob = await response.blob();
                return type ? new Blob([blob], { type: type }) : blob;
            },
            getSize: function (name) {
                return info.entries[name] || 0;
            },
        };
    }

    /**
     * Book of a library EPUB: {info (GET /webui/reader/book), book (foliate EPUB), view (<foliate-view>,
     * appended to `container`), spineOf(section index), sectionOf(spine index)}.
     */
    async function open(path, container) {
        const info = await window.PageKit.fetchJson('/webui/reader/book?path=' + encodeURIComponent(path));
        const [{ EPUB }, { Overlayer }] = await Promise.all([
            import(FOLIATE + 'epub.js'), import(FOLIATE + 'overlayer.js'), import(FOLIATE + 'view.js'),
        ]);
        // Highlight drawing of the clippings (highlights.js)
        PyReader.Overlayer = Overlayer;
        const book = await new EPUB(serverLoader(info)).init();
        // A missing resource leaves a blank instead of breaking the chapter
        book.transformTarget?.addEventListener('data', function (event) {
            event.detail.data = Promise.resolve(event.detail.data).catch(function () { return ''; });
        });
        const view = document.createElement('foliate-view');
        container.append(view);
        await view.open(book);

        // Sections skip the spine items missing from the manifest: index ⇄ spine position
        const spine = book.resources.spine.map(function (item) {
            const manifest = book.resources.getItemByID(item.idref);
            return manifest ? manifest.href : null;
        });
        const sectionIds = book.sections.map(function (section) { return section.id; });
        return {
            info: info,
            book: book,
            view: view,
            spineOf: function (index) { return Math.max(0, spine.indexOf(sectionIds[index])); },
            sectionOf: function (spineIndex) {
                const index = sectionIds.indexOf(spine[spineIndex]);
                return index >= 0 ? index : null;
            },
        };
    }

    /** Document of a section: the rendered one when it is shown, else parsed from the EPUB (same DOM). */
    async function sectionDocument(reader, index) {
        const shown = reader.view.renderer.getContents().find(function (content) { return content.index === index; });
        if (shown && shown.doc) return shown.doc;
        const section = reader.book.sections[index];
        return section && section.createDocument ? section.createDocument() : null;
    }

    /** Flat table of contents [{key, label, href, depth}] of a foliate book. */
    function tableOfContents(book) {
        const items = [];
        const walk = function (list, depth) {
            (list || []).forEach(function (item) {
                items.push({ key: String(items.length), label: (item.label || '').trim(), href: item.href, depth: depth });
                walk(item.subitems, depth + 1);
            });
        };
        walk(book.toc, 0);
        return items;
    }

    PyReader.book = { open: open, sectionDocument: sectionDocument, tableOfContents: tableOfContents };
})();
