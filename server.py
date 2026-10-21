import http.server
import os
import socketserver
from urllib.parse import urlparse

from controllers import opds_auth
from controllers.opds import LIBRARY_DIR, OPDSController, PAGE_SIZE
from routes import Router, register_routes

PORT = int(os.environ.get('PORT', 8080))


class UnifiedHandler(http.server.BaseHTTPRequestHandler):
    """
    Unified HTTP request handler with explicit Laravel-style routing.
    """

    # Drop clients that stall (e.g. announce a body they never send) instead of blocking the server
    timeout = 60

    # Sync account authenticated with HTTP Basic on catalog routes (None when OPDS_AUTH is off)
    opds_user: str | None = None

    # Initialize router with all routes
    router = register_routes(Router())

    def end_headers(self):
        """Add Connection: close header to all responses for HTTP/1.0 compatibility.

        Our server uses HTTP/1.0 but clients (e.g. ESP32 HTTPClient) may send
        HTTP/1.1 requests. The explicit header ensures clients properly detect
        the end of each response.
        """
        self.send_header('Connection', 'close')
        super().end_headers()

    def __init__(self, *args, **kwargs):
        """Initialize handler with controller instances."""
        super().__init__(*args, **kwargs)
        # Controllers are created on demand to have access to self

    def _get_controller(self, controller_class):
        """Create a controller instance bound to this request."""
        return controller_class(self)

    def _handle_request(self, method):
        """Handle request by routing to appropriate controller action."""
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        # Find matching route
        route = self.router.find_route(method, path)

        if route:
            # Authentication middleware (sets self.opds_user, answers 401 itself when refused)
            if not opds_auth.guard(self, route.auth):
                return
            # Named groups of the route pattern (e.g. {document}) are exposed to controllers
            self.route_params = route.pattern.match(path).groupdict()
            # Get controller and call action
            controller = self._get_controller(route.controller_class)
            action_method = getattr(controller, route.action)
            action_method()
        else:
            controller = self._get_controller(OPDSController)
            controller._send_error(404, 'Endpoint not found')

    def do_GET(self):
        """Handle GET requests through router."""
        self._handle_request('GET')

    def do_PUT(self):
        """Handle PUT requests through router."""
        self._handle_request('PUT')

    def do_POST(self):
        """Handle POST requests through router."""
        self._handle_request('POST')

    def do_DELETE(self):
        """Handle DELETE requests through router."""
        self._handle_request('DELETE')


def main():
    """Start the OPDS server with KoReader sync support."""
    if not os.path.exists(LIBRARY_DIR):
        os.makedirs(LIBRARY_DIR)

    print(f"\nAccess the root catalog at http://127.0.0.1:{PORT}/opds")
    print(f"KoReader sync available at http://127.0.0.1:{PORT}/koreader/sync\n")

    with socketserver.TCPServer(("", PORT), UnifiedHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
            httpd.shutdown()

if __name__ == '__main__':
    main()

