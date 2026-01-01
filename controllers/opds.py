"""OPDS catalog HTTP handler and helpers."""
import datetime
import hashlib
import heapq
import os
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
import zipfile
from urllib.parse import parse_qs, quote, unquote, urlparse

LIBRARY_DIR = os.environ.get('LIBRARY_DIR', 'books')
PAGE_SIZE = int(os.environ.get('PAGE_SIZE', 25))


class BookMetadata:
    @staticmethod
    def _parse_opf_from_epub(zf: zipfile.ZipFile) -> tuple[ET.Element | None, str | None]:
        """Internal: parse container.xml and OPF, return (opf_root, opf_dir)."""
        try:
            container_xml = zf.read('META-INF/container.xml')
            container_root = ET.fromstring(container_xml)
            ns_container = {'ocf': 'urn:oasis:names:tc:opendocument:xmlns:container'}
            rootfile = container_root.find(
                ".//ocf:rootfile[@media-type='application/oebps-package+xml']",
                ns_container,
            )
            if rootfile is None:
                return None, None
            opf_path = rootfile.get('full-path')
            if not opf_path:
                return None, None
            opf_xml = zf.read(opf_path)
            opf_root = ET.fromstring(opf_xml)
            opf_dir = os.path.dirname(opf_path)
            return opf_root, opf_dir
        except Exception:
            return None, None

    @staticmethod
    def extract_epub_metadata(epub_path: str) -> tuple[str | None, str | None, str | None]:
        """Extract metadata from EPUB file.
        
        Returns:
            tuple: (title, author, publication_date) or (None, None, None) if not found
        """
        try:
            with zipfile.ZipFile(epub_path) as zf:
                opf_root, _ = BookMetadata._parse_opf_from_epub(zf)
                if opf_root is None:
                    return None, None, None

                ns_dc = {'dc': 'http://purl.org/dc/elements/1.1/'}
                title_elem = opf_root.find(".//dc:title", ns_dc)
                title = title_elem.text if title_elem is not None else None
                author_elem = opf_root.find(".//dc:creator", ns_dc)
                author = author_elem.text if author_elem is not None else None
                date_elem = opf_root.find(".//dc:date", ns_dc)
                publication_date = date_elem.text if date_elem is not None else None

                return title, author, publication_date
        except Exception:
            return None, None, None

    @staticmethod
    def extract_epub_cover(epub_path: str) -> tuple[bytes | None, str | None]:
        """Extract cover image from EPUB file.
        
        Returns:
            tuple: (cover_data, mime_type) or (None, None) if not found
        """
        try:
            with zipfile.ZipFile(epub_path) as zf:
                opf_root, opf_dir = BookMetadata._parse_opf_from_epub(zf)
                if opf_root is None:
                    return None, None

                # Define namespaces
                ns_opf = {'opf': 'http://www.idpf.org/2007/opf'}
                
                # Try to find cover using different methods
                cover_id = None
                cover_href = None
                mime_type = None
                
                # Method 1: Look for meta name="cover"
                cover_meta = opf_root.find(".//opf:meta[@name='cover']", ns_opf)
                if cover_meta is not None:
                    cover_id = cover_meta.get('content')
                
                # Method 2: Look for item with properties="cover-image" (EPUB 3)
                if not cover_id:
                    cover_item = opf_root.find(".//opf:item[@properties='cover-image']", ns_opf)
                    if cover_item is not None:
                        cover_href = cover_item.get('href')
                        mime_type = cover_item.get('media-type')
                
                # If we found a cover ID, get the href from manifest
                if cover_id and not cover_href:
                    cover_item = opf_root.find(f".//opf:item[@id='{cover_id}']", ns_opf)
                    if cover_item is not None:
                        cover_href = cover_item.get('href')
                        mime_type = cover_item.get('media-type', 'image/jpeg')
                
                # If we found a cover href, extract it
                if cover_href:
                    # Resolve relative path
                    if opf_dir:
                        cover_path = os.path.join(opf_dir, cover_href).replace('\\', '/')
                    else:
                        cover_path = cover_href
                    
                    # Read cover image
                    cover_data = zf.read(cover_path)
                    
                    # Determine MIME type from file extension if not already set
                    if mime_type is None:
                        ext = os.path.splitext(cover_href)[1].lower()
                        mime_types = {
                            '.jpg': 'image/jpeg',
                            '.jpeg': 'image/jpeg',
                            '.png': 'image/png',
                            '.gif': 'image/gif',
                            '.webp': 'image/webp',
                        }
                        mime_type = mime_types.get(ext, 'image/jpeg')
                    
                    return cover_data, mime_type
                
                return None, None
        except Exception:
            return None, None


class SecurityUtils:
    @staticmethod
    def is_within_library_dir(file_path: str) -> bool:
        library_realpath = os.path.realpath(LIBRARY_DIR)
        file_realpath = os.path.realpath(file_path)
        return file_realpath.startswith(library_realpath + os.sep) or file_realpath == library_realpath

    @staticmethod
    def has_path_traversal(path: str) -> bool:
        """Check if path contains dangerous traversal sequences."""
        dangerous_patterns = ['..', '~']

        for pattern in dangerous_patterns:
            if pattern in path:
                return True

        path_components = path.replace('\\', '/').split('/')
        for component in path_components:
            if component in dangerous_patterns or component.startswith('.'):
                return True

        return False


