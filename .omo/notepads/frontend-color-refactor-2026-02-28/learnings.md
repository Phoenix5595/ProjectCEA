Summary of changes:
- Replaced hardcoded Tailwind color tokens (bg-gray-950, text-gray-100, text-gray-500, etc.) with unified theme tokens across 9 frontend TSX files.
- Removed dark: prefixes from GrafanaPanel and TopRibbon components.
- Updated multiple UI components to rely on new theme tokens (bg-surface-base, text-text-default, text-text-subtle, text-text-muted, etc.).

Verification performed:
- Grep checks for color tokens returned zero results after changes.
- No dark: prefixes remaining in TypeScript React components.
- Relevant components updated: VegetationMonitoring.tsx, FlowerSoil.tsx, LaboratoryClimate.tsx, LaboratoryWater.tsx, LaboratoryInfrastructure.tsx, ZoneConfig.tsx, GrafanaPanel.tsx, TopRibbon.tsx, FlowerMonitoring.tsx.

Commit reference: 07f2c18

Notes for future maintenance:
- Ensure Tailwind theme tokens (e.g., bg-surface-base, text-text-default, etc.) are defined in Tailwind config.
- Run the frontend test suite and linting to guard against regressions.
- Consider a follow-up pass to unify any remaining inline color tokens discovered later.
