// Sign-in page of the catalog (OPDS_AUTH): opens the catalog session (cookie set by POST /login)
// and keeps the sync key for the reading page, so that one sign-in serves both.
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const params = new URLSearchParams(window.location.search);

    // Only local paths, never another site
    const next = params.get('next');
    $('login-next').value = next && next.charAt(0) === '/' && next.charAt(1) !== '/' && next.charAt(1) !== '\\' ? next : '/opds';

    // Signed out (or session expired): forget the sync key of the reading page too
    window.SyncAccount.save(null);

    function showError(message) {
        $('login-error').textContent = message;
        $('login-error').classList.toggle('hidden', !message);
    }

    $('login-form').addEventListener('submit', async function (event) {
        event.preventDefault();
        const user = $('login-user').value.trim().toLowerCase();
        const password = $('login-password').value;
        $('login-submit').disabled = true;
        showError('');
        try {
            const response = await fetch('/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ username: user, password: password }),
                credentials: 'same-origin',
            });
            if (response.status === 401) {
                showError('Utilisateur ou mot de passe incorrect.');
                return;
            }
            if (!response.ok) {
                showError('Connexion impossible (erreur ' + response.status + ').');
                return;
            }
            window.SyncAccount.save({ user: user, key: window.SyncAccount.md5(password) });
            window.location.replace($('login-next').value);
        } catch (error) {
            showError('Serveur injoignable : ' + error.message);
        } finally {
            $('login-submit').disabled = false;
        }
    });

    const darkIcon = $('theme-toggle-dark-icon');
    const lightIcon = $('theme-toggle-light-icon');
    (document.documentElement.classList.contains('dark') ? lightIcon : darkIcon).classList.remove('hidden');
    $('theme-toggle').addEventListener('click', function () {
        const dark = document.documentElement.classList.toggle('dark');
        darkIcon.classList.toggle('hidden', dark);
        lightIcon.classList.toggle('hidden', !dark);
        try { localStorage.setItem('color-theme', dark ? 'dark' : 'light'); } catch (e) { /* storage unavailable */ }
    });

    if (params.get('error')) showError('Utilisateur ou mot de passe incorrect.');
    lucide.createIcons();
})();
