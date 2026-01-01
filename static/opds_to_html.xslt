<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:atom="http://www.w3.org/2005/Atom"
    exclude-result-prefixes="atom">

    <xsl:output method="html" encoding="UTF-8" indent="yes"/>

    <xsl:template match="/atom:feed">
        <html>
            <head>
                <title><xsl:value-of select="atom:title"/></title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
                <script src="https://cdn.tailwindcss.com"></script>
                <script>
                    tailwind.config = {
                        darkMode: 'class'
                    }
                </script>
                <script src="https://unpkg.com/lucide@latest"></script>
                <style>
                    .book-link:nth-child(6n+1) .book { background-color: #e0f2f1; } /* Teal */
                    .book-link:nth-child(6n+2) .book { background-color: #fbe9e7; } /* Peach */
                    .book-link:nth-child(6n+3) .book { background-color: #e8eaf6; } /* Lavender */
                    .book-link:nth-child(6n+4) .book { background-color: #f9fbe7; } /* Lime */
                    .book-link:nth-child(6n+5) .book { background-color: #fff3e0; } /* Light Orange */
                    .book-link:nth-child(6n+6) .book { background-color: #fce4ec; } /* Light Pink */
                    
                    /* Dark mode colors for books */
                    .dark .book-link:nth-child(6n+1) .book { background-color: #134e4a; } /* Dark Teal */
                    .dark .book-link:nth-child(6n+2) .book { background-color: #7c2d12; } /* Dark Peach */
                    .dark .book-link:nth-child(6n+3) .book { background-color: #312e81; } /* Dark Lavender */
                    .dark .book-link:nth-child(6n+4) .book { background-color: #365314; } /* Dark Lime */
                    .dark .book-link:nth-child(6n+5) .book { background-color: #9a3412; } /* Dark Orange */
                    .dark .book-link:nth-child(6n+6) .book { background-color: #831843; } /* Dark Pink */

                    .book-cover {
                        position: absolute;
                        top: 0;
                        left: 0;
                        right: 0;
                        bottom: 0;
                        object-fit: cover;
                        opacity: 0;
                        transition: opacity 0.3s ease-in-out;
                        height: 100%;
                        width: 100%;
                    }

                    .book-cover.loaded {
                        opacity: 1;
                    }

                    .book-cover-fallback {
                        transition: opacity 0.3s ease-in-out;
                    }

                    .book-cover.loaded ~ .book-cover-fallback {
                        opacity: 0;
                    }
                </style>
            </head>
            <body class="bg-slate-50 font-sans dark:bg-slate-900">
                <div class="bg-white/80 backdrop-blur-sm sticky top-0 z-10 shadow-sm px-4 py-2.5 flex items-center gap-3 dark:bg-slate-800/80 dark:shadow-slate-700">
                    <a id="back-button" class="hidden items-center text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 gap-1.5 flex-shrink-0" style="text-decoration: none;">
                        <i data-lucide="arrow-left" class="w-4 h-4"></i>
                        <span class="text-sm">Retour</span>
                    </a>
                    <h1 class="text-lg font-light text-slate-800 truncate flex-shrink-0 dark:text-slate-200"><xsl:value-of select="atom:title"/></h1>
                    <!-- Search form: hidden on mobile, visible on md+ -->
                    <form id="search-form" class="hidden md:flex flex-grow max-w-sm mx-auto m-0" action="/opds/search" method="get">
                        <div class="relative w-full">
                            <input 
                                type="text" 
                                name="q" 
                                id="search-input"
                                placeholder="Rechercher..." 
                                class="w-full h-7 px-3 pr-8 text-sm rounded-md border border-slate-300 bg-white text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-400 dark:bg-slate-700 dark:border-slate-600 dark:text-slate-100 dark:placeholder-slate-400 dark:focus:ring-slate-500"
                            />
                            <button 
                                type="submit" 
                                class="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
                                title="Rechercher"
                            >
                                <i data-lucide="search" class="w-4 h-4"></i>
                            </button>
                        </div>
                    </form>
                    <!-- Spacer for mobile to push icons to right -->
                    <div class="flex-grow md:hidden"></div>
                    <div id="quick-links" class="flex items-center gap-3 flex-shrink-0">
                        <!-- Mobile search toggle button -->
                        <button id="search-toggle" type="button" class="md:hidden text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200" title="Rechercher">
                            <i data-lucide="search" class="w-4 h-4"></i>
                        </button>
                        <a href="/opds" class="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 flex items-center" title="Accueil">
                            <i data-lucide="home" class="w-4 h-4"></i>
                        </a>
                        <a href="/opds/refresh" class="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 flex items-center" title="Rafraîchir le cache">
                            <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                        </a>
                        <button id="theme-toggle" type="button" class="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200">
                            <i id="theme-toggle-dark-icon" class="hidden w-4 h-4" data-lucide="moon"></i>
                            <i id="theme-toggle-light-icon" class="hidden w-4 h-4" data-lucide="sun"></i>
                        </button>
                    </div>
                </div>
                <!-- Mobile search overlay -->
                <div id="mobile-search-overlay" class="hidden fixed inset-0 bg-black/50 z-20 md:hidden" style="display: none;"></div>
                <div id="mobile-search-bar" class="hidden fixed top-0 left-0 right-0 bg-white dark:bg-slate-800 z-30 p-3 shadow-lg md:hidden" style="display: none;">
                    <form id="mobile-search-form" class="flex items-center gap-2" action="/opds/search" method="get">
                        <button type="button" id="close-search" class="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200">
                            <i data-lucide="x" class="w-5 h-5"></i>
                        </button>
                        <input 
                            type="text" 
                            name="q" 
                            id="mobile-search-input"
                            placeholder="Rechercher un livre ou un auteur..." 
                            class="flex-grow h-9 px-3 text-sm rounded-md border border-slate-300 bg-white text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-400 dark:bg-slate-700 dark:border-slate-600 dark:text-slate-100 dark:placeholder-slate-400 dark:focus:ring-slate-500"
                        />
                        <button type="submit" class="px-3 py-2 bg-slate-700 text-white rounded-md hover:bg-slate-600 dark:bg-slate-600 dark:hover:bg-slate-500">
                            <i data-lucide="search" class="w-4 h-4"></i>
                        </button>
                    </form>
                </div>
                <!-- System Collections -->
                <xsl:if test="atom:entry[atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author')]">
                    <div class="p-4 sm:p-6">
                        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-4 sm:gap-6">
                            <xsl:apply-templates select="atom:entry[atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author')]"/>
                        </div>
                    </div>
                </xsl:if>

                <!-- Divider between System Collections and Disk Folders -->
                <xsl:if test="atom:entry[atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author')] and atom:entry[atom:link[@rel='subsection'] and not(atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author' or starts-with(atom:id, 'urn:author-letter:') or starts-with(atom:id, 'urn:author:'))]">
                    <div class="px-4 sm:px-6 py-6">
                        <div class="flex items-center gap-4">
                            <div class="flex-grow h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent dark:via-slate-700"></div>
                            <div class="flex items-center gap-2 text-slate-500 dark:text-slate-400">
                                <i data-lucide="hard-drive" class="w-5 h-5"></i>
                                <span class="text-sm font-medium uppercase tracking-wider">Files on disk</span>
                            </div>
                            <div class="flex-grow h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent dark:via-slate-700"></div>
                        </div>
                    </div>
                </xsl:if>

                <!-- Disk Folders and other entries -->
                <xsl:if test="atom:entry[not(atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author'))]">
                    <div class="p-4 sm:p-6 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-4 sm:gap-6">
                        <xsl:apply-templates select="atom:entry[not(atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author'))]"/>
                    </div>
                </xsl:if>

                <!-- Unified Navigation: Letter Navigation + Pagination -->
                <xsl:if test="atom:link[starts-with(@rel, 'http://opds-spec.org/facet#')] or atom:link[@rel='first'] or atom:link[@rel='previous'] or atom:link[@rel='next'] or atom:link[@rel='last']">
                    <div class="bg-white/50 dark:bg-slate-800/50 backdrop-blur-sm border-t border-slate-200 dark:border-slate-700">
                        <div class="p-4 sm:p-6">
                            <!-- Letter Navigation (for author pages) -->
                            <xsl:if test="atom:link[starts-with(@rel, 'http://opds-spec.org/facet#')]">
                                <div class="flex flex-wrap justify-center items-center gap-2 mb-4">
                                    <xsl:for-each select="atom:link[starts-with(@rel, 'http://opds-spec.org/facet#')]">
                                        <xsl:variable name="letter" select="substring-after(@rel, 'http://opds-spec.org/facet#')"/>
                                        <a href="{@href}" class="px-3 py-2 rounded-md text-sm font-medium transition-colors hover:bg-slate-200 dark:hover:bg-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 shadow-sm">
                                            <xsl:value-of select="$letter"/>
                                        </a>
                                    </xsl:for-each>
                                </div>
                            </xsl:if>

                            <!-- Enhanced Pagination -->
                            <xsl:if test="atom:link[@rel='first'] or atom:link[@rel='previous'] or atom:link[@rel='next'] or atom:link[@rel='last']">
                                <div class="flex flex-col sm:flex-row justify-center items-center gap-4">
                                    <!-- Page Info -->
                                    <div id="page-info" class="text-sm text-slate-600 dark:text-slate-400 font-medium"></div>
                                    
                                    <!-- Pagination Controls -->
                                    <div class="flex items-center gap-2">
                                        <!-- First Page -->
                                        <xsl:if test="atom:link[@rel='first'] and atom:link[@rel='previous']">
                                            <a href="{atom:link[@rel='first']/@href}" class="p-2 rounded-md bg-white text-slate-600 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 shadow-sm transition-colors" title="Première page">
                                                <i data-lucide="chevrons-left" class="w-5 h-5"></i>
                                            </a>
                                        </xsl:if>
                                        
                                        <!-- Previous Page -->
                                        <xsl:if test="atom:link[@rel='previous']">
                                            <a href="{atom:link[@rel='previous']/@href}" class="p-2 rounded-md bg-white text-slate-600 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 shadow-sm transition-colors" title="Page précédente">
                                                <i data-lucide="chevron-left" class="w-5 h-5"></i>
                                            </a>
                                        </xsl:if>

                                        <!-- Page Numbers Container -->
                                        <div id="page-numbers" class="flex items-center gap-1"></div>

                                        <!-- Next Page -->
                                        <xsl:if test="atom:link[@rel='next']">
                                            <a href="{atom:link[@rel='next']/@href}" class="p-2 rounded-md bg-white text-slate-600 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 shadow-sm transition-colors" title="Page suivante">
                                                <i data-lucide="chevron-right" class="w-5 h-5"></i>
                                            </a>
                                        </xsl:if>
                                        
                                        <!-- Last Page -->
                                        <xsl:if test="atom:link[@rel='last'] and atom:link[@rel='next']">
                                            <a href="{atom:link[@rel='last']/@href}" class="p-2 rounded-md bg-white text-slate-600 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 shadow-sm transition-colors" title="Dernière page">
                                                <i data-lucide="chevrons-right" class="w-5 h-5"></i>
                                            </a>
                                        </xsl:if>
                                    </div>
                                </div>
                            </xsl:if>
                        </div>
                    </div>
                </xsl:if>

                <script>
                    // On page load or when changing themes, best to add inline in `head` to avoid FOUC
                    if (localStorage.getItem('color-theme') === 'dark' || (!('color-theme' in localStorage) &amp;&amp; window.matchMedia('(prefers-color-scheme: dark)').matches)) {
                        document.documentElement.classList.add('dark');
                    } else {
                        document.documentElement.classList.remove('dark')
                    }

                    lucide.createIcons();

                    const backButton = document.getElementById('back-button');
                    const currentPath = window.location.pathname;

                    let parentPath = '';
                    if (currentPath.startsWith('/opds/folder/')) {
                        const pathParts = currentPath.substring('/opds/folder/'.length).split('/').filter(p => p);
                        if (pathParts.length > 1) {
                            parentPath = '/opds/folder/' + pathParts.slice(0, -1).join('/');
                        } else {
                            parentPath = '/opds';
                        }
                    } else if (currentPath === '/opds/books' || currentPath === '/opds/recent') {
                        parentPath = '/opds';
                    } else if (currentPath.startsWith('/opds/by-year/')) {
                        parentPath = '/opds/by-year';
                    } else if (currentPath === '/opds/by-year' || currentPath === '/opds/by-author') {
                        parentPath = '/opds';
                    } else if (currentPath.startsWith('/opds/by-author/letter/')) {
                        parentPath = '/opds/by-author';
                    } else if (currentPath.startsWith('/opds/by-author/')) {
                        // Get the letter from the author name for proper back navigation
                        const authorPart = decodeURIComponent(currentPath.substring('/opds/by-author/'.length));
                        if (authorPart &amp;&amp; !authorPart.startsWith('letter/')) {
                            const firstChar = authorPart.charAt(0).toUpperCase();
                            if (/[A-Z]/.test(firstChar)) {
                                parentPath = '/opds/by-author/letter/' + firstChar + '?page=1';
                            } else {
                                parentPath = '/opds/by-author/letter/%23?page=1';
                            }
                        } else {
                            parentPath = '/opds/by-author';
                        }
                    } else if (currentPath === '/opds/search') {
                        parentPath = '/opds';
                    }

                    if (parentPath) {
                        backButton.href = parentPath;
                        backButton.style.display = 'flex';
                    }

                    // Initialize search form
                    const searchInput = document.getElementById('search-input');
                    const mobileSearchInput = document.getElementById('mobile-search-input');
                    if (currentPath === '/opds/search') {
                        // Pre-fill search input from URL query parameter
                        const urlParams = new URLSearchParams(window.location.search);
                        const query = urlParams.get('q');
                        if (query) {
                            if (searchInput) searchInput.value = query;
                            if (mobileSearchInput) mobileSearchInput.value = query;
                        }
                    }

                    // Mobile search toggle
                    const searchToggle = document.getElementById('search-toggle');
                    const mobileSearchBar = document.getElementById('mobile-search-bar');
                    const mobileSearchOverlay = document.getElementById('mobile-search-overlay');
                    const closeSearch = document.getElementById('close-search');

                    function openMobileSearch() {
                        if (mobileSearchBar &amp;&amp; mobileSearchOverlay) {
                            mobileSearchBar.style.display = 'block';
                            mobileSearchOverlay.style.display = 'block';
                            if (mobileSearchInput) mobileSearchInput.focus();
                            lucide.createIcons();
                        }
                    }

                    function closeMobileSearch() {
                        if (mobileSearchBar &amp;&amp; mobileSearchOverlay) {
                            mobileSearchBar.style.display = 'none';
                            mobileSearchOverlay.style.display = 'none';
                        }
                    }

                    if (searchToggle) {
                        searchToggle.addEventListener('click', openMobileSearch);
                    }
                    if (closeSearch) {
                        closeSearch.addEventListener('click', closeMobileSearch);
                    }
                    if (mobileSearchOverlay) {
                        mobileSearchOverlay.addEventListener('click', closeMobileSearch);
                    }

                    var themeToggleDarkIcon = document.getElementById('theme-toggle-dark-icon');
                    var themeToggleLightIcon = document.getElementById('theme-toggle-light-icon');

                    // Change the icons inside the button based on previous settings
                    if (localStorage.getItem('color-theme') === 'dark' || (!('color-theme' in localStorage) &amp;&amp; window.matchMedia('(prefers-color-scheme: dark)').matches)) {
                        themeToggleLightIcon.classList.remove('hidden');
                    } else {
                        themeToggleDarkIcon.classList.remove('hidden');
                    }

                    var themeToggleBtn = document.getElementById('theme-toggle');

                    themeToggleBtn.addEventListener('click', function() {
                        // toggle icons inside button
                        themeToggleDarkIcon.classList.toggle('hidden');
                        themeToggleLightIcon.classList.toggle('hidden');

                        // if set via local storage previously
                        if (localStorage.getItem('color-theme')) {
                            if (localStorage.getItem('color-theme') === 'light') {
                                document.documentElement.classList.add('dark');
                                localStorage.setItem('color-theme', 'dark');
                            } else {
                                document.documentElement.classList.remove('dark');
                                localStorage.setItem('color-theme', 'light');
                            }
                        // if NOT set via local storage previously
                        } else {
                            if (document.documentElement.classList.contains('dark')) {
                                document.documentElement.classList.remove('dark');
                                localStorage.setItem('color-theme', 'light');
                            } else {
                                document.documentElement.classList.add('dark');
                                localStorage.setItem('color-theme', 'dark');
                            }
                        }
                    });

                    // Load book covers asynchronously only for visible books
                    function setupLazyCoverLoading() {
                        const coverImages = document.querySelectorAll('img[data-cover-src]');
                        
                        // Options for the Intersection Observer
                        const options = {
                            root: null, // viewport
                            rootMargin: '50px', // Start loading slightly before entering viewport
                            threshold: 0.01 // Trigger when at least 1% is visible
                        };
                        
                        // Callback function when an image enters the viewport
                        function handleIntersection(entries, observer) {
                            entries.forEach(function(entry) {
                                if (entry.isIntersecting) {
                                    const img = entry.target;
                                    const coverSrc = img.getAttribute('data-cover-src');
                                    
                                    // Stop observing this image
                                    observer.unobserve(img);
                                    
                                    // Create a new image to test if the cover exists
                                    const testImg = new Image();
                                    
                                    testImg.onload = function() {
                                        img.src = coverSrc;
                                        img.classList.add('loaded');
                                    };
                                    
                                    testImg.onerror = function() {
                                        // Cover not found or error loading, keep the fallback visible
                                        img.remove();
                                    };
                                    
                                    testImg.src = coverSrc;
                                }
                            });
                        }
                        
                        // Create the observer
                        const observer = new IntersectionObserver(handleIntersection, options);
                        
                        // Observe all cover images
                        coverImages.forEach(function(img) {
                            observer.observe(img);
                        });
                    }

                    // Setup enhanced pagination with numbered page links
                    function setupPagination() {
                        const pageInfoEl = document.getElementById('page-info');
                        const pageNumbersEl = document.getElementById('page-numbers');
                        
                        if (!pageInfoEl || !pageNumbersEl) return;
                        
                        // Parse current page and total pages from the title
                        const titleEl = document.querySelector('h1');
                        if (!titleEl) return;
                        
                        const titleText = titleEl.textContent;
                        const pageMatch = titleText.match(/Page (\d+) of (\d+)/);
                        
                        if (!pageMatch) return;
                        
                        const currentPage = parseInt(pageMatch[1]);
                        const totalPages = parseInt(pageMatch[2]);
                        
                        // Display page info
                        pageInfoEl.textContent = `Page ${currentPage} of ${totalPages}`;
                        
                        // Get base URL (remove query params)
                        const currentUrl = new URL(window.location.href);
                        const baseUrl = currentUrl.pathname;
                        
                        // Generate page numbers with smart truncation
                        const pageNumbers = [];
                        const maxVisible = 7; // Maximum number of page buttons to show
                        
                        if (totalPages &lt;= maxVisible) {
                            // Show all pages if total is small
                            for (let i = 1; i &lt;= totalPages; i++) {
                                pageNumbers.push(i);
                            }
                        } else {
                            // Smart truncation algorithm
                            pageNumbers.push(1); // Always show first page
                            
                            let rangeStart = Math.max(2, currentPage - 2);
                            let rangeEnd = Math.min(totalPages - 1, currentPage + 2);
                            
                            // Add ellipsis before range if needed
                            if (rangeStart &gt; 2) {
                                pageNumbers.push('...');
                            }
                            
                            // Add middle range
                            for (let i = rangeStart; i &lt;= rangeEnd; i++) {
                                pageNumbers.push(i);
                            }
                            
                            // Add ellipsis after range if needed
                            if (rangeEnd &lt; totalPages - 1) {
                                pageNumbers.push('...');
                            }
                            
                            pageNumbers.push(totalPages); // Always show last page
                        }
                        
                        // Create page number buttons
                        pageNumbers.forEach(function(pageNum) {
                            if (pageNum === '...') {
                                const ellipsis = document.createElement('span');
                                ellipsis.className = 'px-2 text-slate-400 dark:text-slate-500';
                                ellipsis.textContent = '...';
                                pageNumbersEl.appendChild(ellipsis);
                            } else {
                                const link = document.createElement('a');
                                link.href = `${baseUrl}?page=${pageNum}`;
                                
                                if (pageNum === currentPage) {
                                    link.className = 'px-3 py-2 rounded-md text-sm font-medium bg-slate-700 text-white dark:bg-slate-300 dark:text-slate-900 shadow-sm';
                                } else {
                                    link.className = 'px-3 py-2 rounded-md text-sm font-medium bg-white text-slate-700 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 shadow-sm transition-colors';
                                }
                                
                                link.textContent = pageNum;
                                pageNumbersEl.appendChild(link);
                            }
                        });
                    }

                    // Setup lazy loading and pagination when DOM is ready
                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', function() {
                            setupLazyCoverLoading();
                            setupPagination();
                        });
                    } else {
                        setupLazyCoverLoading();
                        setupPagination();
                    }
                </script>
            </body>
        </html>
    </xsl:template>

    <xsl:template match="atom:entry">
        <xsl:choose>
            <xsl:when test="atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author')">
                <a class="block group rounded-lg overflow-hidden transition-all duration-300 transform hover:-translate-y-1 hover:shadow-xl aspect-[2/3]">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='subsection']/@href"/>
                    </xsl:attribute>
                    <div class="h-full bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <xsl:choose>
                            <xsl:when test="atom:id = 'urn:all-books'">
                                <i data-lucide="library" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
                            </xsl:when>
                            <xsl:when test="atom:id = 'urn:recent-books'">
                                <i data-lucide="clock" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
                            </xsl:when>
                            <xsl:when test="atom:id = 'urn:by-year'">
                                <i data-lucide="calendar" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
                            </xsl:when>
                            <xsl:when test="atom:id = 'urn:by-author'">
                                <i data-lucide="users" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
                            </xsl:when>
                        </xsl:choose>
                        <div class="font-semibold text-slate-700 transition-colors dark:text-slate-300"><xsl:value-of select="atom:title"/></div>
                        <div class="text-sm text-slate-500 mt-1 dark:text-slate-400">System collection</div>
                    </div>
                </a>
            </xsl:when>
            <!-- Letter entries for author navigation -->
            <xsl:when test="atom:link[@rel='subsection'] and starts-with(atom:id, 'urn:author-letter:')">
                <a class="block group rounded-lg overflow-hidden transition-all duration-300 transform hover:-translate-y-1 hover:shadow-xl aspect-[2/3]">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='subsection']/@href"/>
                    </xsl:attribute>
                    <div class="h-full bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <xsl:variable name="letter" select="substring-after(atom:id, 'urn:author-letter:')"/>
                        <div class="text-4xl font-bold text-slate-600 dark:text-slate-300 mb-2">
                            <xsl:value-of select="$letter"/>
                        </div>
                        <div class="text-sm text-slate-500 mt-1 dark:text-slate-400">Auteurs</div>
                    </div>
                </a>
            </xsl:when>
            <!-- Author entries (with book count) -->
            <xsl:when test="atom:link[@rel='subsection'] and starts-with(atom:id, 'urn:author:')">
                <a class="block group rounded-lg overflow-hidden transition-all duration-300 transform hover:-translate-y-1 hover:shadow-xl aspect-[2/3]">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='subsection']/@href"/>
                    </xsl:attribute>
                    <div class="h-full bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <i data-lucide="user" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
                        <div class="font-semibold text-slate-700 transition-colors dark:text-slate-300"><xsl:value-of select="atom:title"/></div>
                        <div class="text-sm text-slate-500 mt-1 dark:text-slate-400">Auteur</div>
                    </div>
                </a>
            </xsl:when>
            <xsl:when test="atom:link[@rel='subsection']">
                <a class="block group rounded-lg overflow-hidden transition-all duration-300 transform hover:-translate-y-1 hover:shadow-xl aspect-[2/3]">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='subsection']/@href"/>
                    </xsl:attribute>
                    <div class="h-full bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <i data-lucide="folder" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
                        <div class="font-semibold text-slate-700 transition-colors dark:text-slate-300 "><xsl:value-of select="atom:title"/></div>
                        <div class="text-sm text-slate-500 mt-1 dark:text-slate-400">Collection</div>
                    </div>
                </a>
            </xsl:when>

            <xsl:otherwise>
                <a class="book-link block group transition-all duration-300 transform hover:-translate-y-2 aspect-[2/3]">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='http://opds-spec.org/acquisition/open-access']/@href"/>
                    </xsl:attribute>
                    <div class="book h-full flex flex-col p-4 text-center relative rounded-r-md shadow-md group-hover:shadow-xl transition-shadow duration-300 overflow-hidden">
                        <xsl:variable name="downloadHref" select="atom:link[@rel='http://opds-spec.org/acquisition/open-access']/@href"/>
                        <xsl:variable name="coverPath" select="concat('/cover/', substring-after($downloadHref, '/download/'))"/>
                        
                        <img class="book-cover rounded-r-md" loading="lazy">
                            <xsl:attribute name="data-cover-src">
                                <xsl:value-of select="$coverPath"/>
                            </xsl:attribute>
                            <xsl:attribute name="alt">
                                <xsl:value-of select="atom:title"/>
                            </xsl:attribute>
                        </img>
                        
                        <div class="book-cover-fallback absolute inset-0 flex flex-col">
                            <div class="absolute top-0 left-0 w-3 h-full bg-black/10 rounded-l-sm dark:bg-black/20" style="border-right: 1px solid rgba(0,0,0,0.1);"></div>
                            <div class="flex-grow flex items-center justify-center px-2">
                                <div class="font-bold text-base text-slate-800 dark:text-slate-100"><xsl:value-of select="atom:title"/></div>
                            </div>
                            <div class="text-sm text-slate-600 mt-2 truncate dark:text-slate-300"><xsl:value-of select="atom:author/atom:name"/></div>
                        </div>
                    </div>
                </a>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

</xsl:stylesheet>