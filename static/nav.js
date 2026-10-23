// Menu of every web UI screen (static/partials/nav.html): highlights the current section, shows the
// sign-in or logout link (session user, static/account.js) and switches the light / dark theme
// (localStorage 'color-theme', the class itself being set before the first paint by theme.js or the
// XSLT's inline script). Plain script, loaded after account.js; it starts itself once the document
// is parsed (also with the XSLT polyfill, which runs the scripts after parsing).
(function () {
    'use strict';

    const SECTIONS = { catalog: /^\/(opds|$)/, reading: /^\/reading/, clippings: /^\/clippings/ };

    function init() {
        const nav = document.getElementById('site-nav');
        if (!nav || nav.dataset.ready) return;
        nav.dataset.ready = '1';

        nav.querySelectorAll('[data-nav]').forEach(function (link) {
            if (SECTIONS[link.dataset.nav].test(location.pathname)) link.setAttribute('aria-current', 'page');
        });

        const user = window.SyncAccount.sessionUser();
        const account = document.getElementById(user ? 'nav-logout' : 'nav-login');
        account.classList.remove('hidden');
        account.classList.add('flex');
        if (user) {
            account.title = 'Se déconnecter (' + user + ')';
        } else if (location.pathname !== '/login') {
            account.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
        }

        document.getElementById('theme-toggle').addEventListener('click', function () {
            const dark = document.documentElement.classList.toggle('dark');
            try { localStorage.setItem('color-theme', dark ? 'dark' : 'light'); } catch (e) { /* storage unavailable */ }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
