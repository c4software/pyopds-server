import os
import socketserver

from koreader_sync import (
    KOREADER_SYNC_DB_PATH,
    KOREADER_SYNC_TOKEN,
    KoReaderSyncStorage,
)
from opds import LIBRARY_DIR, OPDSHandler, PAGE_SIZE

PORT = int(os.environ.get('PORT', 8080))


def main():
    if not os.path.exists(LIBRARY_DIR):
        os.makedirs(LIBRARY_DIR)

    with socketserver.TCPServer(("", PORT), OPDSHandler) as httpd:
        print(f"OPDS server started on port {PORT}")
        print(f"Access the root catalog at http://127.0.0.1:{PORT}/opds")
        httpd.serve_forever()


__all__ = [
    'KOREADER_SYNC_DB_PATH',
    'KOREADER_SYNC_TOKEN',
    'KoReaderSyncStorage',
    'LIBRARY_DIR',
    'PAGE_SIZE',
    'OPDSHandler',
    'PORT',
    'main',
]


if __name__ == '__main__':
    main()
