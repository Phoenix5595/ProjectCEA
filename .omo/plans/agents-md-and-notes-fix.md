# AGENTS.md Updates & Notes Persistence Fix

## TL;DR

> **Quick Summary**: Two tasks - (1) Review and update all 11 AGENTS.md files to match current codebase, (2) Fix frontend notes not persisting across machines by migrating from file-based to database storage.
> 
> **Deliverables**:
> - Updated AGENTS.md files reflecting current code structure
> - Notes persistence working across all machines via TimescaleDB storage
> - New `notes` table in database schema
> - Updated `notes.py` backend to use database
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Task 2 (notes fix) → Verification | Task 1 (AGENTS.md) independent

---

## Context

### Original Request
User wants to:
1. Update all AGENTS.md files that need updating
2. Investigate why frontend notes aren't persisting across sessions/machines

### Research Findings

**AGENTS.md Files Found (11 total)**:
- `/home/antoine/ProjectCEA/AGENTS.md` (root - comprehensive)
- `/home/antoine/ProjectCEA/Infrastructure/AGENTS.md`
- `/home/antoine/ProjectCEA/Infrastructure/automation-service/AGENTS.md`
- `/home/antoine/ProjectCEA/Infrastructure/automation-service/app/control/AGENTS.md`
- `/home/antoine/ProjectCEA/Infrastructure/frontend/AGENTS.md`
- `/home/antoine/ProjectCEA/Infrastructure/frontend/grafana/AGENTS.md`
- `/home/antoine/ProjectCEA/Infrastructure/database/AGENTS.md`
- `/home/antoine/ProjectCEA/Infrastructure/backend/AGENTS.md`
- `/home/antoine/ProjectCEA/Infrastructure/can-processor-service/AGENTS.md`
- `/home/antoine/ProjectCEA/Sensor_Nodes/AGENTS.md`
- `/home/antoine/ProjectCEA/.opencode/philosophy/AGENTS.md`

**Notes Persistence - Root Cause Identified**:
- Frontend `VerticalNotesBlock.tsx` calls `apiClient.getNotes/saveNotes` to automation service (port 8001)
- **On API failure, falls back to `localStorage`** - this is machine/browser-specific!
- Backend `notes.py` currently stores at `/var/lib/projectcea/notes` (file-based)
- API endpoints: `GET/PUT /api/notes/{location}/{cluster}/{mode}`

**Root Cause**: File-based storage is fragile (directory/permission issues) and not shared across machines.

**Solution**: Migrate to database storage (TimescaleDB) - consistent with project architecture:
- Database already shared across all machines
- Backup/restore comes free with existing DB strategy
- Eliminates filesystem permission issues
- Can add timestamps, versioning, audit trail

---

## Work Objectives

### Core Objective
Fix notes to persist server-side and update AGENTS.md files to reflect current codebase structure.

### Concrete Deliverables
- Notes persist across machines via API storage
- All AGENTS.md files accurate to current code

### Definition of Done
- [ ] Notes saved on Machine A appear on Machine B after reload
- [ ] All AGENTS.md files reviewed and updated where needed

### Must Have
- Server-side note persistence working
- AGENTS.md accuracy for key services

### Must NOT Have (Guardrails)
- No changes to note data format or structure
- No refactoring of notes feature beyond fixing persistence
- Don't update AGENTS.md files that are already accurate
- Don't add new features to notes system

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (project has test setup)
- **Automated tests**: Tests-after (if needed for notes fix)
- **Framework**: pytest for backend

### Agent-Executed QA Scenarios (MANDATORY)

**Notes Persistence Verification**:
- Save a note via the UI
- Check server-side storage exists
- Load from different browser/machine context
- Verify note content matches

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - PARALLEL):
├── Task 1: Diagnose & fix notes persistence issue
└── Task 2: Audit AGENTS.md files (compare to code)

Wave 2 (After Wave 1):
├── Task 3: Update AGENTS.md files that need changes
└── Task 4: Verify notes work across machines
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 4 | 2 |
| 2 | None | 3 | 1 |
| 3 | 2 | None | 4 |
| 4 | 1 | None | 3 |

---

## TODOs

