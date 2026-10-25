# Installation and configuration

## Prerequisites

- **Python 3.12+** (if running without Docker).
- **Docker** and **Docker Compose** (for containerized deployment).
- A directory containing EPUB files (e.g., `books/`).

## Installation

### With Docker

1. Clone the repository:

   ```bash
   git clone https://github.com/c4software/pyopds-server.git
   cd pyopds-server
   ```

2. Create a `books/` directory and place your EPUB files in it (e.g., `books/author1/book.epub`).
3. Start the server with Docker Compose:

   ```bash
   docker compose up -d          # prebuilt image (see below)
   docker compose up -d --build  # or built from this checkout
   ```

4. Access the OPDS catalog at: `http://localhost:8080/opds`.

The prebuilt image (amd64, arm64) is published by GitHub Actions on each release tag to `ghcr.io/c4software/pyopds-server`, as `latest` and as the tag version (e.g. `2.4.0`):

```bash
docker run -p 8080:8080 -v ./books:/books ghcr.io/c4software/pyopds-server:latest
```

Machine-specific settings (library path, pinned version, reverse proxy, extra variables) go in a git-ignored `docker-compose.override.yml`, merged automatically by Compose: start from [`docker-compose.override.example.yml`](../docker-compose.override.example.yml). Update with `docker compose pull && docker compose up -d`.

### Without Docker

1. Clone the repository:

   ```bash
   git clone https://github.com/c4software/pyopds-server.git
   cd pyopds-server
   ```

2. Set the environment variable for the books directory:

   ```bash
   export LIBRARY_DIR=/path/to/your/books
   ```

