// Catalog web UI: numbered pagination of the paged feeds. Reads "Page N of M" from the feed
// title (h1), fills #page-info and #page-numbers and sets the first/last page links, keeping the
// other query parameters (e.g. the search query).
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    const MAX_VISIBLE = 7; // Maximum number of page buttons shown
    const CURRENT_CLASS = 'px-3 py-2 rounded-md text-sm font-medium bg-slate-700 text-white dark:bg-slate-300 dark:text-slate-900 shadow-sm';
    const PAGE_CLASS = 'px-3 py-2 rounded-md text-sm font-medium bg-white text-slate-700 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 shadow-sm transition-colors';
    const ELLIPSIS_CLASS = 'px-2 text-slate-400 dark:text-slate-500';

    /** Page numbers to show: all when few, else first, last and the pages around the current one. */
    function pageNumbers(currentPage, totalPages) {
        const pages = [];
        if (totalPages <= MAX_VISIBLE) {
            for (let i = 1; i <= totalPages; i++) pages.push(i);
            return pages;
        }
        pages.push(1);
        const rangeStart = Math.max(2, currentPage - 2);
        const rangeEnd = Math.min(totalPages - 1, currentPage + 2);
        if (rangeStart > 2) pages.push('...');
        for (let i = rangeStart; i <= rangeEnd; i++) pages.push(i);
        if (rangeEnd < totalPages - 1) pages.push('...');
        pages.push(totalPages);
        return pages;
    }

    /** Build the pagination of the current feed (nothing on unpaged feeds). */
    function init() {
        const pageInfoEl = document.getElementById('page-info');
        const pageNumbersEl = document.getElementById('page-numbers');
        if (!pageInfoEl || !pageNumbersEl) return;

        const titleEl = document.querySelector('h1');
        if (!titleEl) return;
        const pageMatch = titleEl.textContent.match(/Page (\d+) of (\d+)/);
        if (!pageMatch) return;

        const currentPage = parseInt(pageMatch[1]);
        const totalPages = parseInt(pageMatch[2]);
        pageInfoEl.textContent = `Page ${currentPage} of ${totalPages}`;

        // Same URL with another page number (keeps other parameters such as the search query)
        const pageUrl = function (pageNum) {
            const url = new URL(window.location.href);
            url.searchParams.set('page', pageNum);
            return url.pathname + url.search;
        };

        const firstPageEl = document.getElementById('first-page');
        if (firstPageEl && currentPage > 1) {
            firstPageEl.href = pageUrl(1);
            firstPageEl.hidden = false;
        }
        const lastPageEl = document.getElementById('last-page');
        if (lastPageEl && currentPage < totalPages) {
            lastPageEl.href = pageUrl(totalPages);
            lastPageEl.hidden = false;
        }

        pageNumbers(currentPage, totalPages).forEach(function (pageNum) {
            if (pageNum === '...') {
                const ellipsis = document.createElement('span');
                ellipsis.className = ELLIPSIS_CLASS;
                ellipsis.textContent = '...';
                pageNumbersEl.appendChild(ellipsis);
            } else {
                const link = document.createElement('a');
                link.href = pageUrl(pageNum);
                link.className = pageNum === currentPage ? CURRENT_CLASS : PAGE_CLASS;
                link.textContent = pageNum;
                pageNumbersEl.appendChild(link);
            }
        });
    }

    PyOPDS.pagination = { init: init, pageNumbers: pageNumbers };
})();
