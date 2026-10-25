"""Shared test setup, imported first by the test modules: the library metadata of the test libraries go to
a temporary database (controllers/library.py), never next to a real sync database."""
import os
import tempfile

os.environ.setdefault('LIBRARY_DB_PATH', os.path.join(tempfile.mkdtemp(prefix='pyopds-tests-'), 'library.db'))
