/** Stylesheet of the web UI (static/tailwind.css), built by `python tools/tailwind.py` (see README, "Web UI styles"). */
module.exports = {
  darkMode: 'class',
  // Every Tailwind class used by the XSLT, the pages and their scripts (never the vendored polyfill)
  content: ['./static/*.xslt', './static/*.html', './static/*.js', '!./static/xslt-polyfill.min.js'],
};
