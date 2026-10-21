// Sign-in / sign-up page: opens the catalog session (cookie set by POST /login) and keeps the sync key
// for the reading page, so that one sign-in serves both. Accounts are created with POST /users/create,
// the same account then syncs KoReader / CrossPoint.
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const params = new URLSearchParams(window.location.search);
    const registrationOpen = $('account-section').dataset.registration === 'open';
    let signUp = false;

    // Only local paths, never another site
    const next = params.get('next');
    $('login-next').value = next && next.charAt(0) === '/' && next.charAt(1) !== '/' && next.charAt(1) !== '\\' ? next : '/opds';
    $('back-link').href = $('login-next').value;

    // Signed out (or session expired): forget the sync key of the reading page too
    window.SyncAccount.save(null);

    function showError(message) {
        $('login-error').textContent = message;
        $('login-error').classList.toggle('hidden', !message);
    }

    function setMode(creating) {
        signUp = creating;
        $('form-title').textContent = creating ? 'Créer un compte' : 'Se connecter';
        $('form-intro').textContent = creating
            ? 'Votre compte vous donne des suggestions de lecture et vos statistiques. Utilisez les mêmes identifiants dans KoReader ou CrossPoint pour synchroniser votre progression.'
            : 'Retrouvez vos suggestions de lecture et vos statistiques. Le même compte synchronise votre progression sur KoReader et CrossPoint.';
        $('login-submit').textContent = creating ? 'Créer le compte' : 'Se connecter';
        $('mode-question').textContent = creating ? 'Déjà un compte ?' : 'Pas encore de compte ?';
        $('mode-toggle').textContent = creating ? 'Se connecter' : 'Créer un compte';
        $('confirm-field').classList.toggle('hidden', !creating);
        $('login-confirm').required = creating;
        $('login-password').autocomplete = creating ? 'new-password' : 'current-password';
        showError('');
    }

    async function createAccount(user, password) {
        const response = await fetch('/users/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ username: user, password: window.SyncAccount.md5(password) }),
        });
        if (response.status === 201) return null;
        const data = await response.json().catch(function () { return null; });
        if (data && data.code === 2002) return 'Ce nom d\'utilisateur est déjà pris.';
        if (data && /disabled/i.test(data.message || '')) return 'La création de comptes est désactivée sur ce serveur.';
        return 'Nom d\'utilisateur invalide (lettres, chiffres et . _ @ + - uniquement).';
    }

    async function openSession(user, password) {
        const response = await fetch('/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ username: user, password: password }),
            credentials: 'same-origin',
        });
        if (response.status === 401) return 'Utilisateur ou mot de passe incorrect.';
        if (!response.ok) return 'Connexion impossible (erreur ' + response.status + ').';
        return null;
    }

    $('login-form').addEventListener('submit', async function (event) {
        event.preventDefault();
        const user = $('login-user').value.trim().toLowerCase();
        const password = $('login-password').value;
        if (signUp && password !== $('login-confirm').value) {
            showError('Les mots de passe ne correspondent pas.');
            return;
        }
        $('login-submit').disabled = true;
        showError('');
        try {
            const error = (signUp && await createAccount(user, password)) || await openSession(user, password);
            if (error) {
                showError(error);
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

    $('mode-toggle').addEventListener('click', function () { setMode(!signUp); });
    $('mode-switch').classList.toggle('hidden', !registrationOpen);

    const darkIcon = $('theme-toggle-dark-icon');
    const lightIcon = $('theme-toggle-light-icon');
    (document.documentElement.classList.contains('dark') ? lightIcon : darkIcon).classList.remove('hidden');
    $('theme-toggle').addEventListener('click', function () {
        const dark = document.documentElement.classList.toggle('dark');
        darkIcon.classList.toggle('hidden', dark);
        lightIcon.classList.toggle('hidden', !dark);
        try { localStorage.setItem('color-theme', dark ? 'dark' : 'light'); } catch (e) { /* storage unavailable */ }
    });

    setMode(registrationOpen && params.get('signup') === '1');
    if (params.get('error')) showError('Utilisateur ou mot de passe incorrect.');
    lucide.createIcons();
})();
