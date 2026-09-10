// Vitest setup (§15): every component render runs inside React.StrictMode,
// exactly like the dev app (`src/main.tsx`), so a hook whose effect cleanup
// leaves a ref in a "gone" state fails here under the simulated dev remount
// instead of hanging the live editor (the 2026-09-06 useDraftJob regression).
import { configure } from '@testing-library/react'

configure({ reactStrictMode: true })