- [ ] 1. Create Notes Database Schema

  **What to do**:
  1. Create migration SQL for `notes` table in TimescaleDB:
     ```sql
     CREATE TABLE IF NOT EXISTS notes (
       id SERIAL PRIMARY KEY,
       location VARCHAR(100) NOT NULL,
       cluster VARCHAR(100) NOT NULL,
       mode VARCHAR(50) NOT NULL DEFAULT 'default',
       content TEXT NOT NULL DEFAULT '',
       created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       UNIQUE(location, cluster, mode)
     );
     
     CREATE INDEX idx_notes_lookup ON notes(location, cluster, mode);
     ```
  2. Add to `Infrastructure/database/cea_schema.sql` or create migration file
  3. Run migration against database
  4. Verify table created

  **Must NOT do**:
  - Don't use TimescaleDB hypertable (notes don't need time-series optimization)
  - Don't add unnecessary columns

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple schema addition, straightforward SQL
  - **Skills**: []
    - No special skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential - must complete before Task 2
  - **Blocks**: Task 2 (backend update)
  - **Blocked By**: None

  **References**:
  - `Infrastructure/database/cea_schema.sql` - Existing schema patterns
  - `Infrastructure/database/AGENTS.md` - Database conventions

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Notes table exists with correct structure
    Tool: Bash (psql)
    Steps:
      1. psql -U cea -d projectcea -c "\d notes"
      2. Assert: Table exists
      3. Assert: Columns include: id, location, cluster, mode, content, created_at, updated_at
      4. Assert: UNIQUE constraint on (location, cluster, mode)
    Expected Result: Table structure matches specification
    Evidence: psql output captured

  Scenario: Notes table accepts inserts
    Tool: Bash (psql)
    Steps:
      1. psql -U cea -d projectcea -c "INSERT INTO notes (location, cluster, mode, content) VALUES ('Test Room', 'main', 'default', 'Test content') ON CONFLICT (location, cluster, mode) DO UPDATE SET content = EXCLUDED.content, updated_at = NOW() RETURNING *;"
      2. Assert: Row inserted/updated successfully
      3. psql -U cea -d projectcea -c "DELETE FROM notes WHERE location = 'Test Room';"
    Expected Result: UPSERT pattern works correctly
    Evidence: Query output captured
  ```

  **Commit**: YES
  - Message: `feat(db): add notes table for persistent note storage`
  - Files: `Infrastructure/database/cea_schema.sql` or migration file
  - Pre-commit: psql table verification

---

- [ ] 2. Update Notes Backend to Use Database

  **What to do**:
  1. Modify `Infrastructure/automation-service/app/routes/notes.py`:
     - Remove file-based storage logic
     - Add database connection (use existing db patterns from automation-service)
     - `get_notes`: SELECT from notes table
     - `save_notes`: UPSERT (INSERT ON CONFLICT UPDATE)
  2. Add database dependency to notes router if not present
  3. Remove `NOTES_DATA_DIR` environment variable usage
  4. Handle migration: optionally import existing file-based notes to DB

  **Must NOT do**:
  - Don't change API contract (same endpoints, same request/response format)
  - Don't remove localStorage fallback in frontend (keep as safety net)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Focused backend change, clear patterns to follow
  - **Skills**: []
    - No special skills needed

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential - after Task 1
  - **Blocks**: Task 5 (verification)
  - **Blocked By**: Task 1 (schema)

  **References**:
  - `Infrastructure/automation-service/app/routes/notes.py` - Current implementation to modify
  - `Infrastructure/automation-service/app/database.py` - DB connection patterns (if exists)
  - `Infrastructure/backend/app/database.py` - Alternative DB pattern reference
  - `Infrastructure/automation-service/app/routes/` - Other routes for DB usage patterns

  **Acceptance Criteria**:
  - [ ] `notes.py` uses database instead of filesystem
  - [ ] API contract unchanged (same endpoints, same JSON format)
  - [ ] GET returns empty string for non-existent notes
  - [ ] PUT creates or updates note

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: API GET notes returns 200 and fetches from DB
    Tool: Bash (curl + psql)
    Steps:
      1. psql -U cea -d projectcea -c "INSERT INTO notes (location, cluster, mode, content) VALUES ('API Test', 'main', 'default', 'DB content') ON CONFLICT (location, cluster, mode) DO UPDATE SET content = EXCLUDED.content;"
      2. curl -s http://localhost:8001/api/notes/API%20Test/main/default
      3. Assert: Response contains "DB content"
      4. psql -U cea -d projectcea -c "DELETE FROM notes WHERE location = 'API Test';"
    Expected Result: API reads from database
    Evidence: curl response captured

  Scenario: API PUT notes persists to database
    Tool: Bash (curl + psql)
    Steps:
      1. curl -s -X PUT -H "Content-Type: application/json" -d '"Test note via API"' http://localhost:8001/api/notes/PUT%20Test/main/default
      2. Assert: HTTP status is 200
      3. psql -U cea -d projectcea -c "SELECT content FROM notes WHERE location = 'PUT Test' AND cluster = 'main' AND mode = 'default';"
      4. Assert: Query returns "Test note via API"
      5. psql -U cea -d projectcea -c "DELETE FROM notes WHERE location = 'PUT Test';"
    Expected Result: Note saved to database via API
    Evidence: psql output captured

  Scenario: API handles non-existent notes gracefully
    Tool: Bash (curl)
    Steps:
      1. curl -s -w "\n%{http_code}" http://localhost:8001/api/notes/NonExistent/cluster/mode
      2. Assert: HTTP status is 200
      3. Assert: Response body is empty string or ""
    Expected Result: No error for missing notes
    Evidence: Response captured
  ```

  **Commit**: YES
  - Message: `feat(notes): migrate from file storage to database persistence`
  - Files: `Infrastructure/automation-service/app/routes/notes.py`
  - Pre-commit: API tests pass

