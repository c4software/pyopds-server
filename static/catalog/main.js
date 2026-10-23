// Catalog web UI entry point: starts the features of static/catalog/ (loaded before it, in order,
// by plain <script src> tags at the end of the body of static/opds_to_html.xslt).
// Works with the native XSLT and with the XSLT polyfill, which runs the scripts of the result
// after the document is parsed and then dispatches a synthetic DOMContentLoaded: the deferred
// part checks document.readyState and runs once only.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS || {};

    /** Run fn once the document is parsed (now if it already is), a single time. */
    function whenReady(fn) {
        let done = false;
        const once = function () {
            if (done) return;
            done = true;
            fn();
        };
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', once);
        } else {
            once();
        }
    }

    // Icons first: the features below look up some of them (lucide keeps their id and classes)
    if (window.lucide) {
        lucide.createIcons();
    }
    // Header and book sheet: their elements come before the scripts in the page
    PyOPDS.backButton.init();
    PyOPDS.search.init();
    PyOPDS.mosaic.initToggle();
    PyOPDS.readStatus.init();
    PyOPDS.bookClippings.init();
    PyOPDS.bookSheet.init();

    // Covers, mosaic and pagination once the whole document is there
    whenReady(function () {
        PyOPDS.covers.init();
        PyOPDS.mosaic.start();
        PyOPDS.pagination.init();
    });
})();
