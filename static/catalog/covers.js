// Catalog web UI: lazy loading of the book tile covers (img[data-cover-src]). A cover is only
// requested once its tile nears the viewport; a book without cover keeps its title block.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    /** Observe the book covers and load each one when it becomes visible. */
    function init() {
        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                const img = entry.target;
                const coverSrc = img.getAttribute('data-cover-src');
                observer.unobserve(img);
                // Test the cover first: the fallback stays when the EPUB has none
                const testImg = new Image();
                testImg.onload = function () {
                    img.src = coverSrc;
                    img.classList.add('loaded');
                };
                testImg.onerror = function () {
                    img.remove();
                };
                testImg.src = coverSrc;
            });
        }, {
            root: null,
            rootMargin: '50px', // Start loading slightly before entering the viewport
            threshold: 0.01,
        });

        document.querySelectorAll('img[data-cover-src]').forEach(function (img) {
            observer.observe(img);
        });
    }

    PyOPDS.covers = { init: init };
})();
