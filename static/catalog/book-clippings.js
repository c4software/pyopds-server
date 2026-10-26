// Catalog web UI: the signed-in reader's clippings of the book shown by the sheet
// (#book-dialog-clippings-block). On each opened sheet, loads GET /webui/clippings?path=… (path sent by
// book-sheet.js) and fills the list from the <template id="book-dialog-clipping-template"> (text set with
// textContent only), with the Markdown export of the book and a link to the /clippings page.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    let block = null;
    let list = null;
    let count = null;
    let exportLink = null;
    let template = null;
    // Book shown by the sheet (PyOPDS.bookSheet.follow)
    let shownPath = null;

    function item(clip) {
        const node = template.content.firstElementChild.cloneNode(true);
        const field = function (name) { return node.querySelector('[data-field="' + name + '"]'); };
        field('text').textContent = clip.text;
        field('note').textContent = clip.note || '';
        field('note').hidden = !clip.note;
        field('caption').textContent = window.PageKit.clippingCaption(clip);
        return node;
    }

    async function show(path) {
        block.hidden = true;
        list.replaceChildren();
        if (!path) return;
        try {
            const data = await window.PageKit.fetchJson('/webui/clippings?path=' + encodeURIComponent(path));
            const clips = data.groups.length ? data.groups[0].clippings : [];
            if (path !== shownPath() || !clips.length) return;
            list.replaceChildren.apply(list, clips.map(item));
            count.textContent = String(clips.length);
            exportLink.href = '/webui/clippings/export?path=' + encodeURIComponent(path);
            block.hidden = false;
        } catch (e) {
            // Unknown book or expired session: no clippings block
        }
    }

    /** Follow the sheets opened by book-sheet.js. */
    function init() {
        block = document.getElementById('book-dialog-clippings-block');
        list = document.getElementById('book-dialog-clippings');
        count = document.getElementById('book-dialog-clippings-count');
        exportLink = document.getElementById('book-dialog-clippings-export');
        template = document.getElementById('book-dialog-clipping-template');
        if (!block || !list || !count || !exportLink || !template) return;
        shownPath = PyOPDS.bookSheet.follow(show);
    }

    PyOPDS.bookClippings = { init: init };
})();
