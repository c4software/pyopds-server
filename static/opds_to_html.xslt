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

                    /* Navigation tiles: animated mosaic of the covers of their books */
                    .tile-mosaic {
                        position: absolute;
                        inset: 0;
                        display: grid;
                        grid-template-columns: repeat(2, 1fr);
                        grid-template-rows: repeat(2, 1fr);
                        opacity: 0;
                        transition: opacity 0.6s ease-out;
                    }

                    .tile-mosaic.single {
                        grid-template-columns: 1fr;
                        grid-template-rows: 1fr;
                    }

                    /* Readability veil over the covers, matching the theme (slate-50 / slate-900) */
                    .tile-mosaic::after {
                        content: '';
                        position: absolute;
                        inset: 0;
                        background:
                            radial-gradient(ellipse at center, rgba(248, 250, 252, 0.55), rgba(248, 250, 252, 0) 70%),
                            linear-gradient(to bottom, rgba(248, 250, 252, 0.68), rgba(248, 250, 252, 0.82));
                        transition: opacity 0.3s ease-in-out;
                    }

                    .dark .tile-mosaic::after {
                        background:
                            radial-gradient(ellipse at center, rgba(15, 23, 42, 0.5), rgba(15, 23, 42, 0) 70%),
                            linear-gradient(to bottom, rgba(15, 23, 42, 0.7), rgba(15, 23, 42, 0.86));
                    }

                    /* On hover, the veil recedes slightly to reveal the covers */
                    .group:hover .has-mosaic .tile-mosaic::after {
                        opacity: 0.88;
                    }

                    .has-mosaic .tile-mosaic {
                        opacity: 1;
                    }

                    .tile-mosaic-cell {
                        position: relative;
                        overflow: hidden;
                    }

                    .tile-mosaic-cover {
                        position: absolute;
                        inset: 0;
                        width: 100%;
                        height: 100%;
                        object-fit: cover;
                        filter: saturate(0.8);
                        opacity: 0;
                        transition: opacity 1.5s ease-in-out;
                    }

                    /* First covers of a tile: shown at once, the whole mosaic fades in a single time */
                    .tile-mosaic-cover.instant {
                        transition: none;
                    }

                    .tile-mosaic-cover.loaded {
                        opacity: 1;
                    }

                    /* Text and icons change color along with the mosaic fade, not at once */
                    .relative > .tile-mosaic ~ *,
                    .relative > .tile-mosaic ~ * * {
                        transition: color 0.6s ease-out, text-shadow 0.6s ease-out;
                    }

                    .has-mosaic > :not(.tile-mosaic) {
                        position: relative;
                        color: #1e293b !important; /* slate-800 */
                        text-shadow: 0 0 6px rgba(248, 250, 252, 0.9), 0 0 2px rgba(248, 250, 252, 0.9);
                    }

                    .dark .has-mosaic > :not(.tile-mosaic) {
                        color: #f1f5f9 !important; /* slate-100 */
                        text-shadow: 0 0 6px rgba(15, 23, 42, 0.9), 0 1px 2px rgba(15, 23, 42, 0.9);
                    }

                    /* Book details dialog */
                    .book-dialog {
                        width: min(52rem, calc(100vw - 1.5rem));
                        max-width: calc(100vw - 1.5rem);
                        max-height: calc(100% - 1.5rem);
                        padding: 0;
                        border-radius: 1rem;
                        overflow-y: auto;
                        overscroll-behavior: contain;
                    }

                    .book-dialog::backdrop {
                        background: rgba(15, 23, 42, 0.6);
                        backdrop-filter: blur(3px);
                    }

                    .book-dialog[open] {
                        animation: book-dialog-in 0.18s ease-out;
                    }

                    @keyframes book-dialog-in {
                        from { opacity: 0; transform: translateY(12px) scale(0.98); }
                        to { opacity: 1; transform: none; }
                    }

                    .book-description {
                        white-space: pre-line;
                        overflow-wrap: anywhere;
                    }

                    .book-description.clamped {
                        display: -webkit-box;
                        -webkit-line-clamp: 8;
                        -webkit-box-orient: vertical;
                        overflow: hidden;
                    }

                    /* Rating: grey stars with the amber ones on top, cut at the rating */
                    .book-stars {
                        position: relative;
                        display: inline-block;
                        line-height: 1;
                        font-size: 1.125rem;
                        letter-spacing: 0.1em;
                        color: #cbd5e1; /* slate-300 */
                    }

                    .dark .book-stars {
                        color: #475569; /* slate-600 */
                    }

                    .book-stars-on {
                        position: absolute;
                        inset: 0 auto 0 0;
                        overflow: hidden;
                        white-space: nowrap;
                        color: #f59e0b; /* amber-500 */
                    }
                </style>
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
                                <a id="book-dialog-author" class="inline-block mt-1 text-slate-600 hover:text-slate-900 hover:underline dark:text-slate-300 dark:hover:text-white"></a>
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
                    } else if (currentPath.startsWith('/opds/by-genre/')) {
                        parentPath = '/opds/by-genre';
                    } else if (currentPath === '/opds/by-year' || currentPath === '/opds/by-author' || currentPath === '/opds/by-genre') {
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
                    } else if (currentPath === '/opds/search' || currentPath === '/opds/series') {
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

                    // Cover mosaic of the navigation tiles: chosen per browser, disabled by default
                    const MOSAIC_STORAGE_KEY = 'tile-mosaic';
                    let mosaicSession = null; // Running mosaic: its observer, timers and cover requests
                    let mosaicEnabled = (function() {
                        try {
                            return localStorage.getItem(MOSAIC_STORAGE_KEY) === 'on';
                        } catch (e) {
                            return false;
                        }
                    })();

                    function isMosaicEnabled() {
                        return mosaicEnabled;
                    }

                    function saveMosaicEnabled(enabled) {
                        mosaicEnabled = enabled;
                        try {
                            localStorage.setItem(MOSAIC_STORAGE_KEY, enabled ? 'on' : 'off');
                        } catch (e) {
                            // Storage unavailable: the choice only lasts for this page
                        }
                    }

                    function updateMosaicToggle(enabled) {
                        const button = document.getElementById('mosaic-toggle');
                        if (!button) return;
                        button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
                        button.title = enabled ? 'Désactiver la mosaïque de couvertures' : 'Activer la mosaïque de couvertures';
                        document.getElementById('mosaic-toggle-on-icon').classList.toggle('hidden', !enabled);
                        document.getElementById('mosaic-toggle-off-icon').classList.toggle('hidden', enabled);
                    }

                    // Stop the mosaic: no more timers nor cover requests, tiles back to their plain look
                    function stopTileMosaics() {
                        document.documentElement.classList.remove('mosaic-enabled');
                        if (!mosaicSession) return;
                        const session = mosaicSession;
                        mosaicSession = null;
                        session.active = false;
                        session.observer.disconnect();
                        session.timers.forEach(clearTimeout);
                        session.timers.clear();
                        session.images.forEach(function(img) {
                            img.onload = img.onerror = null;
                            img.src = '';
                        });
                        session.images.clear();
                        document.querySelectorAll('.tile-mosaic').forEach(function(mosaic) {
                            mosaic.replaceChildren();
                            mosaic.classList.remove('single');
                            mosaic.parentElement.classList.remove('has-mosaic');
                        });
                        session.thumbs.forEach(function(url) { URL.revokeObjectURL(url); });
                        session.thumbs.clear();
                    }

                    // Browser cache of the mosaic covers: small thumbnails in IndexedDB (the full EPUB covers
                    // are heavy for a mosaic cell), books without a cover, and the covers last shown per tile
                    const coverCache = (function() {
                        const DB_NAME = 'pyopds-mosaic';
                        const STORE = 'thumbs';
                        const MAX_ENTRIES = 800;
                        const TTL = 7 * 86400 * 1000;
                        const THUMB_WIDTH = 200;
                        const SEEN_KEY = 'tile-mosaic-seen';
                        let dbPromise = null;

                        function openDb() {
                            if (!dbPromise) {
                                dbPromise = new Promise(function(resolve) {
                                    try {
                                        const request = indexedDB.open(DB_NAME, 1);
                                        request.onupgradeneeded = function() {
                                            request.result.createObjectStore(STORE).createIndex('at', 'at');
                                        };
                                        request.onsuccess = function() { resolve(request.result); };
                                        request.onerror = function() { resolve(null); };
                                    } catch (e) {
                                        resolve(null);
                                    }
                                });
                            }
                            return dbPromise;
                        }

                        function run(mode, fn) {
                            return openDb().then(function(db) {
                                if (!db) return null;
                                return new Promise(function(resolve) {
                                    try {
                                        const tx = db.transaction(STORE, mode);
                                        const request = fn(tx.objectStore(STORE));
                                        tx.oncomplete = function() { resolve(request ? request.result : null); };
                                        tx.onerror = tx.onabort = function() { resolve(null); };
                                    } catch (e) {
                                        resolve(null);
                                    }
                                });
                            });
                        }

                        // Drop the oldest thumbnails above the size limit
                        function prune() {
                            run('readwrite', function(store) {
                                const count = store.count();
                                count.onsuccess = function() {
                                    let excess = count.result - MAX_ENTRIES;
                                    if (excess &lt;= 0) return;
                                    store.index('at').openCursor().onsuccess = function(event) {
                                        const cursor = event.target.result;
                                        if (!cursor || excess-- &lt;= 0) return;
                                        cursor.delete();
                                        cursor.continue();
                                    };
                                };
                                return null;
                            });
                        }

                        // Cached entry of a cover URL: {blob} or {missing: true}, null when unknown or expired
                        async function get(url) {
                            const entry = await run('readonly', function(store) { return store.get(url); });
                            return entry &amp;&amp; Date.now() - entry.at &lt; TTL ? entry : null;
                        }

                        function put(url, entry) {
                            entry.at = Date.now();
                            run('readwrite', function(store) { return store.put(entry, url); }).then(prune);
                        }

                        // Thumbnail of a loaded cover, as a JPEG blob (null if the browser cannot make one)
                        function thumbnail(img) {
                            return new Promise(function(resolve) {
                                try {
                                    const scale = Math.min(1, THUMB_WIDTH / img.naturalWidth);
                                    const canvas = document.createElement('canvas');
                                    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
                                    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
                                    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
                                    canvas.toBlob(resolve, 'image/jpeg', 0.8);
                                } catch (e) {
                                    resolve(null);
                                }
                            });
                        }

                        function loadSeen() {
                            try {
                                return JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') || {};
                            } catch (e) {
                                return {};
                            }
                        }

                        // Covers last shown by a tile, to show them again right away on the next visit
                        function seen(tile) {
                            return loadSeen()[tile] || [];
                        }

                        function remember(tile, covers) {
                            const all = loadSeen();
                            delete all[tile];
                            all[tile] = covers.slice(0, 8);
                            const tiles = Object.keys(all);
                            tiles.slice(0, Math.max(0, tiles.length - 200)).forEach(function(key) { delete all[key]; });
                            try {
                                localStorage.setItem(SEEN_KEY, JSON.stringify(all));
                            } catch (e) {
                                // Storage full or unavailable: covers are only remembered for this page
                            }
                        }

                        return { get: get, put: put, thumbnail: thumbnail, seen: seen, remember: remember };
                    })();

                    // Fill the navigation tiles with a mosaic of covers, one cell changing every few seconds
                    function setupTileMosaics() {
                        if (mosaicSession || !isMosaicEnabled()) return;
                        document.documentElement.classList.add('mosaic-enabled');
                        const session = { observer: null, timers: new Set(), images: new Set(), thumbs: new Map(), active: true };
                        mosaicSession = session;
                        const CELLS = 4;
                        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

                        function schedule(fn, delay) {
                            if (!session.active) return;
                            const id = setTimeout(function() {
                                session.timers.delete(id);
                                fn();
                            }, delay);
                            session.timers.add(id);
                        }

                        function useThumbnail(src, blob) {
                            if (session.active &amp;&amp; blob &amp;&amp; !session.thumbs.has(src)) session.thumbs.set(src, URL.createObjectURL(blob));
                        }

                        // Resolve with the cover URL once displayable (from the browser cache when known),
                        // or null when the EPUB has no cover (or the mosaic was stopped)
                        async function loadCover(src) {
                            if (!session.active) return null;
                            const cached = await coverCache.get(src);
                            if (cached) {
                                useThumbnail(src, cached.blob);
                                return session.active &amp;&amp; !cached.missing ? src : null;
                            }
                            const img = await new Promise(function(resolve) {
                                const image = new Image();
                                session.images.add(image);
                                image.onload = function() { session.images.delete(image); resolve(image); };
                                image.onerror = function() { session.images.delete(image); resolve(null); };
                                image.src = src;
                            });
                            if (!session.active) return null;
                            if (!img) {
                                coverCache.put(src, { missing: true });
                                return null;
                            }
                            const blob = await coverCache.thumbnail(img);
                            if (blob) {
                                coverCache.put(src, { blob: blob });
                                useThumbnail(src, blob);
                            }
                            return session.active ? src : null;
                        }

                        // Next cover of the pool that exists
                        async function nextCover(state) {
                            while (session.active &amp;&amp; state.pending.length) {
                                const src = await loadCover(state.pending.shift());
                                if (src) {
                                    state.covers.push(src);
                                    return src;
                                }
                            }
                            return null;
                        }

                        // Fade a cover in above the previous one, then drop the previous one
                        function showCover(cell, src, instant) {
                            const img = document.createElement('img');
                            img.className = instant ? 'tile-mosaic-cover instant' : 'tile-mosaic-cover';
                            img.alt = '';
                            img.src = session.thumbs.get(src) || src;
                            cell.appendChild(img);
                            void img.offsetWidth; // Start the transition from opacity 0
                            img.classList.add('loaded');
                            img.addEventListener('transitionend', function() {
                                while (cell.firstChild !== img) cell.firstChild.remove();
                            }, { once: true });
                            return img;
                        }

                        async function startMosaic(mosaic, state) {
                            const first = (await Promise.all(state.pending.splice(0, CELLS).map(loadCover))).filter(Boolean);
                            state.covers.push(...first);
                            while (first.length &lt; CELLS) {
                                const src = await nextCover(state);
                                if (!src) break;
                                first.push(src);
                            }
                            if (!session.active || !first.length) return;

                            // Too few covers for a grid: a single cover filling the tile
                            const shown = first.length &lt; CELLS ? first.slice(0, 1) : first;
                            mosaic.classList.toggle('single', shown.length === 1);
                            const images = [];
                            const cells = shown.map(function(src) {
                                const cell = document.createElement('div');
                                cell.className = 'tile-mosaic-cell';
                                mosaic.appendChild(cell);
                                images.push(showCover(cell, src, true));
                                return cell;
                            });
                            // Fade the tile in once its covers are decoded, not cell by cell as they paint
                            await Promise.all(images.map(function(img) { return img.decode().catch(function() {}); }));
                            if (!session.active) return;
                            mosaic.parentElement.classList.add('has-mosaic');
                            coverCache.remember(state.tile, shown);
                            if (reduceMotion) return;

                            let lastCell = -1;
                            async function rotate() {
                                if (!session.active) return;
                                if (!document.hidden &amp;&amp; state.visible) {
                                    // Discover the rest of the pool first, then reuse the known covers
                                    let src = await nextCover(state);
                                    if (!session.active) return;
                                    if (!src) {
                                        const hidden = state.covers.filter(function(cover) { return !shown.includes(cover); });
                                        src = hidden.length ? hidden[Math.floor(Math.random() * hidden.length)] : null;
                                    }
                                    if (!src) return; // Every cover is already on screen
                                    let index = Math.floor(Math.random() * cells.length);
                                    if (index === lastCell &amp;&amp; cells.length > 1) index = (index + 1) % cells.length;
                                    lastCell = index;
                                    shown[index] = src;
                                    showCover(cells[index], src);
                                    coverCache.remember(state.tile, shown);
                                }
                                schedule(rotate, 5000 + Math.random() * 4000);
                            }
                            schedule(rotate, 3000 + Math.random() * 5000);
                        }

                        // Load covers only once a tile is near the viewport, and animate only visible tiles
                        const states = new Map();
                        const observer = new IntersectionObserver(function(entries) {
                            entries.forEach(function(entry) {
                                let state = states.get(entry.target);
                                if (!state) {
                                    if (!entry.isIntersecting) return;
                                    // Covers shown on the last visit first (instant, from the cache), then the feed's sample
                                    const link = entry.target.closest('a');
                                    const tile = link ? link.getAttribute('href') : '';
                                    const pending = coverCache.seen(tile).concat(entry.target.getAttribute('data-covers').split(' ').filter(Boolean));
                                    state = { tile: tile, pending: pending.filter(function(src, i) { return pending.indexOf(src) === i; }), covers: [], visible: true };
                                    states.set(entry.target, state);
                                    startMosaic(entry.target, state);
                                }
                                state.visible = entry.isIntersecting;
                            });
                        }, { rootMargin: '50px' });
                        session.observer = observer;

                        document.querySelectorAll('.tile-mosaic[data-covers]').forEach(function(mosaic) {
                            observer.observe(mosaic);
                        });
                    }

                    // Account button: sign in (back to this page afterwards), or who is signed in and logout
                    (function() {
                        const match = document.cookie.match(/(?:^|;\s*)pyopds_user=([^;]*)/);
                        const link = document.getElementById(match ? 'logout-link' : 'login-link');
                        if (!link) return;
                        link.classList.remove('hidden');
                        link.classList.add('flex');
                        if (match) {
                            link.title = 'Se déconnecter (' + decodeURIComponent(match[1]) + ')';
                        } else {
                            link.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
                        }
                    })();

                    // Header button switching the mosaic on and off, applied without reloading
                    updateMosaicToggle(isMosaicEnabled());
                    const mosaicToggleBtn = document.getElementById('mosaic-toggle');
                    if (mosaicToggleBtn) {
                        mosaicToggleBtn.addEventListener('click', function() {
                            const enabled = !isMosaicEnabled();
                            saveMosaicEnabled(enabled);
                            updateMosaicToggle(enabled);
                            if (enabled) {
                                setupTileMosaics();
                            } else {
                                stopTileMosaics();
                            }
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
                        
                        // Same URL with another page number (keeps other parameters such as the search query)
                        const pageUrl = function(pageNum) {
                            const url = new URL(window.location.href);
                            url.searchParams.set('page', pageNum);
                            return url.pathname + url.search;
                        };

                        const firstPageEl = document.getElementById('first-page');
                        if (firstPageEl &amp;&amp; currentPage > 1) {
                            firstPageEl.href = pageUrl(1);
                            firstPageEl.hidden = false;
                        }
                        const lastPageEl = document.getElementById('last-page');
                        if (lastPageEl &amp;&amp; currentPage &lt; totalPages) {
                            lastPageEl.href = pageUrl(totalPages);
                            lastPageEl.hidden = false;
                        }
                        
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
                                link.href = pageUrl(pageNum);
                                
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

                    // Book details dialog: a plain click on a book opens it, modified clicks still download
                    const bookDialog = document.getElementById('book-dialog');

                    function bookField(link, name) {
                        return (link.getAttribute('data-' + name) || '').trim();
                    }

                    function formatRating(value) {
                        return (Math.round(value * 10) / 10).toString().replace('.', ',');
                    }

                    function chip(text, href) {
                        const el = document.createElement(href ? 'a' : 'span');
                        if (href) el.href = href;
                        el.className = 'px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-200 dark:hover:bg-slate-600';
                        el.textContent = text;
                        return el;
                    }

                    function fillBookDialog(link) {
                        const title = bookField(link, 'title');
                        const author = bookField(link, 'author');
                        document.getElementById('book-dialog-title').textContent = title;
                        document.getElementById('book-dialog-cover-title').textContent = title;

                        const authorEl = document.getElementById('book-dialog-author');
                        authorEl.textContent = author;
                        if (author) {
                            if (author !== 'Unknown') {
                                authorEl.href = '/opds/by-author/' + encodeURIComponent(author);
                            } else {
                                authorEl.removeAttribute('href');
                            }
                        }

                        // Large cover, title block when the EPUB has none
                        const cover = document.getElementById('book-dialog-cover');
                        const coverSrc = bookField(link, 'cover');
                        cover.hidden = true;
                        cover.onload = function() { cover.hidden = false; };
                        cover.onerror = function() { cover.hidden = true; };
                        cover.alt = title;
                        if (coverSrc) {
                            cover.src = coverSrc;
                        } else {
                            cover.removeAttribute('src');
                        }

                        const series = bookField(link, 'series');
                        const seriesIndex = bookField(link, 'series-index');
                        const seriesRow = document.getElementById('book-dialog-series-row');
                        const seriesEl = document.getElementById('book-dialog-series');
                        seriesRow.hidden = !series;
                        seriesEl.textContent = seriesIndex ? 'Tome ' + seriesIndex + ' · ' + series : series;
                        seriesEl.href = bookField(link, 'series-href') || ('/opds/series?name=' + encodeURIComponent(series));

                        const facts = document.getElementById('book-dialog-facts');
                        facts.replaceChildren();
                        const year = bookField(link, 'year');
                        if (year) {
                            const yearEl = document.createElement('span');
                            yearEl.textContent = year;
                            facts.appendChild(yearEl);
                        }
                        const rating = parseFloat(bookField(link, 'rating'));
                        if (rating > 0) {
                            const label = 'Note : ' + formatRating(rating) + ' sur 5';
                            const stars = document.createElement('span');
                            stars.className = 'book-stars';
                            stars.setAttribute('role', 'img');
                            stars.setAttribute('aria-label', label);
                            stars.title = label;
                            stars.textContent = '★★★★★';
                            const on = document.createElement('span');
                            on.className = 'book-stars-on';
                            on.setAttribute('aria-hidden', 'true');
                            on.style.width = Math.min(100, rating * 20) + '%';
                            on.textContent = '★★★★★';
                            stars.appendChild(on);
                            facts.appendChild(stars);
                        }

                        const genres = document.getElementById('book-dialog-genres');
                        genres.replaceChildren();
                        bookField(link, 'genres').split('\n').filter(Boolean).forEach(function(genre) {
                            genres.appendChild(chip(genre, '/opds/by-genre/' + encodeURIComponent(genre) + '?page=1'));
                        });

                        const download = document.getElementById('book-dialog-download');
                        download.href = link.getAttribute('href');

                        const summary = bookField(link, 'summary');
                        const block = document.getElementById('book-dialog-description-block');
                        const description = document.getElementById('book-dialog-description');
                        const more = document.getElementById('book-dialog-more');
                        block.hidden = !summary;
                        description.textContent = summary;
                        description.classList.add('clamped');
                        more.hidden = true;
                        more.textContent = 'Lire la suite';
                    }

                    function openBookDialog(link, fromHash) {
                        if (!bookDialog || typeof bookDialog.showModal !== 'function') {
                            return false;
                        }
                        fillBookDialog(link);
                        bookDialog.showModal();
                        document.documentElement.style.overflow = 'hidden';
                        bookDialog.scrollTop = 0;
                        // "Lire la suite" only when the description is actually cut
                        const description = document.getElementById('book-dialog-description');
                        document.getElementById('book-dialog-more').hidden = description.scrollHeight - description.clientHeight &lt;= 2;
                        // Shareable link to the opened details
                        const id = link.getAttribute('data-id');
                        if (id &amp;&amp; !fromHash) {
                            history.replaceState(null, '', '#book=' + encodeURIComponent(id));
                        }
                        return true;
                    }

                    if (bookDialog) {
                        document.addEventListener('click', function(event) {
                            const link = event.target.closest ? event.target.closest('a.book-link') : null;
                            if (!link || event.defaultPrevented || event.button !== 0
                                || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                                return;
                            }
                            if (openBookDialog(link, false)) {
                                event.preventDefault();
                            }
                        });

                        document.getElementById('book-dialog-close').addEventListener('click', function() {
                            bookDialog.close();
                        });

                        // Click on the backdrop (outside the content) closes; Escape closes natively
                        bookDialog.addEventListener('click', function(event) {
                            if (event.target === bookDialog) {
                                bookDialog.close();
                            }
                        });

                        bookDialog.addEventListener('close', function() {
                            document.documentElement.style.overflow = '';
                            if (location.hash.indexOf('#book=') === 0) {
                                history.replaceState(null, '', location.pathname + location.search);
                            }
                        });

                        document.getElementById('book-dialog-more').addEventListener('click', function() {
                            const description = document.getElementById('book-dialog-description');
                            const clamped = description.classList.toggle('clamped');
                            this.textContent = clamped ? 'Lire la suite' : 'Réduire';
                        });

                        // Opened from a shared link (#book=urn:book:…)
                        if (location.hash.indexOf('#book=') === 0) {
                            const wanted = decodeURIComponent(location.hash.slice('#book='.length));
                            const target = Array.prototype.find.call(document.querySelectorAll('a.book-link'), function(link) {
                                return link.getAttribute('data-id') === wanted;
                            });
                            if (target) {
                                openBookDialog(target, true);
                            }
                        }
                    }

                    // Setup lazy loading and pagination when DOM is ready
                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', function() {
                            setupLazyCoverLoading();
                            setupTileMosaics();
                            setupPagination();
                        });
                    } else {
                        setupLazyCoverLoading();
                        setupTileMosaics();
                        setupPagination();
                    }
                </script>
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