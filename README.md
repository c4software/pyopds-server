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
- **OpenSearch**: Integrated search functionality compliant with OpenSearch specification, allowing users to search books by title or author.
- **Subdirectory Support**: Scans EPUB files in the configured directory and its subdirectories.
- **Sorted by Most Recent**: Books are listed in the OPDS feed sorted by modification date, with the most recently modified files appearing first.
- **Metadata Extraction**: Extracts title and author from EPUB files for rich display in the catalog.
- **Browse by Year, Author and Genre**: Genres come from the EPUB subjects (`dc:subject`); spelling variants are merged (SF, science-fiction...), technical tags and series names are ignored, and genres with fewer than 3 books are hidden. These rules live in [`genres.toml`](genres.toml) (see Configuration).
- **Book Covers**: Covers are extracted from the EPUB files and declared in the feed, so they show up in OPDS clients.
- **Reading Sync API**: KOSync-compatible progress sync for KoReader and CrossPoint / CrossInk readers.
- **CrossPoint Sync Compatible**: Implements the [crosspoint-sync](https://github.com/crosspoint-reader/crosspoint-sync) extensions (`/api/v1`): the CrossPoint Sync app shows your library, reading statuses, statistics, activity and clippings, and CrossPoint readers sync rich positions and clippings.
- **Docker Deployment**: Includes a `Dockerfile` and `docker-compose.yml` for easy containerized deployment.
- **Visual HTML Representation**: Provides a simple HTML view of the OPDS feed for easy browsing in web browsers (using XSLT transformation).
- **Reading Page**: `/reading` (chart icon in the web UI header) signs in with the sync account and shows the books in progress per device with their last sync, finished and abandoned books, reading time, pages, a reading calendar, recent activity, devices and the latest highlights, with links to the matching books of the catalog.

## Prerequisites

- **Python 3.12+** (if running without Docker).
- **Docker** and **Docker Compose** (for containerized deployment).
- A directory containing EPUB files (e.g., `books/`).

## Installation

### With Docker

1. Clone the repository:

   ```bash
   git clone https://github.com/your-username/your-repo.git
   cd your-repo
   ```

2. Create a `books/` directory and place your EPUB files in it (e.g., `books/author1/book.epub`).
3. Start the server with Docker Compose:

   ```bash
   docker-compose up --build
   ```

4. Access the OPDS catalog at: `http://localhost:8080/opds`.

A prebuilt image (amd64, arm64) is also published on each release tag to `ghcr.io/c4software/pyopds-server` (`latest` and the tag version):

```bash
docker run -p 8080:8080 -v ./books:/books ghcr.io/c4software/pyopds-server:latest
```

### Without Docker

1. Clone the repository:

   ```bash
   git clone https://github.com/your-username/your-repo.git
   cd your-repo
   ```

2. Set the environment variable for the books directory:

   ```bash
   export LIBRARY_DIR=/path/to/your/books
   ```

3. Start the server:

   ```bash
   python server.py
   ```

4. Access the OPDS catalog at: `http://localhost:8080/opds`.

## Configuration

The following environment variables can be set:

- **LIBRARY_DIR**: Path to the directory containing EPUB files (default: `books`).
- **KOREADER_SYNC_DB_PATH**: Path to the SQLite database file used by the KoReader sync helper (default: `koreader_sync.db`).
- **REGISTRATION_DISABLED**: Set to `true` to refuse new sync accounts (default: registration open).
- **GENRES_CONFIG_PATH**: Path to a custom genre file completing [`genres.toml`](genres.toml), with the same format: its subject-to-genre aliases and ignored subjects are added to the default ones (and win over them), and its `min_books` overrides the default threshold. Restart the server after editing it. Example (for instance in the mounted `config/` folder):

  ```toml
  ignored = ["my series", "release team"]

  [aliases]
  "Science-fiction" = ["cyberpunk"]
  ```

For Docker, modify these variables in the `docker-compose.yml` file:

```yaml
services:
  opds-server:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./books:/books
      - ./config:/config
    environment:
      - LIBRARY_DIR=/books
      - KOREADER_SYNC_DB_PATH=/config/koreader_sync.db
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 5s
```

## Usage with an OPDS Client

1. In an OPDS-compatible client (e.g., KoReader, Calibre, or the *Browse* tab of the CrossPoint Sync app):
   - Add a new catalog with the URL: `http://<your-ip>:8080/opds`.
2. Browse the book list at the `/opds/books` endpoint.
3. Download books via the links provided in the OPDS feed.

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
- **CrossPoint / CrossInk**: *Settings → KOReader Sync → Sync Server URL* = `http://<your-ip>:8080/koreader/sync` (or the root URL). Choose the *CrossPoint* (or *Other*) server type to sync rich positions; enable clippings sync in the same settings.
- **CrossPoint Sync app** (*Self-hosted*): `http://<your-ip>:8080`. Library, statuses, reading dates, merge of duplicate copies, reading statistics and activity, clippings and account management work. When a synced book matches an EPUB of the library (KoReader content hash, file name hash or file name), its title, author, cover and an estimated print page count come from the library.

### CrossPoint Sync extensions (`/api/v1`)

- Progress: `PUT /progress`, `PUT /progress/batch`, `GET /progress`, `GET /progress/changes`, `GET|DELETE /progress/{document}`.
- Documents: `PUT|GET /documents`, `PUT /documents/{document}/status|dates|info`, `GET /documents/{document}/cover|cover/candidates|about|next|matches`, `POST /documents/merge`, `DELETE /documents/merge/{alias}`.
- Statistics: `PUT|GET /stats/global`, `PUT /stats/books`, `GET /stats/books/{document}`, `GET /stats/summary`, `GET /stats/activity?tz=`.
- Clippings and bookmarks: `GET /clippings`, `GET|PUT /clippings/{document}`, `GET|PUT /bookmarks/{document}`.
- Account: `POST /account/password`, `DELETE /account/data`, `DELETE /account`.

`GET /api/v1/progress` also returns `library_path`, the path of the matching library EPUB (or `null`), used by the reading page for its catalog and download links. When the hashes match no EPUB (file changed since, or sent another way), `PUT /api/v1/documents/{document}/info` accepts `library_path` (relative to `LIBRARY_DIR`, `null` to remove) to associate the document by hand: the reading page offers it with « Associer à un livre », and `library_linked` is then `true`.

External services of crosspoint-sync (Hardcover, Spotify, Readwise... connectors, online cover and description lookups) are not provided: `GET /api/v1/connectors` returns an empty list and the app hides those features.

### Reading page (web UI)

`/reading` is a static page (`static/reading.html`, `reading.js`, `reading.css`) that calls the sync API from the browser with the `x-auth-user` / `x-auth-key` headers: progress (`/api/v1/progress`, and `/api/v1/progress/{document}` for the per-device positions), `/api/v1/stats/summary`, `/api/v1/stats/activity` and `/api/v1/clippings`. The user name and the MD5 of the password are kept in the browser's `localStorage` until you sign out. Reading time, time of day and weekday charts need readers that upload statistics (CrossPoint); with KoReader alone, pages and reading days come from the progress history.

## Security Consideration

This server is designed to be lightweight and does not include built-in authentication or HTTPS support. For production use, it is strongly recommended to place the server behind a reverse proxy (e.g., SWAG, Nginx, or Nginx Proxy Manager).

## License

This project is licensed under the MIT License. See the `LICENSE` file for details (to be added if necessary).