class OPDSFeedGenerator:
    @staticmethod
    def generate_feed(title: str, feed_id: str, links: list[tuple[str, str, str]], entries: list[dict]) -> str:
        feed = ET.Element(
            'feed',
            {
                'xmlns': 'http://www.w3.org/2005/Atom',
                'xmlns:opds': 'http://opds-spec.org/2010/catalog',
            },
        )

        ET.SubElement(feed, 'title').text = title
        ET.SubElement(feed, 'id').text = feed_id
        ET.SubElement(feed, 'updated').text = (
            datetime.datetime.now(datetime.timezone.utc).isoformat() + 'Z'
        )

        for rel, href, type_ in links:
            ET.SubElement(feed, 'link', {'rel': rel, 'href': href, 'type': type_})

        for entry_data in entries:
            entry = ET.SubElement(feed, 'entry')
            ET.SubElement(entry, 'title').text = entry_data['title']
            ET.SubElement(entry, 'id').text = entry_data['id']

            if 'author' in entry_data:
                author = ET.SubElement(entry, 'author')
                ET.SubElement(author, 'name').text = entry_data['author']

            for rel, href, type_ in entry_data['links']:
                ET.SubElement(entry, 'link', {'rel': rel, 'href': href, 'type': type_})

        xml_string = ET.tostring(feed, encoding='unicode', method='xml')
        processing_instruction = (
            '<?xml-stylesheet type="text/xsl" href="/opds_to_html.xslt"?>\n'
        )
        return processing_instruction + xml_string


