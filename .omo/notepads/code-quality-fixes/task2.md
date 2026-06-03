# Task 2: Replace console.* with logger.* in Frontend

**Date:** 2026-06-02

## Summary

Replaced all 7 `console.*` calls across 3 target files with `logger.*` equivalents from the centralized `utils/logger.ts`.

## Files Modified

| File | Calls Replaced | Severity Map |
|------|---------------|-------------|
| `hooks/useSystemStatus.ts` | 2 `console.warn` → `logger.warn` | Same severity |
| `hooks/useSensorPolling.ts` | 1 `console.error` → `logger.error`, 3 `console.warn` → `logger.warn` | Same severity |
| `config/env.ts` | 1 `console.warn` → `logger.warn` | Same severity |

## Changes Made

1. **`hooks/useSystemStatus.ts`**: Added `import { logger } from '../utils/logger'`. Replaced 2 `console.warn` calls.
2. **`hooks/useSensorPolling.ts`**: Added `import { logger } from '../utils/logger'`. Replaced 1 `console.error` and 3 `console.warn` calls.
3. **`config/env.ts`**: Added `import { logger } from '../utils/logger'`. Replaced 1 `console.warn`, removed the now-unnecessary `// eslint-disable-next-line no-console` comment.

## Verification

- **`npm run build`**: Passed with 0 errors, 0 warnings.
- **`grep` check**: All 7 target console.* calls replaced. One remaining `console.error` in `ErrorBoundary.tsx` (not in scope — kept as-is since it's outside the 3 target files).
- **`tsc` (type-checking)**: Passed without issues.
