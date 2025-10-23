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
from opds import LIBRARY_DIR, OPDSController, PAGE_SIZE
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
            controller = self._get_controller(OPDSController)
            controller._send_error(404, 'Endpoint not found')

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

    print(f"\nAccess the root catalog at http://127.0.0.1:{PORT}/opds")
    print(f"KoReader sync available at http://127.0.0.1:{PORT}/koreader/sync\n")

    with socketserver.TCPServer(("", PORT), UnifiedHandler) as httpd:
        httpd.serve_forever()

if __name__ == '__main__':
    main()
