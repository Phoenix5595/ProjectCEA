# Merge UI Branch to Production

## TL;DR

> **Quick Summary**: Merge the `ui/frontend-modernization` branch into `main` to bring all frontend improvements to production. After successful merge, delete the ProjectCEA-ui folder.
> 
> **Deliverables**:
> - Commit uncommitted changes in ProjectCEA-ui (frontend)
> - Fix weather service VRB parsing bug and commit backend fixes
> - Merge ui/frontend-modernization into main
> - Push merged main to remote
> - Build and deploy frontend
> - Verify weather display works in dashboard
> - Delete ProjectCEA-ui worktree and remote branch
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: NO - sequential (dependencies)
> **Critical Path**: Commit changes → Merge → Build/Deploy → Cleanup

---

## Context

### Current State

**ProjectCEA (main)**:
- Branch: `main`
- Remote: `git@github.com:Phoenix5595/ProjectCEA.git`
- 0 commits ahead of ui/frontend-modernization

**ProjectCEA-ui (ui/frontend-modernization)**:
- Branch: `ui/frontend-modernization`
- Remote: Same repository
- **39 commits** ahead of main
- **6 uncommitted files** with changes

### Changes Summary

**39 commits** to merge including:
- Tailwind CSS 3 → 4 migration
- shadcn/ui integration
- Semantic color tokens
- Theme switching system
- Layout fixes and improvements
- Performance optimizations

**7 uncommitted files** in ProjectCEA-ui:
- `Layout.tsx` (2 lines changed)
- `Sidebar.tsx` (4 lines changed)
- `TopRibbon.tsx` (3 lines changed)
- `VerticalLightsBlock.tsx` (8 lines changed)
- `Dashboard.tsx` (1134 lines changed - major refactor)
- `api.ts` (7 lines changed - timeout + cluster fixes)
- `package.json` (Needs peer dependency fix for vitest)

### Merge Type

**Fast-forward merge** - No divergent commits between branches.

---

## Work Objectives

### Core Objective
Merge all frontend improvements from ui/frontend-modernization into main and deploy to production.

### Concrete Deliverables
1. Uncommitted frontend changes committed to ui/frontend-modernization
2. Weather service VRB parsing fix committed to main
3. Backend fixes (clusterA/B, pressure units) committed to main
4. ui/frontend-modernization merged into main
5. main pushed to remote
6. Frontend built and deployed
7. Weather display verified working in dashboard
8. ProjectCEA-ui worktree removed

### Definition of Done
- [ ] `git log main..ui/frontend-modernization` shows 0 commits
- [ ] `./deploy.sh` completes successfully
- [ ] Dashboard loads correctly
- [ ] Weather display shows Quebec City weather data
- [ ] ⚠️ **User explicitly approved Step 8 cleanup**
- [ ] `ProjectCEA-ui` git worktree removed

### Must Have
- All uncommitted changes preserved
- Clean merge with no conflicts
- Production deployment successful

### Must NOT Have
- Do NOT lose uncommitted changes
- Do NOT force push
- Do NOT skip build verification

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (npm run build)
- **Automated tests**: YES (vitest)
- **Framework**: vite build + vitest

### Agent-Executed QA Scenarios

```
Scenario: Frontend builds successfully
  Tool: Bash (npm)
  Steps:
    1. cd /home/antoine/ProjectCEA/Infrastructure/frontend
    2. npm run build
    3. Assert: exit code 0
  Expected Result: Build succeeds
  Evidence: Build output

Scenario: Dashboard loads correctly
  Tool: Bash (curl)
  Steps:
    1. curl -s http://localhost:8001/
    2. Assert: HTTP 200
    3. Assert: response contains "Siberian Jungle"
  Expected Result: Dashboard accessible
  Evidence: Response body
```

---

## Execution Strategy

### Sequential Steps

```
Step 1: Fix package.json peer dependency & commit uncommitted changes in ProjectCEA-ui
Step 2: Push ui/frontend-modernization to remote
Step 3: Fix weather service VRB parsing + commit uncommitted backend fixes in ProjectCEA main
Step 4: Checkout main and merge ui/frontend-modernization locally
Step 5: Push main to remote
Step 6: Run deploy.sh
Step 7: Verify deployment (including weather display)
Step 8: ⚠️ WAIT for user approval → Remove ProjectCEA-ui worktree and remote branch
```

---

## TODOs

