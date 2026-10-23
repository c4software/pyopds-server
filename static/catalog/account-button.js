// Catalog web UI: account button of the header. Without a session, "Se connecter" (back to the
// current page after signing in); signed in (SyncAccount.sessionUser, static/account.js), logout
// with the account name in its title.
(function () {
    'use strict';

    const PyOPDS = window.PyOPDS = window.PyOPDS || {};

    /** Show the sign-in or the logout link. */
    function init() {
        const user = window.SyncAccount.sessionUser();
        const link = document.getElementById(user ? 'logout-link' : 'login-link');
        if (!link) return;
        link.classList.remove('hidden');
        link.classList.add('flex');
        if (user) {
            link.title = 'Se déconnecter (' + user + ')';
        } else {
            link.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
        }
    }

    PyOPDS.accountButton = { init: init };
})();
