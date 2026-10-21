// Catalog web UI: light/dark theme button of the header (choice kept in localStorage 'color-theme').
// The `dark` class itself is set before the first paint by the small inline script of the
// XSLT <head> (static/opds_to_html.xslt); this file only handles the button.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    function prefersDark() {
        return localStorage.getItem('color-theme') === 'dark'
            || (!('color-theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches);
    }

    /** Show the icon of the other theme and switch theme on click. */
    function init() {
        const darkIcon = document.getElementById('theme-toggle-dark-icon');
        const lightIcon = document.getElementById('theme-toggle-light-icon');
        const button = document.getElementById('theme-toggle');
        if (!darkIcon || !lightIcon || !button) return;

        // Change the icons inside the button based on previous settings
        if (prefersDark()) {
            lightIcon.classList.remove('hidden');
        } else {
            darkIcon.classList.remove('hidden');
        }

        button.addEventListener('click', function () {
            darkIcon.classList.toggle('hidden');
            lightIcon.classList.toggle('hidden');

            if (localStorage.getItem('color-theme')) {
                // Set via local storage previously
                if (localStorage.getItem('color-theme') === 'light') {
                    document.documentElement.classList.add('dark');
                    localStorage.setItem('color-theme', 'dark');
                } else {
                    document.documentElement.classList.remove('dark');
                    localStorage.setItem('color-theme', 'light');
                }
            } else if (document.documentElement.classList.contains('dark')) {
                document.documentElement.classList.remove('dark');
                localStorage.setItem('color-theme', 'light');
            } else {
                document.documentElement.classList.add('dark');
                localStorage.setItem('color-theme', 'dark');
            }
        });
    }

    PyOPDS.theme = { init: init };
})();
