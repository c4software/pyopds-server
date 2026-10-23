// Light / dark theme of the static pages (sign-in, reading, clippings), same storage key as the catalog.
// Loaded in <head> without defer: the class is set before the first paint (no flash); the menu's
// button (static/nav.js) switches it.
(function () {
    'use strict';

    try {
        if (localStorage.getItem('color-theme') === 'dark' || (!('color-theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
            document.documentElement.classList.add('dark');
        }
    } catch (e) { /* storage unavailable */ }
})();
