/** Stylesheet of the web UI (static/tailwind.css), built by `python tools/tailwind.py` (see README, "Web UI styles"). */
module.exports = {
  darkMode: 'class',
  // Every Tailwind class used by the XSLT, the pages, the partials and the scripts of each folder of static/
  // (catalog/, reader/, reading/, clippings/, login/, shared/), never the vendored ones (static/vendor/)
  content: ['./static/*/*.{xslt,html,js}', '!./static/vendor/**'],
};
