// Web reader: display settings (text size, line spacing, font, margins, justification, theme, paginated or
// scrolled layout), kept in this browser (localStorage, per-viewer convenience) and applied to the
// foliate-js paginator (layout attributes) and to the book (stylesheet injected in each chapter).
// The page itself follows the theme: CSS variables of reader.css under html[data-reader-theme].
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const STORAGE_KEY = 'pyopds-reader-settings';
    const DEFAULTS = { fontSize: 100, lineHeight: 1.5, font: 'publisher', margins: 'normal', justify: true, theme: 'auto', flow: 'paginated' };
    const LIMITS = { fontSize: [70, 200], lineHeight: [1.1, 2.2] };
    // Text and background of the themes ("auto": light or dark like the site, static/shared/theme.js)
    const THEMES = {
        light: { fg: '#1f2937', bg: '#ffffff', link: '#0f766e' },
        sepia: { fg: '#433422', bg: '#f4ecd8', link: '#8a4b0f' },
        dark: { fg: '#d6d3d1', bg: '#16181d', link: '#5eead4' },
    };
    const FONTS = {
        serif: 'Georgia, "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, serif',
        sans: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    };
    // Paginator gap between columns and around the text (percent of the width), per margins setting
    const GAPS = { narrow: '4%', normal: '7%', wide: '12%' };

    function clamp(value, [min, max], fallback) {
        const number = Number(value);
        return Number.isFinite(number) ? Math.min(max, Math.max(min, number)) : fallback;
    }

    /** Settings of this browser (defaults for the missing or invalid ones). */
    function load() {
        let stored = {};
        try { stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {}; } catch (e) { /* storage unavailable */ }
        return {
            fontSize: clamp(stored.fontSize, LIMITS.fontSize, DEFAULTS.fontSize),
            lineHeight: clamp(stored.lineHeight, LIMITS.lineHeight, DEFAULTS.lineHeight),
            font: stored.font in FONTS ? stored.font : DEFAULTS.font,
            margins: stored.margins in GAPS ? stored.margins : DEFAULTS.margins,
            justify: typeof stored.justify === 'boolean' ? stored.justify : DEFAULTS.justify,
            theme: stored.theme in THEMES || stored.theme === 'auto' ? stored.theme : DEFAULTS.theme,
            flow: stored.flow === 'scrolled' ? 'scrolled' : DEFAULTS.flow,
        };
    }

    function save(settings) {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); } catch (e) { /* storage unavailable */ }
    }

    /** Effective theme: "auto" follows the site's light / dark theme. */
    function themeName(settings) {
        if (settings.theme !== 'auto') return settings.theme;
        return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
    }

    /** Stylesheet injected in every chapter. */
    function bookCSS(settings) {
        const theme = THEMES[themeName(settings)];
        const recolor = themeName(settings) !== 'light';
        const font = FONTS[settings.font];
        return [
            '@namespace epub "http://www.idpf.org/2007/ops";',
            'html { color-scheme: ' + (themeName(settings) === 'dark' ? 'dark' : 'light') + '; color: ' + theme.fg + '; background: ' + theme.bg + ' !important;'
                + ' font-size: ' + settings.fontSize + '% !important; -webkit-text-size-adjust: none; text-size-adjust: none; }',
            'body { background: transparent !important; }',
            // The book's colors would be unreadable on a dark or sepia page
            recolor ? 'body *:not(img, svg, svg *, video) { color: inherit !important; background-color: transparent !important; border-color: currentColor; }' : '',
            'a:any-link { color: ' + theme.link + ' !important; }',
            font ? 'body, body *:not(code, pre, kbd, samp, tt) { font-family: ' + font + ' !important; }' : '',
            'p, li, blockquote, dd, div { line-height: ' + settings.lineHeight + ' !important; }',
            'p, li, blockquote, dd { text-align: ' + (settings.justify ? 'justify' : 'start') + '; -webkit-hyphens: auto; hyphens: auto;'
                + ' hanging-punctuation: allow-end last; widows: 2; orphans: 2; }',
            '[align="left"] { text-align: left; } [align="right"] { text-align: right; } [align="center"] { text-align: center; }',
            'pre { white-space: pre-wrap !important; }',
            // Footnotes are shown when their link is followed, not in the text flow
            'aside[epub|type~="footnote"], aside[epub|type~="endnote"], aside[epub|type~="note"], aside[epub|type~="rearnote"] { display: none; }',
            '::selection { background: rgba(250, 204, 21, 0.45); }',
        ].filter(Boolean).join('\n');
    }

    /**
     * Apply the settings to a foliate view: layout of the paginator (one column on a narrow or portrait
     * screen, two side by side on a wide landscape one, like an open book), book stylesheet, page theme.
     */
    function apply(view, settings) {
        const renderer = view && view.renderer;
        document.documentElement.dataset.readerTheme = themeName(settings);
        if (!renderer) return;
        // Top and bottom margins: room for the running heads; on a computer, the bars (always shown) never hide the text
        const touch = window.matchMedia('(pointer: coarse)').matches;
        const margin = !touch ? 84 : window.innerWidth < 640 ? 36 : 48;
        renderer.setAttribute('flow', settings.flow);
        renderer.setAttribute('gap', GAPS[settings.margins]);
        renderer.setAttribute('margin', margin + 'px');
        renderer.setAttribute('max-inline-size', '720px');
        renderer.setAttribute('max-block-size', '1440px');
        renderer.setAttribute('max-column-count', '2');
        renderer.setAttribute('animated', '');
        if (renderer.setStyles) renderer.setStyles(bookCSS(settings));
    }

    PyReader.settings = { DEFAULTS: DEFAULTS, LIMITS: LIMITS, load: load, save: save, apply: apply, themeName: themeName };
})();
