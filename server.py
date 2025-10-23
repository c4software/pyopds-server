import http.server
import os
import socketserver
from urllib.parse import urlparse

from koreader_sync import (
    KOREADER_SYNC_DB_PATH,
    KOREADER_SYNC_TOKEN,
    KoReaderSyncController,
    KoReaderSyncStorage,
)
from opds import LIBRARY_DIR, OPDSController, OPDSHandler, PAGE_SIZE
from routes import Router, register_routes

PORT = int(os.environ.get('PORT', 8080))


class UnifiedHandler(http.server.BaseHTTPRequestHandler):
    """
    Unified HTTP request handler with explicit Laravel-style routing.
    """

    # Initialize router with all routes
    router = register_routes(Router())

    def __init__(self, *args, **kwargs):
        """Initialize handler with controller instances."""
        super().__init__(*args, **kwargs)
        # Controllers are created on demand to have access to self

    def _get_controller(self, controller_class):
        """Get or create controller instance."""
        if controller_class == OPDSController:
            return OPDSController(self)
        elif controller_class == KoReaderSyncController:
            return KoReaderSyncController(self)
        else:
            raise ValueError(f"Unknown controller: {controller_class}")

    def _handle_request(self, method):
        """Handle request by routing to appropriate controller action."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        # Find matching route
        route = self.router.find_route(method, path)

        if route:
            # Get controller and call action
            controller = self._get_controller(route.controller_class)
            action_method = getattr(controller, route.action)
            
            # Call the action
            if route.controller_class == OPDSController:
                # OPDS actions don't need parsed_url
                action_method()
            else:
                # KoReader actions need parsed_url
                action_method(parsed_url)
        else:
            # No route found - 404
            if path.startswith('/koreader/'):
                # KoReader endpoint not found
                controller = self._get_controller(KoReaderSyncController)
                controller._send_json_error(404, 'Endpoint not found')
            else:
                # OPDS endpoint not found
                controller = self._get_controller(OPDSController)
                controller._send_error(404, 'Not found')

    def do_GET(self):
        """Handle GET requests through router."""
        self._handle_request('GET')

    def do_POST(self):
        """Handle POST requests through router."""
        self._handle_request('POST')


def main():
    """Start the OPDS server with KoReader sync support."""
    if not os.path.exists(LIBRARY_DIR):
        os.makedirs(LIBRARY_DIR)

    # Print registered routes
    print(f"OPDS server started on port {PORT}")
    print(f"\nRegistered routes:")
    for route in UnifiedHandler.router.routes:
        print(f"  {route.method:6} {route.pattern.pattern:30} -> {route.controller_class.__name__}.{route.action}")
    
    print(f"\nAccess the root catalog at http://127.0.0.1:{PORT}/opds")
    print(f"KoReader sync available at http://127.0.0.1:{PORT}/koreader/sync\n")

    with socketserver.TCPServer(("", PORT), UnifiedHandler) as httpd:
        httpd.serve_forever()


__all__ = [
    'KOREADER_SYNC_DB_PATH',
    'KOREADER_SYNC_TOKEN',
    'KoReaderSyncStorage',
    'KoReaderSyncController',
    'LIBRARY_DIR',
    'PAGE_SIZE',
    'OPDSController',
    'OPDSHandler',
    'UnifiedHandler',
    'PORT',
    'main',
]


if __name__ == '__main__':
    main()
