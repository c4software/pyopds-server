/** Stylesheet of the web UI (static/tailwind.css), built by `python tools/tailwind.py` (see README, "Web UI styles"). */
module.exports = {
  darkMode: 'class',
  // Every Tailwind class used by the XSLT, the pages and their scripts, including the catalog
  // scripts of static/catalog/ (never the vendored polyfill)
  content: ['./static/*.xslt', './static/*.html', './static/*.js', './static/catalog/*.js', '!./static/xslt-polyfill.min.js'],
};
