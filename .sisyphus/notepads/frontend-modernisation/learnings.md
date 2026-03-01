# Frontend Modernisation - Learnings

## 2026-02-17 Task 5: Semantic Color Tokens
- Commit 7a90953 converted 31 files (~7600 insertions/deletions)
- Only 1 hardcoded color remains: `src/types/modes.ts:114` has `sleep: 'bg-gray-600'` (mode color mapping string literal - acceptable)
- CSS variables defined in `:root` block in index.css lines 45-63
- @theme inline block in index.css lines 67-179 registers all semantic tokens with Tailwind
- Legacy alias variables (--bg-primary etc) map to semantic vars for backward compat
- ThemeContext.tsx exists with simple light/dark toggle - must be refactored for 6-theme support
- Zero backdrop-blur instances remain
- TypeScript check passes clean