class BookScanner:
    """Scanner for EPUB files with caching."""
    
    _instance = None  # Singleton instance
    
    @classmethod
    def get_instance(cls) -> 'BookScanner':
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.metadata_extractor = BookMetadata()
        self.security = SecurityUtils()
        self._all_paths_cache = None
        self._all_books_metadata_cache = None
        # Lightweight index caches: only store path -> year/author mapping
        self._year_index = None  # {year: [paths...]}
        self._author_index = None  # {author: [paths...]}
        self._recent_books_cache = None
        self._recent_books_cache_time = 0
        self.RECENT_CACHE_TTL = 300
    
    def invalidate_caches(self) -> None:
        """Invalidate all caches. Called when a missing file is detected."""
        self._all_paths_cache = None
        self._all_books_metadata_cache = None
        self._year_index = None
        self._author_index = None
        self._recent_books_cache = None
        self._recent_books_cache_time = 0
    
    def _create_book_info_from_path(self, path: str) -> dict:
        """Create book info dict from file path. Used to avoid code duplication."""
        relative_path = os.path.relpath(path, LIBRARY_DIR)
        title, author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
        title = title or os.path.basename(path)
        author = author or 'Unknown'
        return {
            'path': path,
            'relative_path': relative_path,
            'title': title,
            'author': author,
            'publication_date': pub_date,
            'year': self._extract_year(pub_date),
            'mtime': self._safe_getmtime(path),
        }

    def collect_all_epub_paths(self) -> list[str]:
        paths = []
        for root, _, files in os.walk(LIBRARY_DIR):
            for file in files:
                if file.endswith('.epub'):
                    paths.append(os.path.join(root, file))
        return sorted(paths, key=lambda p: os.path.basename(p).lower())

    def scan_directory_single_level(self, directory_path: str, base_path: str | None = None) -> list[dict]:
        if base_path is None:
            base_path = directory_path

        file_list = []

        try:
            for file in os.listdir(directory_path):
                if file.endswith('.epub'):
                    file_path = os.path.join(directory_path, file)
                    if os.path.isfile(file_path):
                        file_info = self._create_file_info(directory_path, file, base_path)
                        if file_info:
                            file_list.append(file_info)
        except OSError:
            pass

        return sorted(file_list, key=lambda x: x['title'].lower())

    def get_all_books_paginated(self, page: int, size: int) -> tuple[list[dict], int]:
        if self._all_paths_cache is None:
            self._all_paths_cache = self.collect_all_epub_paths()

        paths = self._all_paths_cache
        total_count = len(paths)

        start = (page - 1) * size
        end = start + size
        paginated_paths = paths[start:end]

        paginated_books = []
        for path in paginated_paths:
            relative_path = os.path.relpath(path, LIBRARY_DIR)
            if self.security.has_path_traversal(relative_path) or not self.security.is_within_library_dir(path):
                continue
            title, author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
            title = title or os.path.basename(path)
            author = author or 'Unknown'
            paginated_books.append(
                {
                    'path': path,
                    'relative_path': relative_path,
                    'title': title,
                    'author': author,
                    'publication_date': pub_date,
                    'mtime': os.path.getmtime(path),
                }
            )

        return paginated_books, total_count

    def get_folder_content_paginated(self, folder_full_path: str, parent_folder_path: str, page: int, size: int, base_path: str | None = None) -> tuple[list[dict], int]:
        subfolders = []
        for item in os.listdir(folder_full_path):
            item_path = os.path.join(folder_full_path, item)
            if os.path.isdir(item_path) and not item.startswith('.'):
                subfolders.append(item)
        subfolders = sorted(subfolders)

        subfolder_entries = []
        for subfolder in subfolders:
            subfolder_relative = os.path.join(parent_folder_path, subfolder)
            subfolder_id = f'urn:folder:{hashlib.md5(subfolder_relative.encode()).hexdigest()}'
            encoded_subfolder = quote(subfolder_relative.replace(os.sep, '/'))

            subfolder_entries.append(
                {
                    'title': subfolder,
                    'id': subfolder_id,
                    'type': 'folder',
                    'links': [
                        (
                            'subsection',
                            f'/opds/folder/{encoded_subfolder}?page=1',
                            'application/atom+xml;profile=opds-catalog;kind=acquisition',
                        )
                    ],
                }
            )

        book_list = self.scan_directory_single_level(folder_full_path, base_path=base_path)

        book_entries = []
        for file_info in book_list:
            book_id = f'urn:book:{hashlib.md5(file_info["relative_path"].encode()).hexdigest()}'
            encoded_path = quote(file_info['relative_path'].replace(os.sep, '/'))

            book_entries.append(
                {
                    'title': file_info['title'],
                    'id': book_id,
                    'type': 'book',
                    'author': file_info['author'],
                    'links': [
                        (
                            'http://opds-spec.org/acquisition/open-access',
                            f'/download/{encoded_path}',
                            'application/epub+zip',
                        )
                    ],
                }
            )

        all_entries = subfolder_entries + book_entries
        total_count = len(all_entries)

        start = (page - 1) * size
        end = start + size
        paginated_entries = all_entries[start:end]

        return paginated_entries, total_count

    def scan_recent_books(self, directory_path: str, limit: int = 25) -> list[dict]:
        current_time = time.time()
        if (
            self._recent_books_cache is not None
            and current_time - self._recent_books_cache_time < self.RECENT_CACHE_TTL
        ):
            return self._recent_books_cache[:limit]

        heap = []

        def scan_for_recent_files(path):
            try:
                for entry in os.scandir(path):
                    if entry.is_file() and entry.name.endswith('.epub'):
                        try:
                            stat_info = entry.stat()
                            mtime = stat_info.st_mtime
                            if len(heap) < limit:
                                heapq.heappush(heap, (mtime, entry.path))
                            elif mtime > heap[0][0]:
                                heapq.heapreplace(heap, (mtime, entry.path))
                        except OSError:
                            continue
                    elif entry.is_dir() and not entry.name.startswith('.'):
                        scan_for_recent_files(entry.path)
            except OSError:
                pass

        scan_for_recent_files(directory_path)

        recent_files = sorted(heap, key=lambda x: x[0], reverse=True)

        file_list = []
        for mtime, file_path in recent_files:
            relative_path = os.path.relpath(file_path, directory_path)
            if (
                not self.security.has_path_traversal(relative_path)
                and self.security.is_within_library_dir(file_path)
            ):
                title, author, pub_date = self.metadata_extractor.extract_epub_metadata(file_path)
                title = title or os.path.basename(file_path)
                author = author or 'Unknown'

                file_list.append(
                    {
                        'path': file_path,
                        'relative_path': relative_path,
                        'title': title,
                        'author': author,
                        'publication_date': pub_date,
                        'mtime': mtime,
                    }
                )

        self._recent_books_cache = file_list
        self._recent_books_cache_time = current_time

        return file_list

    def _create_file_info(self, root, filename, base_path):
        path = os.path.join(root, filename)
        relative_path = os.path.relpath(path, base_path)

        if self.security.has_path_traversal(relative_path) or not self.security.is_within_library_dir(path):
            return None

        title, author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
        title = title or filename
        author = author or 'Unknown'

        return {
            'path': path,
            'relative_path': relative_path,
            'title': title,
            'author': author,
            'publication_date': pub_date,
            'mtime': self._safe_getmtime(path),
        }

    def _safe_getmtime(self, path: str) -> float:
        """Safely get file modification time.
        
        Returns 0 if file doesn't exist or cannot be accessed.
        Also invalidates caches when a missing file is detected.
        """
        try:
            return os.path.getmtime(path)
        except (FileNotFoundError, PermissionError, OSError):
            self.invalidate_caches()
            return 0

    @staticmethod
    def _extract_year(publication_date: str | None) -> str:
        """Extract year from publication date string.
        
        Handles formats like '2023-01-15', '2023', etc.
        Returns 'Unknown' if year cannot be extracted.
        """
        if not publication_date:
            return 'Unknown'
        # Try to extract first 4 digits as year
        year_str = publication_date[:4]
        if year_str.isdigit() and len(year_str) == 4:
            return year_str
        return 'Unknown'

    def collect_all_books_with_metadata(self) -> list[dict]:
        """Collect all books with full metadata.
        
        Uses cached metadata if available for better performance.
        Returns list of book info dicts with metadata.
        """
        if self._all_books_metadata_cache is not None:
            return self._all_books_metadata_cache
        
        if self._all_paths_cache is None:
            self._all_paths_cache = self.collect_all_epub_paths()
        
        books = []
        for path in self._all_paths_cache:
            relative_path = os.path.relpath(path, LIBRARY_DIR)
            if self.security.has_path_traversal(relative_path) or not self.security.is_within_library_dir(path):
                continue
            title, author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
            title = title or os.path.basename(path)
            author = author or 'Unknown'
            books.append({
                'path': path,
                'relative_path': relative_path,
                'title': title,
                'author': author,
                'publication_date': pub_date,
                'year': self._extract_year(pub_date),
                'mtime': self._safe_getmtime(path),
            })
        
        self._all_books_metadata_cache = books
        return books

    def _build_year_author_indexes(self) -> None:
        """Build lightweight indexes for year and author lookups.
        
        Only extracts minimal metadata (year, author) without full book info.
        Much faster than collect_all_books_with_metadata.
        """
        if self._year_index is not None and self._author_index is not None:
            return
        
        if self._all_paths_cache is None:
            self._all_paths_cache = self.collect_all_epub_paths()
        
        year_index = {}  # {year: [paths...]}
        author_index = {}  # {author: [paths...]}
        
        for path in self._all_paths_cache:
            relative_path = os.path.relpath(path, LIBRARY_DIR)
            if self.security.has_path_traversal(relative_path) or not self.security.is_within_library_dir(path):
                continue
            
            _, author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
            author = author or 'Unknown'
            year = self._extract_year(pub_date)
            
            if year not in year_index:
                year_index[year] = []
            year_index[year].append(path)
            
            if author not in author_index:
                author_index[author] = []
            author_index[author].append(path)
        
        # Sort paths within each group by filename for consistent ordering
        for paths in year_index.values():
            paths.sort(key=lambda p: os.path.basename(p).lower())
        for paths in author_index.values():
            paths.sort(key=lambda p: os.path.basename(p).lower())
        
        self._year_index = year_index
        self._author_index = author_index

    def get_years_with_counts(self) -> list[tuple[str, int]]:
        """Get all publication years with book counts.
        
        Returns list of (year, count) tuples sorted by year descending.
        """
        self._build_year_author_indexes()
        
        year_counts = [(year, len(paths)) for year, paths in self._year_index.items()]
        
        # Sort years: known years descending, 'Unknown' at the end
        sorted_years = sorted(
            year_counts,
            key=lambda x: (x[0] == 'Unknown', -int(x[0]) if x[0] != 'Unknown' else 0)
        )
        return sorted_years

    def get_authors_with_counts(self) -> list[tuple[str, int]]:
        """Get all authors with book counts.
        
        Returns list of (author, count) tuples sorted alphabetically.
        """
        self._build_year_author_indexes()
        
        author_counts = [(author, len(paths)) for author, paths in self._author_index.items()]
        
        # Sort authors alphabetically, 'Unknown' at the end
        sorted_authors = sorted(
            author_counts,
            key=lambda x: (x[0] == 'Unknown', x[0].lower())
        )
        return sorted_authors

    def get_letters_with_author_counts(self) -> list[str]:
        """Get all letters A-Z plus '#' for navigation.
        
        Returns list of letters for A-Z and '#' for special chars.
        No counting for performance.
        """
        # Return all letters A-Z + '#' without counting
        letters = [chr(i) for i in range(ord('A'), ord('Z') + 1)]
        letters.append('#')
        return letters

    def get_authors_by_letter(self, letter: str, page: int, size: int) -> tuple[list[tuple[str, int]], int]:
        """Get paginated authors starting with a specific letter.
        
        Args:
            letter: Single letter (A-Z) or '#' for special chars
            page: Page number (1-indexed)
            size: Number of authors per page
        
        Returns:
            tuple: (list of (author, count) tuples, total count)
        """
        all_authors = self.get_authors_with_counts()
        
        def matches_letter(author_name, target_letter):
            if target_letter == '#':
                if author_name == 'Unknown':
                    return True
                if not author_name:
                    return True
                return not author_name[0].upper().isalpha()
            else:
                if not author_name or author_name == 'Unknown':
                    return False
                return author_name[0].upper() == target_letter.upper()
        
        filtered_authors = [
            (author, count) for author, count in all_authors
            if matches_letter(author, letter)
        ]
        
        total_count = len(filtered_authors)
        start = (page - 1) * size
        end = start + size
        
        return filtered_authors[start:end], total_count

    def get_books_for_year(self, year: str, page: int, size: int) -> tuple[list[dict], int]:
        """Get paginated books for a specific year.
        
        Uses the same pattern as get_all_books_paginated: 
        only extracts full metadata for the current page's books.
        
        Args:
            year: Publication year string
            page: Page number (1-indexed)
            size: Number of books per page
        
        Returns:
            tuple: (list of book dicts, total count)
        """
        self._build_year_author_indexes()
        
        paths = self._year_index.get(year, [])
        total_count = len(paths)
        
        start = (page - 1) * size
        end = start + size
        paginated_paths = paths[start:end]
        
        # Only extract full metadata for current page (like get_all_books_paginated)
        paginated_books = []
        for path in paginated_paths:
            relative_path = os.path.relpath(path, LIBRARY_DIR)
            title, author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
            title = title or os.path.basename(path)
            author = author or 'Unknown'
            paginated_books.append({
                'path': path,
                'relative_path': relative_path,
                'title': title,
                'author': author,
                'publication_date': pub_date,
                'year': self._extract_year(pub_date),
                'mtime': self._safe_getmtime(path),
            })
        
        return paginated_books, total_count

    def get_books_for_author(self, author: str, page: int, size: int) -> tuple[list[dict], int]:
        """Get paginated books for a specific author.
        
        Uses the same pattern as get_all_books_paginated: 
        only extracts full metadata for the current page's books.
        
        Args:
            author: Author name
            page: Page number (1-indexed)
            size: Number of books per page
        
        Returns:
            tuple: (list of book dicts, total count)
        """
        self._build_year_author_indexes()
        
        paths = self._author_index.get(author, [])
        total_count = len(paths)
        
        start = (page - 1) * size
        end = start + size
        paginated_paths = paths[start:end]
        
        # Only extract full metadata for current page (like get_all_books_paginated)
        paginated_books = []
        for path in paginated_paths:
            relative_path = os.path.relpath(path, LIBRARY_DIR)
            title, book_author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
            title = title or os.path.basename(path)
            book_author = book_author or 'Unknown'
            paginated_books.append({
                'path': path,
                'relative_path': relative_path,
                'title': title,
                'author': book_author,
                'publication_date': pub_date,
                'year': self._extract_year(pub_date),
                'mtime': self._safe_getmtime(path),
            })
        
        return paginated_books, total_count

    def search_books(self, query: str, page: int, size: int) -> tuple[list[dict], int]:
        """Search books by title or author.
        
        Performs case-insensitive search across both title and author fields.
        Uses the same pattern as get_all_books_paginated: 
        only extracts full metadata for the current page's books.
        
        Args:
            query: Search query string
            page: Page number (1-indexed)
            size: Number of books per page
        
        Returns:
            tuple: (list of book dicts, total count)
        """
        # Normalize query for case-insensitive search
        query_lower = query.lower().strip()
        
        if not query_lower:
            # Empty query returns all books
            return self.get_all_books_paginated(page, size)
        
        if self._all_paths_cache is None:
            self._all_paths_cache = self.collect_all_epub_paths()
        
        # First pass: find all matching paths
        matching_paths = []
        for path in self._all_paths_cache:
            relative_path = os.path.relpath(path, LIBRARY_DIR)
            if self.security.has_path_traversal(relative_path) or not self.security.is_within_library_dir(path):
                continue
            
            # Extract metadata to check if it matches
            title, author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
            title = title or os.path.basename(path)
            author = author or 'Unknown'
            
            # Case-insensitive partial match in title or author
            if query_lower in title.lower() or query_lower in author.lower():
                matching_paths.append(path)
        
        total_count = len(matching_paths)
        
        # Paginate the results
        start = (page - 1) * size
        end = start + size
        paginated_paths = matching_paths[start:end]
        
        # Extract full metadata for paginated results only
        paginated_books = []
        for path in paginated_paths:
            relative_path = os.path.relpath(path, LIBRARY_DIR)
            title, author, pub_date = self.metadata_extractor.extract_epub_metadata(path)
            title = title or os.path.basename(path)
            author = author or 'Unknown'
            paginated_books.append({
                'path': path,
                'relative_path': relative_path,
                'title': title,
                'author': author,
                'publication_date': pub_date,
                'mtime': self._safe_getmtime(path),
            })
        
        return paginated_books, total_count


