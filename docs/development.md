# Development

## Docker with hot reload

```bash
docker compose -f docker-compose.dev.yml up --build
```

The source is mounted into two containers built from the `dev` stage of the [`Dockerfile`](../Dockerfile):

- `app` serves `http://localhost:8080` and restarts on every change of the Python code, `genres.toml` or `metadata.toml` ([watchfiles](https://watchfiles.helpmanual.io/)); EPUBs are read from `./books`, the sync database is kept in `./config`. Changes to `static/` (XSLT, pages, scripts) need no restart: reload the page.
- `css` rebuilds `static/tailwind.css` whenever Tailwind classes change in `static/` (Tailwind CLI `--watch`).

With rootful Docker, set `DEV_UID` / `DEV_GID` (e.g. in a `.env` file: `DEV_UID=1000`) so that the rebuilt stylesheet belongs to you; rootless Docker needs nothing.

## Tests

The test suite only needs the standard library (`pytest` works too); it starts real HTTP servers on free ports and uses temporary libraries and databases:

```bash
python -m unittest discover tests
```

## Releasing

Commits go to `main`; a version is published by pushing a tag (plain [semantic version](https://semver.org), no `v` prefix: `2.9.0`), which triggers the [`Docker image`](../.github/workflows/docker.yml) workflow (image `latest` and `<version>`, amd64 and arm64):

```bash
python -m unittest discover tests        # tests must pass
git push                                 # commits on main
git tag 2.9.1 && git push origin 2.9.1   # new version: minor for a feature, patch for a fix
gh release create 2.9.1 --title 2.9.1 --notes "…"   # release notes on GitHub
gh run watch                             # wait for the image build
```

Then update the servers: `docker compose pull && docker compose up -d`.

## Web UI styles

The web UI uses [Tailwind CSS](https://tailwindcss.com) v3, compiled into `static/tailwind.css` (not versioned) by the official [standalone CLI](https://tailwindcss.com/blog/standalone-cli), which needs no Node.js:

```bash
python tools/tailwind.py          # build static/tailwind.css (minified)
python tools/tailwind.py --watch  # rebuild on every change of static/
```

[`tools/tailwind.py`](../tools/tailwind.py) downloads the pinned CLI version once (checked against its SHA-256) into `.cache/`, then runs it with [`tailwind.config.js`](../tailwind.config.js) (dark mode by class, classes scanned in `static/*.xslt|html|js`, `static/catalog/*.js` and `static/reader/*.js`) and [`styles/tailwind.css`](../styles/tailwind.css). Extra arguments go to the Tailwind CLI. The Docker image builds the stylesheet itself (`css` stage of the Dockerfile), and the development compose file rebuilds it on every change.

## Web UI files

The XSLT only holds the page structure; the page code lives next to it:

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
| `static/catalog/library-sync.js` | « Recharger » button: library sync and its result |

They are plain scripts sharing a small `window.PyOPDS` namespace, loaded in order by `<script src>` tags at the end of the page: this runs the same with native XSLT and with the polyfill (which re-creates the scripts of the result one after the other, then dispatches `DOMContentLoaded`). The server adds `?v=<modification time>` to every `/static/…` URL of the XSLT it serves (as for the other pages): versioned files are cached for a year, and a deployment changes their URLs, so browsers and caching proxies never mix old and new scripts. Files of `static/` and of one level of sub-folder (`static/catalog/`) are served; Tailwind classes used in these scripts are picked up by `python tools/tailwind.py`.
