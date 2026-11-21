/*! Clio static search: the search page's own script. MIT License. https://github.com/jordanboyce/clio */
// Loaded by index.html, so the page needs no inline script and can carry a
// Content-Security-Policy that allows none.
import './clio-search.js'

// Name the page after the collection once the index is loaded.
document.querySelector('clio-search')?.addEventListener('clio-search-ready', (event) => {
  const name = event.target.searches?.[0]?.index.manifest.collection.name
  if (name) {
    document.getElementById('title').textContent = `Search ${name}`
    document.title = `Search ${name}`
  }
}, { once: true })
