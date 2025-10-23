"""
HTTP routing system for the OPDS server.
Defines routes and router inspired by Laravel routing.
"""

import re

from koreader_sync import KoReaderSyncController
from opds import OPDSController


class Route:
    """Represents a single route with path pattern and handler."""

    def __init__(self, method, pattern, controller_action, name=None):
        """
        Initialize a route.
        
        Args:
            method: HTTP method ('GET', 'POST', 'PUT'.)
            pattern: URL pattern (string or regex)
            controller_action: Tuple of (controller_class, action_method_name)
            name: Optional name for the route
        """
        self.method = method
        self.pattern = pattern if isinstance(pattern, re.Pattern) else re.compile(f'^{pattern}$')
        self.controller_class, self.action = controller_action
        self.name = name

    def matches(self, method, path):
        """Check if this route matches the given method and path."""
        return self.method == method and self.pattern.match(path)


class Router:
    """HTTP router inspired by Laravel routing."""

    def __init__(self):
        self.routes = []

    def get(self, pattern, controller_action, name=None):
        """Register a GET route."""
        self.routes.append(Route('GET', pattern, controller_action, name))
        return self

    def post(self, pattern, controller_action, name=None):
        """Register a POST route."""
        self.routes.append(Route('POST', pattern, controller_action, name))
        return self
    
    def put(self, pattern, controller_action, name=None):
        """Register a PUT route."""
        self.routes.append(Route('PUT', pattern, controller_action, name))
        return self

    def find_route(self, method, path):
        """Find matching route for given method and path."""
        for route in self.routes:
            if route.matches(method, path):
                return route
        return None


def register_routes(router):
    # OPDS Catalog Routes
    router.get('/', (OPDSController, 'redirect_to_opds'), name='home')
    router.get('/opds', (OPDSController, 'show_root_catalog'), name='opds.root')
    router.get('/opds/', (OPDSController, 'show_root_catalog'), name='opds.root.slash')
    router.get('/opds/books', (OPDSController, 'show_all_books'), name='opds.books')
    router.get('/opds/recent', (OPDSController, 'show_recent_books'), name='opds.recent')
    router.get(r'/opds/folder/.*', (OPDSController, 'show_folder_catalog'), name='opds.folder')
    router.get('/opds_to_html.xslt', (OPDSController, 'serve_xslt'), name='opds.xslt')
    router.get(r'/download/.*', (OPDSController, 'download_book'), name='opds.download')
    router.get(r'/cover/.*', (OPDSController, 'download_cover'), name='opds.cover')

    # KoReader Sync Routes
    router.get('/koreader/sync/syncs/progress/.*', (KoReaderSyncController, 'get_sync_records'), name='koreader.sync.get')
    router.put('/koreader/sync/syncs/progress', (KoReaderSyncController, 'store_sync_records'), name='koreader.sync.store')
    router.post('/koreader/sync/users/create', (KoReaderSyncController, 'register'), name='koreader.register')
    router.get('/koreader/sync/users/auth', (KoReaderSyncController, 'login'), name='koreader.login')

    return router


__all__ = [
    'Route',
    'Router',
    'register_routes',
]
