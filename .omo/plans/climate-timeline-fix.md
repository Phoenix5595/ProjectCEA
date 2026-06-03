# Climate Timeline Sun/Moon Band Fix

## TL;DR

> **Quick Summary**: Fix incorrect math in `ClimatePeriodTimeline.tsx` that causes moon band to show wrong width. Moon (purple) represents real daytime, Sun (yellow) represents overnight grow lights - together they fill 100% of 24h. **CRITICAL**: Must handle midnight-crossing case (e.g., 17:00 to 05:00).
>
> **Deliverables**:
> - Fixed calculation in `ClimatePeriodTimeline.tsx` handling midnight-crossing
> - Verified via production build + manual browser check
>
> **Estimated Effort**: Quick (fix calculation logic)
> **Parallel Execution**: NO - single wave, sequential
> **Critical Path**: Fix calculation → Build → Manual verify in browser

---

## Context

### Original Request
User: "THE CLIMATE timeline in the frontend is wrong. it should have a yellow overlay for sun, and a purple overlay for moon. they should not overlay since they will never overlay. they should follow the schedules and starts times set in the db by the circular time picker."

### Interview Summary
**Key Discussions**:
- Moon period = real daytime (lights OFF, natural light)
- Sun period = overnight (lights ON for grow cycle)
- Sun + Moon = 24h, never overlap
- User suggestion: "render moon and fill the rest with yellow"

**Research Findings**:
- `ClimatePeriodTimeline.tsx` receives `lightDayStart` and `lightDayEnd` props (HH:MM format)
- These come from `RoomModeWithParams.day_start_time` and `night_start_time` via ZoneConfig
- **ACTUAL DATA FROM API**: `day_start_time=17:00`, `night_start_time=05:00` - **CROSSES MIDNIGHT**
- Current code at lines 39-40 has broken math resulting in negative moon width

### ACTUAL DATA (from API)
```bash
curl http://mothernode:8001/api/room-modes/room/Flower%20Room/main
# Result: day_start_time="17:00", night_start_time="05:00"
```

**This crosses midnight!** 17:00 to 05:00 = 12 hours, but dayEnd < dayStart.

### Metis Review (REVISED)
**Identified Gaps** (addressed):
- Midnight-crossing case - **MUST HANDLE** - actual data crosses midnight
- Unused `periods` prop - scope locked: do NOT touch
- Comment misalignment - scope locked: do NOT update comments

---

## Work Objectives

### Core Objective
Fix the sun/moon band calculation in `ClimatePeriodTimeline.tsx` so moon renders at correct position and sun fills remaining space. **Must handle midnight-crossing.**

### Concrete Deliverables
- File: `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx`
- Fix calculation to handle midnight-crossing (dayStartMin > dayEndMin)

### Definition of Done
- [x] `npm run build` succeeds in `Infrastructure/frontend/`
- [x] Deploy to test environment (mothernode)
- [ ] Manual browser verification shows correct bands at ZoneConfig page

### Must Have
- Moon band (purple) correctly positioned - duration calculated correctly
- Sun band (yellow) fills 100% minus moon width
- **Midnight-crossing handled** (e.g., 17:00 to 05:00 = 12h moon)

### Must NOT Have (Guardrails)
- NO changes to `getPosition()` or `timeToMinutes()` functions
- NO changes to CSS classes or styling
- NO changes to unused `periods` prop (marked `_periods`)
- NO changes to comments
- NO addition of TypeScript types or validation
- NO changes to other components (e.g., `SetpointTimeline`)

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: NO
- **Automated tests**: NO (none in frontend)
- **Framework**: N/A
- **Verification**: Manual browser check after build

### Agent-Executed QA Scenarios (MANDATORY — build verification)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
> Build verification is executed by agent via Bash tool.

```
Scenario: Frontend build succeeds
  Tool: Bash
  Preconditions: Node.js and npm available
  Steps:
    1. cd Infrastructure/frontend && npm run build
    2. Assert: Exit code is 0
    3. Assert: dist/ directory created with index.html
  Expected Result: Build completes without errors
  Evidence: Exit code 0, dist/ directory exists
```

---

## Execution Strategy

### Wave 1: Fix + Deploy
1. Fix sun/moon band calculation in ClimatePeriodTimeline.tsx
2. Run `npm run build`
3. Deploy to test (mothernode)

---

## TODOs

- [x] 1. Fix sun/moon band calculation in ClimatePeriodTimeline.tsx (midnight-crossing aware)

  **Deployment**: After fix + build success, deploy to test environment:
  ```bash
  cd Infrastructure/frontend && npm run build && ./deploy.sh
  # OR if deploy script copies dist/ automatically after build:
  sudo systemctl restart automation-service
  ```

