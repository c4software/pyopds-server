"""
HTTP routing system for the OPDS server.
Defines routes and router inspired by Laravel routing.
"""

import re

from controllers.crosspoint_sync import CrossPointSyncController
from controllers.koreader_sync import KoReaderSyncController
from controllers.opds import OPDSController
from controllers.opds_auth import AuthController
from controllers.webui import WebUIController


class Route:
    """Represents a single route with path pattern and handler."""

    def __init__(self, method, pattern, controller_action, name=None, auth: str | None = None):
        """
        Initialize a route.
        
        Args:
            method: HTTP method ('GET', 'POST', 'PUT'.)
            pattern: URL pattern (string or regex)
            controller_action: Tuple of (controller_class, action_method_name)
            name: Optional name for the route
            auth: Optional account guard ('opds': catalog, account required when OPDS_AUTH=true;
                'user': account always required)
        """
        self.method = method
        self.pattern = pattern if isinstance(pattern, re.Pattern) else re.compile(f'^{pattern}$')
        self.controller_class, self.action = controller_action
        self.name = name
        self.auth = auth

    def matches(self, method, path):
        """Check if this route matches the given method and path."""
        return self.method == method and self.pattern.match(path)


class Router:
    """HTTP router inspired by Laravel routing."""

    def __init__(self):
        self.routes = []

    def get(self, pattern, controller_action, name=None, auth: str | None = None):
        """Register a GET route."""
        self.routes.append(Route('GET', pattern, controller_action, name, auth))
        return self

    def post(self, pattern, controller_action, name=None, auth: str | None = None):
        """Register a POST route."""
        self.routes.append(Route('POST', pattern, controller_action, name, auth))
        return self
    
    def put(self, pattern, controller_action, name=None, auth: str | None = None):
        """Register a PUT route."""
        self.routes.append(Route('PUT', pattern, controller_action, name, auth))
        return self

    def delete(self, pattern, controller_action, name=None, auth: str | None = None):
        """Register a DELETE route."""
        self.routes.append(Route('DELETE', pattern, controller_action, name, auth))
        return self

    def find_route(self, method, path):
        """Find matching route for given method and path."""
        for route in self.routes:
            if route.matches(method, path):
                return route
        return None