class OPDSController:
    """Controller for OPDS catalog operations."""

    def __init__(self, request_handler):
        self.request = request_handler
        self.feed_generator = OPDSFeedGenerator()
        self.book_scanner = BookScanner.get_instance()  # Use singleton
        self.security = SecurityUtils()

    def _parse_url_params(self):
        """Parse URL parameters for pagination."""
        parsed_url = urlparse(self.request.path)
        query_params = parse_qs(parsed_url.query)

        try:
            page = int(query_params.get('page', ['1'])[0])
            page = max(1, min(page, 10000))  # Limit to reasonable range
        except ValueError:
            page = 1

        size = PAGE_SIZE

        return page, size, parsed_url

    def redirect_to_opds(self):
        """Redirect root to OPDS catalog."""
        self.request.send_response(302)
        self.request.send_header('Location', '/opds')
        self.request.end_headers()

    def serve_xslt(self):
        """Serve XSLT stylesheet for OPDS catalog."""
        self._serve_xslt()

    def show_root_catalog(self):
        """Display root OPDS catalog."""
        self._handle_root_catalog()

    def show_all_books(self):
        """Display all books with pagination."""
        self._handle_all_books()

    def show_recent_books(self):
        """Display recently added books."""
        self._handle_recent_books()

    def show_folder_catalog(self):
        """Display folder contents."""
        self._handle_folder_catalog()

    def download_book(self):
        """Handle book download."""
        self._handle_download()

    def download_cover(self):
        """Handle cover image download."""
        self._handle_cover_download()

    def health_check(self):
        """Simple health check endpoint for monitoring."""
        self.request.send_response(200)
        self.request.send_header('Content-Type', 'application/json')
        body = b'{"status":"ok"}'
        self.request.send_header('Content-Length', str(len(body)))
        self.request.end_headers()
        self.request.wfile.write(body)

    def show_by_year_catalog(self):
        """Display catalog of years with book counts."""
        self._handle_by_year_catalog()

    def show_year_books(self):
        """Display books for a specific year."""
        self._handle_year_books()

    def show_by_author_catalog(self):
        """Display catalog of letters for author navigation."""
        self._handle_by_author_catalog()

    def show_author_letter_catalog(self):
        """Display catalog of authors for a specific letter."""
        self._handle_author_letter_catalog()

    def show_author_books(self):
        """Display books for a specific author."""
        self._handle_author_books()

    def serve_opensearch_description(self):
        """Serve OpenSearch description document."""
        self._handle_opensearch_description()

    def show_search_results(self):
        """Display search results."""
        self._handle_search_results()

    def refresh_cache(self):
        """Invalidate all caches and redirect to root catalog."""
        self.book_scanner.invalidate_caches()
        self.request.send_response(302)
        self.request.send_header('Location', '/opds')
        self.request.end_headers()

    def _handle_root_catalog(self):
        links = [
            (
                'self',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            ),
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            ),
            (
                'search',
                '/opds/opensearch.xml',
                'application/opensearchdescription+xml',
            ),
        ]

        entries = self._get_root_entries()
        xml = self.feed_generator.generate_feed('My Library', 'urn:library-root', links, entries)

        self._send_xml_response(xml, 'navigation')

    def _get_root_entries(self):
        entries = [
            {
                'title': 'All Books',
                'id': 'urn:all-books',
                'links': [
                    (
                        'subsection',
                        '/opds/books?page=1',
                        'application/atom+xml;profile=opds-catalog;kind=acquisition',
                    )
                ],
            },
            {
                'title': 'Recent Books',
                'id': 'urn:recent-books',
                'links': [
                    (
                        'subsection',
                        '/opds/recent',
                        'application/atom+xml;profile=opds-catalog;kind=acquisition',
                    )
                ],
            },
            {
                'title': 'By Year',
                'id': 'urn:by-year',
                'links': [
                    (
                        'subsection',
                        '/opds/by-year',
                        'application/atom+xml;profile=opds-catalog;kind=navigation',
                    )
                ],
            },
            {
                'title': 'By Author',
                'id': 'urn:by-author',
                'links': [
                    (
                        'subsection',
                        '/opds/by-author',
                        'application/atom+xml;profile=opds-catalog;kind=navigation',
                    )
                ],
            },
        ]


        for folder in sorted(os.listdir(LIBRARY_DIR)):
            folder_path = os.path.join(LIBRARY_DIR, folder)
            if os.path.isdir(folder_path):
                folder_id = f'urn:folder:{hashlib.md5(folder.encode()).hexdigest()}'
                encoded_folder = quote(folder)
                entries.append(
                    {
                        'title': folder,
                        'id': folder_id,
                        'links': [
                            (
                                'subsection',
                                f'/opds/folder/{encoded_folder}?page=1',
                                'application/atom+xml;profile=opds-catalog;kind=acquisition',
                            )
                        ],
                    }
                )

        return entries

    def _handle_all_books(self):
        page, size, parsed_url = self._parse_url_params()
        path_base = parsed_url.path

        paginated_books, total_count = self.book_scanner.get_all_books_paginated(page, size)

        links = self._get_pagination_links(path_base, page, size, total_count)
        links.append(
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )

        entries = self._create_book_entries(paginated_books)

        total_pages = self._get_total_pages(total_count, size)
        title = f'All Books (Page {page} of {total_pages})'
        xml = self.feed_generator.generate_feed(title, 'urn:all-books', links, entries)

        self._send_xml_response(xml, 'acquisition')

    def _handle_recent_books(self):
        links = [
            (
                'self',
                '/opds/recent',
                'application/atom+xml;profile=opds-catalog;kind=acquisition',
            ),
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            ),
        ]

        file_list = self.book_scanner.scan_recent_books(LIBRARY_DIR, limit=25)
        entries = self._create_book_entries(file_list)
        xml = self.feed_generator.generate_feed('Recent Books', 'urn:recent-books', links, entries)

        self._send_xml_response(xml, 'acquisition')

    def _handle_folder_catalog(self):
        page, size, parsed_url = self._parse_url_params()

        folder_path = unquote(parsed_url.path[len('/opds/folder/'):])
        path_base = parsed_url.path
        folder_full_path = os.path.join(LIBRARY_DIR, folder_path)

        if not self._validate_folder_access(folder_full_path):
            return

        paginated_entries, total_count = self.book_scanner.get_folder_content_paginated(
            folder_full_path,
            folder_path,
            page,
            size,
            base_path=LIBRARY_DIR,
        )

        formatted_entries = []
        for entry in paginated_entries:
            if entry.get('type') == 'folder':
                formatted_entries.append(
                    {
                        'title': entry['title'],
                        'id': entry['id'],
                        'links': entry['links'],
                    }
                )
            else:
                formatted_entries.append(
                    {
                        'title': entry['title'],
                        'id': entry['id'],
                        'links': entry['links'],
                        'author': entry['author'],
                    }
                )

        links = self._get_pagination_links(path_base, page, size, total_count)
        links.append(
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )

        feed_id = f'urn:folder:{hashlib.md5(folder_path.encode()).hexdigest()}'
        title = os.path.basename(folder_path) or 'Library'

        total_pages = self._get_total_pages(total_count, size)
        if total_pages > 1:
            title = f'{title} (Page {page} of {total_pages})'

        xml = self.feed_generator.generate_feed(title, feed_id, links, formatted_entries)

        self._send_xml_response(xml, 'acquisition')

    def _handle_by_year_catalog(self):
        """Handle display of years catalog."""
        links = [
            (
                'self',
                '/opds/by-year',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            ),
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            ),
        ]

        years_with_counts = self.book_scanner.get_years_with_counts()
        entries = []
        for year, count in years_with_counts:
            year_id = f'urn:year:{year}'
            encoded_year = quote(year)
            entries.append({
                'title': f'{year} ({count} books)',
                'id': year_id,
                'links': [
                    (
                        'subsection',
                        f'/opds/by-year/{encoded_year}?page=1',
                        'application/atom+xml;profile=opds-catalog;kind=acquisition',
                    )
                ],
            })

        xml = self.feed_generator.generate_feed('By Year', 'urn:by-year', links, entries)
        self._send_xml_response(xml, 'navigation')

    def _handle_year_books(self):
        """Handle display of books for a specific year."""
        page, size, parsed_url = self._parse_url_params()
        year = unquote(parsed_url.path[len('/opds/by-year/'):])
        path_base = parsed_url.path

        paginated_books, total_count = self.book_scanner.get_books_for_year(year, page, size)

        links = self._get_pagination_links(path_base, page, size, total_count)
        links.append(
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )
        links.append(
            (
                'up',
                '/opds/by-year',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )

        entries = self._create_book_entries(paginated_books)

        total_pages = self._get_total_pages(total_count, size)
        title = f'Year {year}'
        if total_pages > 1:
            title = f'{title} (Page {page} of {total_pages})'

        xml = self.feed_generator.generate_feed(title, f'urn:year:{year}', links, entries)
        self._send_xml_response(xml, 'acquisition')

    def _handle_by_author_catalog(self):
        """Handle display of letters catalog for authors."""
        links = [
            (
                'self',
                '/opds/by-author',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            ),
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            ),
        ]

        letters = self.book_scanner.get_letters_with_author_counts()
        entries = []
        for letter in letters:
            letter_id = f'urn:author-letter:{letter}'
            encoded_letter = quote(letter)
            display_letter = letter if letter != '#' else '# (Autres)'
            entries.append({
                'title': display_letter,
                'id': letter_id,
                'links': [
                    (
                        'subsection',
                        f'/opds/by-author/letter/{encoded_letter}?page=1',
                        'application/atom+xml;profile=opds-catalog;kind=navigation',
                    )
                ],
            })

        xml = self.feed_generator.generate_feed('By Author', 'urn:by-author', links, entries)
        self._send_xml_response(xml, 'navigation')

    def _handle_author_letter_catalog(self):
        """Handle display of authors for a specific letter."""
        page, size, parsed_url = self._parse_url_params()
        letter = unquote(parsed_url.path[len('/opds/by-author/letter/'):])
        path_base = parsed_url.path

        paginated_authors, total_count = self.book_scanner.get_authors_by_letter(letter, page, size)

        links = self._get_pagination_links(path_base, page, size, total_count)
        links.append(
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )
        links.append(
            (
                'up',
                '/opds/by-author',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )

        # Add letter navigation links
        all_letters = self.book_scanner.get_letters_with_author_counts()
        for nav_letter in all_letters:
            encoded_nav_letter = quote(nav_letter)
            links.append(
                (
                    f'http://opds-spec.org/facet#{nav_letter}',
                    f'/opds/by-author/letter/{encoded_nav_letter}?page=1',
                    'application/atom+xml;profile=opds-catalog;kind=navigation',
                )
            )

        entries = []
        for author, count in paginated_authors:
            author_id = f'urn:author:{hashlib.md5(author.encode()).hexdigest()}'
            encoded_author = quote(author)
            entries.append({
                'title': f'{author} ({count} livres)',
                'id': author_id,
                'links': [
                    (
                        'subsection',
                        f'/opds/by-author/{encoded_author}?page=1',
                        'application/atom+xml;profile=opds-catalog;kind=acquisition',
                    )
                ],
            })

        total_pages = self._get_total_pages(total_count, size)
        display_letter = letter if letter != '#' else '# (Autres)'
        title = f'Auteurs - {display_letter}'
        if total_pages > 1:
            title = f'{title} (Page {page} of {total_pages})'

        xml = self.feed_generator.generate_feed(title, f'urn:author-letter:{letter}', links, entries)
        self._send_xml_response(xml, 'navigation')

    def _handle_author_books(self):
        """Handle display of books for a specific author."""
        page, size, parsed_url = self._parse_url_params()
        author = unquote(parsed_url.path[len('/opds/by-author/'):])
        path_base = parsed_url.path

        paginated_books, total_count = self.book_scanner.get_books_for_author(author, page, size)

        links = self._get_pagination_links(path_base, page, size, total_count)
        links.append(
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )
        links.append(
            (
                'up',
                '/opds/by-author',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )

        entries = self._create_book_entries(paginated_books)

        total_pages = self._get_total_pages(total_count, size)
        title = author
        if total_pages > 1:
            title = f'{title} (Page {page} of {total_pages})'

        xml = self.feed_generator.generate_feed(title, f'urn:author:{hashlib.md5(author.encode()).hexdigest()}', links, entries)
        self._send_xml_response(xml, 'acquisition')

    def _get_total_pages(self, total_count, size=None):
        if size is None:
            size = PAGE_SIZE
        return max(1, (total_count + size - 1) // size)

    def _get_pagination_links(self, path_base, current_page, size, total_count):
        links = []

        total_pages = self._get_total_pages(total_count, size)

        links.append(
            (
                'self',
                f'{path_base}?page={current_page}',
                'application/atom+xml;profile=opds-catalog;kind=acquisition',
            )
        )

        if total_pages > 1:
            links.append(
                (
                    'first',
                    f'{path_base}?page=1',
                    'application/atom+xml;profile=opds-catalog;kind=acquisition',
                )
            )

        if current_page < total_pages:
            links.append(
                (
                    'next',
                    f'{path_base}?page={current_page + 1}',
                    'application/atom+xml;profile=opds-catalog;kind=acquisition',
                )
            )

        if current_page > 1:
            links.append(
                (
                    'previous',
                    f'{path_base}?page={current_page - 1}',
                    'application/atom+xml;profile=opds-catalog;kind=acquisition',
                )
            )

        if total_pages > 1:
            links.append(
                (
                    'last',
                    f'{path_base}?page={total_pages}',
                    'application/atom+xml;profile=opds-catalog;kind=acquisition',
                )
            )

        return links

    def _validate_folder_access(self, folder_full_path):
        if not self.security.is_within_library_dir(folder_full_path) or not os.path.isdir(folder_full_path):
            self._send_error(404, 'Folder not found or access denied')
            return False
        return True

    def _create_book_entries(self, file_list):
        entries = []
        for file_info in file_list:
            book_id = f'urn:book:{hashlib.md5(file_info["relative_path"].encode()).hexdigest()}'
            encoded_path = quote(file_info['relative_path'].replace(os.sep, '/'))

            entry_links = [
                (
                    'http://opds-spec.org/acquisition/open-access',
                    f'/download/{encoded_path}',
                    'application/epub+zip',
                )
            ]

            entries.append(
                {
                    'title': file_info['title'],
                    'id': book_id,
                    'links': entry_links,
                    'author': file_info['author'],
                }
            )

        return entries

    def _handle_download(self):
        filename = unquote(self.request.path.split('/download/')[1])

        if self.security.has_path_traversal(filename):
            self._send_error(403, 'Access denied: Invalid path')
            return

        file_path = os.path.join(LIBRARY_DIR, filename)

        if not self.security.is_within_library_dir(file_path):
            self._send_error(403, 'Access denied: Path traversal detected')
            return

        if self._is_valid_epub_file(filename, file_path):
            self._serve_file(file_path, filename)
        else:
            self._send_error(404, 'File not found')

    def _is_valid_epub_file(self, filename, file_path):
        return filename.endswith('.epub') and os.path.exists(file_path)

    def _serve_file(self, file_path, filename):
        self.request.send_response(200)
        self.request.send_header('Content-Type', 'application/epub+zip')
        
        # Handle Unicode filenames per RFC 5987
        basename = os.path.basename(filename)
        try:
            # Try ASCII encoding first (simple case)
            basename.encode('ascii')
            self.request.send_header(
                'Content-Disposition',
                f'attachment; filename="{basename}"',
            )
        except UnicodeEncodeError:
            # Use RFC 5987 encoding for non-ASCII filenames
            # Provide ASCII fallback + UTF-8 encoded version
            ascii_name = basename.encode('ascii', 'replace').decode('ascii').replace('?', '_')
            encoded_name = quote(basename, safe='')
            self.request.send_header(
                'Content-Disposition',
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}",
            )
        
        # Add Content-Length for download progress
        file_size = os.path.getsize(file_path)
        self.request.send_header('Content-Length', str(file_size))
        self.request.end_headers()

        # Stream file in chunks to avoid loading entire file into memory
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):  # 8KB chunks
                self.request.wfile.write(chunk)

    def _handle_cover_download(self):
        """Handle cover image download requests."""
        filename = unquote(self.request.path.split('/cover/')[1])

        if self.security.has_path_traversal(filename):
            self._send_error(403, 'Access denied: Invalid path')
            return

        file_path = os.path.join(LIBRARY_DIR, filename)

        if not self.security.is_within_library_dir(file_path):
            self._send_error(403, 'Access denied: Path traversal detected')
            return

        if not filename.endswith('.epub') or not os.path.exists(file_path):
            self._send_error(404, 'File not found')
            return

        # Extract cover from EPUB
        cover_data, mime_type = BookMetadata.extract_epub_cover(file_path)

        if cover_data is None:
            self._send_error(404, 'Cover not found in EPUB')
            return

        # Serve the cover image
        self.request.send_response(200)
        self.request.send_header('Content-Type', mime_type)
        self.request.send_header('Cache-Control', 'public, max-age=86400')
        self.request.end_headers()
        self.request.wfile.write(cover_data)

    def _handle_opensearch_description(self):
        """Generate and serve OpenSearch description document."""
        # Get the host from the request headers
        host = self.request.headers.get('Host', 'localhost:8080')
        scheme = 'http'  # Could be enhanced to detect HTTPS
        base_url = f'{scheme}://{host}'
        
        opensearch_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>PyOPDS</ShortName>
  <Description>Search books in PyOPDS catalog</Description>
  <Tags>opds epub books</Tags>
  <Url type="application/atom+xml;profile=opds-catalog;kind=acquisition" 
       template="{base_url}/opds/search?q={{searchTerms}}&amp;page={{startPage?}}"/>
</OpenSearchDescription>'''
        
        body = opensearch_xml.encode('utf-8')
        self.request.send_response(200)
        self.request.send_header('Content-Type', 'application/opensearchdescription+xml')
        self.request.send_header('Content-Length', str(len(body)))
        self.request.end_headers()
        self.request.wfile.write(body)

    def _handle_search_results(self):
        """Handle search requests and display results."""
        page, size, parsed_url = self._parse_url_params()
        query_params = parse_qs(parsed_url.query)
        
        # Get search query
        query = query_params.get('q', [''])[0]
        
        # Perform search
        search_results, total_count = self.book_scanner.search_books(query, page, size)
        
        # Generate pagination links
        path_base = '/opds/search'
        links = []
        
        total_pages = self._get_total_pages(total_count, size)
        
        # Self link with query
        query_string = f'q={quote(query)}&page={page}'
        links.append(
            (
                'self',
                f'{path_base}?{query_string}',
                'application/atom+xml;profile=opds-catalog;kind=acquisition',
            )
        )
        
        # Start link (root catalog)
        links.append(
            (
                'start',
                '/opds',
                'application/atom+xml;profile=opds-catalog;kind=navigation',
            )
        )
        
        # Pagination links
        if page < total_pages:
            next_query = f'q={quote(query)}&page={page + 1}'
            links.append(
                (
                    'next',
                    f'{path_base}?{next_query}',
                    'application/atom+xml;profile=opds-catalog;kind=acquisition',
                )
            )
        
        if page > 1:
            prev_query = f'q={quote(query)}&page={page - 1}'
            links.append(
                (
                    'previous',
                    f'{path_base}?{prev_query}',
                    'application/atom+xml;profile=opds-catalog;kind=acquisition',
                )
            )
        
        if total_pages > 1:
            first_query = f'q={quote(query)}&page=1'
            last_query = f'q={quote(query)}&page={total_pages}'
            links.append(
                (
                    'first',
                    f'{path_base}?{first_query}',
                    'application/atom+xml;profile=opds-catalog;kind=acquisition',
                )
            )
            links.append(
                (
                    'last',
                    f'{path_base}?{last_query}',
                    'application/atom+xml;profile=opds-catalog;kind=acquisition',
                )
            )
        
        # Create book entries
        entries = self._create_book_entries(search_results)
        
        # Generate title
        if query:
            title = f'Search results for "{query}"'
            if total_pages > 1:
                title = f'{title} (Page {page} of {total_pages})'
        else:
            title = f'All Books (Page {page} of {total_pages})'
        
        # Generate feed
        feed_id = f'urn:search:{hashlib.md5(query.encode()).hexdigest()}'
        xml = self.feed_generator.generate_feed(title, feed_id, links, entries)
        
        self._send_xml_response(xml, 'acquisition')

    def _serve_xslt(self):
        try:
            xslt_path = os.path.join('static', 'opds_to_html.xslt')
            if not os.path.exists(xslt_path):
                self._send_error(404, "XSLT file not found")
                return

            self.request.send_response(200)
            self.request.send_header('Content-Type', 'application/xml')
            self.request.end_headers()
            with open(xslt_path, 'rb') as f:
                self.request.wfile.write(f.read())
        except Exception as exc:
            self._send_error(500, f"Error serving XSLT file: {exc}")

    def _send_xml_response(self, xml, catalog_kind):
        body = xml.encode('utf-8')
        self.request.send_response(200)
        self.request.send_header(
            'Content-Type',
            f'application/xml;profile=opds-catalog;kind={catalog_kind}',
        )
        self.request.send_header('Content-Length', str(len(body)))
        self.request.end_headers()
        self.request.wfile.write(body)

    def _send_error(self, code, message):
        self.request.send_response(code)
        self.request.send_header('Content-Type', 'application/xml')
        self.request.end_headers()
        safe_message = xml_escape(str(message))
        error_xml = (
            '<?xml version="1.0" encoding="UTF-8"?><error><code>'
            f'{code}</code><message>{safe_message}</message></error>'
        )
        self.request.wfile.write(error_xml.encode('utf-8'))


__all__ = [
    'LIBRARY_DIR',
    'PAGE_SIZE',
    'OPDSController',
    'BookMetadata',
    'SecurityUtils',
    'OPDSFeedGenerator',
    'BookScanner',
]
