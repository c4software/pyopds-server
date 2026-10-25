# Catalog and library

## Library metadata

The metadata of the library books (title, author, date, genres, series, description, rating, KoReader document hash) are stored in a SQLite file, `library.db` (`LIBRARY_DB_PATH`), the single cache of the catalog. A **library sync** walks `LIBRARY_DIR`, compares each EPUB's date and size with the database, reads only the new or modified ones and drops the missing ones; every list, index (years, authors, genres, series), the recent books, the search and the readers' tiles then come from that state, in memory. A sync runs:

- at startup (a restart reads the database: no EPUB opened);
- every `LIBRARY_SCAN_INTERVAL` seconds (default 4 hours);
- with the « Recharger » button of the web UI (`POST /webui/library/sync`, signed-in readers; anyone on a server without accounts), which answers `{added, updated, removed, total, seconds, at}`;
- in the background when a listed book is missing on disk.

The database is emptied by itself (then read again from the EPUBs) when `LIBRARY_DIR` or the rules of [`genres.toml`](../genres.toml) / [`metadata.toml`](../metadata.toml) change.

## Book details in the feeds

Every book entry (all books, recent, folders, years, authors, genres, search, « À lire ensuite », series) carries the metadata of its EPUB, read from its OPF once and stored (see [Library metadata](#library-metadata)):

- `<summary type="text">`: the description (`dc:description`, HTML turned into text, paragraphs kept). In « À lire ensuite », the reasons of the suggestion come first, then a blank line and the description.
- `<category term="…" label="…"/>`: the genres (see « By Genre »).
- `<dc:issued>`: the publication year (`xmlns:dc="http://purl.org/dc/terms/"`).
- Series: a standard Atom link `<link rel="related" href="/opds/series?name=…" type="…kind=acquisition" title="Série : … (tome N)"/>` to the other volumes, plus `<pyopds:series index="N">name</pyopds:series>` and `<pyopds:rating>` (0-5) in the server's namespace `urn:pyopds:metadata`. OPDS 1.x has no element for series and rating and clients share no convention for them (Calibre's own feed only writes them as text), so other clients just ignore these two.
- Navigation from the book: titled `rel="related"` links to its author (`Auteur : …`) and to each of its genres (`Genre : …`), after the series one. The CrossPoint Sync app lists them under « More like this »; in the web UI book sheet, the author, the series and the genre tags are links.
- `GET /opds/series?name=<series>`: the library books of that series, sorted by volume (books without a volume last), series names compared ignoring case, accents and punctuation; 404 when no book has it.

Navigation entries only list the links clients need: the cover mosaic links of the web view (`rel="urn:pyopds:mosaic-cover"`) are only sent to browsers (`Accept: text/html`), never to OPDS clients, which may list every link of a navigation entry as a section.

## « À lire ensuite » recommendations

Once signed in, the root catalog shows an **« À lire ensuite »** tile linking to the acquisition feed `/opds/recommendations`: up to 30 books of the library **you never opened**, suggested from your reading sync history and computed locally (no third-party service):

- books read (marked finished or read to 90 % or more) or in progress (paused ones count less) whose synced document matches a library EPUB build your profile; abandoned books (`dnf`) are ignored;
- each candidate scores for the **same author**, **shared genres** (see « By Genre ») and the **next volume of a series** you started (when the series metadata of the EPUBs is available); recent reads weigh more than old ones;
- each entry's summary explains why it is suggested.

Results are cached per user and recomputed as soon as a new reading sync arrives or the library changes. With an empty history, the feed is empty with an explanatory subtitle. Browsers without an account see the « Se connecter » button instead of the tile; OPDS clients always see the tile, and opening it asks for their credentials (HTTP Basic).

## Personal shelves and « Masquer les livres lus »

For a signed-in reader, the root catalog starts with the reader's tiles, in this order: **« En cours »**, **« Suite de vos séries »**, then « À lire ensuite ». They come from the reading sync, matched to the library EPUBs (`controllers/reading_state.py`, shared with the recommendations):

- `/opds/reading` (« En cours »): library books being read (neither finished, paused nor abandoned), most recently synced first; each summary starts with the progress (« Lu à 42 % »).
- `/opds/series-next` (« Suite de vos séries »): for each series with a volume read, in progress or paused, the next library volume you never opened (whole-numbered volumes first, like `/documents/{document}/next`).

Both feeds always require an account; like « À lire ensuite », OPDS clients without credentials see the tiles and opening them asks for the account.

Every book list (all books, recent, folders, years, authors, genres, series, search) carries, for a signed-in reader, the OPDS facets `Tous les livres` / `Masquer les livres lus` (`rel="http://opds-spec.org/facet"`, `opds:facetGroup="Livres lus"`, `opds:activeFacet` on the current one). They reopen the list with `?read=show|hide`; the choice is remembered for the reader (all lists, every client) until changed. Readers listing facets after the entries (CrossPoint) show them as rows; the web UI shows them above the list.
