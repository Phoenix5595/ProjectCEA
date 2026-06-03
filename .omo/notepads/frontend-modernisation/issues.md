# Frontend Modernisation - Issues & Gotchas

## 2026-02-17 General
- `deep` category requires gpt-5.3-codex which is unavailable - use `unspecified-high` or `quick` instead
- Subagents sometimes modify files in production repo `/home/antoine/ProjectCEA/` - ALWAYS warn against this
- Plan file is in production repo, worktree is at `/home/antoine/ProjectCEA-ui/`
- ThemeContext.tsx needs refactoring from light/dark toggle to 6-theme data-theme attribute system
- Port 3002 for dev (production uses 3001)
