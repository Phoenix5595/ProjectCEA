Decision log: ZoneConfig stale closure fix

- assessment: The frontend ZoneConfig.tsx already applied useCallback wrappers to handleModeChange and handleSave and included them in the useEffect dependency array. No code changes required.
- rationale: Keeping stable callbacks prevents stale closures in useEffect-driven actions; ensuring dependencies include the callbacks maintains React's expected re-run behavior.
- verification plan: Run npm run build to confirm no type errors; perform a grep check for useCallback presence; ensure dependencies include handleSave and handleModeChange in the effect hook.

Date: 2026-02-28