---

- [ ] 3. Audit All AGENTS.md Files (init-deep methodology)

  **What to do**:
  
  **Phase 1: Structural Analysis** (per directory)
  ```
  For each service/component directory:
  - File count (*.py, *.ts, *.tsx)
  - Subdirectory count
  - Code concentration ratio
  - Module boundary clarity
  - Symbol density (exports, classes, functions)
  ```
  
  **Phase 2: Complexity Scoring**
  ```
  Score = (file_count × 3) + (subdir_count × 2) + (code_ratio × 2) + (boundary × 2) + (symbols × 2)
  - Score > 15: AGENTS.md required
  - Score 8-15: AGENTS.md if distinct domain
  - Score < 8: Skip (reference parent)
  ```
  
  **Phase 3: Gap Analysis** (focus areas per user requirements)
  - **Architecture changes**: New services, modified data flows, changed responsibilities
  - **Outdated references**: Dead file paths, renamed functions, deprecated APIs
  - **Hierarchy compliance**: Child files must reference root, never repeat parent content
  
  **Files to audit**:
  - `Infrastructure/AGENTS.md` - Service list, ports, data flow
  - `Infrastructure/automation-service/AGENTS.md` - Control logic, routes, config
  - `Infrastructure/automation-service/app/control/AGENTS.md` - PID, scheduling, devices
  - `Infrastructure/frontend/AGENTS.md` - Components, state, API integration
  - `Infrastructure/frontend/grafana/AGENTS.md` - Dashboard catalog
  - `Infrastructure/database/AGENTS.md` - Schema, tables, queries
  - `Infrastructure/backend/AGENTS.md` - Routes, WebSocket, data retrieval
  - `Infrastructure/can-processor-service/AGENTS.md` - Message decoding, Redis keys
  - `Sensor_Nodes/AGENTS.md` - Firmware versions, sensor types
  
  **Skip**: `.opencode/philosophy/AGENTS.md` (meta), root `AGENTS.md` (recently updated)

  **Must NOT do**:
  - Don't make changes yet - audit only
  - Don't recommend updates for accurate files
  - Don't suggest adding content that duplicates root AGENTS.md

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Read-heavy comparison task, no implementation
  - **Skills**: []
    - No special skills needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Task 4 (updates)
  - **Blocked By**: None

  **References**:
  - All AGENTS.md files listed above
  - Corresponding source code directories

  **Acceptance Criteria**:
  - [ ] Each AGENTS.md reviewed against actual code
  - [ ] Complexity score computed for each directory
  - [ ] Summary includes: file path, complexity score, status (accurate/needs-update/create-new), specific changes
  - [ ] Hierarchy violations flagged (child duplicating parent content)
  - [ ] Summary saved to `.sisyphus/drafts/agents-md-audit.md`

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Audit summary follows init-deep methodology
    Tool: Bash
    Steps:
      1. cat .sisyphus/drafts/agents-md-audit.md
      2. Assert: Contains complexity score for each directory
      3. Assert: Each entry has status (accurate/needs-update/create-new)
      4. Assert: Files marked needs-update list specific changes (architecture/outdated refs)
      5. Assert: Hierarchy compliance checked (no duplication of root content)
    Expected Result: Comprehensive audit with scoring and actionable items
    Evidence: File contents captured
  ```

  **Commit**: NO (just audit, no code changes)

---

- [ ] 4. Update AGENTS.md Files Based on Audit (init-deep style)

  **What to do**:
  
  **For existing files needing updates**:
  1. Read audit summary from `.sisyphus/drafts/agents-md-audit.md`
  2. Fix architecture changes (new/removed services, changed data flows)
  3. Fix outdated references (dead paths, renamed functions, deprecated APIs)
  4. Ensure hierarchical compliance: add "See root AGENTS.md for X" references
  
  **For new AGENTS.md files** (where complexity score warranted):
  1. Follow init-deep structure:
     - `## Overview` - 2-3 sentences, what this component does
     - `## Structure` - File tree with annotations
     - `## Key Concepts` - Domain-specific terms
     - `## Where to Look` - Table: Task → File → Why
     - `## Conventions` - Local patterns that differ from root
     - `## Anti-patterns` - What NOT to do here
  2. Max 30-80 lines for subdirectory AGENTS.md
  3. NEVER repeat content from root AGENTS.md - reference it instead
  
  **Hierarchical Reference Pattern**:
  ```markdown
  > For system-wide conventions, see [root AGENTS.md](../../AGENTS.md)
  > This file covers only [component]-specific patterns.
  ```
  
  **Telegraphic Style Requirements**:
  - Tables over prose
  - Bullet points, not paragraphs
  - File paths, not descriptions of file locations
  - Commands, not explanations of how to run things

  **Must NOT do**:
  - Don't rewrite files that are accurate
  - Don't add generic advice (covered in root)
  - Don't exceed 80 lines for subdirectory AGENTS.md
  - Don't duplicate content from parent AGENTS.md

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Documentation updates requiring technical accuracy
  - **Skills**: []
    - No special skills needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 4)
  - **Blocks**: None
  - **Blocked By**: Task 3 (audit)

  **References**:
  - `.sisyphus/drafts/agents-md-audit.md` - Audit results
  - Each AGENTS.md file and corresponding source directories

  **Acceptance Criteria**:
  - [ ] All files marked "needs-update" have been updated
  - [ ] New AGENTS.md created where complexity score warranted
  - [ ] Updated files accurately reflect current code structure
  - [ ] No broken internal references (file paths exist)
  - [ ] Hierarchical references added ("See root AGENTS.md for X")
  - [ ] No child file exceeds 80 lines
  - [ ] No duplication of root AGENTS.md content

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Updated AGENTS.md files match code structure
    Tool: Bash
    Steps:
      1. For each updated AGENTS.md, extract documented file paths
      2. Verify each documented path exists: ls -la <path>
      3. Assert: All documented files/directories exist
    Expected Result: No broken references in documentation
    Evidence: Verification output captured

  Scenario: Hierarchical compliance verified
    Tool: Bash (grep)
    Steps:
      1. For each subdirectory AGENTS.md, grep for "See root" or "see root AGENTS.md"
      2. Assert: Each child file contains at least one root reference
      3. wc -l on each subdirectory AGENTS.md
      4. Assert: No file exceeds 80 lines
    Expected Result: All child files reference root, stay concise
    Evidence: grep and wc output captured

  Scenario: No content duplication with root
    Tool: Bash
    Steps:
      1. Extract key sections from root AGENTS.md (Non-Negotiable Rules, Performance Requirements)
      2. For each child AGENTS.md, grep for duplicated phrases
      3. Assert: No child file duplicates root section content verbatim
    Expected Result: Children reference, not repeat, root content
    Evidence: grep output captured
  ```

  **Commit**: YES
  - Message: `docs(agents): update AGENTS.md files to reflect current codebase`
  - Files: All modified AGENTS.md files
  - Pre-commit: None

---

- [ ] 5. Verify Notes Persistence Across Machines

  **What to do**:
  1. Clear any localStorage notes on test machine
  2. Create a unique test note via the UI
  3. Verify note saved to database (not filesystem)
  4. Access dashboard from different machine/browser
  5. Verify same note appears
  6. Clean up test note from database

  **Must NOT do**:
  - Don't leave test data in production

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple verification task
  - **Skills**: [`playwright`]
    - Playwright: Browser automation for cross-context testing

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Task 4)
  - **Blocks**: None
  - **Blocked By**: Task 2 (backend update)

  **References**:
  - Task 2 implementation
  - `Infrastructure/frontend/src/components/VerticalNotesBlock.tsx`

  **Acceptance Criteria**:
  - [ ] Notes created in one browser context appear in another
  - [ ] Notes are stored in database (verify with psql)
  - [ ] localStorage fallback NOT used when API works
  - [ ] Test cleanup completed

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: End-to-end cross-machine persistence via database
    Tool: Playwright + Bash
    Preconditions: Task 1 & 2 complete, services restarted
    Steps:
      1. Browser Context A: Navigate to http://localhost:5173 (or production URL)
      2. Navigate to Flower Room dashboard
      3. Execute JS: localStorage.removeItem('cea-notes-Flower Room-main-default')
      4. Locate notes textarea
      5. Clear existing content
      6. Type: "DB_PERSISTENCE_TEST_" + Date.now()
      7. Wait 1 second for debounce
      8. Capture the typed value as EXPECTED_NOTE
      9. Screenshot: .sisyphus/evidence/task-5-note-saved.png
      10. Verify in DB: psql -U cea -d projectcea -c "SELECT content FROM notes WHERE location = 'Flower Room' AND cluster = 'main';"
      11. Assert: DB contains EXPECTED_NOTE
      12. Create new Browser Context B (incognito)
      13. Navigate to same dashboard URL
      14. Wait for notes textarea visible
      15. Assert: textarea value contains EXPECTED_NOTE
      16. Screenshot: .sisyphus/evidence/task-5-note-persisted.png
    Expected Result: Note saved to DB in Context A appears in Context B
    Failure Indicators: textarea empty or contains different text, DB query returns no rows
    Evidence: 
      - .sisyphus/evidence/task-5-note-saved.png
      - .sisyphus/evidence/task-5-note-persisted.png
      - psql output

  Scenario: API uses database, not filesystem
    Tool: Bash
    Steps:
      1. curl -X PUT -H "Content-Type: application/json" -d '"Filesystem check"' http://localhost:8001/api/notes/FS%20Check/main/default
      2. Assert: No file created at /var/lib/projectcea/notes/
      3. psql -U cea -d projectcea -c "SELECT content FROM notes WHERE location = 'FS Check';"
      4. Assert: DB contains "Filesystem check"
      5. Cleanup: psql -U cea -d projectcea -c "DELETE FROM notes WHERE location = 'FS Check';"
    Expected Result: Data in DB, not filesystem
    Evidence: Commands output captured
  ```

  **Commit**: NO (verification only)

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `fix(notes): ensure server-side persistence directory exists` | scripts or config if needed | curl API test |
| 3 | `docs(agents): update AGENTS.md files to reflect current codebase` | AGENTS.md files | file paths exist |

---

## Success Criteria

### Verification Commands
```bash
# Notes directory exists with correct permissions
ls -la /var/lib/projectcea/notes  # Should show cea:cea ownership

# API responds correctly
curl -s http://localhost:8001/api/notes/Flower%20Room/main/default  # Should return 200

# Save and retrieve note
curl -X PUT -H "Content-Type: application/json" -d '"test"' http://localhost:8001/api/notes/Test/test/test
curl http://localhost:8001/api/notes/Test/test/test  # Should return "test"
```

### Final Checklist
- [ ] Notes persist across different machines/browsers
- [ ] All AGENTS.md files reviewed
- [ ] Outdated AGENTS.md files updated
- [ ] No broken file references in documentation
