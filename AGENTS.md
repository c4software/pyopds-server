# Instructions pour les Agents (AGENTS.md)

Ce document définit les règles, l'architecture et les standards de développement pour le projet **PyOPDS Server**. 

## 0. But du projet

Le but est de créer un serveur OPDS pour permettre la lecture de livres EPUB sur des lecteurs mobiles comme KoReader.

Le serveur propose :

- Un catalogue OPDS
- Une interface web pour visualiser le catalogue (via transformation XSLT du flux OPDS)
- Une API de synchronisation pour KoReader

## 1. Principes Fondamentaux

* **Zéro Dépendance Externe :** Ce projet ne doit utiliser **QUE** la bibliothèque standard de Python (Python 3.12+).
    * 🚫 Pas de Flask, Django, FastAPI.
    * 🚫 Pas de SQLAlchemy (utiliser `sqlite3` directement).
    * 🚫 Pas de `lxml` (utiliser `xml.etree.ElementTree`).
    * 🚫 Pas de `requests` (utiliser `urllib` et `http.client`).
* **Compatibilité :** Le code doit rester compatible avec les clients OPDS standards (Calibre, KoReader, applications de lecture).
* **Performance :** Le serveur doit rester léger. Le chargement des métadonnées des livres (EPUB) doit être efficace (mise en cache si nécessaire).

## 2. Architecture et Organisation

Le projet suit une architecture de type MVC (Modèle-Vue-Contrôleur) simplifiée et faite maison.

