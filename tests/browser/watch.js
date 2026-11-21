// Loaded first by the test pages: records what a Content-Security-Policy
// refused, and errors, where a test can read them.
window.__violations = []
window.__errors = []
document.addEventListener('securitypolicyviolation', (e) => window.__violations.push(`${e.violatedDirective} ${e.blockedURI}`))
window.addEventListener('error', (e) => window.__errors.push(String(e.message)))
window.addEventListener('unhandledrejection', (e) => window.__errors.push(String(e.reason)))
