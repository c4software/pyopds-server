// Sign-in / sign-up page: the `login` Alpine component (template in login.html) opens the web UI session
// (cookie set by POST /login), the single sign-in of the catalog, the pages and their sync API calls.
// Accounts are created with POST /users/create, the same account then syncs KoReader / CrossPoint.
(function () {
    'use strict';

    const TEXTS = {
        signIn: {
            title: 'Se connecter',
            intro: 'Retrouvez vos suggestions de lecture et vos statistiques. Le même compte synchronise votre progression sur KoReader et CrossPoint.',
            submit: 'Se connecter',
            question: 'Pas encore de compte ?',
            toggle: 'Créer un compte',
        },
        signUp: {
            title: 'Créer un compte',
            intro: 'Votre compte vous donne des suggestions de lecture et vos statistiques. Utilisez les mêmes identifiants dans KoReader ou CrossPoint pour synchroniser votre progression.',
            submit: 'Créer le compte',
            question: 'Déjà un compte ?',
            toggle: 'Se connecter',
        },
    };

    // Only local paths, never another site
    function safeNext(next) {
        return next && next.charAt(0) === '/' && next.charAt(1) !== '/' && next.charAt(1) !== '\\' ? next : '/opds';
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

    document.addEventListener('alpine:init', function () {
        window.Alpine.data('login', function () {
            const params = new URLSearchParams(window.location.search);
            return {
                registrationOpen: false,
                signUp: false,
                busy: false,
                error: '',
                next: safeNext(params.get('next')),

                get text() {
                    return this.signUp ? TEXTS.signUp : TEXTS.signIn;
                },

                init() {
                    // Rewritten by the server (REGISTRATION_DISABLED)
                    this.registrationOpen = document.getElementById('account-section').dataset.registration === 'open';
                    this.signUp = this.registrationOpen && params.get('signup') === '1';
                    if (params.get('error')) this.error = 'Utilisateur ou mot de passe incorrect.';
                    this.$nextTick(function () { window.lucide.createIcons(); });
                },

                toggleMode() {
                    this.signUp = !this.signUp;
                    this.error = '';
                },

                // Fields read on submit (not x-model): browser autofill does not always send input events
                async submit() {
                    const user = this.$refs.user.value.trim().toLowerCase();
                    const password = this.$refs.password.value;
                    if (this.signUp && password !== this.$refs.confirm.value) {
                        this.error = 'Les mots de passe ne correspondent pas.';
                        return;
                    }
                    this.busy = true;
                    this.error = '';
                    try {
                        const error = (this.signUp && await createAccount(user, password)) || await openSession(user, password);
                        if (error) {
                            this.error = error;
                            return;
                        }
                        window.location.replace(this.next);
                    } catch (error) {
                        this.error = 'Serveur injoignable : ' + error.message;
                    } finally {
                        this.busy = false;
                    }
                },
            };
        });
    });
})();
