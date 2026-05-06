# Design tokens (source: frontend CSS variables)

These files mirror the **actual UI tokens used by the React app** (via CSS variables in `Infrastructure/frontend/src/styles/index.css` and `Infrastructure/frontend/src/styles/themes.css`).

## Files

- `semantic.css-vars.json`
  - Semantic token names (surfaces, text, accents, status, borders, timeline bands).
  - This is what you should model as Figma “Variables” (the names should stay stable).

- `themes/`
  - Concrete theme values for the per-theme CSS variables (`--surface-base`, `--text-default`, `--accent`, etc).
  - The app default is `botanical`.

## Intended Figma workflow

- Create collections:
  - `Theme` (per-theme raw values like `surface.base`, `text.default`, `accent.primary`)
  - `Semantic` (semantic variables like `color.surface.base`, `color.text.default`, etc.)
- Bind `Semantic` to `Theme` (so swapping themes updates the UI kit).