def register_routes(router):
    """Register every route; catalog and web UI routes take auth='opds' (reader identified, required when OPDS_AUTH=true)."""
    opds = 'opds'
    # OPDS Catalog Routes
    router.get('/', (OPDSController, 'redirect_to_opds'), name='home', auth=opds)
    # Monitoring probe: never authenticated
    router.get('/health', (OPDSController, 'health_check'), name='health')
    router.get('/opds', (OPDSController, 'show_root_catalog'), name='opds.root', auth=opds)
    router.get('/opds/', (OPDSController, 'show_root_catalog'), name='opds.root.slash', auth=opds)
    router.get('/opds/books', (OPDSController, 'show_all_books'), name='opds.books', auth=opds)
    router.get('/opds/recent', (OPDSController, 'show_recent_books'), name='opds.recent', auth=opds)
    # Depends on the reader: an account is always required (sign-in page or HTTP Basic)
    router.get('/opds/recommendations', (OPDSController, 'show_recommendations'), name='opds.recommendations', auth='user')
    router.get('/opds/by-year', (OPDSController, 'show_by_year_catalog'), name='opds.by-year', auth=opds)
    router.get(r'/opds/by-year/.*', (OPDSController, 'show_year_books'), name='opds.year', auth=opds)
    router.get('/opds/by-author', (OPDSController, 'show_by_author_catalog'), name='opds.by-author', auth=opds)
    router.get(r'/opds/by-author/letter/.*', (OPDSController, 'show_author_letter_catalog'), name='opds.author-letter', auth=opds)
    router.get(r'/opds/by-author/.*', (OPDSController, 'show_author_books'), name='opds.author', auth=opds)
    router.get('/opds/by-genre', (OPDSController, 'show_by_genre_catalog'), name='opds.by-genre', auth=opds)
    router.get(r'/opds/by-genre/.*', (OPDSController, 'show_genre_books'), name='opds.genre', auth=opds)
    router.get(r'/opds/folder/.*', (OPDSController, 'show_folder_catalog'), name='opds.folder', auth=opds)
    router.get('/opds/refresh', (OPDSController, 'refresh_cache'), name='opds.refresh', auth=opds)
    router.get('/opds/opensearch.xml', (OPDSController, 'serve_opensearch_description'), name='opds.opensearch', auth=opds)
    router.get('/opds/search', (OPDSController, 'show_search_results'), name='opds.search', auth=opds)
    router.get('/opds_to_html.xslt', (OPDSController, 'serve_xslt'), name='opds.xslt', auth=opds)
    router.get(r'/download/.*', (OPDSController, 'download_book'), name='opds.download', auth=opds)
    # Covers stay public: the CrossPoint Sync app shows them with <img> tags, without credentials
    router.get(r'/cover/.*', (OPDSController, 'download_cover'), name='opds.cover')

    # Web UI: reading statistics / sync page (data fetched from the sync API by the page)
    router.get('/reading', (WebUIController, 'reading_page'), name='webui.reading', auth=opds)
    # Page assets only (no library data): public, the sign-in page needs them
    router.get(r'/static/(?P<name>[^/]+)', (WebUIController, 'static_file'), name='webui.static')
    # Sign-in and sign-up page of the browsers, one session for the catalog and the reading page
    router.get('/login', (AuthController, 'login_page'), name='auth.login.page')
    router.post('/login', (AuthController, 'login'), name='auth.login')
    router.get('/logout', (AuthController, 'logout'), name='auth.logout')

    # Sync API, mounted at the root (CrossPoint Sync app: "host:port") and under /koreader/sync (KoReader)
    for prefix in ('', '/koreader/sync'):
        register_sync_routes(router, prefix)

    return router


DOC = r'(?P<document>[^/]+)'


