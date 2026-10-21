// Builds static/tailwind.css, the stylesheet of the web UI (development only, the server needs no Node):
//   npx tailwindcss@3.4.19 -c tools/tailwind.config.js -i tools/tailwind.input.css -o static/tailwind.css --minify
// Rebuild it after using new Tailwind classes in static/ (XSLT, pages, scripts).
module.exports = {
  darkMode: 'class',
  content: ['./static/*.xslt', './static/*.html', './static/*.js', '!./static/xslt-polyfill.min.js', '!./static/tailwind.css'],
};
