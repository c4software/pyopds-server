/** Stylesheet of the web UI (static/tailwind.css), built by `python tools/tailwind.py` (see README, "Web UI styles"). */
module.exports = {
  darkMode: 'class',
  // Every Tailwind class used by the XSLT, the pages and their scripts, including the catalog
  // scripts of static/catalog/ and the reader's of static/reader/ (never the vendored polyfill nor foliate-js)
  content: ['./static/*.xslt', './static/*.html', './static/partials/*.html', './static/*.js', './static/catalog/*.js', './static/reader/*.js', '!./static/xslt-polyfill.min.js'],
};
