# God Objects & Performance Audit - ProjectCEA

## TL;DR

> **Quick Summary**: Comprehensive audit of ProjectCEA codebase to identify god objects (SRP violations) and performance anti-patterns, then refactor the most critical issues.
> 
> **Deliverables**:
> - Audit report of god objects with severity ratings
> - Audit report of performance issues with impact ratings
> - Refactored code for HIGH severity items
> 
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Audit → Prioritize → Refactor critical items

---

## Context

### Original Request
Check for god objects and performance errors in ProjectCEA code.

### Scope
- Infrastructure/automation-service/ (CRITICAL - control loop, must be fast)
- Infrastructure/backend/ (sensor API)
- Infrastructure/can-processor-service/ (CAN message processing)
- Infrastructure/soil-sensor-service/ (Modbus sensors)
- Infrastructure/frontend/src/ (React dashboard)

---

## Work Objectives

### Core Objective
Identify and remediate architectural (god objects) and performance issues that impact system reliability and speed.

### Concrete Deliverables
- `.sisyphus/drafts/god-objects-audit.md` - Full audit report
- `.sisyphus/drafts/performance-audit.md` - Full audit report
- Refactored code for HIGH severity issues

### Definition of Done
- [ ] All services analyzed for god objects
- [ ] All services analyzed for performance anti-patterns
- [ ] HIGH severity items refactored or documented as tech debt
- [ ] No new test failures introduced

### Must Have
- Line counts and method counts for god object detection
- File paths and line numbers for all issues
- Severity ratings (HIGH/MEDIUM/LOW)

### Must NOT Have (Guardrails)
- Do NOT refactor without understanding existing behavior
- Do NOT break existing tests
- Do NOT change public API contracts
- Do NOT refactor MEDIUM/LOW items without explicit approval

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES (pytest for Python, possibly vitest for React)
- **Automated tests**: Run after any refactoring
- **Framework**: pytest

### Agent-Executed QA
After each refactoring:
1. Run `pytest` in affected service
2. Check `ruff check` passes
3. Verify service starts without errors

---

## TODOs

- [ ] 1. Audit Python Services for God Objects

  **What to do**:
  - Find all Python files > 300 lines
  - Count methods per class (>10 = suspect)
  - Count imports per file (>15 = suspect)
  - Identify classes with multiple unrelated responsibilities
  - Rate each: HIGH (clear god object) / MEDIUM / LOW
  
  **Commands to run**:
  ```bash
  # Find largest files
  find Infrastructure -name "*.py" -exec wc -l {} \; | sort -rn | head -30
  
  # Count imports per file
  for f in $(find Infrastructure -name "*.py"); do
    imports=$(grep -c "^import\|^from" "$f" 2>/dev/null || echo 0)
    lines=$(wc -l < "$f")
    echo "$lines lines, $imports imports: $f"
  done | sort -rn | head -20
  
  # Find classes with many methods
  grep -rn "def " Infrastructure --include="*.py" | cut -d: -f1 | uniq -c | sort -rn | head -20
  ```
  
  **Output**: Save to `.sisyphus/drafts/god-objects-audit.md`

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Task 3
  - **Blocked By**: None

  **Acceptance Criteria**:
  - [ ] All 4 Python services analyzed
  - [ ] Audit report saved to drafts
  - [ ] Each suspect rated by severity

---

- [ ] 2. Audit All Services for Performance Issues

  **What to do**:
  - Find N+1 query patterns (DB calls inside loops)
  - Find blocking I/O in async code
  - Find unbounded queries (SELECT without LIMIT)
  - Find inefficient loops (nested loops on large data)
  - Find missing caching opportunities
  - Check React components for re-render issues
  
  **Patterns to grep**:
  ```bash
  # N+1 queries (DB in loops)
  grep -rn "for.*:" --include="*.py" -A5 | grep -E "execute|fetch|await.*query"
  
  # Unbounded queries
  grep -rn "SELECT \*\|fetchall\|fetch_all" --include="*.py" | grep -v "LIMIT\|WHERE.*time"
  
  # Sync calls in async functions
  grep -rn "async def" --include="*.py" -A20 | grep -E "open\(|time\.sleep|requests\."
  
  # React inline functions
  grep -rn "onClick={() =>" --include="*.tsx"
  ```
  
  **Output**: Save to `.sisyphus/drafts/performance-audit.md`

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Task 3
  - **Blocked By**: None

  **Acceptance Criteria**:
  - [ ] Python services analyzed for all anti-patterns
  - [ ] React frontend analyzed
  - [ ] Audit report saved to drafts
  - [ ] Each issue rated by impact

