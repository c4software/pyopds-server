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
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                        background-color: #f4f1ea;
                        margin: 0;
                        padding: 0;
                    }
                    .header {
                        display: flex;
                        align-items: center;
                        background-color: #d7ccc8;
                        border-bottom: 3px solid #a1887f;
                        padding: 10px 20px;
                    }
                    h1 {
                        flex: 1;
                        text-align: center;
                        font-weight: 300;
                        color: #44352e;
                        margin: 0;
                        text-shadow: 1px 1px 1px #fff;
                    }
                    #back-button {
                        font-size: 1.5em;
                        color: #44352e;
                        cursor: pointer;
                        display: none;
                        margin-right: 20px;
                        min-width: 50px;
                    }
                    .bookshelf {
                        padding: 30px;
                        display: flex;
                        flex-wrap: wrap;
                        justify-content: center;
                        align-items: flex-start;
                        gap: 35px;
                        background-color: #855E42;
                        background-image: linear-gradient(45deg, rgba(255, 255, 255, .05) 25%, transparent 25%, transparent 50%, rgba(255, 255, 255, .05) 50%, rgba(255, 255, 255, .05) 75%, transparent 75%, transparent);
                        box-shadow: inset 0 8px 15px -5px rgba(0, 0, 0, 0.4);
                        min-height: 100vh;
                        flex-direction: row;
                        align-content: flex-start;
                    }
                    .book-link {
                        text-decoration: none;
                        color: inherit;
                        display: block;
                        transition: transform 0.2s ease-in-out, box-shadow 0.2s ease-in-out;
                    }
                    .book-link:hover {
                        transform: translateY(-10px) scale(1.03);
                        box-shadow: 0 15px 25px rgba(0,0,0,0.3);
                    }
                    .book {
                        width: 170px;
                        height: 250px;
                        padding: 20px 15px;
                        border-radius: 4px 6px 6px 4px;
                        box-shadow: 5px 5px 12px rgba(0,0,0,0.25);
                        display: flex;
                        flex-direction: column;
                        justify-content: space-between;
                        overflow: hidden;
                        position: relative;
                        background-color: #fff;
                    }

                    .book::before {
                        content: '';
                        position: absolute;
                        top: 0;
                        left: 0;
                        width: 10px;
                        height: 100%;
                        background: linear-gradient(to right, rgba(0,0,0,0.15) 0%, rgba(0,0,0,0) 100%);
                        border-right: 1px solid rgba(0,0,0,0.1);
                        border-radius: 4px 0 0 4px;
                    }
                    .book-title {
                        font-weight: bold;
                        font-size: 1.1em;
                        color: #333;
                    }
                    .book-author {
                        font-size: 0.9em;
                        color: #666;
                        text-align: center;
                        font-weight: 200;
                    }

                    .book-link:nth-child(6n+1) .book { background-color: #e0f2f1; } /* Teal */
                    .book-link:nth-child(6n+2) .book { background-color: #fbe9e7; } /* Peach */
                    .book-link:nth-child(6n+3) .book { background-color: #e8eaf6; } /* Lavender */
                    .book-link:nth-child(6n+4) .book { background-color: #f9fbe7; } /* Lime */
                    .book-link:nth-child(6n+5) .book { background-color: #fff3e0; } /* Light Orange */
                    .book-link:nth-child(6n+6) .book { background-color: #fce4ec; } /* Light Pink */
                    
                    .collection {
                        justify-content: center !important;
                        text-align: center;
                        border: 2px dashed #a1887f;
                        background-color: #f5f5f5 !important; /* Override nth-child color */
                    }
                    .collection .book-author {
                        font-style: italic;
                        color: #888;
                    }
                    #quick-links {
                        min-width: 50px;
                        text-align: right;
                    }
                    #quick-links a {
                        color: #44352e;
                        text-decoration: none;
                        margin-left: 15px;
                        font-weight: 500;
                    }
                    #quick-links a:hover {
                        text-decoration: underline;
                    }
                </style>
            </head>
            <body>
                <div class="header">
                    <a id="back-button" style="display: none; text-decoration: none; color: #44352e; font-size: 1.5em; margin-right: 20px; min-width: 50px;">← Back</a>
                    <h1><xsl:value-of select="atom:title"/></h1>
                    <div id="quick-links">
                        <a href="/opds">Home</a>
                    </div>
                </div>
                <div class="bookshelf">
                    <xsl:apply-templates select="atom:entry"/>
                </div>
                <script>
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
                    }

                    if (parentPath) {
                        backButton.href = parentPath;
                        backButton.style.display = 'block';
                    }
                </script>
            </body>
        </html>
    </xsl:template>

    <xsl:template match="atom:entry">
        <xsl:choose>
            <xsl:when test="atom:link[@rel='subsection']">
                <a class="book-link">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='subsection']/@href"/>
                    </xsl:attribute>
                    <div class="book collection">
                        <div class="book-title"><xsl:value-of select="atom:title"/></div>
                        <div class="book-author">Collection</div>
                    </div>
                </a>
            </xsl:when>

            <xsl:otherwise>
                <a class="book-link">
                    <xsl:attribute name="href">
                        <xsl:value-of select="atom:link[@rel='http://opds-spec.org/acquisition/open-access']/@href"/>
                    </xsl:attribute>
                    <div class="book">
                        <div class="book-title"><xsl:value-of select="atom:title"/></div>
                        <div class="book-author"><xsl:value-of select="atom:author/atom:name"/></div>
                    </div>
                </a>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

</xsl:stylesheet>