"""Optional parts of the server: MINIMAL_MODE=true keeps the OPDS catalog and its web view (XSLT) only.

Turned off in minimal mode: the sync APIs (KOSync, CrossPoint Sync, Nextcloud Notes), the reader's
pages and feeds (reading statistics, clippings, web reader, "En cours", "Suite de vos séries",
"À lire ensuite", read status and "Masquer les livres lus") and the account creation. Signing in
stays possible when the catalog is private (OPDS_AUTH=true), with the existing accounts.
"""
import os

from controllers.opds_auth import is_private

TRUE_VALUES = ('1', 'true', 'yes', 'on')


def minimal_mode() -> bool:
    """MINIMAL_MODE=true: catalog only. Read on every request (toggled in tests)."""
    return os.environ.get('MINIMAL_MODE', '').strip().lower() in TRUE_VALUES


def accounts_enabled() -> bool:
    """Whether the web UI offers to sign in: always, except in minimal mode with a public catalog."""
    return not minimal_mode() or is_private()


def page_flags() -> dict[str, str]:
    """`{{name}}` values of the partials (controllers.webui.include_partials) describing the enabled parts."""
    return {
        'mode': 'minimal' if minimal_mode() else 'full',
        'accounts': 'on' if accounts_enabled() else 'off',
        # Classes of the menu entries of the reader's pages (reading statistics, clippings)
        'display': 'hidden' if minimal_mode() else 'flex',
    }


__all__ = ['accounts_enabled', 'minimal_mode', 'page_flags']