def register_sync_routes(router, prefix):
    """Register the KOSync and CrossPoint Sync (/api/v1) routes under a URL prefix."""
    name = f"sync{prefix.replace('/', '.')}"
    api = f'{prefix}/api/v1'

    # KOSync (KoReader, CrossPoint readers)
    router.get(f'{prefix}/healthz', (KoReaderSyncController, 'health'), name=f'{name}.healthz')
    router.post(f'{prefix}/users/create', (KoReaderSyncController, 'register'), name=f'{name}.register')
    router.get(f'{prefix}/users/auth', (KoReaderSyncController, 'login'), name=f'{name}.login')
    router.put(f'{prefix}/syncs/progress', (KoReaderSyncController, 'store_sync_records'), name=f'{name}.progress.store')
    router.get(f'{prefix}/syncs/progress/{DOC}', (KoReaderSyncController, 'get_sync_records'), name=f'{name}.progress.get')

    # CrossPoint Sync extensions (literal segments before {document} patterns)
    router.put(f'{api}/progress', (CrossPointSyncController, 'progress_put'), name=f'{name}.api.progress.put')
    router.put(f'{api}/progress/batch', (CrossPointSyncController, 'progress_batch'), name=f'{name}.api.progress.batch')
    router.get(f'{api}/progress', (CrossPointSyncController, 'progress_list'), name=f'{name}.api.progress.list')
    router.get(f'{api}/progress/changes', (CrossPointSyncController, 'progress_changes'), name=f'{name}.api.progress.changes')
    router.get(f'{api}/progress/{DOC}', (CrossPointSyncController, 'progress_show'), name=f'{name}.api.progress.show')
    router.delete(f'{api}/progress/{DOC}', (CrossPointSyncController, 'progress_delete'), name=f'{name}.api.progress.delete')

    router.put(f'{api}/documents', (CrossPointSyncController, 'documents_put'), name=f'{name}.api.documents.put')
    router.get(f'{api}/documents', (CrossPointSyncController, 'documents_list'), name=f'{name}.api.documents.list')
    router.post(f'{api}/documents/merge', (CrossPointSyncController, 'documents_merge'), name=f'{name}.api.documents.merge')
    router.delete(f'{api}/documents/merge/(?P<alias>[^/]+)', (CrossPointSyncController, 'documents_unmerge'), name=f'{name}.api.documents.unmerge')
    router.put(f'{api}/documents/{DOC}/status', (CrossPointSyncController, 'document_status'), name=f'{name}.api.documents.status')
    router.put(f'{api}/documents/{DOC}/dates', (CrossPointSyncController, 'document_dates'), name=f'{name}.api.documents.dates')
    router.put(f'{api}/documents/{DOC}/info', (CrossPointSyncController, 'document_info'), name=f'{name}.api.documents.info')
    router.get(f'{api}/documents/{DOC}/cover', (CrossPointSyncController, 'document_cover'), name=f'{name}.api.documents.cover')
    router.get(f'{api}/documents/{DOC}/cover/candidates', (CrossPointSyncController, 'document_cover_candidates'), name=f'{name}.api.documents.cover.candidates')
    router.get(f'{api}/documents/{DOC}/about', (CrossPointSyncController, 'document_about'), name=f'{name}.api.documents.about')
    router.get(f'{api}/documents/{DOC}/next', (CrossPointSyncController, 'document_next'), name=f'{name}.api.documents.next')
    router.get(f'{api}/documents/{DOC}/matches', (CrossPointSyncController, 'document_matches'), name=f'{name}.api.documents.matches')

    router.put(f'{api}/stats/global', (CrossPointSyncController, 'stats_global_put'), name=f'{name}.api.stats.global.put')
    router.get(f'{api}/stats/global', (CrossPointSyncController, 'stats_global_get'), name=f'{name}.api.stats.global')
    router.put(f'{api}/stats/books', (CrossPointSyncController, 'stats_books_put'), name=f'{name}.api.stats.books.put')
    router.get(f'{api}/stats/books/{DOC}', (CrossPointSyncController, 'stats_book_get'), name=f'{name}.api.stats.book')
    router.get(f'{api}/stats/summary', (CrossPointSyncController, 'stats_summary'), name=f'{name}.api.stats.summary')
    router.get(f'{api}/stats/activity', (CrossPointSyncController, 'stats_activity'), name=f'{name}.api.stats.activity')

    router.get(f'{api}/clippings', (CrossPointSyncController, 'clippings_all'), name=f'{name}.api.clippings.all')
    router.get(f'{api}/clippings/{DOC}', (CrossPointSyncController, 'clippings_get'), name=f'{name}.api.clippings.get')
    router.put(f'{api}/clippings/{DOC}', (CrossPointSyncController, 'clippings_put'), name=f'{name}.api.clippings.put')
    router.get(f'{api}/bookmarks/{DOC}', (CrossPointSyncController, 'bookmarks_get'), name=f'{name}.api.bookmarks.get')
    router.put(f'{api}/bookmarks/{DOC}', (CrossPointSyncController, 'bookmarks_put'), name=f'{name}.api.bookmarks.put')

    router.post(f'{api}/account/password', (CrossPointSyncController, 'account_password'), name=f'{name}.api.account.password')
    router.delete(f'{api}/account/data', (CrossPointSyncController, 'account_data_delete'), name=f'{name}.api.account.data')
    router.delete(f'{api}/account', (CrossPointSyncController, 'account_delete'), name=f'{name}.api.account.delete')

    router.get(f'{api}/connectors', (CrossPointSyncController, 'connectors_list'), name=f'{name}.api.connectors')
    for method in (router.get, router.put, router.post, router.delete):
        method(f'{api}/connectors/.+', (CrossPointSyncController, 'connector_unknown'), name=f'{name}.api.connectors.unknown')
        method(f'{api}/.*', (CrossPointSyncController, 'not_found'), name=f'{name}.api.not_found')


__all__ = [
    'Route',
    'Router',
    'register_routes',
    'register_sync_routes',
]
