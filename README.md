# PyOPDS Server - OPDS Server for EPUB Library

This project is a lightweight OPDS (Open Publication Distribution System) server written in Python 3, designed to expose a library of EPUB files stored in a local directory, compatible with clients like Calibre, KoReader, [CrossPoint](https://github.com/crosspoint-reader/crosspoint-reader) readers, or any other OPDS-compliant reader. It uses only Python's standard libraries.

It is also a reading progress sync server for KoReader and CrossPoint readers, compatible with the [CrossPoint Sync](https://github.com/crosspoint-reader/crosspoint-sync) app: library, reading statistics and clippings, with book details and covers taken from your EPUB library.

![Logo](./preview/ereader.jpeg)

If you browse your OPDS feed in a web browser, it will display a simple HTML representation of the feed using XSLT transformation.

![Home](./preview/preview.png)
![Book list sample](./preview/preview2.png)
![All books with pagination](./preview/preview3.png)

## Features

- **OPDS Catalog**: Exposes EPUB books via an OPDS feed accessible at the `/opds` endpoint.
- **OpenSearch**: Integrated search functionality compliant with OpenSearch specification, allowing users to search books by title, author or series.
- **Subdirectory Support**: Scans EPUB files in the configured directory and its subdirectories.
- **Sorted by Most Recent**: Books are listed in the OPDS feed sorted by modification date, with the most recently modified files appearing first.
- **Metadata Extraction**: Extracts title and author from EPUB files for rich display in the catalog.
- **Browse by Year, Author and Genre**: Genres come from the EPUB subjects (`dc:subject`); spelling variants are merged (SF, science-fiction...), technical tags and series names are ignored, and genres with fewer than 3 books are hidden. These rules live in [`genres.toml`](genres.toml) (see Configuration).
- **Book Covers**: Covers are extracted from the EPUB files and declared in the feed, so they show up in OPDS clients.
- **Book Details**: Series and volume, description, publication year and rating are read from the EPUBs (`calibre:series`, EPUB 3 collections, `dc:description`…) and shown in every book feed of the catalog (see [Book details in the feeds](#book-details-in-the-feeds)) and in the web UI book sheet, and served to the CrossPoint Sync app, including the next volume of a series. A series feed (`/opds/series?name=…`) lists the volumes of a series in order. Optional online lookups (Open Library, iTunes, Hardcover) fill the gaps for books outside the library.
- **Reading Sync API**: KOSync-compatible progress sync for KoReader and CrossPoint / CrossInk readers.
- **CrossPoint Sync Compatible**: Implements the [crosspoint-sync](https://github.com/crosspoint-reader/crosspoint-sync) extensions (`/api/v1`): the CrossPoint Sync app shows your library, reading statuses, statistics, activity and clippings, and CrossPoint readers sync rich positions and clippings.
- **Accounts**: the catalog is public; readers can sign in (or create an account) from the web UI or with HTTP Basic in their reading app, with the same account as the sync. `OPDS_AUTH=true` makes the whole catalog private.
- **« À lire ensuite » Recommendations**: once signed in, a tile and feed (`/opds/recommendations`) suggest unread books of the library from your reading sync history, computed locally.
- **Personal shelves**: once signed in, the root catalog starts with « En cours » (books being read) and « Suite de vos séries » (next volume of your series), and every book list can hide the books you finished (« Masquer les livres lus »).
- **Docker Deployment**: Prebuilt multi-arch image (`ghcr.io/c4software/pyopds-server`) published on each release, a versioned `docker-compose.yml` and a git-ignored override for your own settings.
- **Web Interface**: The OPDS feed rendered as a web page in browsers (XSLT transformation), with light/dark theme, search, sign-in / account creation and an optional animated mosaic of covers on the navigation tiles (see [Web interface](#web-interface)).
- **Web Reader**: « Lire » in the book sheet opens the EPUB in the browser (`/read`, [foliate-js](https://github.com/johnfactotum/foliate-js)), on phones (full screen, taps and swipes, screen kept on), tablets (two pages side by side in landscape) and computers (centered reading page). Once signed in, the position, the clippings and the reading time are those of your sync account: continue on KoReader or a CrossPoint reader where the browser stopped, and the other way round (see [Web reader](#web-reader)).
- **Installable**: the web UI is a web app (manifest, icons): « Add to Home Screen » / « Install » opens it like an app (HTTPS required by the browsers).
- **Reading Page**: `/reading` (chart icon in the web UI header) shows the books in progress per device with their last sync, finished and abandoned books, reading time, pages, a reading calendar, recent activity, devices and the latest highlights, with links to the matching books of the catalog; a synced book the server could not match can be associated by hand with a book of the library.

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

Machine-specific settings (library path, pinned version, reverse proxy, extra variables) go in a git-ignored `docker-compose.override.yml`, merged automatically by Compose: start from [`docker-compose.override.example.yml`](docker-compose.override.example.yml). Update with `docker compose pull && docker compose up -d`.

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

3. Build the web UI stylesheet once (see [Web UI styles](#web-ui-styles)):

   ```bash
   python tools/tailwind.py
   ```

4. Start the server:

   ```bash
   python server.py
   ```

5. Access the OPDS catalog at: `http://localhost:8080/opds`.

### Development (Docker, hot reload)

```bash
docker compose -f docker-compose.dev.yml up --build
```

The source is mounted into two containers built from the `dev` stage of the [`Dockerfile`](Dockerfile):

- `app` serves `http://localhost:8080` and restarts on every change of the Python code, `genres.toml` or `metadata.toml` ([watchfiles](https://watchfiles.helpmanual.io/)); EPUBs are read from `./books`, the sync database is kept in `./config`. Changes to `static/` (XSLT, pages, scripts) need no restart: reload the page.
- `css` rebuilds `static/tailwind.css` whenever Tailwind classes change in `static/` (Tailwind CLI `--watch`).

With rootful Docker, set `DEV_UID` / `DEV_GID` (e.g. in a `.env` file: `DEV_UID=1000`) so that the rebuilt stylesheet belongs to you; rootless Docker needs nothing.

### Releasing

Commits go to `main`; a version is published by pushing a tag (plain [semantic version](https://semver.org), no `v` prefix: `2.9.0`), which triggers the [`Docker image`](.github/workflows/docker.yml) workflow (image `latest` and `<version>`, amd64 and arm64):

```bash
python -m unittest discover tests        # tests must pass
git push                                 # commits on main
git tag 2.9.1 && git push origin 2.9.1   # new version: minor for a feature, patch for a fix
gh release create 2.9.1 --title 2.9.1 --notes "…"   # release notes on GitHub
gh run watch                             # wait for the image build
```

Then update the servers: `docker compose pull && docker compose up -d`.

## Configuration

The following environment variables can be set:

- **LIBRARY_DIR**: Path to the directory containing EPUB files (default: `books`; `/books` in the Docker image).
- **PORT**: Listening port (default: `8080`).
- **PAGE_SIZE**: Number of books per page in the OPDS feeds (default: `25`).
- **KOREADER_SYNC_DB_PATH**: Path to the SQLite database file used by the KoReader sync helper (default: `koreader_sync.db`).
- **REGISTRATION_DISABLED**: Set to `true` to refuse new accounts, from the readers as well as from the web sign-in page (default: registration open).
- **OPDS_AUTH**: Set to `true` to make the whole catalog and web UI private (default: public catalog, optional sign-in). See [Accounts and sign-in](#accounts-and-sign-in).
- **OPDS_SESSION_SECRET**: Key signing the browser sessions (default: a random key written once to `session.secret` next to the sync database).
- **GENRES_CONFIG_PATH**: Path to a custom genre file completing [`genres.toml`](genres.toml), with the same format: its subject-to-genre aliases and ignored subjects are added to the default ones (and win over them), and its `min_books` overrides the default threshold. Restart the server after editing it. Example (for instance in the mounted `config/` folder):

  ```toml
  ignored = ["my series", "release team"]

  [aliases]
  "Science-fiction" = ["cyberpunk"]
  ```
- **METADATA_CONFIG_PATH**: Path to a custom author and series file completing [`metadata.toml`](metadata.toml), with the same format. By default, "Last, First" creators become "First Last", names in capitals or lowercase get title case, a trailing role ("(Auteur)") is dropped and bogus series ("Novels") are ignored; author variants differing only by case or accents are listed once in "By Author". Add aliases for what these rules cannot fix. Restart the server after editing it. Example:

  ```toml
  [authors.aliases]
  "Robin Hobb" = ["Hobb Robin"]

  [series]
  ignored = ["camus"]

  [series.aliases]
  "L'Assassin royal" = ["Assasin Royal"]
  ```
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

## Usage with an OPDS Client

1. In an OPDS-compatible client (e.g., KoReader, Calibre, or the *Browse* tab of the CrossPoint Sync app):
   - Add a new catalog with the URL: `http://<your-ip>:8080/opds`.
2. Browse the book list at the `/opds/books` endpoint.
3. Download books via the links provided in the OPDS feed.

### Book details in the feeds

Every book entry (all books, recent, folders, years, authors, genres, search, « À lire ensuite », series) carries the metadata of its EPUB, read from the OPF only (no cover, no text) for the books of the current page, and cached per file modification time:

- `<summary type="text">`: the description (`dc:description`, HTML turned into text, paragraphs kept). In « À lire ensuite », the reasons of the suggestion come first, then a blank line and the description.
- `<category term="…" label="…"/>`: the genres (see « By Genre »).
- `<dc:issued>`: the publication year (`xmlns:dc="http://purl.org/dc/terms/"`).
- Series: a standard Atom link `<link rel="related" href="/opds/series?name=…" type="…kind=acquisition" title="Série : … (tome N)"/>` to the other volumes, plus `<pyopds:series index="N">name</pyopds:series>` and `<pyopds:rating>` (0-5) in the server's namespace `urn:pyopds:metadata`. OPDS 1.x has no element for series and rating and clients share no convention for them (Calibre's own feed only writes them as text), so other clients just ignore these two.
- Navigation from the book: titled `rel="related"` links to its author (`Auteur : …`) and to each of its genres (`Genre : …`), after the series one. The CrossPoint Sync app lists them under « More like this »; in the web UI book sheet, the author, the series and the genre tags are links.
- `GET /opds/series?name=<series>`: the library books of that series, sorted by volume (books without a volume last), series names compared ignoring case, accents and punctuation; 404 when no book has it.

Navigation entries only list the links clients need: the cover mosaic links of the web view (`rel="urn:pyopds:mosaic-cover"`) are only sent to browsers (`Accept: text/html`), never to OPDS clients, which may list every link of a navigation entry as a section.

### Web interface

Opening the catalog URL in a browser shows it as a web page: the browser turns the feed into HTML with [`static/opds_to_html.xslt`](static/opds_to_html.xslt), so browsers and OPDS clients read the very same feed. Browsers without native XSLT (Chrome removes it in version 158, November 2026) use [xslt-polyfill](https://github.com/mfreed7/xslt_polyfill), the XSLT engine compiled to WebAssembly (BSD-3, `static/xslt-polyfill.min.js`): every feed references it, it is only downloaded when native XSLT is missing (1.5 MB, cached for a year), and OPDS clients ignore it. Styles come from a compiled Tailwind stylesheet (see [Web UI styles](#web-ui-styles)), so they also work with the polyfill.

The header holds the search field and these buttons:

- **Home**, **Reading page** (chart icon, see [Reading page](#reading-page-web-ui)) and **Refresh** (rescans the library and clears the caches).
- **Se connecter** / **logout**: optional sign-in (see below), which shows the « À lire ensuite » tile.
- **Cover mosaic** (image icon): fills the navigation tiles (collections, authors, years, genres, folders) with an animated mosaic of the covers of their books, under a veil that keeps the text readable. It is off by default and remembered per browser. Covers are only requested while it is on; small thumbnails, books without a cover and the covers last shown on each tile are cached in the browser (IndexedDB, 7 days), so tiles fill at once on the next visits.
- **Theme**: light or dark, remembered per browser.

A click on a book opens its **book sheet** (a dialog closed with Escape, a click outside or the close button) instead of downloading it: large cover, title, author (link, with a person icon, to all the books of the author), « Tome N · Série » (link to the series feed), year, rating (stars), genres (tag chips linking to the genre feeds), description (« Lire la suite » when long), a **Lire** button (the [web reader](#web-reader)) and a **Télécharger** button. A middle click or Ctrl/⌘-click still downloads the book, as does a click without JavaScript; the address of an opened sheet (`#book=…`) can be shared. Series books show their volume (« T2 ») on their tile.

**Files.** The XSLT only holds the page structure; the page code lives next to it:

| File | Role |
| --- | --- |
| `static/opds_to_html.xslt` | Feed → HTML: header, tiles, pagination, book sheet markup (plus a tiny inline script setting the dark theme before the first paint) |
| `static/catalog/catalog.css` | Styles Tailwind utilities cannot express: tile colors, cover fade-in, mosaic, book sheet dialog, rating stars (served as is, after `tailwind.css`) |
| `static/catalog/main.js` | Entry point: starts every feature below (loaded last) |
| `static/partials/nav.html`, `static/nav.js` | Menu shared by every screen (catalog, reading, clippings, sign-in): catalog, statistics, clippings, sign-in / logout, light/dark theme |
| `static/catalog/back-button.js` | « Retour » link to the parent feed |
| `static/catalog/search.js` | Search query pre-fill, full-width search bar on phones |
| `static/catalog/covers.js` | Lazy loading of the book covers |
| `static/catalog/mosaic.js` | Cover mosaic of the navigation tiles and its button |
| `static/catalog/cover-cache.js` | Browser cache of the mosaic (IndexedDB thumbnails, covers last shown) |
| `static/catalog/pagination.js` | Numbered pagination, first/last page links |
| `static/catalog/book-sheet.js` | Book sheet dialog, `#book=` links |

They are plain scripts sharing a small `window.PyOPDS` namespace, loaded in order by `<script src>` tags at the end of the page: this runs the same with native XSLT and with the polyfill (which re-creates the scripts of the result one after the other, then dispatches `DOMContentLoaded`). The server adds `?v=<modification time>` to every `/static/…` URL of the XSLT it serves (as for the other pages): versioned files are cached for a year, and a deployment changes their URLs, so browsers and caching proxies never mix old and new scripts. Files of `static/` and of one level of sub-folder (`static/catalog/`) are served; Tailwind classes used in these scripts are picked up by `python tools/tailwind.py`.

### Accounts and sign-in

The catalog is **public**: anyone can browse and download. Signing in is optional and enables what depends on the reader, the « À lire ensuite » recommendations (`/opds/recommendations` always requires an account). With `OPDS_AUTH=true` the whole catalog is private: `/`, every `/opds…` feed (search, OpenSearch, refresh included), `/download/…`, `/opds_to_html.xslt` and `/reading`.

The accounts are the **sync accounts** (created from the web UI, KoReader, a CrossPoint reader or the CrossPoint Sync app): the server checks the MD5 of the password against the stored sync key. UTF-8 passwords are supported.

- **OPDS clients** (KoReader, CrossPoint readers, the CrossPoint Sync app) use HTTP Basic (`WWW-Authenticate: Basic realm="PyOPDS", charset="UTF-8"`): enter the user name and password in the catalog settings.
- **Browsers** use the sign-in page `/login` (the « Se connecter » button of the header, or a redirect when an account is required). It also **creates accounts** (unless `REGISTRATION_DISABLED`). One sign-in opens a single session (signed `HttpOnly` cookie `pyopds_sid`, 90 days, plus the readable `pyopds_name`) for the catalog, the reading page, the clippings page and the sync API calls of the pages (`/api/v1` accepts the session when no `x-auth-*` header is sent); the header shows a logout button (`/logout`). The cookies are never `Secure`, so the same session works whether the site is opened over HTTP or HTTPS. The `/reading` and `/clippings` pages require it (redirect to `/login`). The signing key is `OPDS_SESSION_SECRET` when set, otherwise a random key written once to `session.secret` next to the sync database.

Left public on purpose: the page assets `/static/…` (no library data), the whole sync API (KOSync at the root and under `/koreader/sync`, `/api/v1/…`, `/healthz`), which keeps its own `x-auth-user` / `x-auth-key` authentication (or the web UI session for the pages), `/cover/…` (the CrossPoint Sync app shows covers with plain image requests) and the `/health` monitoring probe. Basic authentication sends the password in clear text: use HTTPS (reverse proxy) when exposing the server.

### « À lire ensuite » recommendations

Once signed in, the root catalog shows an **« À lire ensuite »** tile linking to the acquisition feed `/opds/recommendations`: up to 30 books of the library **you never opened**, suggested from your reading sync history and computed locally (no third-party service):

- books read (marked finished or read to 90 % or more) or in progress (paused ones count less) whose synced document matches a library EPUB build your profile; abandoned books (`dnf`) are ignored;
- each candidate scores for the **same author**, **shared genres** (see « By Genre ») and the **next volume of a series** you started (when the series metadata of the EPUBs is available); recent reads weigh more than old ones;
- each entry's summary explains why it is suggested.

Results are cached for 5 minutes per user and refreshed as soon as a new sync arrives. With an empty history, the feed is empty with an explanatory subtitle. Browsers without an account see the « Se connecter » button instead of the tile; OPDS clients always see the tile, and opening it asks for their credentials (HTTP Basic).

### Personal shelves and « Masquer les livres lus »

For a signed-in reader, the root catalog starts with the reader's tiles, in this order: **« En cours »**, **« Suite de vos séries »**, then « À lire ensuite ». They come from the reading sync, matched to the library EPUBs (`controllers/reading_state.py`, shared with the recommendations):

- `/opds/reading` (« En cours »): library books being read (neither finished, paused nor abandoned), most recently synced first; each summary starts with the progress (« Lu à 42 % »).
- `/opds/series-next` (« Suite de vos séries »): for each series with a volume read, in progress or paused, the next library volume you never opened (whole-numbered volumes first, like `/documents/{document}/next`).

Both feeds always require an account; like « À lire ensuite », OPDS clients without credentials see the tiles and opening them asks for the account.

Every book list (all books, recent, folders, years, authors, genres, series, search) carries, for a signed-in reader, the OPDS facets `Tous les livres` / `Masquer les livres lus` (`rel="http://opds-spec.org/facet"`, `opds:facetGroup="Livres lus"`, `opds:activeFacet` on the current one). They reopen the list with `?read=show|hide`; the choice is remembered for the reader (all lists, every client) until changed. Readers listing facets after the entries (CrossPoint) show them as rows; the web UI shows them above the list.

## Sync API (KoReader, CrossPoint)

The server includes a KOSync-compatible progress sync server, plus the CrossPoint Sync extensions (`/api/v1`) used by CrossPoint / CrossInk readers and the CrossPoint Sync app. Every endpoint is available both at the server root and under `/koreader/sync`.

Data is stored in a local SQLite database (`KOREADER_SYNC_DB_PATH`). Databases created by older versions are migrated automatically on the first sync request; back the file up before upgrading.

### KOSync endpoints

- `POST /users/create`, `GET /users/auth`: account creation and login (`x-auth-user` / `x-auth-key` headers, key = MD5 of the password).
- `PUT /syncs/progress`: stores a device's position. Each device keeps its own record; the most recent one wins.
- `GET /syncs/progress/{document}`: newest position of a book across devices.
- `GET /healthz`: health probe.

Errors, status codes and timestamps follow the reference KOSync server.

### Configuring the readers

- **KoReader**: *Tools → Progress sync → Custom sync server* = `http://<your-ip>:8080/koreader/sync`.
- **KoReader highlights** (KOSync only syncs positions): *Export highlights → Choose formats and services → Nextcloud Notes → Setup*, with the server address as host (`http://<your-ip>:8080`, no trailing `/`), the sync account's user name and password, and any category; then *Export all notes in this book* (or from the file browser, all books). The server emulates the Nextcloud Notes API (`/index.php/apps/notes/api/v1/notes`, HTTP Basic): each highlight of the note (text, chapter, page, date, note) becomes a clipping of the book, matched by title to the library EPUB (then to the synced book), shown by the CrossPoint Sync app and the reading page. Exporting a book again adds new highlights, updates edited ones and deletes those removed on the reader; a clipping deleted in the app does not come back.
- **CrossPoint / CrossInk**: *Settings → KOReader Sync → Sync Server URL* = `http://<your-ip>:8080/koreader/sync` (or the root URL). Choose the *CrossPoint* (or *Other*) server type to sync rich positions; enable clippings sync in the same settings.
- **CrossPoint Sync app** (*Self-hosted*): `http://<your-ip>:8080`. Library, statuses, reading dates, merge of duplicate copies, reading statistics and activity, clippings and account management work. When a synced book matches an EPUB of the library (KoReader content hash, file name hash or file name), its title, author, cover and an estimated print page count come from the library.

### CrossPoint Sync extensions (`/api/v1`)

- Progress: `PUT /progress`, `PUT /progress/batch`, `GET /progress`, `GET /progress/changes`, `GET|DELETE /progress/{document}`.
- Documents: `PUT|GET /documents`, `PUT /documents/{document}/status|dates|info`, `GET /documents/{document}/cover|cover/candidates|about|next|matches`, `POST /documents/merge`, `DELETE /documents/merge/{alias}`.
- Statistics: `PUT|GET /stats/global`, `PUT /stats/books`, `GET /stats/books/{document}`, `GET /stats/summary`, `GET /stats/activity?tz=`.
- Clippings and bookmarks: `GET /clippings`, `GET|PUT /clippings/{document}`, `GET|PUT /bookmarks/{document}`.
- Account: `POST /account/password`, `DELETE /account/data`, `DELETE /account`.

`GET /api/v1/progress` also returns `library_path`, the path of the matching library EPUB (or `null`), used by the reading page for its catalog and download links. When the hashes match no EPUB (file changed since, or sent another way), `PUT /api/v1/documents/{document}/info` accepts `library_path` (relative to `LIBRARY_DIR`, `null` to remove) to associate the document by hand: the reading page offers it with « Associer à un livre », and `library_linked` is then `true`.

Book metadata is provided locally, without any third-party service, from the library EPUB (OPF) matching the synced document:

- `GET /api/v1/progress`: `series` and `series_position` (`calibre:series` / `calibre:series_index`, or the EPUB 3 `belongs-to-collection` of type `series` with its `group-position`), `release_year` (`dc:date`) and `rating` (`calibre:rating`, 0-5). For a book missing from the library, the series and series index sent by CrossPoint readers with their progress (server type *Other*) are used; the ISBN they send is stored too.
- `GET /api/v1/documents/{document}/about`: the EPUB's `dc:description`, HTML converted to plain text (paragraphs and line breaks kept, cut at about 6000 characters).
- `GET /api/v1/documents/{document}/next`: the next volume of the series found in the library (series names compared ignoring case, accents and punctuation; whole-numbered volumes preferred over novellas like 1.5), with its absolute cover URL. The app's « Find it in your catalogs » button then finds it by title with the OPDS search.

When `ONLINE_METADATA` / `HARDCOVER_API_KEY` are set (see Configuration), online lookups fill what the library leaves empty: cover, page count, description, Hardcover moods, genres, content warnings, rating, series and next-in-series.

The connectors of crosspoint-sync (Hardcover, Spotify, Readwise... progress sync) are not provided: `GET /api/v1/connectors` returns an empty list and the app hides those features.

### Web reader

`/read?path=<EPUB relative to LIBRARY_DIR>` (« Lire » in the book sheet, « Continuer la lecture » on the reading page, « Lire » on the clippings page) reads a library EPUB in the browser with [foliate-js](https://github.com/johnfactotum/foliate-js) (MIT, vendored in `static/foliate/`: `view.js`, `epub.js`, `paginator.js`… from commit `78914ae`, unchanged). Same access rule as `/download` (public unless `OPDS_AUTH=true`).

- **Phones**: the book fills the screen (full screen on the first tap where the browser allows it, the bottom bar button leaves it); a tap on the left / right third turns the page (swipes too) and hides the bars, a tap in the middle or in the top / bottom band (even on a link) shows or hides them (back, table of contents, position slider, clippings, display settings, full screen). The screen stays on while reading (Screen Wake Lock, HTTPS only; released after 10 minutes without a page turned).
- **Tablets and computers**: two pages side by side when the screen is wide and in landscape, one column in portrait; on a computer the reading page keeps a limited width, with arrows, keys (←/→, Space, Page Up/Down) and the mouse wheel.
- **Display settings** (kept in the browser): text size, line spacing, font (the book's, serif, sans), margins, justification, theme (auto, light, sepia, dark), pages or scrolling.
- **Not signed in**: reading only, the last position is kept in the browser.
- **Signed in** (the sync account, see [Accounts and sign-in](#accounts-and-sign-in)):
  - **Position**: sent like a reader does (`PUT /api/v1/progress`, a few seconds after each page and when the page is left), as a KoReader XPointer (`/body/DocFragment[3]/body/section[1]/p[10]/text()[1].20`, the position CrossPoint readers also use first) and a CrossPoint rich position (`pctQ`, spine, page, paragraph). The browser is a device of its own (« Navigateur (Android) »…). On opening, the newest position of all the devices is used, with a notice (« Reprise à 72 %, position de CrossPoint ») and a way back; back on the tab, a newer position of another device is offered.
  - **Clippings**: select text to highlight it (4 colors) or add a note; a tap on a highlight edits or deletes it. They are CrossPoint clippings (`/api/v1/clippings/{document}`: spine index, visible offsets of the selection in its chapter, paragraph, chapter title), so the readers syncing clippings, the CrossPoint Sync app, the book sheet and `/clippings` show them; the clippings of the readers are drawn in the book (KoReader's Nextcloud Notes exports are found by their text).
  - **Statistics**: the time the page is visible while you read (pauses of more than 3 minutes excluded) and the pages turned are added to the statistics of the browser's sync device, the one of its position (`POST /webui/reader/session`, every minute and when the page is left): reading time, sessions, time of day, weekday, reading days, pace and finished books on the reading page.
- `GET /webui/reader/book?path=…` lists the EPUB entries (`{path, title, author, filename, version, entries: {name: size}, document}`; `document` is the signed-in reader's sync document of the EPUB) and `GET /webui/reader/file?path=…&entry=…&v=<version>` serves one entry (chapters gzip-compressed, cached by the browser): the EPUB is never downloaded whole.

### Clippings page and book sheet (web UI)

The clippings synced by the readers (CrossPoint « Sync Clippings », KoReader's Nextcloud Notes export) are shown to the signed-in reader (catalog session; the routes always require an account):

- in the catalog **book sheet**, « Vos surlignages »: the clippings of the book in reading order (text, note, chapter, page, date), with its Markdown export;
- on the **`/clippings` page** (highlighter icon of the catalog header, « Tous les surlignages » on the reading page): every clipping grouped by book, newest book first, with a search field (passage, note, chapter, title, author), a book filter and the Markdown export;
- `GET /webui/clippings[?path=<EPUB relative to LIBRARY_DIR>]` returns `{groups: [{book: {document, title, author, path}, clippings: [{id, text, note, chapter, page, created_at}]}]}`; `GET /webui/clippings/export[?path=…]` the same as a Markdown file (`## Title — Author`, each clipping quoted, then its note and `*chapter · p. N*`).

### Reading page (web UI)

`/reading` is a static page (`static/reading.html`, `reading.js`, `reading.css`) that calls the sync API from the browser with the web UI session: progress (`/api/v1/progress`, and `/api/v1/progress/{document}` for the per-device positions), `/api/v1/stats/summary`, `/api/v1/stats/activity` and `/api/v1/clippings`. It uses the common sign-in page and the single web UI session. Reading time, time of day and weekday charts need readers that upload statistics (CrossPoint); with KoReader alone, pages and reading days come from the progress history.

A synced book that matches no EPUB of the library (shown by its document hash, e.g. a file changed since it was downloaded, or sent to the reader another way) has an **« Associer à un livre »** button: search the catalog and pick the book; its title, cover, pages and links then come from that EPUB (« Dissocier » removes the association).

The reading and sign-in pages are written with [Alpine.js](https://alpinejs.dev) (3.17.4, pinned, loaded from jsDelivr, no build step): the templates are in the HTML (`x-for`, `x-text`…), the scripts only call the API and compute the view data (`Alpine.data('reading')`, `Alpine.data('login')`); `static/theme.js` sets the light/dark class before the first paint; the menu (`static/partials/nav.html`, `static/nav.js`) is the same as the catalog's.

The page assets are served with a version (`/static/reading.js?v=<modification time>`), so that a reverse proxy caching static files never serves an outdated script after an update.

## Web UI styles

The web UI uses [Tailwind CSS](https://tailwindcss.com) v3, compiled into `static/tailwind.css` (not versioned) by the official [standalone CLI](https://tailwindcss.com/blog/standalone-cli), which needs no Node.js:

```bash
python tools/tailwind.py          # build static/tailwind.css (minified)
python tools/tailwind.py --watch  # rebuild on every change of static/
```

[`tools/tailwind.py`](tools/tailwind.py) downloads the pinned CLI version once (checked against its SHA-256) into `.cache/`, then runs it with [`tailwind.config.js`](tailwind.config.js) (dark mode by class, classes scanned in `static/*.xslt|html|js`, `static/catalog/*.js` and `static/reader/*.js`) and [`styles/tailwind.css`](styles/tailwind.css). Extra arguments go to the Tailwind CLI. The Docker image builds the stylesheet itself (`css` stage of the Dockerfile), and the development compose file rebuilds it on every change.

## Duplicate books

A command line tool finds the duplicate EPUBs of the library (`LIBRARY_DIR`), and can remove them:

```bash
python -m controllers.duplicates                         # report only
python -m controllers.duplicates --json                  # report as JSON
python -m controllers.duplicates --move-to ../doublons   # move the extra copies out of the library
python -m controllers.duplicates --delete                # delete the extra copies
# In the Docker image
docker exec cops python -m controllers.duplicates --move-to /config/doublons
```

- **Identical files** (same SHA-256), wherever they are stored.
- **Same book**: different files whose metadata give the same title, author (word order, case, accents and punctuation ignored: "Asimov, Isaac" = "Isaac Asimov") and series position, with texts of similar lengths (`--identical-only` skips them).
- **To check**: same metadata but texts of very different lengths (one volume of a book published in several, abridged edition…): reported, never removed.

In each group one file is kept: the one already read on a reader (known to the sync database, so its progress stays attached to a library book), then the one with a cover, a readable file name containing the title (not garbled by a wrong encoding), metadata, the largest file, the oldest. Folders left empty are removed. `--move-to` must be outside the library, otherwise the copies would still appear in the catalog.

## Fixing metadata (genres, authors, titles)

A command line tool lists the books whose metadata need fixing in a CSV file, then writes the completed file back into the EPUBs:

```bash
python -m controllers.metadata_fix export livres.csv            # books to fix, with genre suggestions
python -m controllers.metadata_fix export livres.csv --online   # suggestions from Open Library too
python -m controllers.metadata_fix apply livres.csv             # check the completed file (dry run)
python -m controllers.metadata_fix apply livres.csv --write     # write into the EPUBs
# In the Docker image (the library volume must be writable)
docker exec cops python -m controllers.metadata_fix export /config/livres.csv
docker exec cops python -m controllers.metadata_fix apply /config/livres.csv --write && docker restart cops
```

`export` lists the books with no genre shown in the catalog (none, or only subjects that are not genres of `genres.toml`), a missing author ("Unknown", "Inconnu"…) or a title to clean ("untitled", "(French Edition)", ".epub"…). The `suggestion` column holds the genres of the author's other books (the genres of at least 40 % of them, for authors with 3 books or more); with `--online`, Open Library is searched for the others (only the title and author are sent).

The CSV is UTF-8, comma separated, with a header line; rows may be reordered or left empty, columns must stay:

| Column | Content |
|---|---|
| `chemin` | Path relative to the library: the key of the row, **do not change it** |
| `probleme` | What is wrong (`aucun genre`, `genre non affiché…`, `auteur manquant`, `titre à nettoyer`), read only |
| `titre`, `auteur`, `serie`, `tome`, `annee`, `description` | Current metadata (description cut at 500 characters), read only |
| `suggestion` | Suggested genres, read only (used by `apply --accept-suggestions` when `genres` is empty) |
| `genres` | **To fill**: 1 to 3 genres of `genres.toml` (or their aliases: "SF", "Polar"…), separated by `;` |
| `titre_corrige` | Optional: the title alone, without volume number, year or publisher mention |
| `auteur_corrige` | Optional: "First Last", several authors separated by ` & ` |

An empty cell changes nothing. `apply` reports the unknown genres and missing files, then (with `--write`) adds the genres as `dc:subject` and replaces `dc:title` / `dc:creator` in the OPF only: the other entries are kept, the result is read back before replacing the file, and a calibre series named after a genre ("Thriller", which would hide the subject of the same name) is removed. The file modification time is kept, so the books do not come back in "Recent Books": **restart the server** to read the new metadata. A rewritten EPUB gets a new KOReader document hash; the server still finds the books synced by their file name. Back up the library first.

## Security Consideration

This server is designed to be lightweight and does not include HTTPS support; the catalog is public unless `OPDS_AUTH=true`, and passwords travel in clear text over plain HTTP. For production use, it is strongly recommended to place the server behind a reverse proxy (e.g., SWAG, Nginx, or Nginx Proxy Manager).

## License

This project is licensed under the MIT License. See the `LICENSE` file for details (to be added if necessary).