3. Build the web UI stylesheet once (see [Web UI styles](development.md#web-ui-styles)):

   ```bash
   python tools/tailwind.py
   ```

4. Start the server:

   ```bash
   python server.py
   ```

5. Access the OPDS catalog at: `http://localhost:8080/opds`.

## Configuration

The following environment variables can be set:

- **LIBRARY_DIR**: Path to the directory containing EPUB files (default: `books`; `/books` in the Docker image).
- **PORT**: Listening port (default: `8080`).
- **PAGE_SIZE**: Number of books per page in the OPDS feeds (default: `25`).
- **KOREADER_SYNC_DB_PATH**: Path to the SQLite database file used by the KoReader sync helper (default: `koreader_sync.db`).
- **REGISTRATION_DISABLED**: Set to `true` to refuse new accounts, from the readers as well as from the web sign-in page (default: registration open).
- **OPDS_AUTH**: Set to `true` to make the whole catalog and web UI private (default: public catalog, optional sign-in). See [Accounts and sign-in](#accounts-and-sign-in).
- **MINIMAL_MODE**: Set to `true` for a minimal server: the OPDS catalog and its web view (XSLT: browsing, search, book sheet, download) only. The sync APIs (KOSync, CrossPoint Sync, Nextcloud Notes), the reading and clippings pages, the web reader, the reader's feeds and tiles (« En cours », « Suite de vos séries », « À lire ensuite », « Masquer les livres lus », « Marquer comme lu ») and the account creation answer 404 or disappear from the web view; the stored data is kept for when the mode is turned off. With `OPDS_AUTH=true` the sign-in stays, with the existing accounts (default: full server).
- **LIBRARY_DB_PATH**: SQLite file holding the metadata of the library books (default: `library.db` next to the sync database). A disposable cache: deleting it only costs one full read of the library.
- **LIBRARY_SCAN_INTERVAL**: Seconds between two automatic library syncs (default: `14400`, 4 hours; `0`: only at startup and with the « Recharger » button). See [Library metadata](catalog.md#library-metadata).
- **OPDS_SESSION_SECRET**: Key signing the browser sessions (default: a random key written once to `session.secret` next to the sync database).
- **GENRES_CONFIG_PATH**: Path to a custom genre file completing [`genres.toml`](../genres.toml), with the same format: its subject-to-genre aliases and ignored subjects are added to the default ones (and win over them), and its `min_books` overrides the default threshold. Restart the server after editing it. Example (for instance in the mounted `config/` folder):

  ```toml
  ignored = ["my series", "release team"]

  [aliases]
  "Science-fiction" = ["cyberpunk"]
  ```
- **METADATA_CONFIG_PATH**: Path to a custom author and series file completing [`metadata.toml`](../metadata.toml), with the same format. By default, "Last, First" creators become "First Last", names in capitals or lowercase get title case, a trailing role ("(Auteur)") is dropped and bogus series ("Novels") are ignored; author variants differing only by case or accents are listed once in "By Author". Add aliases for what these rules cannot fix. Restart the server after editing it. Example:

  ```toml
  [authors.aliases]
  "Robin Hobb" = ["Hobb Robin"]

  [series]
  ignored = ["camus"]

  [series.aliases]
  "L'Assassin royal" = ["Assasin Royal"]
  ```
- **AUDIOBOOK_CONFIG_PATH**: Path to a file completing [`audiobook.toml`](../audiobook.toml) (same keys, its values win) to turn on the audiobook of the web reader (« Écouter »): an OpenAI-compatible text-to-speech server, its models, the default voice and reading tone, the cloned voices. Off by default. See [Audiobook](audiobook.md#settings) for every setting and an example.
- **ONLINE_METADATA**: Set to `true` to look up, without any API key, the cover ([iTunes Search](https://performance-partners.apple.com/search-api), then [Open Library](https://openlibrary.org/developers/api)), print page count and description (Open Library) of synced books the library does not provide (default: disabled).
- **HARDCOVER_API_KEY**: [Hardcover](https://hardcover.app/account/api) API key (`read:catalog` is enough) to add moods, genres, content warnings, community rating, series and position, release year and description to synced books, and the next book of a series (`GET /api/v1/documents/{document}/next`) (default: disabled). Requests are throttled (1.1 s apart, 4,000 a day, 10 min pause after a 429).

  Online lookups are only a fallback: they run for books that match no EPUB of the library, and their results only fill fields the library and the reader left empty. Only the book's **title, author and ISBN** are sent to those services (with a `PyOPDS-Server` User-Agent), never anything about the user. They run in a background thread (at most 10 books per `GET /api/v1/progress`), never while answering a request, except the cover picker (`GET /api/v1/documents/{document}/cover/candidates`) which searches live. Results are cached for every user in the `catalog_cache` table of the sync database (30 days; misses 7 days for Open Library, 30 days for Hardcover; series 7 days).

For Docker, set these variables in `docker-compose.override.yml` (see above); the base `docker-compose.yml` already mounts `./books` and `./config` (sync database, custom genres and metadata rules, session key). For example:

```yaml
services:
  cops:
    # Pin a version instead of following `latest`
    image: ghcr.io/c4software/pyopds-server:2.4.0
    volumes:
      - /srv/epub:/books        # replaces the ./books volume of the base file
    environment:
      - REGISTRATION_DISABLED=true
      - ONLINE_METADATA=true
```

Check the merged configuration with `docker compose config`.

## Connecting OPDS clients

1. In an OPDS-compatible client (e.g., KoReader, Calibre, or the *Browse* tab of the CrossPoint Sync app):
   - Add a new catalog with the URL: `http://<your-ip>:8080/opds`.
2. Browse the book list at the `/opds/books` endpoint.
3. Download books via the links provided in the OPDS feed.

## Configuring the readers (sync)

- **KoReader**: *Tools → Progress sync → Custom sync server* = `http://<your-ip>:8080/koreader/sync`.
- **KoReader highlights** (KOSync only syncs positions): *Export highlights → Choose formats and services → Nextcloud Notes → Setup*, with the server address as host (`http://<your-ip>:8080`, no trailing `/`), the sync account's user name and password, and any category; then *Export all notes in this book* (or from the file browser, all books). The server emulates the Nextcloud Notes API (`/index.php/apps/notes/api/v1/notes`, HTTP Basic): each highlight of the note (text, chapter, page, date, note) becomes a clipping of the book, matched by title to the library EPUB (then to the synced book), shown by the CrossPoint Sync app and the reading page. Exporting a book again adds new highlights, updates edited ones and deletes those removed on the reader; a clipping deleted in the app does not come back.
- **CrossPoint / CrossInk**: *Settings → KOReader Sync → Sync Server URL* = `http://<your-ip>:8080/koreader/sync` (or the root URL). Choose the *CrossPoint* (or *Other*) server type to sync rich positions; enable clippings sync in the same settings.
- **CrossPoint Sync app** (*Self-hosted*): `http://<your-ip>:8080`. Library, statuses, reading dates, merge of duplicate copies, reading statistics and activity, clippings and account management work. When a synced book matches an EPUB of the library (KoReader content hash, file name hash or file name), its title, author, cover and an estimated print page count come from the library.

## Accounts and sign-in

The catalog is **public**: anyone can browse and download. Signing in is optional and enables what depends on the reader, the « À lire ensuite » recommendations (`/opds/recommendations` always requires an account). With `OPDS_AUTH=true` the whole catalog is private: `/`, every `/opds…` feed (search, OpenSearch, refresh included), `/download/…`, `/opds_to_html.xslt` and `/reading`.

The accounts are the **sync accounts** (created from the web UI, KoReader, a CrossPoint reader or the CrossPoint Sync app): the server checks the MD5 of the password against the stored sync key. UTF-8 passwords are supported.

- **OPDS clients** (KoReader, CrossPoint readers, the CrossPoint Sync app) use HTTP Basic (`WWW-Authenticate: Basic realm="PyOPDS", charset="UTF-8"`): enter the user name and password in the catalog settings.
- **Browsers** use the sign-in page `/login` (the « Se connecter » button of the header, or a redirect when an account is required). It also **creates accounts** (unless `REGISTRATION_DISABLED`). One sign-in opens a single session (signed `HttpOnly` cookie `pyopds_sid`, 90 days, plus the readable `pyopds_name`) for the catalog, the reading page, the clippings page and the sync API calls of the pages (`/api/v1` accepts the session when no `x-auth-*` header is sent); the header shows a logout button (`/logout`). The cookies are never `Secure`, so the same session works whether the site is opened over HTTP or HTTPS. The `/reading` and `/clippings` pages require it (redirect to `/login`). The signing key is `OPDS_SESSION_SECRET` when set, otherwise a random key written once to `session.secret` next to the sync database.

Left public on purpose: the page assets `/static/…` (no library data), the whole sync API (KOSync at the root and under `/koreader/sync`, `/api/v1/…`, `/healthz`), which keeps its own `x-auth-user` / `x-auth-key` authentication (or the web UI session for the pages), `/cover/…` (the CrossPoint Sync app shows covers with plain image requests) and the `/health` monitoring probe. Basic authentication sends the password in clear text: use HTTPS (reverse proxy) when exposing the server.

## Security

This server is designed to be lightweight and does not include HTTPS support; the catalog is public unless `OPDS_AUTH=true`, and passwords travel in clear text over plain HTTP. For production use, it is strongly recommended to place the server behind a reverse proxy (e.g., SWAG, Nginx, or Nginx Proxy Manager).