- [ ] 1. Fix package.json and commit all changes in ProjectCEA-ui

  **What to do**:
  - Open `Infrastructure/frontend/package.json`
  - Change `"@vitest/coverage-v8": "^1.0.0"` to `"@vitest/coverage-v8": "^4.0.18"` (matches vitest version to fix `npm ci` conflict)
  - Run `npm install` to update `package-lock.json`
  - Run `ruff check --fix .` and `ruff format .` in `/home/antoine/ProjectCEA-ui`
  - Stage all modified files
  - Commit with message: `feat(frontend): dashboard performance, layout fixes, clusterA/B support, vitest peer dependency fix`
  - Files to commit:
    - `Infrastructure/frontend/package.json`
    - `Infrastructure/frontend/package-lock.json`
    - `Infrastructure/frontend/src/components/Layout.tsx`
    - `Infrastructure/frontend/src/components/Sidebar.tsx`
    - `Infrastructure/frontend/src/components/TopRibbon.tsx`
    - `Infrastructure/frontend/src/components/VerticalLightsBlock.tsx`
    - `Infrastructure/frontend/src/pages/Dashboard.tsx`
    - `Infrastructure/frontend/src/services/api.ts`

  **Must NOT do**:
  - Do NOT add unrelated files
  - Do NOT use generic commit message

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple git commit

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 2
  - **Blocked By**: None

  **Acceptance Criteria**:
  - [ ] `ruff check .` passes with 0 errors
  - [ ] `git status` shows clean working tree
  - [ ] Commit created with all modified files

  **Commit**: YES
  - Message: `feat(frontend): dashboard performance, layout fixes, clusterA/B support, vitest peer dependency fix`
  - Files: All modified files

---

- [ ] 2. Push ui/frontend-modernization to remote

  **What to do**:
  - Push the branch to origin: `git push origin ui/frontend-modernization`
  - Verify push succeeded

  **Must NOT do**:
  - Do NOT force push

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple git push

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 3
  - **Blocked By**: Task 1

  **Acceptance Criteria**:
  - [ ] `git log origin/ui/frontend-modernization` shows new commit

  **Commit**: NO

---

- [ ] 3. Fix weather service VRB parsing and commit uncommitted backend fixes in ProjectCEA main

  **What to do**:
  - In `/home/antoine/ProjectCEA/Infrastructure/weather-service/app/weather_client.py`:
    - Find the wind direction parsing code (around line 131): `weather_data["wind_direction"] = int(wdir)`
    - Change it to handle "VRB" (variable wind direction):
      ```python
      if wdir is not None:
          try:
              weather_data["wind_direction"] = int(wdir)
          except ValueError:
              # Handle "VRB" (variable) wind direction - skip it
              if str(wdir).upper() != "VRB":
                  logger.warning(f"Invalid wind direction value: {wdir}")
      ```
  - Run `ruff check --fix .` and `ruff format .` in `/home/antoine/ProjectCEA`
  - Stage and commit the uncommitted backend fixes:
    - `Infrastructure/backend/app/database.py` (clusterA/clusterB support)
    - `Infrastructure/backend/app/routes/sensors.py` (clusterA/clusterB support)
    - `Infrastructure/weather-service/app/routes/weather.py` (timestamp fix)
    - `Infrastructure/weather-service/app/weather_client.py` (pressure fix + VRB fix)
  - Commit message: `fix(backend): weather VRB parsing, pressure units, clusterA/B sensor suffix support`

  **Must NOT do**:
  - Do NOT commit `.sisyphus/` files (they are planning artifacts)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Bug fix + commit

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 4
  - **Blocked By**: Task 2

  **Acceptance Criteria**:
  - [ ] `ruff check .` passes with 0 errors
  - [ ] `git status` shows clean working tree (except .sisyphus/)
  - [ ] Commit created with 4 backend files

  **Commit**: YES
  - Message: `fix(backend): weather VRB parsing, pressure units, clusterA/B sensor suffix support`
  - Files: 4 backend files

---

- [ ] 4. Checkout main and merge ui/frontend-modernization

  **What to do**:
  - In `ProjectCEA` directory
  - `git checkout main`
  - Merge the local branch: `git merge ui/frontend-modernization`
  - Expect: Fast-forward merge
  - **Fallback**: If conflicts occur, resolve them manually, `git add .`, `git commit -m "Merge branch ui/frontend-modernization"`, and proceed.

  **Must NOT do**:
  - Do NOT use --force
  - Do NOT skip conflict resolution if any

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple git merge

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 5
  - **Blocked By**: Task 3

  **Acceptance Criteria**:
  - [ ] Merge completes without conflicts
  - [ ] `git log` shows all 40 commits from ui branch

  **Commit**: NO (merge commit created automatically)

