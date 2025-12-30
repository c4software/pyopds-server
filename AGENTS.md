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
    * `opds.py` : Gestion du catalogue OPDS, scan des fichiers EPUB, génération XML.
    * `koreader_sync.py` : API de synchronisation pour KoReader (Authentification + Stockage SQLite).
* `static/` : Fichiers statiques (XSLT pour l'affichage navigateur).
* `tests/` : Tests unitaires (`unittest`).

### Ajout de fonctionnalités
1.  **Contrôleur :** Créer ou modifier une méthode dans une classe de contrôleur (`controllers/`).
2.  **Route :** Enregistrer l'URL et la méthode HTTP dans `register_routes` (`routes.py`).
3.  **Vue (OPDS) :** Si c'est une réponse XML, utiliser `xml.etree.ElementTree` pour construire la réponse.

## 3. Standards de Code

* **Typage :** Utiliser le typage statique moderne de Python 3.12 autant que possible (ex: `def func(a: int) -> list[str]:`).
* **Docstrings :** Chaque classe et méthode publique doit avoir une docstring explicative.
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

## 5. Workflow de Développement

1.  **Environnement Virtuel :**
    * Toujours travailler dans un `venv`.
    * Commande : `python -m venv .venv` puis activation.
2.  **Dépendances :**
    * Aucune installation via `pip` n'est nécessaire pour le runtime.
    * `pytest` peut être installé pour le développement.

## 6. Tests et Qualité

* **Exécution :** Lancer les tests avant toute soumission avec `pytest` ou `python -m unittest discover tests`.
* **Concurrence :** Les tests lancent un serveur HTTP réel dans un thread séparé. Assurez-vous de bien gérer la fermeture des sockets (`server_close`) dans le `tearDownClass`.
* **Couverture :** Tout nouveau endpoint API doit avoir un test correspondant dans `tests/test_opds.py` ou `tests/test_koreader_sync.py`.

## 7. Variables d'Environnement

L'application doit respecter ces variables de configuration :
* `LIBRARY_DIR` : Dossier racine des livres (défaut: `books`).
* `PORT` : Port d'écoute (défaut: `8080`).
* `KOREADER_SYNC_DB_PATH` : Chemin de la DB SQLite.
* `PAGE_SIZE` : Nombre de livres par page dans le flux OPDS.