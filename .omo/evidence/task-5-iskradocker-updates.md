# Task 5: Immich pgvecto-rs → VectorChord Migration Runbook

## Completion Status: ✅ COMPLETE

## Artifacts Created
- `/home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md`

## Verification Results

### File Existence
```bash
ssh iskradocker 'test -f /home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md'
# Result: EXISTS (exit 0)
```

### Content Verification
```bash
# Old image reference present
ssh iskradocker 'grep -q "pgvecto-rs:pg14-v0.2.0" ...'
# Result: FOUND_OLD

# New image reference present
ssh iskradocker 'grep -q "vectorchord0.4.3-pgvectors0.2.0" ...'
# Result: FOUND_NEW

# Section count >= 8
ssh iskradocker 'grep -c "^## " ...'
# Result: 9 (1 title + 8 sections)
```

### Git Commit
```bash
ssh iskradocker 'git -C /home/antoine/docker/compose log --oneline -- docs/runbooks/'
# Result:
# c99b54d docs(runbooks): add immich pgvecto→VectorChord migration runbook
# d7d1fb0 chore(compose): initial commit of iskra homelab compose configs
```

## Sections Present
1. ✅ Preconditions
2. ✅ Step 1: DB Dump
3. ✅ Step 2: Kopia Snapshot
4. ✅ Step 3: Update Compose
5. ✅ Step 4: Pull New Images
6. ✅ Step 5: Recreate Containers
7. ✅ Step 6: Monitor Migration
8. ✅ Step 7: Post-Migration Verification
9. ✅ Step 8: Important Notes

## Adversarial Checks
- **stale_state**: Runbook references current pgvecto-rs image `pg14-v0.2.0` and target VectorChord image `14-vectorchord0.4.3-pgvectors0.2.0` ✅
- **misleading_success_output**: Verified content with grep, not just file existence ✅
- **dirty_worktree**: File is committed to git (commit c99b54d) ✅

## Notes
- Runbook committed to `/home/antoine/docker/compose/` git repository
- All 8 required sections present plus title header
- DB dump step (Step 1) is explicit and mandatory
- Instructions always group all 4 containers together (never pull postgres alone)