---

- [ ] 5. Push main to remote

  **What to do**:
  - Push main: `git push origin main`
  - Verify push succeeded

  **Must NOT do**:
  - Do NOT force push

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple git push

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 6
  - **Blocked By**: Task 4

  **Acceptance Criteria**:
  - [ ] Push succeeds
  - [ ] Remote main has all commits

  **Commit**: NO

---

- [ ] 6. Run deploy.sh

  **What to do**:
  - In `ProjectCEA` directory, run `./deploy.sh`
  - Ensure the script succeeds, including the frontend build (`npm ci` and `npm run build`) and service restart
  - **Fallback**: If `deploy.sh` fails, run `./rollback.sh` immediately, verify `main` is stable, and abort the task.

  **Must NOT do**:
  - Do NOT skip build verification

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Build + deploy
  - **Tools needed**: `Bash`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 7
  - **Blocked By**: Task 5

  **Acceptance Criteria**:
  - [ ] `./deploy.sh` exits 0
  - [ ] Expected output shows frontend built successfully and services restarted

  **Commit**: NO

---

- [ ] 7. Verify deployment (including weather display)

  **What to do**:
  - Test: `curl -s http://localhost:8001/api/health`
  - Verify dashboard loads: `curl -s http://localhost:8001/`
  - **Verify weather service works**: `curl -s http://localhost:8003/weather/latest` should return valid JSON with weather data (not `{"error":""}`)
  - Check service logs: `journalctl -u automation-service -n 50 --no-pager`
  - Check weather logs: `journalctl -u weather-service -n 20 --no-pager | grep -i error`
  - Verify that the updated frontend assets are being served correctly without runtime crashes

  **Must NOT do**:
  - Do NOT skip verification

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple verification

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 8
  - **Blocked By**: Task 6

  **Acceptance Criteria**:
  - [ ] Dashboard returns HTTP 200
  - [ ] `http://localhost:8001/api/health` returns HTTP 200 / healthy
  - [ ] `http://localhost:8003/weather/latest` returns valid weather data (temperature, humidity, pressure)
  - [ ] No crash errors in service logs

  **Commit**: NO

---

- [ ] 8. Delete ProjectCEA-ui worktree and remote branch ⚠️ **REQUIRES EXPLICIT USER APPROVAL**

  **What to do**:
  - **WAIT for user's explicit "go" before executing this step**
  - Verify all changes are merged in `ProjectCEA` main
  - Remove git worktree: `git worktree remove /home/antoine/ProjectCEA-ui` (Run this in `/home/antoine/ProjectCEA`)
  - Delete remote branch: `git push origin --delete ui/frontend-modernization`

  **Must NOT do**:
  - Do NOT execute without user approval
  - Do NOT delete before verifying merge is complete
  - Do NOT use `rm -rf` since it is a git worktree

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Cleanup

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: None
  - **Blocked By**: Task 7 + **User Approval**

  **Acceptance Criteria**:
  - [ ] User explicitly approved this step
  - [ ] `ProjectCEA-ui` worktree removed
  - [ ] Remote `ui/frontend-modernization` branch deleted
  - [ ] All code preserved in `ProjectCEA` main

  **Commit**: NO

---

## Commit Strategy

| After Task | Message | Files |
|------------|---------|-------|
| 1 | `feat(frontend): dashboard performance, layout fixes, clusterA/B support, vitest peer dependency fix` | Frontend files in ProjectCEA-ui |
| 3 | `fix(backend): weather VRB parsing, pressure units, clusterA/B sensor suffix support` | Backend files in ProjectCEA |

---

## Success Criteria

### Verification Commands
```bash
# Verify merge
cd /home/antoine/ProjectCEA
git log --oneline main..ui/frontend-modernization  # Should be empty

# Verify deployment
curl -s http://localhost:8001/  # Should return 200

# Verify weather service
curl -s http://localhost:8003/weather/latest  # Should return valid JSON with weather data
```

### Final Checklist
- [ ] `package.json` dependency fix committed (Task 1)
- [ ] Weather service VRB parsing fix committed (Task 3)
- [ ] ui/frontend-modernization merged into main
- [ ] main pushed to remote
- [ ] `./deploy.sh` executed successfully
- [ ] Dashboard loads correctly
- [ ] Weather display shows Quebec City data
- [ ] ⚠️ **User explicitly approved cleanup**
- [ ] `ProjectCEA-ui` worktree removed
