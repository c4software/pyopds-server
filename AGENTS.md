# Agent Instructions

- Run `pytest` before submitting changes.
- Keep KoReader sync logic in `koreader_sync.py` and OPDS catalog logic in `opds.py`.
- Use standard library modules when available unless a dependency already exists in the project.
- Use a create a `venv` for development to avoid polluting the global Python environment. The `.gitignore` file already ignores the `.venv` directory.