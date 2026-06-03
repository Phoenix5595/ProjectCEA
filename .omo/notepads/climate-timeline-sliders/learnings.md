# Learnings

## 2026-06-01 — Vitest + RTL test infrastructure setup

- Pre-existing Playwright tests in `tests/smoke.spec.ts` get picked up by Vitest and fail because they use `@playwright/test`'s syntax. Must add `exclude: ['node_modules/**', 'tests/**']` to Vitest config.
- jsdom@28.1.0 was already in devDependencies — did not need reinstalling.
- Vitest 4.1.7 warns about deprecated `esbuild` option from `vite:react-babel` plugin, suggesting migration to `oxc`. Non-blocking.
- Configuration: `environment: 'jsdom'`, `globals: true`, `setupFiles: ['./src/test-setup.ts']`.
- Test script: `"test": "vitest run"`, `"test:watch": "vitest"`.
- `@testing-library/jest-dom` setup via `import '@testing-library/jest-dom'` in `test-setup.ts`.

## 2026-06-01 — Created 6 TDD failing tests for ClimatePeriodTimeline sliders

- Created `src/components/__tests__/ClimatePeriodTimeline.interaction.test.tsx` with 6 tests
- Tests 1-3, 5-6 fail because they reference `data-testid` elements that don't exist yet
  (`timeline-handle-start`, `timeline-handle-end`, `timeline-day-band`, `timeline-ramp-up-gradient`)
- Test 4 fails because right-click doesn't trigger a ramp popover yet
- All 6 tests compile with zero TS errors via `as any` for new props not in the interface
- Verified: `npx vitest run -- ClimatePeriodTimeline.interaction` → 6 failed, 0 errors
- Feature props expected by tests: `onDayStartChange`, `onDayEndChange`, `lockedPhotoperiodHours`,
  `rampUpDuration`, `rampDownDuration`, `onRampUpChange`, `onRampDownChange`

## 2026-06-01 — Task 2: Removed CircularTimePicker dead code

- Deleted 3 files: `CircularTimePicker.tsx`, `CircularClockFace.tsx`, `useClockInteraction.ts`
- Cleaned `timeMath.ts`: removed React import (`MouseEvent as ReactMouseEvent`) and all 8 angle-related functions
  (`angleToMinutes`, `minutesToAngle`, `calculatePhotoperiod`, `isOvernightPeriod`, `calculateMidAngle`,
  `getAngleFromMouse`, `getDistanceFromCenter`, `normalizeAngle`)
- Kept only `timeToMinutes` and `minutesToTime` — both needed by `ClimatePeriodTimeline` and climate period utils
- `rg` confirms zero remaining references to deleted files across `src/`
- `npm run build` expected to fail due to `ZoneConfig.tsx` still importing `CircularTimePicker` — will be fixed in Task 5

## 2026-06-01 — Task 5: Updated ZoneConfig.tsx

- Removed `CircularTimePicker` import
- Changed timeline height `h-[270px]` → `h-[300px]`
- Wired interactive props to `ClimatePeriodTimeline`: `onDayStartChange`, `onDayEndChange`, `lockedPhotoperiodHours`, `rampUpDuration`, `rampDownDuration`, `onRampUpChange`, `onRampDownChange`
- Replaced 3-column layout (25% CircularTimePicker + 40% climate periods + 35% relay matrix) with 2 `flex-1` columns: ClimatePeriodsTable+LightIntensity | RelayChannelMatrix
  - In constant mode: ManualLightControl replaces table/intensity in first column
  - MCP23017 disconnected banner preserved above relay matrix
