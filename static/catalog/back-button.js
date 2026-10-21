// Catalog web UI: "Retour" button of the header, pointing to the parent feed of the current one
// (folder parent, author letter, list of years/genres…). Hidden on the root catalog.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    /** URL of the parent feed of a catalog path, '' when there is none. */
    function parentPath(currentPath) {
        if (currentPath.startsWith('/opds/folder/')) {
            const pathParts = currentPath.substring('/opds/folder/'.length).split('/').filter((p) => p);
            return pathParts.length > 1 ? '/opds/folder/' + pathParts.slice(0, -1).join('/') : '/opds';
        }
        if (currentPath === '/opds/books' || currentPath === '/opds/recent') return '/opds';
        if (currentPath.startsWith('/opds/by-year/')) return '/opds/by-year';
        if (currentPath.startsWith('/opds/by-genre/')) return '/opds/by-genre';
        if (currentPath === '/opds/by-year' || currentPath === '/opds/by-author' || currentPath === '/opds/by-genre') return '/opds';
        if (currentPath.startsWith('/opds/by-author/letter/')) return '/opds/by-author';
        if (currentPath.startsWith('/opds/by-author/')) {
            // Back to the letter of the author name
            const authorPart = decodeURIComponent(currentPath.substring('/opds/by-author/'.length));
            if (authorPart && !authorPart.startsWith('letter/')) {
                const firstChar = authorPart.charAt(0).toUpperCase();
                return /[A-Z]/.test(firstChar)
                    ? '/opds/by-author/letter/' + firstChar + '?page=1'
                    : '/opds/by-author/letter/%23?page=1';
            }
            return '/opds/by-author';
        }
        if (currentPath === '/opds/search' || currentPath === '/opds/series') return '/opds';
        return '';
    }

    /** Show the button when the current feed has a parent. */
    function init() {
        const backButton = document.getElementById('back-button');
        const parent = parentPath(window.location.pathname);
        if (backButton && parent) {
            backButton.href = parent;
            backButton.style.display = 'flex';
        }
    }

    PyOPDS.backButton = { init: init, parentPath: parentPath };
})();