- [x] 2. Deploy to test environment
  **Deployment Command**:
  ```bash
  cd /home/antoine/ProjectCEA && ./deploy.sh
  ```
  This will:
  - Copy code to new release
  - Build frontend (npm run build)
  - Restart all services
  - Run health checks

  **What to do**:
  - Replace lines 39-40 with proper midnight-crossing logic:
    ```typescript
    // Moon band: from dayStartMin to dayEndMin (may cross midnight)
    const moonCrossesMidnight = dayStartMin > dayEndMin
    const moonDuration = moonCrossesMidnight
      ? (1440 - dayStartMin) + dayEndMin  // e.g., (1440-1020)+300 = 720min = 12h
      : dayEndMin - dayStartMin
    const moonWidth = getPosition(moonDuration)
    const sunWidth = 100 - moonWidth
    ```

  **Math verification with actual data** (17:00 to 05:00):
  - dayStartMin = 1020 (17:00)
  - dayEndMin = 300 (05:00)
  - moonCrossesMidnight = true (1020 > 300)
  - moonDuration = (1440 - 1020) + 300 = 420 + 300 = 720 minutes = 12 hours = 50%
  - moonWidth = 50%
  - sunWidth = 100 - 50 = 50%
  - ✅ Correct!

  **Must NOT do**:
  - Do NOT change `getPosition` or `timeToMinutes` functions
  - Do NOT change CSS classes or styling
  - Do NOT change comments
  - Do NOT touch `_periods` prop or `periods` variable
  - Do NOT add validation

  **Recommended Agent Profile**:
  > **Category**: `quick` - Simple calculation fix
  > **Skills**: None required
  > - `quick`: Single file, simple logic fix

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential
  - **Blocks**: None
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References** (code to modify):
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:39-40` - Current code:
    ```typescript
    const moonWidth = getPosition(dayEndMin) - getPosition(dayStartMin)  // WRONG: -50% for 17:00-05:00
    const sunWidth = 100 - moonWidth
    ```

  **WHY Each Reference Matters**:
  - The file path is exact. Lines 39-40 contain the code to change.

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios (MANDATORY — build verification)**:

  \`\`\`
  Scenario: Frontend build succeeds after fix
    Tool: Bash
    Preconditions: Node.js and npm available
    Steps:
      1. cd Infrastructure/frontend && npm run build
      2. Assert: Exit code is 0
      3. Assert: dist/ directory created with index.html
    Expected Result: Build completes without errors
    Evidence: Exit code captured in output

  Scenario: Build output contains expected files
    Tool: Bash
    Preconditions: npm run build succeeded
    Steps:
      1. ls Infrastructure/frontend/dist/
      2. Assert: index.html exists
      3. Assert: assets/ directory exists
    Expected Result: dist/ contains built frontend
    Evidence: Directory listing captured
  \`\`\`

  **Manual QA (User Action Required)**:
  > The following requires user to manually verify in browser since no automated browser testing is set up.

  \`\`\`
  Scenario: Manual browser verification of sun/moon bands
    Tool: Manual browser check (user action required)
    Preconditions: Dev server running OR fresh build deployed
    Steps:
      1. Navigate to: http://mothernode:8001 (ZoneConfig page)
      2. Select "Flower Room" (has 17:00-05:00 schedule)
      3. Locate ClimatePeriodTimeline component
      4. Verify: Purple moon band visible from approximately 17:00-05:00 (50% width)
      5. Verify: Yellow sun band visible from approximately 05:00-17:00 (remaining 50%)
      6. Verify: Bands do NOT overlap
    Expected Result: Correct visual representation of day/night periods
    Evidence: User provides screenshot if incorrect
  \`\`\`

  **Commit**: NO (user didn't request commit)
  - Files: `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx`

---

## Success Criteria

### Verification Commands
```bash
# 1. Local build test
cd Infrastructure/frontend && npm run build  # Expected: exit code 0

# 2. Deploy to test
cd /home/antoine/ProjectCEA && ./deploy.sh  # Expected: all health checks pass
```

### Final Checklist
- [ ] Moon duration calculated correctly for midnight-crossing case
- [ ] Moon band width = moonDuration in minutes → percentage
- [ ] Sun band width = 100 - moonWidth (fills remaining)
- [ ] Build succeeds without errors
- [ ] Deploy script completes successfully
- [ ] Guardrails respected: no changes outside calculation

---

## Gap Classification

| Gap Type | Classification | Handling |
|----------|---------------|----------|
| Midnight-crossing case (Start > End) | **CRITICAL: MUST HANDLE** | Actual data (17:00-05:00) crosses midnight |
| Unused `periods` prop | **MINOR: Self-Resolved** | Scope locked: do NOT touch |
| Comment misalignment | **MINOR: Self-Resolved** | Scope locked: do NOT update comments |
| No test infrastructure | **AUTO-RESOLVED** | Manual browser verification accepted |

---

## Metadata

**Metis Review Completed**: 2026-03-20
**Plan Version**: 2.0 (REVISED - added midnight-crossing handling)
**Confidence**: High - calculation fix with verified data
**Risk**: Low - isolated change, handles actual use case