### Structure des fichiers
* `server.py` : Point d'entrée. Configure le `TCPServer` et le `UnifiedHandler`. Ne contient pas de logique métier.
* `routes.py` : Système de routage inspiré de Laravel. C'est ici que **toutes** les nouvelles routes URL doivent être déclarées.
* `controllers/` : Contient la logique métier.
    * `http_io.py` : Plomberie HTTP commune aux contrôleurs : lecture du corps (`read_body`, `read_json`, taille bornée) et envoi des réponses (`send_body`, `redirect`). Aucun contrôleur n'écrit ses en-têtes à la main (seul le téléchargement d'EPUB est envoyé en flux).
    * `opds.py` : Gestion du catalogue OPDS, scan des fichiers EPUB, génération XML (entrées de livres enrichies : résumé, genres, année, série via `LibraryIndex.book_meta`).
    * `koreader_sync.py` : API KOSync (KoReader, liseuses CrossPoint) et classe de base commune des contrôleurs de synchronisation.
    * `crosspoint_sync.py` : Extensions CrossPoint Sync (`/api/v1`) utilisées par les liseuses CrossPoint et l'app CrossPoint Sync.
    * `nextcloud_notes.py` : Émulation de l'API Nextcloud Notes (`/index.php/apps/notes/api/v1/notes`, HTTP Basic du compte de synchro) : les surlignages exportés par KoReader (export « Nextcloud Notes », Markdown par livre) deviennent des clippings du livre retrouvé par son titre.
    * `sync_storage.py` : Stockage SQLite de la synchronisation (progression par appareil, documents, alias, stats, clippings).
    * `reading_activity.py` : Calculs purs des statistiques et de l'activité de lecture.
    * `genres.py` : Normalisation des sujets EPUB (`dc:subject`) en genres pour la navigation « By Genre », selon les règles de `genres.toml`.
    * `metadata_rules.py` : Normalisation des noms d'auteurs et de séries lus dans les EPUB, selon les règles de `metadata.toml`.
    * `library_index.py` : Correspondance entre les hash de documents KoReader et les EPUB de la bibliothèque, métadonnées légères cachées par mtime (`book_meta`) et index des séries (`series_books`, `next_in_series`).
    * `opds_auth.py` : Comptes du catalogue : middleware des routes `auth='opds'` (lecteur identifié si des identifiants sont fournis, obligatoire si `OPDS_AUTH=true`) et `auth='user'` (compte toujours requis), HTTP Basic ou cookie de session ; page de connexion / création de compte `/login` et `/logout` des navigateurs.
    * `reading_state.py` : État de lecture d'un lecteur sur la bibliothèque (historique de synchro → EPUB : lus, en cours…), avec cache par utilisateur ; partagé par les recommandations et les flux personnels.
    * `reader_feeds.py` : Flux OPDS personnels (`/opds/reading` « En cours », `/opds/series-next` « Suite de vos séries »), tuiles en tête du catalogue et facette « Masquer les livres lus » (préférence du lecteur) appliquée aux listes de livres.
    * `recommendations.py` : Recommandations « À lire ensuite » locales (fonctions de scoring pures + service avec cache par utilisateur).
    * `online_metadata.py` : Détails de livres en ligne optionnels (iTunes, Open Library, Hardcover) en repli de la bibliothèque : cache SQLite `catalog_cache` partagé, recherches dans un thread de fond (jamais dans le chemin d'une requête, sauf `cover/candidates`).
    * `duplicates.py` : Outil en ligne de commande (`python -m controllers.duplicates`, aussi dans l'image Docker) de détection des EPUB en double (fichiers identiques, même livre selon les métadonnées), avec suppression (`--delete`) ou déplacement hors bibliothèque (`--move-to`).
    * `metadata_fix.py` : Outil en ligne de commande (`python -m controllers.metadata_fix export|apply`) de correction des métadonnées par CSV : export des livres sans genre affiché, sans auteur ou au titre à nettoyer (avec suggestions de genres : profil de l'auteur, Open Library avec `--online`), application du CSV complété dans l'OPF des EPUB (simulation sans `--write`, date de modification conservée).
    * `clippings.py` : Surlignages du lecteur connecté dans la webui (fiche livre, page `/clippings` avec recherche, export Markdown) : regroupement par livre et Markdown en fonctions pures, `ClippingsController` (sous-classe de `WebUIController`).
    * `web_reader.py` : Lecteur web `/read` (foliate-js) : entrées de l'EPUB servies une à une (`/webui/reader/book`, `/webui/reader/file`), temps de lecture ajouté aux statistiques de l'appareil de synchro du navigateur, celui de sa position (`POST /webui/reader/session`, fonctions pures de cumul ; les statistiques de l'ancien appareil commun « Lecteur web » passent au premier navigateur qui envoie les siennes). La position (XPointer KoReader + position riche CrossPoint) et les surlignages (décalages visibles CrossPoint) passent par `/api/v1`, appelée par la page.
    * `features.py` : Parties optionnelles du serveur (`MINIMAL_MODE`, `minimal_mode()`, `accounts_enabled()`) et marqueurs des fragments HTML qui les décrivent (`page_flags`).
    * `webui.py` : Pages statiques de l'interface web hors flux OPDS (page « Lecture » `/reading`), fichiers `/static/...` (y compris `/static/catalog/...`), versionnés par `?v=`, et statut « lu » d'un livre du catalogue pour le lecteur connecté (`GET`/`PUT /webui/book-status`, bouton « Marquer comme lu » de la fiche livre).
* `metadata.toml` : Normalisation des auteurs (« Nom, Prénom » → « Prénom Nom », casse, alias) et des séries (séries ignorées, alias), appliquée à l'extraction OPF (complétable via `METADATA_CONFIG_PATH`).
* `genres.toml` : Correspondances sujets → genres, sujets ignorés et seuil d'affichage (complétable via `GENRES_CONFIG_PATH`).
* `static/` : Fichiers statiques (XSLT pour l'affichage navigateur, pages `reading`, `clippings` et `login`, `account.js` (compte et session), `page-kit.js` (helpers communs aux pages et au catalogue : dates, liens d'un EPUB, appels JSON, icônes), `nav.js` (menu commun), `theme.js` (classe `dark` avant le premier rendu des pages), `xslt-polyfill.min.js` vendorisé ; `tailwind.css` est généré, non versionné).
    * `static/partials/` : fragments HTML inclus côté serveur dans les pages (`<!--#include nom clé="valeur" -->`, `include_partials` de `controllers/webui.py`) : menu commun à tous les écrans (`nav.html`, inclus aussi dans le XSLT par `serve_xslt` : XML bien formé, sans directive Alpine), en-tête des pages, carte d'un surlignage. Tout balisage commun à plusieurs pages va dans un fragment ; le menu est le même partout (catalogue, statistiques, surlignages, compte, thème), les boutons propres à un écran se placent avant lui.
    * `static/partials/app.html` : balises PWA (manifeste `static/manifest.webmanifest`, icônes `static/icons/`), incluses dans le `<head>` de chaque écran (pages et XSLT).
    * `static/reader.html`, `static/reader/` : lecteur web (composant Alpine `reader` dans `reader.js`, un script par fonctionnalité : `positions.js` (xpointers et décalages comptés comme le firmware CrossPoint), `book.js`, `settings.js`, `sync.js`, `stats.js`, `wake.js` (écran maintenu allumé pendant la lecture), `highlights.js`, styles `reader.css`).
    * `static/foliate/` : foliate-js vendorisé (MIT, modules ES chargés par `import()`, fichiers non modifiés : ne pas les éditer).
    * `static/catalog/` : scripts (un par fonctionnalité, `main.js` en point d'entrée) et feuille `catalog.css` de la webui du catalogue, chargés par le XSLT.
* `tailwind.config.js`, `styles/tailwind.css`, `tools/tailwind.py` : génération de `static/tailwind.css` (outil de développement, jamais requis par le serveur).
* `Dockerfile` : étapes `css` (compile la feuille de style), `dev` (rechargement à chaud) et image de production ; `docker-compose.dev.yml` pour le développement.
* `tests/` : Tests unitaires (`unittest`).

### Ajout de fonctionnalités
1.  **Contrôleur :** Créer ou modifier une méthode dans une classe de contrôleur (`controllers/`).
2.  **Route :** Enregistrer l'URL et la méthode HTTP dans `register_routes` (`routes.py`).
3.  **Vue (OPDS) :** Si c'est une réponse XML, utiliser `xml.etree.ElementTree` pour construire la réponse.

## 3. Standards de Code

* **Typage :** Utiliser le typage statique moderne de Python 3.12 autant que possible (ex: `def func(a: int) -> list[str]:`).
* **Docstrings :** Chaque classe et méthode publique doit avoir une docstring explicative.
* **Modularité :** code découpé en modules par fonctionnalité (pas de contrôleur fourre-tout). Aucun code dupliqué : dès qu'un bout de code se répète (Python, JS, HTML, XSLT), il devient un helper, une constante, un module ou un fragment partagé.
* **Gestion des erreurs :**
    * Ne jamais laisser le serveur crasher.
    * Utiliser `_send_error` ou `_send_json_error` dans les contrôleurs pour renvoyer des codes HTTP appropriés (400, 404, 500).
* **Sécurité :**
    * Toujours vérifier les chemins de fichiers pour éviter les attaques par traversée de répertoire (`SecurityUtils.has_path_traversal`).
    * Ne jamais exposer de fichiers hors du `LIBRARY_DIR`.

## 4. Spécificités Techniques

### Base de Données (KoReader Sync)
* Utiliser `sqlite3` avec le gestionnaire de contexte `with self._get_connection() as conn:`.
* Le fichier DB est défini par la variable d'environnement `KOREADER_SYNC_DB_PATH`.
* Les requêtes doivent utiliser des paramètres liés (`?`) pour éviter les injections SQL.

### Génération OPDS (XML)
* Utiliser `xml.etree.ElementTree`.
* Les flux doivent inclure l'espace de noms Atom (`http://www.w3.org/2005/Atom`).
* Toujours inclure le lien vers la feuille de style XSLT pour l'affichage navigateur : `<?xml-stylesheet type="text/xsl" href="/opds_to_html.xslt"?>`.

### Routage
* Le routeur est "custom". Il supporte les Regex.
* Format : `router.get(r'/url/pattern', (ControllerClass, 'method_name'), name='route.name')`.
* Toute route qui n'est pas le catalogue ou sa vue web (synchro, données du lecteur, nouvelles pages) se déclare dans un bloc `with router.full_only():` (coupée par `MINIMAL_MODE`).
* Les routes du catalogue et de la webui prennent `auth='opds'` (catalogue public, privé si `OPDS_AUTH=true`) ; celles qui dépendent du lecteur prennent `auth='user'` (compte requis). L'utilisateur identifié est exposé dans `request_handler.opds_user` (`None` sinon).

### Interface web (XSLT côté client)
* Le même flux sert tous les clients : les navigateurs le transforment en HTML avec `static/opds_to_html.xslt` (instruction `<?xml-stylesheet?>`). Chrome 158+ n'a plus de XSLT natif : chaque flux embarque `<script xmlns="http://www.w3.org/1999/xhtml" src="/static/xslt-polyfill.min.js?v=…"/>` (polyfill WebAssembly, ignoré quand le XSLT natif existe et par les clients OPDS). Ne pas retirer ce script de `generate_feed`.
* Les scripts de la page doivent fonctionner aussi avec le polyfill : ne pas dépendre uniquement de `DOMContentLoaded` (tester `document.readyState`).
* **Pas de JS ni de CSS inline dans le XSLT** (seule exception : le petit script du `<head>` qui pose la classe `dark` avant le premier rendu). Le code de la page va dans `static/catalog/` : un fichier par fonctionnalité (`mosaic.js`, `book-sheet.js`, `pagination.js`…), scripts classiques (pas de modules) qui s'enregistrent dans `window.PyOPDS` avec une fonction `init`, commentaire d'en-tête décrivant leur rôle ; `main.js` (chargé en dernier) les initialise. Ajouter un script = une balise `<script src="/static/catalog/….js"></script>` avant celle de `main.js` (sans `defer`/`async`/`type="module"` : l'ordre d'exécution ne serait plus garanti avec le polyfill) et son appel dans `main.js`. Les styles que Tailwind ne sait pas exprimer vont dans `static/catalog/catalog.css` (chargé après `tailwind.css`).
* **Versionnement automatique :** `serve_xslt` et les pages HTML ajoutent `?v=<mtime>` aux URL `/static/…` (`version_asset_refs`, `controllers/webui.py`) ; ne jamais écrire de `?v=` à la main. `/static/<nom>` et `/static/<dossier>/<nom>` (un seul niveau) sont servis, avec extensions autorisées et sans `..` ni nom commençant par un point.
* Les styles viennent de `static/tailwind.css`, compilé par le CLI Tailwind autonome via `python tools/tailwind.py` (pas de CDN : il ne s'applique pas correctement avec le polyfill). Après avoir ajouté des classes Tailwind dans `static/` (y compris `static/catalog/*.js`), relancer `python tools/tailwind.py` (ou travailler avec `docker compose -f docker-compose.dev.yml up`, qui le fait à chaque changement). L'image Docker le compile elle-même.
* Pages statiques (`static/reading.html`, `static/clippings.html`, `static/login.html`, `static/reader.html`) : **Alpine.js** (version épinglée exacte depuis jsDelivr, sans build). Gabarits déclaratifs dans le HTML (`x-for`, `x-if`, `x-text` pour toute donnée de l'API, jamais `x-html`), un composant `Alpine.data(...)` par page dans le JS, enregistré sur `alpine:init` (scripts `defer` chargés avant Alpine) ; le JS ne construit pas de HTML. Icônes lucide : `<i data-lucide>` sans directive Alpine (sauf `:data-lucide` stable), `lucide.createIcons()` rappelé dans `$nextTick` après chaque rendu (`PageKit.icons`). Pas de code dupliqué entre pages ou scripts : un helper commun dans `account.js` / `page-kit.js` (chargés aussi par le catalogue), un fragment dans `static/partials/`.
* Lecteur web : les objets foliate-js (vue, livre) ne sont jamais placés dans les données réactives d'Alpine (ses proxys cassent leurs champs privés) ; positions et décalages doivent rester compatibles avec KoReader et le firmware CrossPoint (`static/reader/positions.js`). Sync, statistiques et surlignages seulement pour un lecteur connecté.
* `wants_html()` (`controllers/opds_auth.py`) distingue navigateur et client OPDS (`Accept: text/html` ou en-têtes `Sec-Fetch-*`) : les ajouts propres à la webui (liens de mosaïque) ne sont envoyés qu'aux navigateurs.

## 5. Workflow de Développement

1.  **Environnement Virtuel :**
    * Toujours travailler dans un `venv`.
    * Commande : `python -m venv .venv` puis activation.
2.  **Dépendances :**
    * Aucune installation via `pip` n'est nécessaire pour le runtime.
    * `pytest` peut être installé pour le développement.
3.  **Feuille de style :** `python tools/tailwind.py` (une fois, puis à chaque nouvelle classe Tailwind) ou `--watch`.
4.  **Docker de développement :** `docker compose -f docker-compose.dev.yml up --build` : serveur relancé à chaque modification Python (watchfiles), CSS recompilé à chaque modification de `static/`.

5.  **Push et version :** commits sur `main` (`git push`), sans ligne d'attribution dans les messages. Une version = un tag poussé, au format semver sans préfixe (`git tag 2.9.1 && git push origin 2.9.1`, mineure pour une fonctionnalité, patch pour un correctif), suivi de `gh release create <version> --title <version> --notes "…"` (notes en français). Le tag déclenche `.github/workflows/docker.yml`, qui publie `ghcr.io/c4software/pyopds-server:latest` et `:<version>` ; attendre la fin du build (`gh run watch`) avant de mettre à jour un serveur (`docker compose pull && docker compose up -d`). Tests au vert avant tout tag.

## 6. Tests et Qualité

* **Exécution :** Lancer les tests avant toute soumission avec `pytest` ou `python -m unittest discover tests`.
* **Concurrence :** Les tests lancent un serveur HTTP réel dans un thread séparé. Assurez-vous de bien gérer la fermeture des sockets (`server_close`) dans le `tearDownClass`.
* **Couverture :** Tout nouveau endpoint API doit avoir un test correspondant dans `tests/test_opds.py`, `tests/test_koreader_sync.py` ou `tests/test_crosspoint_sync.py`.

## 7. Variables d'Environnement

L'application doit respecter ces variables de configuration :
* `LIBRARY_DIR` : Dossier racine des livres (défaut: `books`).
* `PORT` : Port d'écoute (défaut: `8080`).
* `KOREADER_SYNC_DB_PATH` : Chemin de la DB SQLite.
* `REGISTRATION_DISABLED` : `true` pour refuser la création de comptes de synchronisation.
* `OPDS_AUTH` : `true` pour rendre privés le catalogue et la webui (routes `auth='opds'` ; synchro, `/cover/*`, `/static/*` et `/health` restent publics). Par défaut le catalogue est public et la connexion facultative (HTTP Basic pour les clients OPDS, page `/login` et cookie de session pour les navigateurs, une seule connexion pour le catalogue, les pages Lecture et Surlignages et leurs appels à `/api/v1` ; cookies jamais `Secure`, le site pouvant être ouvert en HTTP comme en HTTPS) ; elle active la tuile/flux « À lire ensuite » (`/opds/recommendations`).
* `MINIMAL_MODE` : `true` pour ne garder que le catalogue OPDS et sa vue web XSLT (`controllers/features.py`) : routes déclarées dans un bloc `with router.full_only():` (synchro, pages et données du lecteur, lecteur web, notes Nextcloud) en 404, flux et tuiles personnels, filtre « Masquer les livres lus » et création de comptes coupés ; la vue web masque les entrées du lecteur (marqueurs `{{display}}`, `data-mode`, `data-accounts` du menu, `PageKit.features()` / `PageKit.reader()` côté JS). Avec `OPDS_AUTH=true`, la connexion reste possible avec les comptes existants.
* `OPDS_SESSION_SECRET` : Clé de signature des cookies de session (par défaut, clé aléatoire écrite dans `session.secret` à côté de la DB de synchro).
* `GENRES_CONFIG_PATH` : Fichier TOML de genres complétant `genres.toml` (mêmes clés, prioritaire).
* `METADATA_CONFIG_PATH` : Fichier TOML de règles auteurs/séries complétant `metadata.toml` (mêmes clés, prioritaire).
* `PAGE_SIZE` : Nombre de livres par page dans le flux OPDS.
* `ONLINE_METADATA` : `true` pour chercher en ligne, sans clé, couverture (iTunes puis Open Library), nombre de pages imprimées et description (Open Library) des livres synchronisés absents de la bibliothèque (désactivé par défaut).
* `HARDCOVER_API_KEY` : Clé API Hardcover pour ajouter ambiances, genres, avertissements, note, série/position, année et description, et le livre suivant de la série (désactivé par défaut ; débit limité à 1 requête/1,1 s, 4000/jour, pause de 10 min après un 429).
    * Ces services ne reçoivent que le titre, l'auteur et l'ISBN du livre, jamais d'information sur l'utilisateur. Ils ne remplissent que les champs laissés vides par la bibliothèque et le lecteur.
