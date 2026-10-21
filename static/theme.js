// Light / dark theme of the static pages (sign-in, reading), same storage key as the catalog pages.
// Loaded in <head> without defer: the class is set before the first paint (no flash), and the
// `themeToggle` Alpine component of the header button is registered before Alpine starts.
(function () {
    'use strict';

    try {
        if (localStorage.getItem('color-theme') === 'dark' || (!('color-theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
            document.documentElement.classList.add('dark');
        }
    } catch (e) { /* storage unavailable */ }

    document.addEventListener('alpine:init', function () {
        window.Alpine.data('themeToggle', function () {
            return {
                dark: document.documentElement.classList.contains('dark'),
                toggle: function () {
                    this.dark = document.documentElement.classList.toggle('dark');
                    try { localStorage.setItem('color-theme', this.dark ? 'dark' : 'light'); } catch (e) { /* storage unavailable */ }
                },
            };
        });
    });
})();
