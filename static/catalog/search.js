// Catalog web UI: search fields of the header. Pre-fills the query on /opds/search and opens or
// closes the full-width search bar of phones (the header form is only shown from the md breakpoint).
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    /** Pre-fill the search fields and wire the mobile search bar. */
    function init() {
        const searchInput = document.getElementById('search-input');
        const mobileSearchInput = document.getElementById('mobile-search-input');
        if (window.location.pathname === '/opds/search') {
            const query = new URLSearchParams(window.location.search).get('q');
            if (query) {
                if (searchInput) searchInput.value = query;
                if (mobileSearchInput) mobileSearchInput.value = query;
            }
        }

        const searchToggle = document.getElementById('search-toggle');
        const mobileSearchBar = document.getElementById('mobile-search-bar');
        const mobileSearchOverlay = document.getElementById('mobile-search-overlay');
        const closeSearch = document.getElementById('close-search');

        function openMobileSearch() {
            if (mobileSearchBar && mobileSearchOverlay) {
                mobileSearchBar.style.display = 'block';
                mobileSearchOverlay.style.display = 'block';
                if (mobileSearchInput) mobileSearchInput.focus();
                if (window.lucide) lucide.createIcons();
            }
        }

        function closeMobileSearch() {
            if (mobileSearchBar && mobileSearchOverlay) {
                mobileSearchBar.style.display = 'none';
                mobileSearchOverlay.style.display = 'none';
            }
        }

        if (searchToggle) searchToggle.addEventListener('click', openMobileSearch);
        if (closeSearch) closeSearch.addEventListener('click', closeMobileSearch);
        if (mobileSearchOverlay) mobileSearchOverlay.addEventListener('click', closeMobileSearch);
    }

    PyOPDS.search = { init: init };
})();
