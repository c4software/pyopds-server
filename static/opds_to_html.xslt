<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:atom="http://www.w3.org/2005/Atom"
    xmlns:dc="http://purl.org/dc/terms/"
    xmlns:pyopds="urn:pyopds:metadata"
    exclude-result-prefixes="atom dc pyopds">

    <xsl:output method="html" encoding="UTF-8" indent="yes"/>

    <xsl:template match="/atom:feed">
        <html>
            <head>
                <title><xsl:value-of select="atom:title"/></title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
                <!-- Built by tools/tailwind.py (automatically in the Docker image): works with the XSLT polyfill too -->
                <link rel="stylesheet" href="/static/tailwind.css"/>
                <!-- Catalog styles that Tailwind utilities cannot express (after tailwind.css: wins at equal specificity) -->
                <link rel="stylesheet" href="/static/catalog/catalog.css"/>
                <script src="https://unpkg.com/lucide@1.61.0"></script>
                <!-- Dark theme before the first paint (no flash); the theme button is static/catalog/theme.js -->
                <script>
                    (function () {
                        var stored = localStorage.getItem('color-theme');
                        var dark = stored === null ? window.matchMedia('(prefers-color-scheme: dark)').matches : stored === 'dark';
                        document.documentElement.classList.toggle('dark', dark);
                    })();
                </script>
            </head>
            <body class="bg-slate-50 font-sans dark:bg-slate-900">
                <div class="bg-white/80 backdrop-blur-sm sticky top-0 z-10 shadow-sm px-4 py-2.5 flex items-center gap-3 dark:bg-slate-800/80 dark:shadow-slate-700">
                    <a id="back-button" class="hidden items-center text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 gap-1.5 flex-shrink-0" style="text-decoration: none;">
                        <i data-lucide="arrow-left" class="w-4 h-4"></i>
                        <span class="text-sm">Retour</span>
                    </a>
                    <!-- Shrinks (truncated) so that the header buttons always fit on a phone -->
                    <h1 class="text-lg font-light text-slate-800 truncate min-w-0 dark:text-slate-200"><xsl:value-of select="atom:title"/></h1>
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
                    <div id="quick-links" class="flex items-center gap-2.5 sm:gap-3 flex-shrink-0">
                        <!-- Mobile search toggle button -->
                        <button id="search-toggle" type="button" class="md:hidden text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200" title="Rechercher">
                            <i data-lucide="search" class="w-4 h-4"></i>
                        </button>
                        <a href="/opds" class="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 flex items-center" title="Accueil">
                            <i data-lucide="home" class="w-4 h-4"></i>
                        </a>
                        <a href="/reading" class="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 flex items-center" title="Lecture et statistiques">
                            <i data-lucide="bar-chart-3" class="w-4 h-4"></i>
                        </a>
                        <!-- Account: sign-in button (enables « À lire ensuite »), or logout once signed in -->
                        <a id="login-link" href="/login" class="hidden text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 items-center gap-1" title="Se connecter pour obtenir des suggestions de lecture">
                            <i data-lucide="log-in" class="w-4 h-4"></i>
                            <span class="hidden sm:inline">Se connecter</span>
                        </a>
                        <a id="logout-link" href="/logout" class="hidden text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 items-center" title="Se déconnecter">
                            <i data-lucide="log-out" class="w-4 h-4"></i>
                        </a>
                        <a href="/opds/refresh" class="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 flex items-center" title="Rafraîchir le cache">
                            <i data-lucide="refresh-cw" class="w-4 h-4"></i>
                        </a>
                        <button id="mosaic-toggle" type="button" aria-pressed="false" class="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200" title="Activer la mosaïque de couvertures">
                            <i id="mosaic-toggle-on-icon" class="hidden w-4 h-4" data-lucide="image"></i>
                            <i id="mosaic-toggle-off-icon" class="w-4 h-4" data-lucide="image-off"></i>
                        </button>
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
                <!-- Feed subtitle (e.g. why the recommendations are empty) -->
                <xsl:if test="atom:subtitle">
                    <p class="px-4 sm:px-6 pt-4 text-sm text-slate-500 dark:text-slate-400"><xsl:value-of select="atom:subtitle"/></p>
                </xsl:if>
                <!-- System Collections -->
                <xsl:if test="atom:entry[atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author' or atom:id = 'urn:by-genre' or atom:id = 'urn:recommendations')]">
                    <div class="p-4 sm:p-6">
                        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-4 sm:gap-6">
                            <xsl:apply-templates select="atom:entry[atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author' or atom:id = 'urn:by-genre' or atom:id = 'urn:recommendations')]"/>
                        </div>
                    </div>
                </xsl:if>

                <!-- Divider between System Collections and Disk Folders -->
                <xsl:if test="atom:entry[atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author' or atom:id = 'urn:by-genre' or atom:id = 'urn:recommendations')] and atom:entry[atom:link[@rel='subsection'] and not(atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author' or atom:id = 'urn:by-genre' or atom:id = 'urn:recommendations' or starts-with(atom:id, 'urn:author-letter:') or starts-with(atom:id, 'urn:author:'))]">
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
                <xsl:if test="atom:entry[not(atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author' or atom:id = 'urn:by-genre' or atom:id = 'urn:recommendations'))]">
                    <div class="p-4 sm:p-6 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-4 sm:gap-6">
                        <xsl:apply-templates select="atom:entry[not(atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author' or atom:id = 'urn:by-genre' or atom:id = 'urn:recommendations'))]"/>
                    </div>
                </xsl:if>

                <!-- Unified Navigation: Letter Navigation + Pagination -->
                <xsl:if test="atom:link[starts-with(@rel, 'http://opds-spec.org/facet#')] or atom:link[@rel='previous'] or atom:link[@rel='next']">
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
                            <xsl:if test="atom:link[@rel='previous'] or atom:link[@rel='next']">
                                <div class="flex flex-col sm:flex-row justify-center items-center gap-4">
                                    <!-- Page Info -->
                                    <div id="page-info" class="text-sm text-slate-600 dark:text-slate-400 font-medium"></div>
                                    
                                    <!-- Pagination Controls -->
                                    <div class="flex items-center gap-2">
                                        <!-- First Page (href set by setupPagination) -->
                                        <a id="first-page" hidden="hidden" class="p-2 rounded-md bg-white text-slate-600 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 shadow-sm transition-colors" title="Première page">
                                                <i data-lucide="chevrons-left" class="w-5 h-5"></i>
                                            </a>
                                        
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
                                        
                                        <!-- Last Page (href set by setupPagination) -->
                                        <a id="last-page" hidden="hidden" class="p-2 rounded-md bg-white text-slate-600 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 shadow-sm transition-colors" title="Dernière page">
                                                <i data-lucide="chevrons-right" class="w-5 h-5"></i>
                                            </a>
                                    </div>
                                </div>
                            </xsl:if>
                        </div>
                    </div>
                </xsl:if>

                <!-- Book details: filled from the data-* attributes of the clicked book tile -->
                <dialog id="book-dialog" class="book-dialog bg-white text-slate-800 shadow-2xl dark:bg-slate-800 dark:text-slate-100" aria-labelledby="book-dialog-title">
                    <div class="relative p-5 sm:p-7">
                        <button type="button" id="book-dialog-close" class="absolute top-3 right-3 p-2 rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-100" title="Fermer" aria-label="Fermer">
                            <i data-lucide="x" class="w-5 h-5"></i>
                        </button>
                        <div class="flex flex-col sm:flex-row gap-5 sm:gap-7">
                            <div class="flex-shrink-0 mx-auto sm:mx-0 w-40 sm:w-56">
                                <div class="relative aspect-[2/3] rounded-r-md rounded-l-sm overflow-hidden shadow-lg bg-slate-200 dark:bg-slate-700">
                                    <div id="book-dialog-cover-fallback" class="absolute inset-0 flex flex-col items-center justify-center p-4 text-center">
                                        <div class="absolute top-0 left-0 w-3 h-full bg-black/10 dark:bg-black/20"></div>
                                        <div id="book-dialog-cover-title" class="font-bold text-slate-700 dark:text-slate-200"></div>
                                    </div>
                                    <img id="book-dialog-cover" class="absolute inset-0 w-full h-full object-cover" alt="" hidden="hidden"/>
                                </div>
                            </div>
                            <div class="flex-1 min-w-0">
                                <h2 id="book-dialog-title" class="text-xl sm:text-2xl font-semibold leading-tight pr-8 text-slate-900 dark:text-slate-50"></h2>
                                <!-- Author: link to all their books (plain text for an unknown author) -->
                                <a id="book-dialog-author" class="group/author mt-1.5 inline-flex items-center gap-1.5 max-w-full text-slate-600 dark:text-slate-300">
                                    <i id="book-dialog-author-icon" data-lucide="user-round" class="w-4 h-4 flex-shrink-0 text-slate-400"></i>
                                    <span id="book-dialog-author-name" class="truncate group-[[href]]/author:text-indigo-700 group-[[href]]/author:hover:underline dark:group-[[href]]/author:text-indigo-300"></span>
                                </a>
                                <div id="book-dialog-series-row" class="mt-3 flex items-center gap-1.5 text-sm" hidden="hidden">
                                    <i data-lucide="library" class="w-4 h-4 text-slate-400"></i>
                                    <a id="book-dialog-series" class="text-indigo-700 hover:underline dark:text-indigo-300"></a>
                                </div>
                                <div id="book-dialog-facts" class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-600 dark:text-slate-300"></div>
                                <div id="book-dialog-genres" class="mt-3 flex flex-wrap gap-1.5"></div>
                                <a id="book-dialog-download" class="mt-5 inline-flex items-center justify-center gap-2 w-full sm:w-auto px-5 py-2.5 rounded-lg bg-slate-800 text-white font-medium shadow hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-400 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white" download="">
                                    <i data-lucide="download" class="w-4 h-4"></i>
                                    <span>Télécharger</span>
                                </a>
                                <div id="book-dialog-description-block" class="mt-6" hidden="hidden">
                                    <p id="book-dialog-description" class="book-description text-sm leading-relaxed text-slate-700 dark:text-slate-300"></p>
                                    <button type="button" id="book-dialog-more" class="mt-2 text-sm font-medium text-indigo-700 hover:underline dark:text-indigo-300" hidden="hidden">Lire la suite</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </dialog>

                <!-- Page scripts (static/catalog/, versioned on the fly by serve_xslt): plain scripts run in order,
                     natively and with the XSLT polyfill; main.js starts everything -->
                <script src="/static/catalog/cover-cache.js"></script>
                <script src="/static/catalog/covers.js"></script>
                <script src="/static/catalog/mosaic.js"></script>
                <script src="/static/catalog/theme.js"></script>
                <script src="/static/catalog/back-button.js"></script>
                <script src="/static/catalog/search.js"></script>
                <script src="/static/catalog/account-button.js"></script>
                <script src="/static/catalog/pagination.js"></script>
                <script src="/static/catalog/book-sheet.js"></script>
                <script src="/static/catalog/main.js"></script>
            </body>
        </html>
    </xsl:template>

    <xsl:template match="atom:entry">
        <xsl:choose>
            <xsl:when test="atom:link[@rel='subsection'] and (atom:id = 'urn:all-books' or atom:id = 'urn:recent-books' or atom:id = 'urn:by-year' or atom:id = 'urn:by-author' or atom:id = 'urn:by-genre' or atom:id = 'urn:recommendations')">
                <a class="block group rounded-lg overflow-hidden transition-all duration-300 transform hover:-translate-y-1 hover:shadow-xl aspect-[2/3]">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='subsection']/@href"/>
                    </xsl:attribute>
                    <div class="h-full relative bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <xsl:call-template name="tile-mosaic"/>
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
                            <xsl:when test="atom:id = 'urn:by-genre'">
                                <i data-lucide="tags" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
                            </xsl:when>
                            <xsl:when test="atom:id = 'urn:recommendations'">
                                <i data-lucide="sparkles" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
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
                    <div class="h-full relative bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <xsl:call-template name="tile-mosaic"/>
                        <xsl:variable name="letter" select="substring-after(atom:id, 'urn:author-letter:')"/>
                        <div class="text-4xl font-bold text-slate-600 dark:text-slate-300 mb-2">
                            <xsl:value-of select="$letter"/>
                        </div>
                        <div class="text-sm text-slate-500 mt-1 dark:text-slate-400">Auteurs</div>
                    </div>
                </a>
            </xsl:when>
            <!-- Genre entries (with book count) -->
            <xsl:when test="atom:link[@rel='subsection'] and starts-with(atom:id, 'urn:genre:')">
                <a class="block group rounded-lg overflow-hidden transition-all duration-300 transform hover:-translate-y-1 hover:shadow-xl aspect-[2/3]">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='subsection']/@href"/>
                    </xsl:attribute>
                    <div class="h-full relative bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <xsl:call-template name="tile-mosaic"/>
                        <i data-lucide="tag" class="w-12 h-12 text-slate-400 mb-3 transition-colors dark:text-slate-500"></i>
                        <div class="font-semibold text-slate-700 transition-colors dark:text-slate-300"><xsl:value-of select="atom:title"/></div>
                        <div class="text-sm text-slate-500 mt-1 dark:text-slate-400">Genre</div>
                    </div>
                </a>
            </xsl:when>
            <!-- Author entries (with book count) -->
            <xsl:when test="atom:link[@rel='subsection'] and starts-with(atom:id, 'urn:author:')">
                <a class="block group rounded-lg overflow-hidden transition-all duration-300 transform hover:-translate-y-1 hover:shadow-xl aspect-[2/3]">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='subsection']/@href"/>
                    </xsl:attribute>
                    <div class="h-full relative bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <xsl:call-template name="tile-mosaic"/>
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
                    <div class="h-full relative bg-slate-100 flex flex-col items-center justify-center p-4 text-center border border-slate-200 dark:bg-slate-800 dark:border-slate-700">
                        <xsl:call-template name="tile-mosaic"/>
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
                    <!-- Book details for the details dialog (the link still downloads without JavaScript) -->
                    <xsl:attribute name="data-id"><xsl:value-of select="atom:id"/></xsl:attribute>
                    <xsl:attribute name="data-title"><xsl:value-of select="atom:title"/></xsl:attribute>
                    <xsl:attribute name="data-author"><xsl:value-of select="atom:author/atom:name"/></xsl:attribute>
                    <xsl:attribute name="data-cover"><xsl:value-of select="atom:link[@rel='http://opds-spec.org/image']/@href"/></xsl:attribute>
                    <xsl:if test="atom:summary">
                        <xsl:attribute name="data-summary"><xsl:value-of select="atom:summary"/></xsl:attribute>
                    </xsl:if>
                    <xsl:if test="dc:issued">
                        <xsl:attribute name="data-year"><xsl:value-of select="dc:issued"/></xsl:attribute>
                    </xsl:if>
                    <xsl:if test="pyopds:rating">
                        <xsl:attribute name="data-rating"><xsl:value-of select="pyopds:rating"/></xsl:attribute>
                    </xsl:if>
                    <xsl:if test="pyopds:series">
                        <xsl:attribute name="data-series"><xsl:value-of select="pyopds:series"/></xsl:attribute>
                        <xsl:attribute name="data-series-index"><xsl:value-of select="pyopds:series/@index"/></xsl:attribute>
                        <xsl:attribute name="data-series-href"><xsl:value-of select="atom:link[@rel='related']/@href"/></xsl:attribute>
                    </xsl:if>
                    <xsl:if test="atom:category">
                        <xsl:attribute name="data-genres">
                            <xsl:for-each select="atom:category">
                                <xsl:if test="position() &gt; 1"><xsl:text>&#10;</xsl:text></xsl:if>
                                <xsl:choose>
                                    <xsl:when test="@label"><xsl:value-of select="@label"/></xsl:when>
                                    <xsl:otherwise><xsl:value-of select="@term"/></xsl:otherwise>
                                </xsl:choose>
                            </xsl:for-each>
                        </xsl:attribute>
                    </xsl:if>
                    <div class="book h-full flex flex-col p-4 text-center relative rounded-r-md shadow-md group-hover:shadow-xl transition-shadow duration-300 overflow-hidden">
                        <!-- Cover declared by the feed: thumbnail first, full image otherwise -->
                        <xsl:variable name="coverPath">
                            <xsl:choose>
                                <xsl:when test="atom:link[@rel='http://opds-spec.org/image/thumbnail']">
                                    <xsl:value-of select="atom:link[@rel='http://opds-spec.org/image/thumbnail']/@href"/>
                                </xsl:when>
                                <xsl:otherwise>
                                    <xsl:value-of select="atom:link[@rel='http://opds-spec.org/image']/@href"/>
                                </xsl:otherwise>
                            </xsl:choose>
                        </xsl:variable>

                        <img class="book-cover rounded-r-md" loading="lazy">
                            <xsl:if test="string($coverPath)">
                                <xsl:attribute name="data-cover-src">
                                    <xsl:value-of select="$coverPath"/>
                                </xsl:attribute>
                            </xsl:if>
                            <xsl:attribute name="alt">
                                <xsl:value-of select="atom:title"/>
                            </xsl:attribute>
                        </img>
                        
                        <!-- Volume badge of a series book -->
                        <xsl:if test="pyopds:series/@index">
                            <span class="absolute top-1.5 right-1.5 z-10 px-1.5 py-0.5 rounded text-[11px] font-semibold leading-none bg-slate-900/70 text-white shadow-sm backdrop-blur-sm">
                                <xsl:attribute name="title">
                                    <xsl:value-of select="concat('Tome ', pyopds:series/@index, ' · ', pyopds:series)"/>
                                </xsl:attribute>
                                <xsl:value-of select="concat('T', pyopds:series/@index)"/>
                            </span>
                        </xsl:if>
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

    <!-- Background of a navigation tile: covers listed by the feed, animated by setupTileMosaics -->
    <xsl:template name="tile-mosaic">
        <xsl:if test="atom:link[@rel='urn:pyopds:mosaic-cover']">
            <div class="tile-mosaic" aria-hidden="true">
                <xsl:attribute name="data-covers">
                    <xsl:for-each select="atom:link[@rel='urn:pyopds:mosaic-cover']">
                        <xsl:if test="position() &gt; 1"><xsl:text> </xsl:text></xsl:if>
                        <xsl:value-of select="@href"/>
                    </xsl:for-each>
                </xsl:attribute>
            </div>
        </xsl:if>
    </xsl:template>

</xsl:stylesheet>