// Catalog web UI: account button of the header. Without a session, "Se connecter" (back to the
// current page after signing in); signed in (cookie pyopds_user, readable by scripts), logout
// with the account name in its title.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    /** Show the sign-in or the logout link. */
    function init() {
        const match = document.cookie.match(/(?:^|;\s*)pyopds_user=([^;]*)/);
        const link = document.getElementById(match ? 'logout-link' : 'login-link');
        if (!link) return;
        link.classList.remove('hidden');
        link.classList.add('flex');
        if (match) {
            link.title = 'Se déconnecter (' + decodeURIComponent(match[1]) + ')';
        } else {
            link.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
        }
    }

    PyOPDS.accountButton = { init: init };
})();