---

- [ ] 3. Prioritize and Create Refactoring Plan

  **What to do**:
  - Review both audit reports
  - Identify HIGH severity god objects
  - Identify HIGH impact performance issues
  - Create prioritized refactoring list
  - Estimate effort for each item
  
  **Must consider**:
  - automation-service issues are CRITICAL (affects control loop)
  - Issues in hot paths get priority
  - Test coverage affects refactoring safety

  **Output**: Update this plan with specific refactoring tasks

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (sequential)
  - **Blocks**: Task 4
  - **Blocked By**: Tasks 1, 2

  **Acceptance Criteria**:
  - [ ] Prioritized list created
  - [ ] HIGH items have refactoring approach defined
  - [ ] Plan updated with specific refactoring tasks

---

- [ ] 4. Refactor HIGH Severity God Objects

  **What to do**:
  - For each HIGH severity god object:
    1. Read the file, understand responsibilities
    2. Identify logical separation points
    3. Extract into focused modules/classes
    4. Update imports in consumers
    5. Run tests after each extraction
  
  **Refactoring approach**:
  - Extract Method → Extract Class → Extract Module
  - One responsibility per class
  - Use composition over inheritance
  - Keep public API stable where possible

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (sequential refactoring)
  - **Blocks**: Task 6
  - **Blocked By**: Task 3

  **Acceptance Criteria**:
  - [ ] Each refactored file < 300 lines
  - [ ] Each class has single responsibility
  - [ ] All tests pass
  - [ ] ruff check passes

---

- [ ] 5. Fix HIGH Impact Performance Issues

  **What to do**:
  - For each HIGH impact issue:
    1. Understand the hot path
    2. Apply appropriate fix:
       - N+1 → Batch queries or JOIN
       - Unbounded → Add LIMIT/time filters
       - Blocking I/O → Use async alternatives
       - React re-renders → Add memo/useCallback
    3. Verify fix doesn't break functionality
    4. Measure improvement if possible

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 4 if different files)
  - **Blocks**: Task 6
  - **Blocked By**: Task 3

  **Acceptance Criteria**:
  - [ ] Each HIGH issue addressed
  - [ ] No N+1 patterns in hot paths
  - [ ] No blocking I/O in async code
  - [ ] All tests pass

---

- [ ] 6. Final Verification

  **What to do**:
  - Run full test suite for all modified services
  - Run linting on all modified files
  - Verify services start correctly
  - Document remaining MEDIUM/LOW items as tech debt

  **Verification Commands**:
  ```bash
  # Python services
  cd Infrastructure/automation-service && pytest
  cd Infrastructure/backend && pytest
  
  # Lint
  ruff check Infrastructure/
  
  # Service health
  systemctl is-active automation-service cea-backend
  ```

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (final step)
  - **Blocked By**: Tasks 4, 5

  **Acceptance Criteria**:
  - [ ] All tests pass
  - [ ] No lint errors
  - [ ] Services running
  - [ ] Tech debt documented

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
├── Task 1: Audit god objects
└── Task 2: Audit performance issues

Wave 2 (After Wave 1):
└── Task 3: Prioritize and plan

Wave 3 (After Wave 2):
├── Task 4: Refactor god objects
└── Task 5: Fix performance issues

Wave 4 (After Wave 3):
└── Task 6: Final verification
```

---

## Commit Strategy

| After Task | Message | Files |
|------------|---------|-------|
| 1-2 | `docs: add god objects and performance audit reports` | .sisyphus/drafts/*.md |
| 4 | `refactor(service): extract X from god object Y` | per refactoring |
| 5 | `perf(service): fix N+1 query in X` | per fix |
| 6 | `docs: document remaining tech debt` | AGENTS.md or similar |

---

## Success Criteria

### Verification Commands
```bash
pytest  # All tests pass
ruff check Infrastructure/  # No lint errors
systemctl is-active automation-service  # Service healthy
```

### Final Checklist
- [ ] No HIGH severity god objects remaining
- [ ] No HIGH impact performance issues in hot paths
- [ ] All tests pass
- [ ] Tech debt documented for MEDIUM/LOW items
