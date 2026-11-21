/*! Clio static search: makes an exported site work offline. MIT License. https://github.com/jordanboyce/clio */
// Included by the pages of an export made with include=offline. Registers
// the site's service worker (sw.js at the site's root, which clio-sw.js
// becomes), which keeps the site's files for when there is no connection.
// See docs/STATIC_SEARCH.md, "A site that works offline".

const here = new URL('./', import.meta.url)

if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost' ||
    location.hostname === '127.0.0.1')) {
  navigator.serviceWorker.register(new URL('sw.js?export=search/', here), { scope: here.pathname, type: 'classic' })
    .catch((error) => console.warn('Clio: this site cannot be kept for offline use:', error.message))
}
