# Sync API (KoReader, CrossPoint)

The server includes a KOSync-compatible progress sync server, plus the CrossPoint Sync extensions (`/api/v1`) used by CrossPoint / CrossInk readers and the CrossPoint Sync app. Every endpoint is available both at the server root and under `/koreader/sync`.

Data is stored in a local SQLite database (`KOREADER_SYNC_DB_PATH`). Databases created by older versions are migrated automatically on the first sync request; back the file up before upgrading.

## KOSync endpoints

- `POST /users/create`, `GET /users/auth`: account creation and login (`x-auth-user` / `x-auth-key` headers, key = MD5 of the password).
- `PUT /syncs/progress`: stores a device's position. Each device keeps its own record; the most recent one wins.
- `GET /syncs/progress/{document}`: newest position of a book across devices.
- `GET /healthz`: health probe.

Errors, status codes and timestamps follow the reference KOSync server.

## CrossPoint Sync extensions (`/api/v1`)

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
