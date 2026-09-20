// Shared app constants. The repo URL is one constant for the §9.4 About links
// and the §9.5 report modal — never two copies.
export const REPO_URL = 'https://github.com/hansololz/autowright'

// §22 visibility parking switch: the marketplace is hidden for everyone
// while true. False now (un-parked 2026-09-14; the Developer-mode preview
// gate was lifted 2026-09-19, so the page and its nav row render for
// everyone). Nothing else is gated by it: the routes, the store, and the CLI
// group stay live (§2, one code path for every mode). Flip this and the
// §22.6 e2e skip together. It lives here rather than in App.tsx so pages
// App.tsx renders can read it without importing App back.
export const MARKETPLACE_HIDDEN = false
